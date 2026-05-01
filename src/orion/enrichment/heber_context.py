"""Heber-backed context data for feature enrichment.

Provides ticker discovery, VIX proxy, market tide, SPY return, and
regime snapshot persistence. All reads go through the Heber reader;
DB writes are best-effort for durability.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import func, select

from orion.clients.heber_reader import HeberReader
from orion.config import system_settings
from orion.shared.dataframe_utils import first_existing_column as _first_existing_column
from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory
from orion.storage.models import BronzeEvent, RegimeSnapshot, SystemStatus

logger = setup_struct_logger("orion.enrichment.heber_context")

STATIC_TICKER_FALLBACK = ["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMD", "META", "AMZN", "GOOG", "MSFT"]

# SystemStatus key written by feature_enrichment when ticker-discovery
# falls back to the hardcoded static list past the warn-streak threshold.
# ExecutionEngine consumes this key in _check_system_health and rejects
# trades while the flag is DEGRADED. Defined here so the producer
# (feature_enrichment) and consumer (execution_engine) share a single
# constant.
DEGRADED_DISCOVERY_KEY = "degraded_discovery"
DISCOVERY_STATUS_OK = "OK"
DISCOVERY_STATUS_DEGRADED = "DEGRADED"

_heber_reader = HeberReader()
_recent_regime_snapshots: list[dict[str, Any]] = []
_latest_greek_exposure: list[dict] = []
_latest_max_pain: list[dict] = []
_latest_iv_rank: list[dict] = []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _prefer_heber_context_reads() -> bool:
    return system_settings.feature_enrichment_prefer_heber_context


def _coerce_time_series(df: pd.DataFrame) -> pd.Series:
    ts_col = _first_existing_column(df, ["ts_event", "ts_utc", "bar_start_ts", "bar_start_ts_utc", "timestamp"])
    if ts_col is None:
        return pd.Series(index=df.index, dtype="datetime64[ns, UTC]")
    return pd.Series(
        pd.to_datetime(df[ts_col], utc=True, errors="coerce", format="mixed", dayfirst=False), index=df.index
    )


# ---------------------------------------------------------------------------
# Ticker discovery
# ---------------------------------------------------------------------------


def _extract_top_tickers_from_flow_df(flow_df: pd.DataFrame, limit: int) -> list[str]:
    if flow_df.empty:
        return []

    ticker_col = None
    for candidate in ("ticker", "symbol", "underlying"):
        if candidate in flow_df.columns:
            ticker_col = candidate
            break

    if ticker_col is None:
        return []

    ts_col = None
    for candidate in ("flow_ts_utc", "ts_event", "timestamp", "created_at"):
        if candidate in flow_df.columns:
            ts_col = candidate
            break

    if ts_col is not None:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)
        ts = pd.to_datetime(flow_df[ts_col], utc=True, errors="coerce")
        flow_df = flow_df.loc[ts >= cutoff]

    tickers = flow_df[ticker_col].dropna().astype(str).str.upper().str.strip().replace("", pd.NA).dropna()
    if tickers.empty:
        return []

    counts = tickers.value_counts()
    return counts.head(limit).index.tolist()


def _extract_tickers_from_bars(limit: int) -> list[str]:
    """Extract active tickers from Heber bars (equity instrument keys)."""
    try:
        now_utc = datetime.now(UTC)
        return _heber_reader.read_recent_equity_symbols(
            asof_time=now_utc,
            start_time=now_utc - pd.Timedelta(days=1),
            limit=limit,
        )
    except Exception:
        logger.warning("Heber bars ticker discovery failed; falling back to static list", exc_info=True)
        return []


async def _get_active_tickers_from_bronze(limit: int, lookback_hours: int = 24) -> list[str]:
    """Primary ticker-discovery source: TimescaleDB bronze_events.

    Orders of magnitude cheaper than the Heber parquet scan — uses the
    (ticker, event_ts_utc) index and materializes only the top-N ticker
    names. Runs on every refresh cycle of feature_enrichment, so must be
    fast and bounded in memory.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    stmt = (
        select(BronzeEvent.ticker, func.count(BronzeEvent.event_id).label("n"))
        .where(BronzeEvent.ticker.isnot(None))
        .where(BronzeEvent.received_ts_utc >= cutoff)
        .group_by(BronzeEvent.ticker)
        .order_by(func.count(BronzeEvent.event_id).desc())
        .limit(limit)
    )
    async with async_session_factory() as session:
        result = await session.execute(stmt)
        return [row[0] for row in result.all() if row[0]]


async def get_active_tickers_with_source(limit: int = 20) -> tuple[list[str], str]:
    """Get tickers with recent flow activity and the source used.

    Tries sources in order, cheapest first:
    1. TimescaleDB bronze_events (indexed, milliseconds)
    2. Heber flow_alerts (parquet scan — fallback only; historically OOM-prone)
    3. Heber bars (equity bars)
    4. Static fallback list
    """
    # Primary: DB-backed, indexed, bounded. This is the ONLY hot-path source —
    # all Heber parquet scanning was removed from the discovery hot path after
    # the 2026-04-22 OOM crash-loop incident (see docs/rca/feature_enrichment_crash_loop.md).
    bronze_failed = False
    try:
        tickers = await _get_active_tickers_from_bronze(limit)
        if tickers:
            return tickers, "bronze_db"
    except Exception:
        bronze_failed = True
        logger.warning("bronze_events ticker discovery failed", exc_info=True)

    # Heber fallbacks only run when the DB path is unavailable (not just empty) —
    # an empty bronze result in post-market hours is normal and should not
    # trigger a multi-GB parquet scan.
    if bronze_failed:
        try:
            now_utc = datetime.now(UTC)
            flow_df = _heber_reader.read_flow(
                asof_time=now_utc,
                start_time=now_utc - pd.Timedelta(hours=2),
            )
            tickers = _extract_top_tickers_from_flow_df(flow_df, limit=limit)
            if tickers:
                return tickers, "heber"
        except Exception:
            logger.warning("Heber flow ticker discovery failed", exc_info=True)

        bars_tickers = _extract_tickers_from_bars(limit=limit)
        if bars_tickers:
            logger.info(
                "Ticker discovery fell back to Heber bars",
                extra={
                    "event": "feature_enrichment_ticker_source_bars_fallback",
                    "tickers_count": len(bars_tickers),
                },
            )
            return bars_tickers, "heber"

    return STATIC_TICKER_FALLBACK[:limit], "static_fallback"


async def get_active_tickers(limit: int = 20) -> list[str]:
    """Get tickers with recent flow activity (Heber first, static fallback)."""
    tickers, _source = await get_active_tickers_with_source(limit=limit)
    return tickers


# ---------------------------------------------------------------------------
# Market tide
# ---------------------------------------------------------------------------


def _get_latest_market_tide_from_heber() -> float | None:
    try:
        now = datetime.now(UTC)
        tide_df = _heber_reader.read_market_tide(
            asof_time=now,
            start_time=now - pd.Timedelta(days=2),
        )
    except Exception:
        logger.debug("Heber market tide read failed, falling back to local DB", exc_info=True)
        return None

    if tide_df.empty:
        return None

    tide_df = tide_df.copy()
    tide_df["_ts"] = _coerce_time_series(tide_df)
    if tide_df["_ts"].notna().any():
        tide_df = tide_df.dropna(subset=["_ts"]).sort_values("_ts", ascending=False)
    if tide_df.empty:
        return None

    latest = tide_df.iloc[0]
    net_col = _first_existing_column(tide_df, ["net_premium", "market_tide_net"])
    if net_col is not None:
        value = pd.to_numeric(pd.Series([latest.get(net_col)]), errors="coerce").iloc[0]
        if pd.notna(value):
            return float(value)

    call_col = _first_existing_column(tide_df, ["net_call_premium", "call_premium"])
    put_col = _first_existing_column(tide_df, ["net_put_premium", "put_premium"])
    if call_col is None or put_col is None:
        return None

    call_value = pd.to_numeric(pd.Series([latest.get(call_col)]), errors="coerce").iloc[0]
    put_value = pd.to_numeric(pd.Series([latest.get(put_col)]), errors="coerce").iloc[0]
    if pd.isna(call_value) or pd.isna(put_value):
        return None
    return float(call_value - put_value)


async def get_latest_market_tide() -> float | None:
    """Get latest market tide net premium (calls - puts)."""
    if _prefer_heber_context_reads():
        heber_net = _get_latest_market_tide_from_heber()
        if heber_net is not None:
            return heber_net
    return None


# ---------------------------------------------------------------------------
# VIX proxy
# ---------------------------------------------------------------------------


def _map_vix_proxy_to_regime(vix_proxy: float) -> str:
    if vix_proxy > 30:
        return "EXTREME"
    if vix_proxy > 20:
        return "ELEVATED"
    if vix_proxy > 12:
        return "NORMAL"
    return "LOW"


def _try_vix_proxy_from_heber(proxy_symbol: str, multiplier: float) -> dict[str, float | str | None] | None:
    try:
        now = datetime.now(UTC)
        # 3-day window is enough for latest-close + 1-day-change; a wider
        # window adds memory pressure without adding signal.
        bars_df = _heber_reader.read_bars(
            symbols=[proxy_symbol],
            asof_time=now,
            start_time=now - pd.Timedelta(days=3),
        )
    except Exception:
        logger.debug("Heber %s bars read failed", proxy_symbol, exc_info=True)
        return None

    if bars_df.empty:
        return None

    bars_df = bars_df.copy()
    symbol_col = _first_existing_column(bars_df, ["symbol", "ticker", "underlying", "instrument_key"])
    if symbol_col is not None:
        symbols = bars_df[symbol_col].astype(str).str.upper().str.split(":").str[-1]
        bars_df = bars_df.loc[symbols == proxy_symbol.upper()]
    if bars_df.empty:
        return None

    close_col = _first_existing_column(bars_df, ["close", "c"])
    if close_col is None:
        return None

    bars_df["_ts"] = _coerce_time_series(bars_df)
    bars_df["_close"] = pd.to_numeric(bars_df[close_col], errors="coerce")
    bars_df = bars_df.dropna(subset=["_ts", "_close"]).sort_values("_ts")
    if bars_df.empty:
        return None

    latest = bars_df.iloc[-1]
    latest_close = float(latest["_close"])
    vix_approx = latest_close * multiplier
    target_prior_ts = latest["_ts"] - pd.Timedelta(days=1)
    prior_rows = bars_df.loc[bars_df["_ts"] <= target_prior_ts]
    if prior_rows.empty:
        vix_1d_change = 0.0
    else:
        prior_close = float(prior_rows.iloc[-1]["_close"])
        prior_vix = prior_close * multiplier
        vix_1d_change = ((vix_approx - prior_vix) / prior_vix) * 100 if prior_vix > 0 else 0.0

    return {
        "vix": vix_approx,
        "vvix": None,
        "vix_1d_change": vix_1d_change,
        "vix_regime": _map_vix_proxy_to_regime(vix_approx),
    }


def _get_latest_vix_data_from_heber() -> dict[str, float | str | None] | None:
    vix_proxy_candidates = [
        ("VIXY", 2.0),
        ("UVIX", 2.85),
        ("VIXM", 1.25),
    ]
    for proxy_symbol, multiplier in vix_proxy_candidates:
        result = _try_vix_proxy_from_heber(proxy_symbol, multiplier)
        if result is not None:
            return result
    logger.debug("No VIX proxy data found in Heber (tried %s)", [c[0] for c in vix_proxy_candidates])
    return None


async def get_latest_vix_data() -> dict[str, Any]:
    """Get latest VIX context, preferring Heber VIXY proxy bars."""
    if _prefer_heber_context_reads():
        heber_vix = _get_latest_vix_data_from_heber()
        if heber_vix is not None:
            return heber_vix
    return {}


# ---------------------------------------------------------------------------
# SPY cumulative return
# ---------------------------------------------------------------------------


def _get_spy_cumulative_return_from_heber() -> float | None:
    try:
        now = datetime.now(UTC)
        # Only need the last ~20 bars for intraday cumulative return.
        bars_df = _heber_reader.read_bars(
            symbols=["SPY"],
            asof_time=now,
            start_time=now - pd.Timedelta(days=1),
        )
    except Exception:
        logger.debug("Heber SPY bars read failed, falling back to local DB", exc_info=True)
        return None

    if bars_df.empty:
        return None

    bars_df = bars_df.copy()
    symbol_col = _first_existing_column(bars_df, ["symbol", "ticker", "underlying", "instrument_key"])
    if symbol_col is not None:
        symbols = bars_df[symbol_col].astype(str).str.upper().str.split(":").str[-1]
        bars_df = bars_df.loc[symbols == "SPY"]
    if bars_df.empty:
        return None

    close_col = _first_existing_column(bars_df, ["close", "c"])
    if close_col is None:
        return None

    bars_df["_ts"] = _coerce_time_series(bars_df)
    if bars_df["_ts"].notna().any():
        bars_df = bars_df.dropna(subset=["_ts"]).sort_values("_ts", ascending=False)

    bars_df["_close"] = pd.to_numeric(bars_df[close_col], errors="coerce")
    bars_df = bars_df.dropna(subset=["_close"]).head(20)
    if bars_df.empty:
        return None
    if len(bars_df) < 2:
        return 0.0

    latest_close = float(bars_df.iloc[0]["_close"])
    oldest_close = float(bars_df.iloc[-1]["_close"])
    if oldest_close == 0:
        return 0.0
    return (latest_close - oldest_close) / oldest_close


async def get_spy_cumulative_return() -> float:
    """Get SPY cumulative return over past 20 bars (approximate trend)."""
    if _prefer_heber_context_reads():
        heber_return = _get_spy_cumulative_return_from_heber()
        if heber_return is not None:
            return heber_return
    return 0.0


# ---------------------------------------------------------------------------
# Greek exposure (Heber reads)
# ---------------------------------------------------------------------------


def get_latest_greek_exposure(tickers: list[str]) -> list[dict]:
    """Get latest greek exposure data from Heber for the given tickers."""
    global _latest_greek_exposure
    try:
        now_utc = datetime.now(UTC)
        today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        df = _heber_reader.read_greek_exposure(
            symbols=tickers,
            asof_time=now_utc,
            start_time=today_start_utc,
        )
        if df.empty:
            _latest_greek_exposure = []
            return []
        records = df.to_dict("records")
        _latest_greek_exposure = records
        logger.info("heber_greek_exposure_read", rows=len(records))
        return records
    except Exception:
        logger.warning("Heber greek exposure read failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Max pain (Heber reads)
# ---------------------------------------------------------------------------


def get_latest_max_pain(tickers: list[str]) -> list[dict]:
    """Get latest max pain data from Heber for the given tickers."""
    global _latest_max_pain
    try:
        now_utc = datetime.now(UTC)
        today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        df = _heber_reader.read_max_pain(
            symbols=tickers,
            asof_time=now_utc,
            start_time=today_start_utc,
        )
        if df.empty:
            _latest_max_pain = []
            return []
        records = df.to_dict("records")
        _latest_max_pain = records
        logger.info("heber_max_pain_read", rows=len(records))
        return records
    except Exception:
        logger.warning("Heber max pain read failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# IV rank (Heber reads)
# ---------------------------------------------------------------------------


def get_latest_iv_rank(tickers: list[str]) -> list[dict]:
    """Get latest IV rank data from Heber for the given tickers."""
    global _latest_iv_rank
    try:
        now_utc = datetime.now(UTC)
        today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        df = _heber_reader.read_iv_rank(
            symbols=tickers,
            asof_time=now_utc,
            start_time=today_start_utc,
        )
        if df.empty:
            _latest_iv_rank = []
            return []
        records = df.to_dict("records")
        _latest_iv_rank = records
        logger.info("heber_iv_rank_read", rows=len(records))
        return records
    except Exception:
        logger.warning("Heber IV rank read failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Regime snapshot persistence
# ---------------------------------------------------------------------------


async def _persist_regime_to_db(snapshot_dict: dict[str, Any]) -> None:
    """Write regime snapshot to TimescaleDB for durability (best-effort)."""
    try:
        async with async_session_factory() as session:
            row = RegimeSnapshot(**snapshot_dict)
            session.add(row)
            await session.commit()
    except Exception:
        logger.warning("regime_snapshot_db_write_failed", exc_info=True)


async def persist_regime_snapshot(
    ts: datetime,
    snapshot: Any,
    ticker: str = "SPY",
) -> None:
    """Persist regime snapshot in memory (hot path) and to DB (durable, fire-and-forget)."""
    import json

    record = {
        "ts_utc": ts,
        "ticker": ticker,
        "trend_regime": snapshot.trend.value if snapshot.trend else None,
        "vol_regime": snapshot.vol.value if snapshot.vol else None,
        "risk_regime": snapshot.risk.value if snapshot.risk else None,
        "session_regime": snapshot.session.value if snapshot.session else None,
        "vix_regime": snapshot.vix_regime.value if snapshot.vix_regime else None,
        "vix_level": snapshot.vix_level,
        "realized_vol": snapshot.realized_vol,
        "trend_strength": snapshot.trend_strength,
        "risk_score": snapshot.risk_score,
        "confidence_json": json.dumps(snapshot.confidence) if snapshot.confidence else None,
    }

    # Hot path: in-memory list for signal pipeline reads
    _recent_regime_snapshots.append(record)
    if len(_recent_regime_snapshots) > 2000:
        del _recent_regime_snapshots[:1000]

    # Durable path: fire-and-forget DB write (don't block enrichment loop)
    asyncio.create_task(_persist_regime_to_db(record))


async def seed_regime_snapshots_from_db(limit: int = 500) -> None:
    """Load recent regime snapshots from DB into in-memory list on startup.

    Recovers state after a restart so the signal pipeline has immediate
    access to recent regime history without waiting for new snapshots.
    """
    try:
        async with async_session_factory() as session:
            stmt = select(RegimeSnapshot).order_by(RegimeSnapshot.ts_utc.desc()).limit(limit)
            result = await session.execute(stmt)
            rows = result.scalars().all()

        if not rows:
            logger.info("regime_snapshot_seed_empty", msg="No regime snapshots in DB to seed")
            return

        # Reverse so oldest is first (append order)
        for row in reversed(rows):
            _recent_regime_snapshots.append(
                {
                    "ts_utc": row.ts_utc,
                    "ticker": row.ticker,
                    "trend_regime": row.trend_regime,
                    "vol_regime": row.vol_regime,
                    "risk_regime": row.risk_regime,
                    "session_regime": row.session_regime,
                    "vix_regime": row.vix_regime,
                    "vix_level": row.vix_level,
                    "realized_vol": row.realized_vol,
                    "trend_strength": row.trend_strength,
                    "risk_score": row.risk_score,
                    "confidence_json": row.confidence_json,
                }
            )

        logger.info("regime_snapshot_seed_complete", count=len(rows))
    except Exception:
        logger.warning("regime_snapshot_seed_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Discovery-degradation status flag (consumed by ExecutionEngine.
# _check_system_health to hard-block trading when ticker discovery has fallen
# back to the hardcoded static list)
# ---------------------------------------------------------------------------


def _is_discovery_degraded(source: str, streak: int, warn_streak: int) -> bool:
    """Pure logic: is discovery in a degraded state?

    Healthy sources (`bronze_db`, `heber`) are never degraded. Static
    fallback is degraded only after the streak crosses the warn threshold —
    matching the existing warn-log semantics so we don't block trading on
    transient single-cycle blips.
    """
    if source in ("bronze_db", "heber"):
        return False
    return streak >= warn_streak


async def persist_discovery_status(source: str, streak: int, warn_streak: int) -> None:
    """Upsert the `degraded_discovery` SystemStatus row.

    Called every cycle by feature_enrichment so `last_updated_utc` doubles
    as a liveness signal — if feature_enrichment crashes, downstream
    consumers can detect staleness against this key. Best-effort: a write
    failure does not block the enrichment loop.
    """
    is_degraded = _is_discovery_degraded(source, streak, warn_streak)
    new_status = DISCOVERY_STATUS_DEGRADED if is_degraded else DISCOVERY_STATUS_OK
    details = f"source={source} streak={streak} warn_streak={warn_streak}"

    try:
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
            result = await session.execute(stmt)
            existing = result.scalars().first()

            now = datetime.now(UTC)
            if existing:
                status_changed = existing.status != new_status
                existing.status = new_status
                existing.details = details
                existing.last_updated_utc = now
                if status_changed:
                    if is_degraded:
                        logger.critical(
                            "ticker_discovery_degraded",
                            extra={
                                "event": "ticker_discovery_degraded",
                                "source": source,
                                "streak": streak,
                                "warn_streak": warn_streak,
                            },
                        )
                    else:
                        logger.info(
                            "ticker_discovery_recovered",
                            extra={
                                "event": "ticker_discovery_recovered",
                                "source": source,
                                "streak": streak,
                            },
                        )
            else:
                session.add(
                    SystemStatus(
                        key=DEGRADED_DISCOVERY_KEY,
                        status=new_status,
                        details=details,
                        last_updated_utc=now,
                    )
                )
            await session.commit()
    except Exception:
        logger.warning("discovery_status_persist_failed", exc_info=True)
