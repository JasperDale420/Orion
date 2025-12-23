import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pandas as pd
from orion.storage.db import async_session_factory
from orion.storage.models_gold import GoldTickerRollup, LabelWindow
from sqlalchemy import select

logger = logging.getLogger("orion.jobs.window_label_job")


class WindowLabelingJob:
    """
    PRD 6.3: labels_window
    Computes forward returns for rollup windows (period-based), storing into labels_window.
    """

    def __init__(self, *, period: str = "5m"):
        self.period = period
        self.forward_horizons_min = self._parse_forward_horizons()

    @staticmethod
    def _parse_forward_horizons() -> List[int]:
        raw = os.getenv("ORION_WINDOW_LABEL_FORWARD_HORIZONS_MIN", "5,15,60,390")
        out: List[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                val = int(part)
            except ValueError:
                continue
            if val > 0:
                out.append(val)
        return sorted(set(out))

    @staticmethod
    def _compute_forward_returns(
        close_series: pd.Series, entry_ts: pd.Timestamp, horizons_min: List[int]
    ) -> Dict[str, float | None]:
        prices = close_series.sort_index()
        if prices.empty:
            return {f"{h}m": None for h in horizons_min}

        if entry_ts not in prices.index:
            idx = prices.index.get_indexer([entry_ts], method="bfill")
            if idx is None or len(idx) == 0 or idx[0] < 0:
                return {f"{h}m": None for h in horizons_min}
            entry_ts = prices.index[idx[0]]

        p0 = float(prices.loc[entry_ts])
        if p0 == 0:
            return {f"{h}m": None for h in horizons_min}

        out: Dict[str, float | None] = {}
        for h in horizons_min:
            target_ts = entry_ts + pd.Timedelta(minutes=h)
            idx = prices.index.get_indexer([target_ts], method="bfill")
            if idx is None or len(idx) == 0 or idx[0] < 0:
                out[f"{h}m"] = None
                continue
            ts_h = prices.index[idx[0]]
            try:
                ph = float(prices.loc[ts_h])
                out[f"{h}m"] = (ph - p0) / p0
            except Exception:
                out[f"{h}m"] = None
        return out

    async def run_once(self, *, lookback_hours: int = 24, limit: int = 2000) -> int:
        """
        Labels recent rollup windows for the configured period.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        async with async_session_factory() as session:
            stmt = (
                select(GoldTickerRollup)
                .where(GoldTickerRollup.period == self.period)
                .where(GoldTickerRollup.timestamp_utc >= cutoff)
                .order_by(GoldTickerRollup.timestamp_utc.asc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                return 0

            # Group per ticker for efficient series ops.
            by_ticker: Dict[str, List[GoldTickerRollup]] = {}
            for r in rows:
                by_ticker.setdefault(r.ticker, []).append(r)

            written = 0
            for ticker, ticker_rows in by_ticker.items():
                df = pd.DataFrame(
                    [{"ts": r.timestamp_utc, "close": r.close} for r in ticker_rows if r.close is not None]
                )
                if df.empty:
                    continue
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                df = df.set_index("ts").sort_index()
                close = df["close"]

                for r in ticker_rows:
                    ts = pd.Timestamp(r.timestamp_utc)
                    ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
                    fwd = self._compute_forward_returns(close, ts, self.forward_horizons_min)
                    lw = LabelWindow(
                        ticker=ticker,
                        period=self.period,
                        window_end_ts_utc=r.timestamp_utc,
                        forward_returns=fwd,
                        label_config={
                            "forward_horizons_min": list(self.forward_horizons_min),
                            "price_source": f"gold_ticker_rollup:{self.period}",
                        },
                    )
                    await session.merge(lw)
                    written += 1

            await session.commit()
            return written


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    job = WindowLabelingJob(period=os.getenv("ORION_WINDOW_LABEL_PERIOD", "5m"))
    count = asyncio.run(job.run_once())
    logger.info("Window labeling complete", extra={"event_type": "LABELS_WINDOW_COMPLETE", "count": count})
