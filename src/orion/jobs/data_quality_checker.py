"""
Data Quality Checker - Scheduled job to detect data quality issues.

Runs hourly to check for:
1. Zero-valued bars in recent data
2. Missing/stale data for critical tickers
3. Data gaps during market hours
4. UW Flow data quality
5. Darkpool data quality
6. ML Features population status

Usage:
    docker-compose run --rm price_target_labeler python -m orion.jobs.data_quality_checker
"""

import asyncio
import gc
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from orion.clients.heber_reader import get_heber_reader
from orion.config import system_settings
from orion.core.market_schedule import MarketSchedule
from orion.shared.logger import setup_logging
from orion.shared.dataframe_utils import first_existing_column as _first_existing_column
from orion.storage.db import init_db

logger = logging.getLogger(__name__)

# Critical tickers that must have continuous data
CRITICAL_TICKERS = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "TSLA"]

# Market hours (Eastern Time, simplified as UTC-5)
MARKET_OPEN_HOUR = 14  # 9:30 ET = 14:30 UTC
MARKET_CLOSE_HOUR = 21  # 4:00 PM ET = 21:00 UTC

# Per-run Heber DataFrame cache. Set by `run_quality_checks` for the
# duration of one invocation; read by the `_read_heber_*` helpers so
# duplicate consumers within a single run (e.g. flow summary + flow
# staleness, ML features + recent labels) share one materialization
# instead of pulling parquet twice. Default `None` means "no caching" —
# unit tests that call public per-check functions directly see the
# original behavior. ContextVar (not module-global) so concurrent
# `run_quality_checks` tasks each get their own dict.
#
# Closes FOLLOWUPS #6: the May 13 cgroup-OOM cascade traced back to
# data_quality holding ~5 GiB across consecutive redundant Heber reads.
_run_cache: ContextVar[dict[str, Any] | None] = ContextVar("data_quality_run_cache", default=None)


def _cache_get(key: str) -> Any | None:
    cache = _run_cache.get()
    if cache is None:
        return None
    return cache.get(key)


def _cache_set(key: str, value: Any) -> None:
    cache = _run_cache.get()
    if cache is not None:
        cache[key] = value


def _cache_evict_prefix(prefix: str) -> None:
    """Drop all cached entries whose key starts with ``prefix`` and
    request a GC pass.

    Called between logical phases of `run_quality_checks` (bars → flow
    → darkpool → ml) so a phase's large DataFrames become eligible for
    collection before the next phase's read allocates more memory.
    pyarrow-backed DataFrames don't always release C-extension memory
    back to the OS immediately on ref-drop, but at least the Python
    references are gone and the next allocation can reuse the slot.
    """
    cache = _run_cache.get()
    if cache is None:
        return
    for k in [k for k in cache if k.startswith(prefix)]:
        del cache[k]
    gc.collect()


def _is_market_hours(now: datetime | None = None) -> bool:
    """Return whether the NYSE session is open, with a logged UTC-hour fallback."""
    ts = now or datetime.now(UTC)
    try:
        return MarketSchedule().is_market_open(ts)
    except Exception as exc:
        in_hours = MARKET_OPEN_HOUR <= ts.hour < MARKET_CLOSE_HOUR
        logger.warning(
            "Market calendar unavailable during data-quality check; falling back to UTC hour gate",
            extra={
                "event_type": "DATA_QUALITY_MARKET_HOURS_FALLBACK",
                "timestamp_utc": ts.isoformat(),
                "fallback_open_hour": MARKET_OPEN_HOUR,
                "fallback_close_hour": MARKET_CLOSE_HOUR,
                "error": str(exc),
            },
            exc_info=True,
        )
        return in_hours


# =============================================================================
# ALPACA BARS CHECKS
# =============================================================================


async def check_zero_valued_bars(lookback_hours: int = 24) -> list[dict]:
    """Check for bars with invalid close values in recent data."""
    if not _prefer_heber_source():
        return []
    heber_rows = await _check_zero_valued_bars_from_heber(lookback_hours=lookback_hours)
    return heber_rows or []


async def check_data_staleness(stale_minutes: int = 15) -> list[dict]:
    """Check for tickers with stale data during market hours."""
    now = datetime.now(UTC)

    # Only check during market hours
    if not _is_market_hours(now):
        return []

    if not _prefer_heber_source():
        return []
    heber_rows = await _check_data_staleness_from_heber(stale_minutes=stale_minutes)
    return heber_rows or []


async def check_bar_gaps(ticker: str = "SPY", gap_minutes: int = 5) -> list[dict]:
    """Check for gaps in bar data for a ticker."""
    if not _prefer_heber_source():
        return []
    heber_rows = await _check_bar_gaps_from_heber(ticker=ticker, gap_minutes=gap_minutes)
    return heber_rows or []


async def get_bars_summary() -> dict:
    """Get Alpaca bars data quality summary."""
    if not _prefer_heber_source():
        return {
            "total_bars_24h": 0,
            "valid_bars": 0,
            "invalid_bars": 0,
            "unique_tickers": 0,
            "latest_bar": None,
            "validity_pct": 0,
            "backend": "source_unavailable",
        }

    heber_summary = await _get_bars_summary_from_heber()
    if heber_summary is not None:
        return heber_summary

    return {
        "total_bars_24h": 0,
        "valid_bars": 0,
        "invalid_bars": 0,
        "unique_tickers": 0,
        "latest_bar": None,
        "validity_pct": 0,
        "backend": "heber_unavailable",
    }


# =============================================================================
# UW FLOW CHECKS
# =============================================================================


async def get_flow_summary() -> dict:
    """Get UW Flow data quality summary."""
    if not _prefer_heber_source():
        return {
            "total_flows_24h": 0,
            "valid_premium": 0,
            "missing_premium": 0,
            "unique_tickers": 0,
            "latest_flow": None,
            "validity_pct": 0,
            "backend": "source_unavailable",
        }

    heber_summary = await _get_flow_summary_from_heber()
    if heber_summary is not None:
        return heber_summary

    return {
        "total_flows_24h": 0,
        "valid_premium": 0,
        "missing_premium": 0,
        "unique_tickers": 0,
        "latest_flow": None,
        "validity_pct": 0,
        "backend": "heber_unavailable",
    }


async def check_flow_staleness(stale_minutes: int = 30) -> bool | None:
    """Check if flow data is stale during market hours."""
    now = datetime.now(UTC)

    if not _is_market_hours(now):
        return False  # Outside market hours, no alert

    if not _prefer_heber_source():
        return False
    heber_stale = await _check_flow_staleness_from_heber(stale_minutes=stale_minutes)
    return heber_stale


# =============================================================================
# DARKPOOL CHECKS
# =============================================================================


async def get_darkpool_summary() -> dict:
    """Get Darkpool data quality summary."""
    if not _prefer_heber_source():
        return {
            "total_trades_24h": 0,
            "valid_trades": 0,
            "invalid_price": 0,
            "unique_tickers": 0,
            "latest_trade": None,
            "validity_pct": 0,
            "backend": "source_unavailable",
        }

    heber_summary = await _get_darkpool_summary_from_heber()
    if heber_summary is not None:
        return heber_summary

    return {
        "total_trades_24h": 0,
        "valid_trades": 0,
        "invalid_price": 0,
        "unique_tickers": 0,
        "latest_trade": None,
        "validity_pct": 0,
        "backend": "heber_unavailable",
    }


async def check_darkpool_staleness(stale_minutes: int = 60) -> bool:
    """Check if darkpool data is stale during market hours."""
    now = datetime.now(UTC)

    if not _is_market_hours(now):
        return False

    if not _prefer_heber_source():
        return False
    heber_stale = await _check_darkpool_staleness_from_heber(stale_minutes=stale_minutes)
    return bool(heber_stale) if heber_stale is not None else False


# =============================================================================
# ML FEATURES (PRICE TARGET LABELS) CHECKS
# =============================================================================


async def get_ml_features_summary() -> dict:
    """Get ML feature population summary from Heber watch Gold datasets."""
    if not _prefer_heber_source():
        return _empty_ml_features_summary(backend="source_unavailable")

    summary = await _get_ml_features_summary_from_heber()
    if summary is not None:
        return summary
    return _empty_ml_features_summary(backend="heber_unavailable")


async def check_recent_labels_features() -> dict:
    """Check 24h ML feature population from Heber watch Gold datasets."""
    if not _prefer_heber_source():
        return _empty_recent_labels_summary(backend="source_unavailable")

    summary = await _get_recent_labels_features_from_heber()
    if summary is not None:
        return summary
    return _empty_recent_labels_summary(backend="heber_unavailable")


# =============================================================================
# MAIN RUNNER
# =============================================================================


async def run_quality_checks():
    """Run all data quality checks and log results.

    Sets a per-run Heber DataFrame cache via the `_run_cache`
    ContextVar so duplicate consumers (flow summary + staleness,
    darkpool summary + staleness, ML features + recent labels) share
    one materialization. Caches are evicted between phases
    (`_cache_evict_prefix`) so a phase's large DataFrames become
    eligible for collection before the next phase allocates.
    """
    await init_db()

    logger.info("=" * 60)
    logger.info("STARTING DATA QUALITY CHECKS")
    logger.info("=" * 60)

    results: dict[str, Any] = {}
    cache: dict[str, Any] = {}
    token = _run_cache.set(cache)
    try:
        # 1. Alpaca Bars Summary
        bars_summary = await get_bars_summary()
        results["bars"] = bars_summary
        logger.info(
            f"[BARS] 24h: {bars_summary['total_bars_24h']} bars, "
            f"{bars_summary['validity_pct']}% valid, "
            f"{bars_summary['unique_tickers']} tickers"
        )

        # 2. Zero-valued bars
        zero_bars = await check_zero_valued_bars(lookback_hours=1)
        results["zero_bars"] = zero_bars
        if zero_bars:
            logger.warning(f"[BARS] ALERT: {len(zero_bars)} tickers with zero-valued bars!")
        else:
            logger.info("[BARS] No zero-valued bars in last hour ✓")

        # 3. Bar Staleness
        stale = await check_data_staleness(stale_minutes=15)
        results["stale_bars"] = stale
        if stale:
            logger.warning(f"[BARS] ALERT: {len(stale)} critical tickers stale!")
        else:
            logger.info("[BARS] All critical tickers fresh ✓")

        # 4. SPY Gaps
        gaps = await check_bar_gaps("SPY", gap_minutes=5)
        results["spy_gaps"] = gaps
        if gaps:
            logger.warning(f"[BARS] ALERT: {len(gaps)} gaps in SPY bars")
        else:
            logger.info("[BARS] No SPY gaps ✓")

        # Bars phase complete — drop all bar DataFrames before the next
        # phase allocates.
        _cache_evict_prefix("bars_")

        # 5. UW Flow Summary
        flow_summary = await get_flow_summary()
        results["flow"] = flow_summary
        flow_backend = str(flow_summary.get("backend") or "unknown")
        flow_summary_message = (
            f"[FLOW] 24h: {flow_summary['total_flows_24h']} flows, "
            f"{flow_summary['validity_pct']}% valid premium, "
            f"{flow_summary['unique_tickers']} tickers"
        )
        if flow_backend == "heber":
            logger.info(flow_summary_message)
        else:
            logger.warning(
                f"[FLOW] Source unavailable ({flow_backend}); zero counts do not mean the market was quiet",
                extra={"event_type": "FLOW_SOURCE_UNAVAILABLE", "backend": flow_backend},
            )

        flow_stale = await check_flow_staleness(stale_minutes=30)
        if flow_stale is None:
            logger.warning(
                "[FLOW] Flow freshness unknown because the source could not be read",
                extra={"event_type": "FLOW_FRESHNESS_UNKNOWN", "backend": flow_backend},
            )
        elif flow_stale:
            logger.warning("[FLOW] ALERT: Flow data is stale (>30 min)")
        else:
            logger.info("[FLOW] Flow data fresh ✓")

        # Flow phase complete — drop the cached flow DataFrame.
        _cache_evict_prefix("flow_")

        # 6. Darkpool Summary
        dp_summary = await get_darkpool_summary()
        results["darkpool"] = dp_summary
        logger.info(
            f"[DARKPOOL] 24h: {dp_summary['total_trades_24h']} trades, "
            f"{dp_summary['validity_pct']}% valid, "
            f"{dp_summary['unique_tickers']} tickers"
        )

        dp_stale = await check_darkpool_staleness(stale_minutes=60)
        if dp_stale:
            logger.warning("[DARKPOOL] ALERT: Darkpool data is stale (>60 min)")
        else:
            logger.info("[DARKPOOL] Darkpool data fresh ✓")

        # Darkpool phase complete — drop the cached darkpool DataFrame.
        _cache_evict_prefix("darkpool_")

        # 7. ML Features Summary - Comprehensive coverage check
        ml_summary = await get_ml_features_summary()
        results["ml_features"] = ml_summary
        logger.info(f"[ML] Total labels: {ml_summary['total_labels']}, ML-ready: {ml_summary['ml_ready_count']}")

        # Log all features by category
        logger.info(
            f"[ML] Greeks: delta={ml_summary['delta_pct']}%, gamma={ml_summary['gamma_pct']}%, "
            f"iv={ml_summary['iv_pct']}%, iv_rank={ml_summary['iv_rank_pct']}%"
        )
        logger.info(
            f"[ML] Context: sector={ml_summary['sector_pct']}%, vix={ml_summary['vix_pct']}%, "
            f"trend={ml_summary['trend_regime_pct']}%, gex={ml_summary['gex_pct']}%"
        )
        logger.info(
            f"[ML] Volume: vol={ml_summary['volume_pct']}%, oi={ml_summary['oi_pct']}%, "
            f"rvol={ml_summary['rvol_pct']}%, darkpool={ml_summary['darkpool_pct']}%"
        )
        logger.info(
            f"[ML] Checkpoints: 1h={ml_summary['return_1h_pct']}%, 2h={ml_summary['return_2h_pct']}%, "
            f"4h={ml_summary['return_4h_pct']}%, eod={ml_summary['return_eod_pct']}%"
        )

        # Alert on ANY feature below threshold (comprehensive check)
        threshold = 90.0
        critical_threshold = 95.0
        alerts = []
        for key, val in ml_summary.items():
            if key.endswith("_pct") and isinstance(val, (int, float)):
                if val < threshold:
                    alerts.append(f"{key.replace('_pct', '')}={val}%")

        if alerts:
            logger.warning(f"[ML] ALERT: Features below {threshold}%: {', '.join(alerts)}")
        else:
            logger.info(f"[ML] All features above {threshold}% ✓")

        # 8. Recent Labels Features (reuses the cached gold DataFrames
        # from step 7 — single read of both `labels_alert_barriers`
        # and `meta_label_features` services both ML phases).
        recent = await check_recent_labels_features()
        results["recent_labels"] = recent
        if recent["recent_labels"] > 0:
            logger.info(f"[ML] Recent 24h: {recent['recent_labels']} labels, ML-ready: {recent['ml_ready']}")

            # Alert on recent label gaps
            for feat in ["delta", "gamma", "sector", "vix", "iv_rank"]:
                pct = recent.get(f"{feat}_pct", 100)
                if pct < critical_threshold:
                    logger.warning(f"[ML] ALERT: Recent {feat} coverage at {pct}% (below {critical_threshold}%)")

        # ML phase complete — drop cached gold DataFrames before return
        # so a long-lived caller of `run_quality_checks` doesn't hold
        # the parquet payloads in the ContextVar.
        _cache_evict_prefix("gold:")

        logger.info("=" * 60)
        logger.info("DATA QUALITY CHECKS COMPLETE")
        logger.info("=" * 60)

        return results
    finally:
        # Drop any straggler entries and reset the ContextVar so the
        # cache dict becomes eligible for collection. `reset()` also
        # restores the prior token (None) so nested or sibling
        # invocations don't see this cache.
        cache.clear()
        _run_cache.reset(token)


def _prefer_heber_source() -> bool:
    return system_settings.data_quality_checker_prefer_heber


def _coerce_ticker_column(df: pd.DataFrame) -> pd.DataFrame:
    if "ticker" in df.columns:
        return df
    if "symbol" in df.columns:
        return df.assign(ticker=df["symbol"].astype(str).str.upper())
    if "instrument_key" in df.columns:
        return df.assign(ticker=df["instrument_key"].astype(str).str.split(":").str[-1].str.upper())
    return df


def _latest_event_time(df: pd.DataFrame) -> datetime | None:
    time_col = _first_existing_column(
        df,
        ["ts_event", "flow_ts_utc", "dark_ts_utc", "bar_start_ts", "bar_start_ts_utc", "ts_utc"],
    )
    if time_col is None:
        return None
    series = pd.to_datetime(df[time_col], utc=True, errors="coerce").dropna()
    if series.empty:
        return None
    return series.max().to_pydatetime()


def _empty_ml_features_summary(backend: str) -> dict[str, Any]:
    return {
        "total_labels": 0,
        "ml_ready_count": 0,
        "event_id_pct": 0.0,
        "ticker_pct": 0.0,
        "entry_ts_pct": 0.0,
        "delta_pct": 0.0,
        "gamma_pct": 0.0,
        "iv_pct": 0.0,
        "iv_rank_pct": 0.0,
        "underlying_pct": 0.0,
        "sector_pct": 0.0,
        "vix_pct": 0.0,
        "trend_regime_pct": 0.0,
        "vol_regime_pct": 0.0,
        "gex_pct": 0.0,
        "market_tide_pct": 0.0,
        "max_pain_pct": 0.0,
        "volume_pct": 0.0,
        "oi_pct": 0.0,
        "rvol_pct": 0.0,
        "darkpool_pct": 0.0,
        "entry_hour_pct": 0.0,
        "minutes_to_close_pct": 0.0,
        "dte_pct": 0.0,
        "return_1h_pct": 0.0,
        "return_2h_pct": 0.0,
        "return_4h_pct": 0.0,
        "return_eod_pct": 0.0,
        "spy_return_pct": 0.0,
        "overnight_gap_pct": 0.0,
        "vwap_pct": 0.0,
        "latest_entry": None,
        "backend": backend,
    }


def _empty_recent_labels_summary(backend: str) -> dict[str, Any]:
    return {
        "recent_labels": 0,
        "ml_ready": 0,
        "delta_pct": 0.0,
        "gamma_pct": 0.0,
        "iv_rank_pct": 0.0,
        "sector_pct": 0.0,
        "vix_pct": 0.0,
        "rvol_pct": 0.0,
        "spy_pct": 0.0,
        "darkpool_pct": 0.0,
        "backend": backend,
    }


def _coerce_dataframe_payload(payload: Any) -> pd.DataFrame:
    if isinstance(payload, pd.DataFrame):
        return payload.copy()
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return pd.DataFrame()


async def _read_heber_gold_dataset(dataset: str) -> pd.DataFrame | None:
    cache_key = f"gold:{dataset}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    now = datetime.now(UTC)
    reader = get_heber_reader()
    try:
        payload = await asyncio.to_thread(
            reader.read_gold_features,
            dataset=dataset,
            asof_time=now,
        )
    except Exception as exc:
        logger.warning(
            "heber_gold_read_failed",
            extra={"event_type": "HEBER_GOLD_READ_FAILED", "dataset": dataset, "error": str(exc)},
        )
        return None
    df = _coerce_dataframe_payload(payload)
    _cache_set(cache_key, df)
    return df


def _resolve_join_key_column(df: pd.DataFrame) -> str | None:
    return _first_existing_column(df, ["alert_id", "event_id", "watch_id"])


def _to_joinable_frame(df: pd.DataFrame, key_column: str | None, key_name: str = "__join_key") -> pd.DataFrame:
    frame = df.copy()
    if key_column is None or key_column not in frame.columns:
        frame[key_name] = pd.Series(index=frame.index, dtype=object)
        return frame
    frame[key_name] = frame[key_column].astype(str)
    frame.loc[frame[key_column].isna(), key_name] = None
    return frame


def _extract_ticker_series(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(index=df.index, dtype=object)
    ticker_col = _first_existing_column(df, ["ticker", "underlying", "symbol", "instrument_key"])
    if ticker_col is None:
        return pd.Series(index=df.index, dtype=object)
    series = df[ticker_col].astype(str)
    if ticker_col == "instrument_key":
        series = series.str.split(":").str[-1]
    return series.str.upper()


def _extract_time_series(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(index=df.index, dtype="datetime64[ns, UTC]")
    time_col = _first_existing_column(df, ["ts_event", "alert_time", "entry_ts", "ts_available"])
    if time_col is None:
        return pd.Series(index=df.index, dtype="datetime64[ns, UTC]")
    return pd.to_datetime(df[time_col], utc=True, errors="coerce")


def _coverage_pct(df: pd.DataFrame, total: int, candidate_columns: list[str]) -> float:
    if total <= 0 or df.empty:
        return 0.0
    mask = pd.Series(False, index=df.index)
    for column in candidate_columns:
        if column in df.columns:
            mask = mask | df[column].notna()
    return round(100 * float(mask.sum()) / float(total), 1)


def _build_label_feature_coverage(outcomes_df: pd.DataFrame, features_df: pd.DataFrame) -> dict[str, Any]:
    base = _empty_ml_features_summary(backend="heber")
    if outcomes_df.empty:
        return base

    out_key = _resolve_join_key_column(outcomes_df)
    feat_key = _resolve_join_key_column(features_df)
    outcomes = _to_joinable_frame(outcomes_df, out_key)
    features = _to_joinable_frame(features_df, feat_key)

    outcomes["__ticker"] = _extract_ticker_series(outcomes)
    outcomes["__entry_ts"] = _extract_time_series(outcomes)

    if features.empty:
        merged = outcomes.copy()
        feature_match_mask = pd.Series(False, index=merged.index)
    else:
        merged = outcomes.merge(features, on="__join_key", how="left", suffixes=("", "_feature"), indicator=True)
        feature_match_mask = merged["_merge"] == "both"

    total = int(len(merged))
    latest_entry = None
    if "__entry_ts" in merged.columns:
        latest_ts = pd.to_datetime(merged["__entry_ts"], utc=True, errors="coerce").dropna()
        if not latest_ts.empty:
            latest_entry = latest_ts.max().to_pydatetime()

    base.update(
        {
            "total_labels": total,
            "ml_ready_count": int(feature_match_mask.sum()),
            "event_id_pct": _coverage_pct(merged, total, ["__join_key"]),
            "ticker_pct": _coverage_pct(merged, total, ["__ticker"]),
            "entry_ts_pct": _coverage_pct(merged, total, ["__entry_ts"]),
            "delta_pct": _coverage_pct(merged, total, ["delta", "delta_at_entry"]),
            "gamma_pct": _coverage_pct(merged, total, ["gamma", "gamma_at_entry"]),
            "iv_pct": _coverage_pct(merged, total, ["iv", "iv_at_entry"]),
            "iv_rank_pct": _coverage_pct(merged, total, ["iv_rank", "iv_rank_at_entry"]),
            "underlying_pct": _coverage_pct(merged, total, ["spot_price", "underlying_at_entry"]),
            "sector_pct": _coverage_pct(merged, total, ["sector"]),
            "vix_pct": _coverage_pct(merged, total, ["vix_at_entry", "vix"]),
            "trend_regime_pct": _coverage_pct(merged, total, ["trend_regime_at_entry", "trend_regime"]),
            "vol_regime_pct": _coverage_pct(merged, total, ["vol_regime_at_entry", "vol_regime"]),
            "gex_pct": _coverage_pct(merged, total, ["gex_at_entry", "gex"]),
            "market_tide_pct": _coverage_pct(merged, total, ["market_tide_30m", "market_tide"]),
            "max_pain_pct": _coverage_pct(merged, total, ["max_pain_distance_pct", "max_pain_distance"]),
            "volume_pct": _coverage_pct(merged, total, ["volume", "volume_at_entry"]),
            "oi_pct": _coverage_pct(merged, total, ["open_interest", "open_interest_at_entry"]),
            "rvol_pct": _coverage_pct(merged, total, ["rvol_1h", "rvol"]),
            "darkpool_pct": _coverage_pct(merged, total, ["darkpool_volume_1h", "darkpool_1h"]),
            "entry_hour_pct": _coverage_pct(merged, total, ["hour_of_day", "entry_hour"]),
            "minutes_to_close_pct": _coverage_pct(merged, total, ["minutes_to_close"]),
            "dte_pct": _coverage_pct(merged, total, ["days_to_expiry", "dte"]),
            "return_1h_pct": _coverage_pct(merged, total, ["return_at_1h"]),
            "return_2h_pct": _coverage_pct(merged, total, ["return_at_2h"]),
            "return_4h_pct": _coverage_pct(merged, total, ["return_at_4h"]),
            "return_eod_pct": _coverage_pct(merged, total, ["return_at_eod"]),
            "spy_return_pct": _coverage_pct(merged, total, ["spy_return_1h"]),
            "overnight_gap_pct": _coverage_pct(merged, total, ["overnight_gap_pct"]),
            "vwap_pct": _coverage_pct(merged, total, ["vwap_distance_pct"]),
            "latest_entry": latest_entry,
            "backend": "heber",
        }
    )
    return base


async def _get_ml_features_summary_from_heber() -> dict[str, Any] | None:
    outcomes_df = await _read_heber_gold_dataset("labels_alert_barriers")
    if outcomes_df is None:
        return None
    features_df = await _read_heber_gold_dataset("meta_label_features")
    if features_df is None:
        return None
    return _build_label_feature_coverage(outcomes_df, features_df)


async def _get_recent_labels_features_from_heber() -> dict[str, Any] | None:
    outcomes_df = await _read_heber_gold_dataset("labels_alert_barriers")
    if outcomes_df is None:
        return None
    features_df = await _read_heber_gold_dataset("meta_label_features")
    if features_df is None:
        return None

    entry_ts = _extract_time_series(outcomes_df)
    cutoff = pd.Timestamp(datetime.now(UTC) - pd.Timedelta(hours=24))
    recent_mask = entry_ts >= cutoff
    recent_outcomes = outcomes_df[recent_mask].copy()

    if recent_outcomes.empty:
        summary = _empty_recent_labels_summary(backend="heber")
        return summary

    coverage = _build_label_feature_coverage(recent_outcomes, features_df)
    return {
        "recent_labels": int(coverage["total_labels"]),
        "ml_ready": int(coverage["ml_ready_count"]),
        "delta_pct": float(coverage["delta_pct"]),
        "gamma_pct": float(coverage["gamma_pct"]),
        "iv_rank_pct": float(coverage["iv_rank_pct"]),
        "sector_pct": float(coverage["sector_pct"]),
        "vix_pct": float(coverage["vix_pct"]),
        "rvol_pct": float(coverage["rvol_pct"]),
        "spy_pct": float(coverage["spy_return_pct"]),
        "darkpool_pct": float(coverage["darkpool_pct"]),
        "backend": "heber",
    }


async def _read_heber_flow_24h() -> pd.DataFrame | None:
    cache_key = "flow_24h"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    now = datetime.now(UTC)
    start = now - pd.Timedelta(hours=24)
    reader = get_heber_reader()
    try:
        df = await asyncio.to_thread(reader.read_flow, start_time=start, asof_time=now)
    except Exception as exc:
        logger.warning(
            "flow_heber_read_failed",
            extra={"event_type": "FLOW_HEBER_READ_FAILED", "error": str(exc)},
            exc_info=True,
        )
        return None
    _cache_set(cache_key, df)
    return df


async def _read_heber_darkpool_24h() -> pd.DataFrame | None:
    cache_key = "darkpool_24h"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    now = datetime.now(UTC)
    start = now - pd.Timedelta(hours=24)
    reader = get_heber_reader()
    try:
        df = await asyncio.to_thread(reader.read_darkpool, start_time=start, asof_time=now)
    except Exception as exc:
        logger.warning(
            "darkpool_heber_read_failed",
            extra={"event_type": "DARKPOOL_HEBER_READ_FAILED", "error": str(exc)},
        )
        return None
    _cache_set(cache_key, df)
    return df


async def _get_flow_summary_from_heber() -> dict | None:
    df = await _read_heber_flow_24h()
    if df is None:
        return None
    if df.empty:
        return {
            "total_flows_24h": 0,
            "valid_premium": 0,
            "missing_premium": 0,
            "unique_tickers": 0,
            "latest_flow": None,
            "validity_pct": 0,
            "backend": "heber",
        }

    df = _coerce_ticker_column(df)
    premium_col = _first_existing_column(df, ["premium_usd", "premium"])
    premiums = (
        pd.to_numeric(df[premium_col], errors="coerce") if premium_col else pd.Series(index=df.index, dtype=float)
    )
    valid_premium = int((premiums > 0).sum()) if premium_col else 0
    total = int(len(df))
    latest = _latest_event_time(df)
    unique_tickers = int(df["ticker"].dropna().astype(str).nunique()) if "ticker" in df.columns else 0
    return {
        "total_flows_24h": total,
        "valid_premium": valid_premium,
        "missing_premium": int(total - valid_premium),
        "unique_tickers": unique_tickers,
        "latest_flow": latest,
        "validity_pct": round(100 * valid_premium / total, 2) if total > 0 else 0,
        "backend": "heber",
    }


async def _check_flow_staleness_from_heber(stale_minutes: int) -> bool | None:
    df = await _read_heber_flow_24h()
    if df is None:
        return None
    latest = _latest_event_time(df)
    if latest is None:
        return False
    minutes_ago = (datetime.now(UTC) - latest).total_seconds() / 60.0
    return minutes_ago > stale_minutes


async def _get_darkpool_summary_from_heber() -> dict | None:
    df = await _read_heber_darkpool_24h()
    if df is None:
        return None
    if df.empty:
        return {
            "total_trades_24h": 0,
            "valid_trades": 0,
            "invalid_price": 0,
            "unique_tickers": 0,
            "latest_trade": None,
            "validity_pct": 0,
            "backend": "heber",
        }

    df = _coerce_ticker_column(df)
    size_col = _first_existing_column(df, ["size_shares", "size"])
    price_col = _first_existing_column(df, ["trade_price", "price"])
    sizes = pd.to_numeric(df[size_col], errors="coerce") if size_col else pd.Series(index=df.index, dtype=float)
    prices = pd.to_numeric(df[price_col], errors="coerce") if price_col else pd.Series(index=df.index, dtype=float)
    valid_trades = int((sizes > 0).sum()) if size_col else 0
    invalid_price = int(((prices.isna()) | (prices == 0)).sum()) if price_col else int(len(df))
    total = int(len(df))
    latest = _latest_event_time(df)
    unique_tickers = int(df["ticker"].dropna().astype(str).nunique()) if "ticker" in df.columns else 0
    return {
        "total_trades_24h": total,
        "valid_trades": valid_trades,
        "invalid_price": invalid_price,
        "unique_tickers": unique_tickers,
        "latest_trade": latest,
        "validity_pct": round(100 * valid_trades / total, 2) if total > 0 else 0,
        "backend": "heber",
    }


async def _check_darkpool_staleness_from_heber(stale_minutes: int) -> bool | None:
    df = await _read_heber_darkpool_24h()
    if df is None:
        return None
    latest = _latest_event_time(df)
    if latest is None:
        return False
    minutes_ago = (datetime.now(UTC) - latest).total_seconds() / 60.0
    return minutes_ago > stale_minutes


async def _read_heber_bars_24h(symbols: list[str] | None = None, lookback_hours: int = 24) -> pd.DataFrame | None:
    # Cache key encodes both arguments so the 4 distinct bar-read shapes
    # in `run_quality_checks` (full-24h-all-symbols, 1h-all-symbols,
    # 24h-CRITICAL_TICKERS, 24h-SPY) don't collide. Tuple→str so the
    # `_cache_evict_prefix("bars_")` sweep at the end of the bars phase
    # catches everything in one pass.
    sym_key = ",".join(sorted(symbols)) if symbols else "*"
    cache_key = f"bars_{lookback_hours}h_{sym_key}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    now = datetime.now(UTC)
    start = now - pd.Timedelta(hours=lookback_hours)
    reader = get_heber_reader()
    try:
        df = await asyncio.to_thread(
            reader.read_bars,
            symbols=symbols or [],
            asof_time=now,
            start_time=start,
        )
    except Exception as exc:
        logger.warning("bars_heber_read_failed", extra={"event_type": "BARS_HEBER_READ_FAILED", "error": str(exc)})
        return None
    _cache_set(cache_key, df)
    return df


def _coerce_bar_time_series(df: pd.DataFrame) -> pd.Series:
    time_col = _first_existing_column(df, ["ts_event", "bar_start_ts", "bar_start_ts_utc", "ts_utc"])
    if time_col is None:
        return pd.Series(index=df.index, dtype="datetime64[ns, UTC]")
    return pd.to_datetime(df[time_col], utc=True, errors="coerce")


async def _get_bars_summary_from_heber() -> dict | None:
    df = await _read_heber_bars_24h()
    if df is None:
        return None
    if df.empty:
        return {
            "total_bars_24h": 0,
            "valid_bars": 0,
            "invalid_bars": 0,
            "unique_tickers": 0,
            "latest_bar": None,
            "validity_pct": 0,
            "backend": "heber",
        }

    df = _coerce_ticker_column(df)
    close_col = _first_existing_column(df, ["close", "c"])
    close_values = (
        pd.to_numeric(df[close_col], errors="coerce") if close_col else pd.Series(index=df.index, dtype=float)
    )
    total = int(len(df))
    valid = int((close_values > 0).sum()) if close_col else 0
    invalid = int(((close_values.isna()) | (close_values == 0)).sum()) if close_col else total
    unique_tickers = int(df["ticker"].dropna().astype(str).nunique()) if "ticker" in df.columns else 0
    latest = _latest_event_time(df)
    return {
        "total_bars_24h": total,
        "valid_bars": valid,
        "invalid_bars": invalid,
        "unique_tickers": unique_tickers,
        "latest_bar": latest,
        "validity_pct": round(100 * valid / total, 2) if total > 0 else 0,
        "backend": "heber",
    }


async def _check_zero_valued_bars_from_heber(lookback_hours: int) -> list[dict] | None:
    df = await _read_heber_bars_24h(lookback_hours=lookback_hours)
    if df is None:
        return None
    if df.empty:
        return []

    df = _coerce_ticker_column(df)
    close_col = _first_existing_column(df, ["close", "c"])
    if close_col is None or "ticker" not in df.columns:
        return []

    ts_series = _coerce_bar_time_series(df)
    close_values = pd.to_numeric(df[close_col], errors="coerce")
    invalid_mask = close_values.isna() | (close_values == 0)
    invalid_df = pd.DataFrame({"ticker": df["ticker"], "bar_ts": ts_series, "close": close_values})[invalid_mask]
    if invalid_df.empty:
        return []

    grouped = (
        invalid_df.dropna(subset=["bar_ts"])
        .groupby("ticker", as_index=False)
        .agg(zero_count=("close", "size"), earliest=("bar_ts", "min"), latest=("bar_ts", "max"))
        .sort_values("zero_count", ascending=False)
    )

    return [
        {
            "ticker": str(row["ticker"]),
            "zero_count": int(row["zero_count"]),
            "earliest": row["earliest"].to_pydatetime()
            if isinstance(row["earliest"], pd.Timestamp)
            else row["earliest"],
            "latest": row["latest"].to_pydatetime() if isinstance(row["latest"], pd.Timestamp) else row["latest"],
        }
        for _, row in grouped.iterrows()
    ]


async def _check_data_staleness_from_heber(stale_minutes: int) -> list[dict] | None:
    df = await _read_heber_bars_24h(symbols=CRITICAL_TICKERS)
    if df is None:
        return None
    if df.empty:
        return []

    df = _coerce_ticker_column(df)
    if "ticker" not in df.columns:
        return []
    ts_series = _coerce_bar_time_series(df)
    temp_df = pd.DataFrame({"ticker": df["ticker"].astype(str).str.upper(), "bar_ts": ts_series}).dropna(
        subset=["bar_ts"]
    )
    if temp_df.empty:
        return []

    latest_by_ticker = temp_df.groupby("ticker", as_index=False)["bar_ts"].max()
    now = datetime.now(UTC)
    stale_rows: list[dict] = []
    for _, row in latest_by_ticker.iterrows():
        ticker = str(row["ticker"])
        if ticker not in {t.upper() for t in CRITICAL_TICKERS}:
            continue
        last_bar = row["bar_ts"]
        minutes_ago = (now - last_bar.to_pydatetime()).total_seconds() / 60.0
        if minutes_ago > stale_minutes:
            stale_rows.append(
                {
                    "ticker": ticker,
                    "last_bar": last_bar.to_pydatetime(),
                    "minutes_ago": minutes_ago,
                }
            )

    stale_rows.sort(key=lambda item: item["minutes_ago"], reverse=True)
    return stale_rows


async def _check_bar_gaps_from_heber(ticker: str, gap_minutes: int) -> list[dict] | None:
    df = await _read_heber_bars_24h(symbols=[ticker], lookback_hours=24)
    if df is None:
        return None
    if df.empty:
        return []

    df = _coerce_ticker_column(df)
    ts_series = _coerce_bar_time_series(df)
    ticker_upper = ticker.upper()
    bars = pd.DataFrame({"ticker": df.get("ticker"), "bar_ts": ts_series}).dropna(subset=["bar_ts"])
    bars = bars[bars["ticker"].astype(str).str.upper() == ticker_upper]
    if bars.empty:
        return []

    bars = bars.sort_values("bar_ts")
    bars = bars[
        bars["bar_ts"].map(lambda ts: _is_market_hours(ts.to_pydatetime() if isinstance(ts, pd.Timestamp) else ts))
    ]
    if bars.empty:
        return []

    bars["prev_bar"] = bars["bar_ts"].shift(1)
    bars["gap_minutes"] = (bars["bar_ts"] - bars["prev_bar"]).dt.total_seconds() / 60.0
    gaps = bars[bars["gap_minutes"] > gap_minutes].sort_values("bar_ts", ascending=False).head(10)
    return [
        {
            "bar_ts": row["bar_ts"].to_pydatetime() if isinstance(row["bar_ts"], pd.Timestamp) else row["bar_ts"],
            "prev_bar": row["prev_bar"].to_pydatetime()
            if isinstance(row["prev_bar"], pd.Timestamp)
            else row["prev_bar"],
            "gap_minutes": float(row["gap_minutes"]),
        }
        for _, row in gaps.iterrows()
    ]


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_quality_checks())
