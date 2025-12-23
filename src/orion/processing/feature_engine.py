import asyncio
import hashlib
import logging

import pandas as pd

try:
    import pandas_ta as ta
except ImportError:
    ta = None
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import dateutil.parser
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

    def __init__(self):
        # In-memory buffer for ongoing aggregation (not used for simple 1m passthrough,
        # but needed if preserving state for rolling windows if we process row-by-row)
        # For V1, we will process batches statelessly or rely on looking up past data (future V2).
        # To calculate RSI correctly, we actually need HISTORY.
        # For this V1 Slice, we will implement the mechanism to 'append' new bars
        # to a small in-memory history buffer per ticker to allow computing rolling metrics.

        self.history: Dict[str, pd.DataFrame] = {}  # ticker -> DataFrame(OHLCV)
        self.max_history_len = 100  # Keep last 100 bars for calculation context

        self.flow_history: Dict[str, List[Dict]] = {}
        self.flow_max_age_seconds = 900  # 15 minutes window

    async def hydrate_history(self):
        """
        Hydrates in-memory history from SilverAlpacaBar to avoid cold-start issues.
        """
        from orion.config import system_settings
        from orion.storage.db import async_session_factory
        from orion.storage.models_silver import SilverAlpacaBar
        from sqlalchemy import select

        tickers = system_settings.static_watchlist
        logger.info(f"Hydrating FeatureEngine history for {len(tickers)} tickers...")

        try:
            async with async_session_factory() as session:
                for ticker in tickers:
                    # Fetch last N bars
                    stmt = (
                        select(SilverAlpacaBar)
                        .where(SilverAlpacaBar.ticker == ticker)
                        .order_by(SilverAlpacaBar.bar_start_ts_utc.desc())
                        .limit(self.max_history_len)
                    )
                    result = await session.execute(stmt)
                    bars = result.scalars().all()

                    if not bars:
                        continue

                    # Sort ascending for DataFrame
                    bars = sorted(bars, key=lambda x: x.bar_start_ts_utc)

                    data = []
                    for b in bars:
                        data.append(
                            {
                                "ts": b.bar_start_ts_utc,
                                "open": b.open,
                                "high": b.high,
                                "low": b.low,
                                "close": b.close,
                                "volume": b.volume,
                                "vwap": b.vwap,
                            }
                        )

                    df = pd.DataFrame(data)
                    df.set_index("ts", inplace=True)

                    # Compute Indicators immediately
                    try:
                        if ta:
                            if len(df) >= 14:
                                df.ta.rsi(length=14, append=True)
                            if len(df) >= 20:
                                df.ta.sma(length=20, append=True)
                    except Exception as e:
                        logger.warning(f"Indicator hydration failed for {ticker}: {e}")

                    self.history[ticker] = df

            logger.info("FeatureEngine hydration complete.")

        except Exception as e:
            logger.error(f"Failed to hydrate FeatureEngine: {e}")

    async def persist_features(
        self, ticker: str, ts: datetime, features: Dict[str, float], feature_set_id: str = "v1_legacy"
    ):
        """
        Writes computed features to the GoldFeatureEvent table.
        """
        from orion.storage.db import async_session_factory
        from orion.storage.models_gold import GoldFeatureEvent
        from sqlalchemy.dialects.postgresql import insert

        # Ensure ID/PK uniqueness
        if not ticker or not ts:
            return

        try:
            async with async_session_factory() as session:
                stmt = insert(GoldFeatureEvent).values(
                    ticker=ticker, event_ts_utc=ts, feature_set_id=feature_set_id, features=features
                )
                # Upsert
                stmt = stmt.on_conflict_do_update(
                    index_elements=["ticker", "event_ts_utc", "feature_set_id"], set_=dict(features=features)
                )

                await session.execute(stmt)
                await session.commit()

        except Exception as e:
            logger.error(f"Failed to persist features for {ticker} at {ts}: {e}")

    async def persist_signal_batch(self, signals: List[SilverSignal], feature_set_id: str = "v1_legacy"):
        """
        Batch write features to Gold store.
        """
        from orion.storage.db import async_session_factory
        from orion.storage.models_gold import GoldFeatureEvent
        from sqlalchemy.dialects.postgresql import insert

        if not signals:
            return

        # Dedupe or group?
        rows = []
        for s in signals:
            rows.append(
                {
                    "ticker": s.ticker,
                    "event_ts_utc": s.signal_ts_utc,
                    "feature_set_id": feature_set_id,
                    "features": s.features,
                }
            )

        # Bulk insert with upsert
        # Sqlalchemy bulk operations can be tricky with upsert across varying sets?
        # Postgres insert().values(rows) works efficiently.

        chunk_size = 500
        total = len(rows)

        try:
            async with async_session_factory() as session:
                for i in range(0, total, chunk_size):
                    chunk = rows[i : i + chunk_size]
                    stmt = insert(GoldFeatureEvent).values(chunk)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["ticker", "event_ts_utc", "feature_set_id"],
                        set_=dict(features=stmt.excluded.features),
                    )
                    await session.execute(stmt)
                await session.commit()
                logger.info(f"Persisted {total} feature events to Gold Store.")
        except Exception as e:
            logger.error(f"Failed to batch persist features: {e}")

    async def fetch_signal_batch(
        self, ticker: str, start_ts: datetime, end_ts: datetime, feature_set_id: str = "v1_legacy"
    ) -> List[SilverSignal]:
        """
        Hydrates SilverSignals from Gold Feature store.
        """
        from orion.storage.db import async_session_factory
        from orion.storage.models_gold import GoldFeatureEvent
        from sqlalchemy import and_, select

        signals = []
        try:
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
                    # Note: We lose 'signal_id' and 'signal_type' specific nuances unless we inferred them or stored them.
                    # GoldFeatureEvent is purely features.
                    # We can regenerate generic ID and assume type based on feature contents or usage?
                    # For V1 Analysis, we assume generic OHLCV type mapping.

                    sig_id = self._generate_id(r.ticker, r.event_ts_utc, "GOLD_HYDRATED")

                    # We assume these are 1M bars usually
                    signals.append(
                        SilverSignal(
                            signal_id=sig_id,
                            ticker=r.ticker,
                            signal_ts_utc=r.event_ts_utc,
                            signal_type="GOLD_FEATURE",  # Distinct type? Or map to OHLCV_1M?
                            features=r.features,
                        )
                    )

        except Exception as e:
            logger.error(f"Failed to fetch features from Gold: {e}")

        return signals

    def process_uw_flow(self, events: List[BronzeEvent]):
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
            premium = float(e.payload.get("premium") or 0.0)

            # Append
            if ticker not in self.flow_history:
                self.flow_history[ticker] = []

            self.flow_history[ticker].append(
                {"ts": e.event_ts_utc, "premium": premium, "is_put": is_put, "type": e.event_type}
            )

    def process_uw_flow_events(self, events: List[BronzeEvent]) -> List[SilverSignal]:
        """
        Pass-through wrapper to treat significant Flow events as Signals themselves.
        Needed for rules that trigger on specific sweeps (BullishSweepRule).
        """
        signals = []
        for e in events:
            if e.event_type != "UW_FLOW":
                continue

            p = e.payload
            # Extract features for rule eval
            # We flatten the payload into features
            features = p.copy()
            features["event_id"] = e.event_id
            features["source_event_id"] = getattr(e, "source_event_id", None)

            # Normalize put_call
            pc = p.get("put_call")
            if pc == "C":
                features["put_call"] = "CALL"
            if pc == "P":
                features["put_call"] = "PUT"

            # Normalize is_sweep to boolean for rules.
            is_sweep = features.get("is_sweep")
            if isinstance(is_sweep, str):
                features["is_sweep"] = is_sweep.strip().lower() in {"true", "1", "yes", "y"}
            elif isinstance(is_sweep, bool):
                features["is_sweep"] = is_sweep
            elif is_sweep is None:
                features["is_sweep"] = False
            else:
                features["is_sweep"] = bool(is_sweep)

            # Unify aggressor field name used by rules.
            if "aggressor_ind" not in features and "aggressor" in features:
                features["aggressor_ind"] = features.get("aggressor")

            # DTE calculation (days to expiry) if we have expiry.
            if "dte" not in features:
                expiry_raw = features.get("expiry")
                if expiry_raw:
                    try:
                        exp_dt = dateutil.parser.parse(str(expiry_raw))
                        exp_date = exp_dt.date()
                        flow_dt = e.event_ts_utc.astimezone(timezone.utc)
                        features["dte"] = max(0, (exp_date - flow_dt.date()).days)
                    except Exception:
                        pass

            # Ensure numeric types
            try:
                features["premium"] = float(p.get("premium") or p.get("premium_usd") or 0.0)
            except:
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

    def _compute_flow_features(self, ticker: str, ref_ts: datetime) -> Dict[str, float]:
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

    def process_alpaca_bars(self, events: List[BronzeEvent]) -> List[SilverSignal]:
        """
        Takes ALPACA_BAR_1M events, updates history, calcs features, returns SilverSignals at 1m resolution.
        """
        signals = []

        # Group by ticker
        events_by_ticker = {}
        for e in events:
            if e.event_type != "ALPACA_BAR_1M":
                continue

            p = e.payload or {}
            # Support both raw Alpaca payloads (symbol/S/o/h/...) and normalized payloads (ticker/open/high/...).
            ticker = p.get("symbol") or p.get("S") or p.get("ticker") or e.ticker
            if not ticker:
                continue  # Should not happen if confirmed valid payload

            if ticker not in events_by_ticker:
                events_by_ticker[ticker] = []
            events_by_ticker[ticker].append(e)

        for ticker, ticker_events in events_by_ticker.items():
            # Convert to DataFrame
            # Payload keys vary (t/timestamp, o, h, l, c, v). Normalizing:
            data = []
            for e in ticker_events:
                p = e.payload or {}
                # Basic mapping
                raw_ts = p.get("bar_start_ts_utc") or p.get("t") or p.get("timestamp") or e.event_ts_utc
                if isinstance(raw_ts, datetime):
                    ts = raw_ts
                else:
                    ts = parse_timestamptz(raw_ts, strict=False) if raw_ts is not None else None
                if ts is None:
                    continue
                row = {
                    "ts": ts,
                    "open": p.get("o") or p.get("open"),
                    "high": p.get("h") or p.get("high"),
                    "low": p.get("l") or p.get("low"),
                    "close": p.get("c") or p.get("close"),
                    "volume": p.get("v") or p.get("volume"),
                    "vwap": p.get("vw") or p.get("vwap"),  # Optional
                }
                data.append(row)

            new_df = pd.DataFrame(data).sort_values("ts")
            new_df.set_index("ts", inplace=True)

            # Update history
            if ticker not in self.history:
                self.history[ticker] = new_df
            else:
                self.history[ticker] = pd.concat([self.history[ticker], new_df])

            # Dedupe (index) just in case
            self.history[ticker] = self.history[ticker][~self.history[ticker].index.duplicated(keep="last")]

            # Trim
            if len(self.history[ticker]) > self.max_history_len:
                self.history[ticker] = self.history[ticker].iloc[-self.max_history_len :]

            # Compute Indicators on FLUSHED history
            df = self.history[ticker].copy()

            # pandas-ta requires 'close' column
            try:
                if len(df) >= 14:  # Min periods for RSI
                    df.ta.rsi(length=14, append=True)
                if len(df) >= 20:
                    df.ta.sma(length=20, append=True)
            except AttributeError:
                # pandas_ta likely mocked or missing
                pass
            except Exception as e:
                logger.error(f"Indicator calculation failed: {e}")

            # Generating signals only for the NEW events (don't re-emit old signals)
            # Find the timestamps that are in new_df
            new_timestamps = set(new_df.index)

            for ts, row in df.iterrows():
                if ts not in new_timestamps:
                    continue

                # Create Signal Object
                features = row.to_dict()
                # Clean up NaN
                features = {k: v for k, v in features.items() if pd.notna(v)}

                # --- ENRICH WITH FLOW FEATURES ---
                # ts in index is Timestamp.
                # Ensure it's python datetime for comparison?
                # Pandas Timestamp corresponds well.
                try:
                    ts_pydt = ts.to_pydatetime()
                    if ts_pydt.tzinfo is None:
                        ts_pydt = ts_pydt.replace(tzinfo=timezone.utc)
                except:
                    ts_pydt = ts

                flow_feats = self._compute_flow_features(ticker, ts_pydt)
                features.update(flow_feats)
                # ---------------------------------

                # Ensure serializable (pandas timestamp to iso)
                # ts is index, already datetime

                sig_id = self._generate_id(ticker, ts, SignalType.OHLCV_1M)

                sig = SilverSignal(
                    signal_id=sig_id,
                    ticker=ticker,
                    signal_ts_utc=ts,
                    signal_type=SignalType.OHLCV_1M.value,
                    features=features,
                )
                signals.append(sig)

        return signals

    def _generate_id(self, ticker, ts, sig_type):
        raw = f"{ticker}_{ts.isoformat()}_{sig_type}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def compute(self, candidate, context=None, feature_set_id: str = "v1_legacy") -> Dict[str, float]:
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
        if ticker in self.history:
            df = self.history[ticker]
            # Get row nearest to ts
            try:
                # Ensure ts is compatible with index (pandas Timestamp vs datetime)
                idx = df.index.get_indexer([ts], method="pad")[0]
                if idx >= 0:
                    row = df.iloc[idx]
                    # Map available columns to potential keys
                    # This is a basic mapping; in a real V2 engine this would be more dynamic

                    potential_features = {
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "vwap": float(row["vwap"]) if "vwap" in row else 0.0,
                        "rsi_14": float(row["RSI_14"]) if "RSI_14" in row else 0.0,
                        "sma_20": float(row["SMA_20"]) if "SMA_20" in row else 0.0,
                    }

                    for k, v in potential_features.items():
                        if return_all or k in required_keys:
                            features[k] = v

            except Exception as e:
                logger.warning(f"Feature fetch failed for {ticker}: {e}")

        # 2. Flow Features
        # Ensure ts is python datetime for flow computation
        try:
            ts_py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if ts_py.tzinfo is None:
                ts_py = ts_py.replace(tzinfo=timezone.utc)
        except:
            ts_py = ts

        flow_feats = self._compute_flow_features(ticker, ts_py)

        for k, v in flow_feats.items():
            if return_all or k in required_keys:
                features[k] = v

        # 3. Validation
        if not return_all:
            missing = required_keys - set(features.keys())
            if missing:
                pass

        # PERSISTENCE (Async fire-and-forget or awaited?)
        # Since compute is async, we can await. But this adds latency to live loop.
        # However, for robustness/debuggability, we should persist.
        # We'll use a try-except to not block execution on DB failure.
        try:
            # We can't await fire-and-forget easily without background tasks.
            # Ideally we'd use a BackgroundTask. For this vertical slice, we await cleanly.
            # Optimization: Make this async background if latency is critical.
            # Given we are in async, awaiting a quick DB insert is okayish for now.
            # But wait, compute is called in a loop?
            # Actually persist logic might belong in the batch processor (process_alpaca_bars).
            # But this compute() is "on-demand" for the pipeline.
            # The Compliance Report says "Update FeatureEngine to write... after computation"
            # It also suggests "Update FeatureEngine.compute() to try fetching from store first".

            # Let's add the fetch fallback logic here too?
            # Step 1: Write (Non-blocking background task)
            asyncio.create_task(self.persist_features(ticker, ts, features, feature_set_id))
        except Exception as e:
            logger.warning(f"Persistence in compute failed: {e}")

        return features
