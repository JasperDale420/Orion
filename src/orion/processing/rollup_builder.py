import logging
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from orion.storage.models_gold import GoldTickerRollup
from orion.storage.models_silver import SignalType, SilverSignal

logger = logging.getLogger(__name__)


class RollupBuilder:
    """
    Aggregates Silver Signals (1M OHLCV) into Gold Ticker Rollups (5M, 1H, 1D).
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def build_rollups(self, ticker: str, start_time: datetime, end_time: datetime) -> datetime | None:
        """
        Fetches 1m bars for a ticker and builds rollups for 5m, 1h, 1d.
        Upserts results into GoldTickerRollup table.
        """
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=UTC)

        # 1. Fetch Source Data
        stmt = (
            select(SilverSignal)
            .where(SilverSignal.ticker == ticker)
            .where(SilverSignal.signal_type == SignalType.OHLCV_1M.value)
            .where(SilverSignal.signal_ts_utc >= start_time)
            .where(SilverSignal.signal_ts_utc <= end_time)
            .order_by(SilverSignal.signal_ts_utc.asc())
        )

        result = await self.session.execute(stmt)
        signals = result.scalars().all()

        if not signals:
            logger.info(f"No Silver Signals found for {ticker} in range {start_time}-{end_time}")
            return

        # 2. Convert to Pandas for Resampling
        data = []
        for s in signals:
            f = s.features
            # Assume strict schema from Silver
            data.append(
                {
                    "ts": s.signal_ts_utc,
                    "open": f.get("open"),
                    "high": f.get("high"),
                    "low": f.get("low"),
                    "close": f.get("close"),
                    "volume": f.get("volume"),
                    "vwap": f.get("vwap"),
                }
            )

        df = pd.DataFrame(data)
        df.set_index("ts", inplace=True)
        df.index = pd.to_datetime(df.index, utc=True)

        if df.empty:
            return None

        # Ensure numeric columns to avoid object dtype which raises ZeroDivisionError mathematically
        for col in ["open", "high", "low", "close", "volume", "vwap"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 3. Define Rollup Periods (5m, 1h, 1d, 1w - no 1m as per user preference)
        periods = {"5m": "5min", "1h": "1h", "1d": "1D", "1w": "1W"}

        latest_ts: datetime | None = None
        try:
            latest_ts = df.index.max().to_pydatetime()
        except Exception:
            latest_ts = None

        df["dollar_vol"] = df["vwap"] * df["volume"]

        for period_name, freq in periods.items():
            # Update agg dict for custom vwap
            # We can't do custom funcs easily in resample().agg without lambda, which can be slow or tricky with named columns
            # Strategy: Resample dollar_vol and volume, then divide.

            resampled = (
                df.resample(freq)
                .agg(
                    {
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                        "volume": "sum",
                        "dollar_vol": "sum",
                    }
                )
                .dropna(subset=["open", "high", "low", "close", "volume"])
            )

            if resampled.empty:
                continue

            import numpy as np

            # Avoid division by zero when volume is zero
            safe_volume = resampled["volume"].replace(0, np.nan)
            resampled["vwap"] = resampled["dollar_vol"] / safe_volume
            resampled["vwap"] = resampled["vwap"].fillna(resampled["close"])  # Fallback

            # 4. Persist to Gold
            for ts, row in resampled.iterrows():
                # Upsert Logic? For now, simplistic add.
                # In prod, we'd use merge statement or check existence.
                # Here we blindly create objects. IntegrityError if exists (PK).
                # We should merge. Since we are using SQLAlchemy, we can use merge() if attached or manual check.

                # Check exist? Too slow for bulk.
                # Use Postgres ON CONFLICT (upsert)?
                # For this implementation, we will try to fetch or merge.

                await self.session.merge(
                    GoldTickerRollup(
                        ticker=ticker,
                        period=period_name,
                        timestamp_utc=ts,
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row["volume"],
                        vwap=row["vwap"],
                    )
                )
                # merge returns the instance attached to session

        # Flush
        await self.session.commit()
        logger.info(f"Built rollups for {ticker}: {len(signals)} source bars processed.")
        return latest_ts
