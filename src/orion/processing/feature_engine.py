import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

try:
    import pandas_ta as ta
except ImportError:
    ta = None

import dateutil.parser

from orion.clients.heber_reader import get_heber_reader
from orion.shared.dataframe_utils import first_existing_column as _first_existing_column_func
from orion.shared.db_utils import db_write
from orion.shared.utils import parse_timestamptz
from orion.storage.models import BronzeEvent
from orion.storage.models_silver import SignalType, SilverSignal

logger = logging.getLogger(__name__)


class FeatureEngine:
    """
    Processes BronzeEvents into SilverSignals (Features).
    Currently supports:
    - 1m OHLCV aggregation/passthrough for Alpaca Bars
    - Basic Technical Indicators (SMA, RSI)
    """

    def __init__(self) -> None:
        # In-memory buffer for ongoing aggregation (not used for simple 1m passthrough,
        # but needed if preserving state for rolling windows if we process row-by-row)
        # For V1, we will process batches statelessly or rely on looking up past data (future V2).
        # To calculate RSI correctly, we actually need HISTORY.
        # For this V1 Slice, we will implement the mechanism to 'append' new bars
        # to a small in-memory history buffer per ticker to allow computing rolling metrics.

        self.history: dict[str, pd.DataFrame] = {}  # ticker -> DataFrame(OHLCV)
        self.max_history_len = 100  # Keep last 100 bars for calculation context

        self.flow_history: dict[str, list[dict[str, Any]]] = {}
        self.flow_max_age_seconds = 900  # 15 minutes window
        self.flow_max_size_per_ticker = 500  # Max entries per ticker to prevent memory growth
        self._hydrated = False  # Track initialization state for cold-start detection

    async def hydrate_history(self) -> None:
        """
        Hydrates in-memory history from Heber bars to avoid cold-start issues.
        """

        from orion.config import system_settings

        # from orion.storage.db import async_session_factory # Removed as db_query/db_write are used directly

        tickers = system_settings.static_watchlist
        logger.info(f"Hydrating FeatureEngine history for {len(tickers)} tickers...")

        try:
            for ticker in tickers:
                await self._hydrate_single_ticker(ticker)

            logger.info("FeatureEngine hydration complete.")
            self._hydrated = True

        except Exception as e:
            logger.error(f"Failed to hydrate FeatureEngine: {e}")

    async def _hydrate_single_ticker(self, ticker: str) -> None:
        try:
            reader = get_heber_reader()
            asof_time = datetime.now(UTC)
            start_time = asof_time - timedelta(days=5)
            frame = reader.read_bars(
                symbols=[ticker],
                asof_time=asof_time,
                start_time=start_time,
                end_time=asof_time,
            )
            if frame.empty:
                return

            ticker_col = self._first_existing_column(frame, ("ticker", "symbol", "instrument_key"))
            ts_col = self._first_existing_column(frame, ("bar_start_ts", "bar_start_ts_utc", "ts_event", "t"))
            open_col = self._first_existing_column(frame, ("open", "o"))
            high_col = self._first_existing_column(frame, ("high", "h"))
            low_col = self._first_existing_column(frame, ("low", "l"))
            close_col = self._first_existing_column(frame, ("close", "c"))
            volume_col = self._first_existing_column(frame, ("volume", "v"))
            vwap_col = self._first_existing_column(frame, ("vwap", "vw"))
            required_cols = (ticker_col, ts_col, open_col, high_col, low_col, close_col, volume_col)
            if any(col is None for col in required_cols):
                return

            selected_cols = [ticker_col, ts_col, open_col, high_col, low_col, close_col, volume_col]
            if vwap_col:
                selected_cols.append(vwap_col)
            rows = frame[selected_cols].copy()
            rows["ticker_norm"] = rows[ticker_col].map(self._normalize_ticker)
            rows = rows[rows["ticker_norm"] == ticker.upper()]
            if rows.empty:
                return

            rows["ts"] = pd.to_datetime(rows[ts_col], utc=True, errors="coerce")
            rows["open"] = pd.to_numeric(rows[open_col], errors="coerce")
            rows["high"] = pd.to_numeric(rows[high_col], errors="coerce")
            rows["low"] = pd.to_numeric(rows[low_col], errors="coerce")
            rows["close"] = pd.to_numeric(rows[close_col], errors="coerce")
            rows["volume"] = pd.to_numeric(rows[volume_col], errors="coerce")
            rows["vwap"] = pd.to_numeric(rows[vwap_col], errors="coerce") if vwap_col else None
            rows = rows.dropna(subset=["ts", "open", "high", "low", "close", "volume"])
            if rows.empty:
                return

            rows = rows.sort_values("ts").tail(self.max_history_len)
            df = rows[["ts", "open", "high", "low", "close", "volume", "vwap"]].copy()
            df.set_index("ts", inplace=True)

            # Compute Indicators immediately
            if ta:
                if len(df) >= 14:
                    df.ta.rsi(length=14, append=True)
                if len(df) >= 20:
                    df.ta.sma(length=20, append=True)

            self.history[ticker] = df
        except Exception as e:
            logger.warning(f"Indicator hydration failed for {ticker}: {e}")

    _first_existing_column = staticmethod(_first_existing_column_func)

    @staticmethod
    def _normalize_ticker(value: Any) -> str | None:
        if value is None:
            return None
        ticker = str(value).strip().upper()
        if not ticker:
            return None
        if ":" in ticker:
            ticker = ticker.split(":")[-1]
        return ticker or None

    async def persist_features(
        self, ticker: str, ts: datetime, features: dict[str, float], feature_set_id: str = "v1_legacy"
    ) -> None:
        """
        Writes computed features to the GoldFeatureEvent table.
        """
        # from orion.storage.db import async_session_factory # Removed as db_query/db_write are used directly
        from sqlalchemy.dialects.postgresql import insert

        from orion.storage.models_gold import GoldFeatureEvent

        # Ensure ID/PK uniqueness
        if not ticker or not ts:
            return

        try:

            async def insert_feature(session: Any) -> None:
                stmt = insert(GoldFeatureEvent).values(
                    ticker=ticker, event_ts_utc=ts, feature_set_id=feature_set_id, features=features
                )
                # Upsert
                stmt = stmt.on_conflict_do_update(
                    index_elements=["ticker", "event_ts_utc", "feature_set_id"], set_={"features": features}
                )
                await session.execute(stmt)

            await db_write(insert_feature)

        except Exception as e:
            logger.error(f"Failed to persist features for {ticker} at {ts}: {e}")

    async def persist_signal_batch(self, signals: list[SilverSignal], feature_set_id: str = "v1_legacy") -> None:
        """
        Batch write features to Gold store.
        """
        # from orion.storage.db import async_session_factory # Removed as db_query/db_write are used directly
        from sqlalchemy.dialects.postgresql import insert

        from orion.storage.models_gold import GoldFeatureEvent

        if not signals:
            return

        # Dedupe: keep last occurrence of each (ticker, event_ts_utc, feature_set_id)
        rows_dict = {}
        for s in signals:
            key = (s.ticker, s.signal_ts_utc, feature_set_id)
            rows_dict[key] = {
                "ticker": s.ticker,
                "event_ts_utc": s.signal_ts_utc,
                "feature_set_id": feature_set_id,
                "features": s.features,
            }
        rows = list(rows_dict.values())

        # Bulk insert with upsert
        # Sqlalchemy bulk operations can be tricky with upsert across varying sets?
        # Postgres insert().values(rows) works efficiently.

        chunk_size = 500
        total = len(rows)

        try:
            from orion.storage.db import async_session_factory

            async with async_session_factory() as session:
                for i in range(0, total, chunk_size):
                    chunk = rows[i : i + chunk_size]
                    stmt = insert(GoldFeatureEvent).values(chunk)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["ticker", "event_ts_utc", "feature_set_id"],
                        set_={"features": stmt.excluded.features},
                    )
                    await session.execute(stmt)
                await session.commit()
            logger.info(f"Persisted {total} feature events to Gold Store.")
        except Exception as e:
            logger.error(f"Failed to batch persist features: {e}")

    async def fetch_signal_batch(
        self, ticker: str, start_ts: datetime, end_ts: datetime, feature_set_id: str = "v1_legacy"
    ) -> list[SilverSignal]:
        """
        Hydrates SilverSignals from Gold Feature store.
        """
        # from orion.storage.db import async_session_factory # Removed as db_query/db_write are used directly
        from sqlalchemy import and_, select

        from orion.storage.models_gold import GoldFeatureEvent

        signals = []
        try:
            from orion.storage.db import async_session_factory

            async with async_session_factory() as session:
                stmt = (
                    select(GoldFeatureEvent)
                    .where(
                        and_(
                            GoldFeatureEvent.ticker == ticker,
                            GoldFeatureEvent.event_ts_utc >= start_ts,
                            GoldFeatureEvent.event_ts_utc <= end_ts,
                            GoldFeatureEvent.feature_set_id == feature_set_id,
                        )
                    )
                    .order_by(GoldFeatureEvent.event_ts_utc.asc())
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()

            for r in rows:
                # Reconstruct SilverSignal
                sig_id = self._generate_id(r.ticker, r.event_ts_utc, "GOLD_HYDRATED")

                # We assume these are 1M bars usually
                signals.append(
                    SilverSignal(
                        signal_id=sig_id,
                        ticker=r.ticker,
                        signal_ts_utc=r.event_ts_utc,
                        signal_type="GOLD_FEATURE",
                        features=r.features,
                    )
                )

        except Exception as e:
            logger.error(f"Failed to fetch features from Gold: {e}")

        return signals

    def process_uw_flow(self, events: list[BronzeEvent]) -> None:
        """
        Updates in-memory flow state from UW events.
        """
        for e in events:
            if e.event_type not in ["UW_FLOW", "UW_DARKPOOL"]:
                continue

            ticker = e.payload.get("ticker")
            if not ticker:
                continue

            # Extract relevant fields for aggregation
            # We normalize crudely here for the V1 slice
            is_put = e.payload.get("put_call") == "P"
            premium = float(e.payload.get("premium_usd") or 0.0)

            # Append
            if ticker not in self.flow_history:
                self.flow_history[ticker] = []

            self.flow_history[ticker].append(
                {"ts": e.event_ts_utc, "premium": premium, "is_put": is_put, "type": e.event_type}
            )

            # Enforce size cap to prevent memory growth
            if len(self.flow_history[ticker]) > self.flow_max_size_per_ticker:
                self.flow_history[ticker] = self.flow_history[ticker][-self.flow_max_size_per_ticker :]

    def process_uw_flow_events(self, events: list[BronzeEvent]) -> list[SilverSignal]:
        """
        Pass-through wrapper to treat significant Flow events as Signals themselves.
        """
        signals = []
        for e in events:
            if e.event_type != "UW_FLOW":
                continue

            features = self._extract_flow_features(e)
            if not features:
                continue

            sig_id = self._generate_id(e.ticker or "UNK", e.event_ts_utc, f"UW_FLOW_{e.event_id}")

            sig = SilverSignal(
                signal_id=sig_id,
                ticker=e.ticker,
                signal_ts_utc=e.event_ts_utc,
                signal_type="UW_FLOW",
                features=features,
            )
            signals.append(sig)
        return signals

    def _extract_flow_features(self, event: BronzeEvent) -> dict[str, Any] | None:
        p = event.payload
        features = p.copy()
        features["event_id"] = event.event_id
        features["source_event_id"] = getattr(event, "source_event_id", None)

        # Normalize put_call
        pc = p.get("put_call")
        if pc == "C":
            features["put_call"] = "CALL"
        elif pc == "P":
            features["put_call"] = "PUT"

        # Normalize is_sweep
        is_sweep = features.get("is_sweep")
        if is_sweep is None:
            is_sweep = features.get("sweep")
        if isinstance(is_sweep, str):
            features["is_sweep"] = is_sweep.strip().lower() in {"true", "1", "yes", "y"}
        elif is_sweep is None:
            features["is_sweep"] = False
        else:
            features["is_sweep"] = bool(is_sweep)

        # Normalize aggressor
        if "aggressor_ind" not in features and "aggressor" in features:
            features["aggressor_ind"] = features.get("aggressor")

        # DTE
        if "dte" not in features:
            self._calc_dte(features, event.event_ts_utc)

        # Premium
        try:
            prem = p.get("premium") or p.get("premium_usd") or 0.0
            features["premium"] = float(prem)
        except (ValueError, TypeError):
            return None

        return features

    def _calc_dte(self, features: dict[str, Any], current_ts: datetime) -> None:
        expiry_raw = features.get("expiry")
        if not expiry_raw:
            return
        try:
            exp_dt = dateutil.parser.parse(str(expiry_raw))
            exp_date = exp_dt.date()
            flow_dt = current_ts.astimezone(UTC)
            features["dte"] = max(0, (exp_date - flow_dt.date()).days)
        except Exception:
            pass

    def _compute_flow_features(self, ticker: str, ref_ts: datetime) -> dict[str, float]:
        """
        Computes rolling flow metrics for a ticker relative to ref_ts.
        """
        if ticker not in self.flow_history:
            return {}

        # Prune old events first (lazy cleanup)
        cutoff = ref_ts - timedelta(seconds=self.flow_max_age_seconds)
        valid_events = [x for x in self.flow_history[ticker] if x["ts"] > cutoff and x["ts"] <= ref_ts]
        self.flow_history[ticker] = valid_events  # Update state

        # Aggregation
        call_prem = sum(x["premium"] for x in valid_events if not x["is_put"] and x["type"] == "UW_FLOW")
        put_prem = sum(x["premium"] for x in valid_events if x["is_put"] and x["type"] == "UW_FLOW")

        return {
            "call_premium_15m": call_prem,
            "put_premium_15m": put_prem,
            "flow_net_premium_15m": call_prem - put_prem,
            "flow_count_15m": len(valid_events),
        }

    def process_alpaca_bars(self, events: list[BronzeEvent]) -> list[SilverSignal]:
        """
        Takes ALPACA_BAR_1M events, updates history, calcs features, returns SilverSignals at 1m resolution.
        """
        if not self._hydrated:
            logger.warning(
                "FeatureEngine.process_alpaca_bars called before hydrate_history(). "
                "Indicators may be incomplete for cold-start tickers."
            )
        signals = []

        # Group by ticker
        events_by_ticker = self._group_events_by_ticker(events)

        for ticker, ticker_events in events_by_ticker.items():
            new_df = self._convert_events_to_df(ticker_events)
            if new_df.empty:
                continue

            self._update_history(ticker, new_df)

            # Compute Indicators on FLUSHED history
            self._compute_indicators_on_history(ticker)

            # Generate signals for new timestamps
            signals.extend(self._generate_signals_for_new_data(ticker, new_df.index))

        return signals

    def _group_events_by_ticker(self, events: list[BronzeEvent]) -> dict[str, list[BronzeEvent]]:
        events_by_ticker = {}
        for e in events:
            if e.event_type != "ALPACA_BAR_1M":
                continue

            p = e.payload or {}
            # Support both raw Alpaca payloads and normalized payloads.
            ticker = p.get("symbol") or p.get("S") or p.get("ticker") or e.ticker
            if not ticker:
                continue

            if ticker not in events_by_ticker:
                events_by_ticker[ticker] = []
            events_by_ticker[ticker].append(e)
        return events_by_ticker

    def _convert_events_to_df(self, events: list[BronzeEvent]) -> pd.DataFrame:
        data = []
        for e in events:
            row = self._parse_event_to_row(e)
            if row:
                data.append(row)

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data).sort_values("ts")
        df.set_index("ts", inplace=True)
        return df

    def _parse_event_to_row(self, e: BronzeEvent) -> dict[str, Any] | None:
        p = e.payload or {}
        raw_ts = p.get("bar_start_ts_utc") or p.get("t") or p.get("timestamp") or e.event_ts_utc

        if isinstance(raw_ts, datetime):
            ts = raw_ts
        else:
            ts = parse_timestamptz(raw_ts, strict=False) if raw_ts is not None else None

        if ts is None:
            return None

        return {
            "ts": ts,
            "open": p.get("o") or p.get("open"),
            "high": p.get("h") or p.get("high"),
            "low": p.get("l") or p.get("low"),
            "close": p.get("c") or p.get("close"),
            "volume": p.get("v") or p.get("volume"),
            "vwap": p.get("vw") or p.get("vwap"),
        }

    def _update_history(self, ticker: str, new_df: pd.DataFrame) -> None:
        if ticker not in self.history:
            self.history[ticker] = new_df
        else:
            self.history[ticker] = pd.concat([self.history[ticker], new_df])

        # Dedupe (index)
        self.history[ticker] = self.history[ticker][~self.history[ticker].index.duplicated(keep="last")]

        # Trim
        if len(self.history[ticker]) > self.max_history_len:
            self.history[ticker] = self.history[ticker].iloc[-self.max_history_len :]

    def _compute_indicators_on_history(self, ticker: str) -> None:
        df = self.history[ticker]
        try:
            if len(df) >= 14:
                df.ta.rsi(length=14, append=True)
            if len(df) >= 20:
                df.ta.sma(length=20, append=True)
        except AttributeError:
            pass
        except Exception as e:
            logger.error(f"Indicator calculation failed for {ticker}: {e}")

    def _generate_signals_for_new_data(self, ticker: str, new_timestamps: Any) -> list[SilverSignal]:
        signals = []
        df = self.history[ticker]
        new_ts_set = set(new_timestamps)

        for ts, row in df.iterrows():
            if ts not in new_ts_set:
                continue

            features = {k: v for k, v in row.to_dict().items() if pd.notna(v)}

            # Enrich with flow
            ts_pydt = self._to_pydatetime(ts)
            flow_feats = self._compute_flow_features(ticker, ts_pydt)
            features.update(flow_feats)

            sig_id = self._generate_id(ticker, ts, SignalType.OHLCV_1M)
            signals.append(
                SilverSignal(
                    signal_id=sig_id,
                    ticker=ticker,
                    signal_ts_utc=ts,
                    signal_type=SignalType.OHLCV_1M.value,
                    features=features,
                )
            )
        return signals

    def _to_pydatetime(self, ts: Any) -> datetime:
        try:
            ts_pydt = ts.to_pydatetime()
            if ts_pydt.tzinfo is None:
                ts_pydt = ts_pydt.replace(tzinfo=UTC)
            return ts_pydt
        except Exception:
            return ts

    def _generate_id(self, ticker: str, ts: Any, sig_type: Any) -> str:
        raw = f"{ticker}_{ts.isoformat()}_{sig_type}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def compute(self, candidate: Any, context: Any = None, feature_set_id: str = "v1_legacy") -> dict[str, float]:
        """
        Computes features for a single candidate event on demand.
        Required by SolverPipeline (VS3).
        """
        # Feature Registry Lookup
        from orion.core.feature_registry import FeatureRegistry

        fset = FeatureRegistry.get(feature_set_id)
        if not fset:
            logger.warning(f"Unknown feature_set_id '{feature_set_id}', defaulting to v1_legacy")
            fset = FeatureRegistry.get("v1_legacy")

        required_keys = set(fset.feature_keys)
        return_all = "*" in required_keys

        # Simple V1 Logic: Fetch latest from memory history
        ticker = candidate.ticker
        ts = candidate.timestamp_utc

        features = {}

        # 1. Price Features
        price_feats = self._extract_price_features(ticker, ts)
        for k, v in price_feats.items():
            if return_all or k in required_keys:
                features[k] = v

        # 2. Flow Features
        ts_py = self._to_pydatetime(ts)
        flow_feats = self._compute_flow_features(ticker, ts_py)

        for k, v in flow_feats.items():
            if return_all or k in required_keys:
                features[k] = v

        # 3. Validation
        if not return_all:
            missing = required_keys - set(features.keys())
            if missing:
                logger.debug(f"Missing features for {ticker}: {missing}")

        # PERSISTENCE
        try:
            # Background persistence - use asyncio.shield to protect from cancellation
            # and ensure task completes even if caller context is cancelled
            import asyncio

            asyncio.ensure_future(self.persist_features(ticker, ts, features, feature_set_id))
        except Exception as e:
            logger.warning(f"Persistence scheduling failed: {e}")

        return features

    def _extract_price_features(self, ticker: str, ts: datetime) -> dict[str, float]:
        features = {}
        if ticker in self.history:
            df = self.history[ticker]
            # Get row nearest to ts
            try:
                # Ensure ts is compatible with index (pandas Timestamp vs datetime)
                idx = df.index.get_indexer([ts], method="pad")[0]
                if idx >= 0:
                    row = df.iloc[idx]
                    features = {
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "vwap": float(row["vwap"]) if "vwap" in row else 0.0,
                        "rsi_14": float(row["RSI_14"]) if "RSI_14" in row else 0.0,
                        "sma_20": float(row["SMA_20"]) if "SMA_20" in row else 0.0,
                    }
            except Exception as e:
                logger.warning(f"Feature fetch failed for {ticker}: {e}")
        return features
