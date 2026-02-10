"""
Feature Validation Script for price_target_labels.

Validates that all 130+ ML features are calculated correctly by:
1. Spot-checking individual records against raw source data
2. Running sanity checks on value ranges and distributions
3. Auditing data source availability
4. Cross-validating derived features

Usage:
    python -m orion.jobs.validate_features [--spot-check EVENT_ID] [--sanity] [--audit-sources]
"""

import argparse
import asyncio
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text

from orion.clients.heber_reader import get_heber_reader
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.validate_features")

MINUTES_TO_CLOSE_MAX = 390


# ============================================================================
# SPOT-CHECK VALIDATION
# ============================================================================


async def spot_check_record(event_id: str) -> Dict[str, Any]:
    """
    Validate a single record by comparing computed features to raw source data.

    Returns dict with validation results for each feature category.
    """
    results: Dict[str, Any] = {
        "event_id": event_id,
        "passed": [],
        "failed": [],
        "warnings": [],
    }

    # Get the label record
    async def get_label(session: Any) -> Optional[Dict[str, Any]]:
        stmt = text("""
            SELECT * FROM price_target_labels WHERE event_id = :event_id
        """)
        result = await session.execute(stmt, {"event_id": event_id})
        row = result.fetchone()
        return dict(row._mapping) if row else None

    label = await db_query(get_label)
    if not label:
        results["failed"].append(f"Record not found: {event_id}")
        return results

    ticker = label["ticker"]
    entry_ts = label["entry_ts"]

    # 1. Validate time features
    time_checks = validate_time_features(label, entry_ts)
    results["passed"].extend(time_checks["passed"])
    results["failed"].extend(time_checks["failed"])

    # 2. Validate overnight_gap from raw bars
    gap_checks = await validate_overnight_gap(label, ticker, entry_ts)
    results["passed"].extend(gap_checks["passed"])
    results["failed"].extend(gap_checks["failed"])

    # 3. Validate darkpool from raw darkpool table
    dp_checks = await validate_darkpool(label, ticker, entry_ts)
    results["passed"].extend(dp_checks["passed"])
    results["failed"].extend(dp_checks["failed"])
    results["warnings"].extend(dp_checks.get("warnings", []))

    # 4. Validate Greeks are reasonable
    greek_checks = validate_greeks(label)
    results["passed"].extend(greek_checks["passed"])
    results["failed"].extend(greek_checks["failed"])

    return results


def validate_time_features(label: Dict, entry_ts: datetime) -> Dict[str, List[str]]:
    """Validate time-derived features match entry_ts."""
    passed, failed = [], []

    # entry_hour should match entry_ts.hour
    if label.get("entry_hour") is not None:
        if label["entry_hour"] == entry_ts.hour:
            passed.append(f"entry_hour={label['entry_hour']} ✓")
        else:
            failed.append(f"entry_hour mismatch: got {label['entry_hour']}, expected {entry_ts.hour}")

    # entry_day_of_week should match
    if label.get("entry_day_of_week") is not None:
        if label["entry_day_of_week"] == entry_ts.weekday():
            passed.append(f"entry_day_of_week={label['entry_day_of_week']} ✓")
        else:
            failed.append(
                f"entry_day_of_week mismatch: got {label['entry_day_of_week']}, expected {entry_ts.weekday()}"
            )

    # minutes_to_close should be within regular US session minutes.
    if label.get("minutes_to_close") is not None:
        if 0 <= label["minutes_to_close"] <= MINUTES_TO_CLOSE_MAX:
            passed.append(f"minutes_to_close={label['minutes_to_close']} in range ✓")
        else:
            failed.append(f"minutes_to_close={label['minutes_to_close']} out of range [0, {MINUTES_TO_CLOSE_MAX}]")

    return {"passed": passed, "failed": failed}


async def validate_overnight_gap(label: Dict, ticker: str, entry_ts: datetime) -> Dict[str, List[str]]:
    """Validate overnight_gap against raw bar data."""
    passed, failed = [], []

    if label.get("overnight_gap_pct") is None:
        return {"passed": [], "failed": []}

    entry_date = entry_ts.date()

    async def query(session: Any) -> Tuple[Optional[float], Optional[float]]:
        # Get today's open
        today_stmt = text("""
            SELECT open FROM silver_alpaca_bars
            WHERE ticker = :ticker AND DATE(bar_start_ts_utc) = :entry_date
            ORDER BY bar_start_ts_utc ASC LIMIT 1
        """)
        today_result = await session.execute(today_stmt, {"ticker": ticker, "entry_date": entry_date})
        today_row = today_result.fetchone()
        today_open = today_row[0] if today_row else None

        # Get prior day's close
        prior_stmt = text("""
            SELECT close FROM silver_alpaca_bars
            WHERE ticker = :ticker AND DATE(bar_start_ts_utc) < :entry_date
            ORDER BY bar_start_ts_utc DESC LIMIT 1
        """)
        prior_result = await session.execute(prior_stmt, {"ticker": ticker, "entry_date": entry_date})
        prior_row = prior_result.fetchone()
        prior_close = prior_row[0] if prior_row else None

        return today_open, prior_close

    today_open, prior_close = await db_query(query)

    if today_open and prior_close and prior_close > 0:
        expected_gap = ((today_open - prior_close) / prior_close) * 100
        actual_gap = label["overnight_gap_pct"]

        if abs(expected_gap - actual_gap) < 0.001:
            passed.append(f"overnight_gap_pct={actual_gap:.4f} matches raw data ✓")
        else:
            failed.append(f"overnight_gap mismatch: got {actual_gap:.4f}, expected {expected_gap:.4f}")

    return {"passed": passed, "failed": failed}


async def validate_darkpool(label: Dict, ticker: str, entry_ts: datetime) -> Dict[str, List[str]]:
    """Validate darkpool volume against raw darkpool table."""
    passed, failed, warnings = [], [], []

    async def count_dp(session: Any, minutes: int) -> int:
        start_ts = entry_ts - timedelta(minutes=minutes)
        stmt = text("""
            SELECT COALESCE(SUM(size_shares), 0) as total
            FROM silver_uw_darkpool
            WHERE ticker = :ticker
            AND dark_ts_utc BETWEEN :start_ts AND :entry_ts
        """)
        result = await session.execute(stmt, {"ticker": ticker, "start_ts": start_ts, "entry_ts": entry_ts})
        row = result.fetchone()
        return int(row[0]) if row else 0

    # Check darkpool_1h
    if label.get("darkpool_volume_1h") is not None:
        expected = await db_query(lambda s: count_dp(s, 60))
        actual = label["darkpool_volume_1h"]
        if expected == actual:
            passed.append(f"darkpool_1h={actual} matches ✓")
        elif expected == 0:
            warnings.append(f"darkpool_1h: no raw darkpool data found for {ticker}")
        else:
            failed.append(f"darkpool_1h mismatch: got {actual}, expected {expected}")

    return {"passed": passed, "failed": failed, "warnings": warnings}


def validate_greeks(label: Dict) -> Dict[str, List[str]]:
    """Validate Greeks are in reasonable ranges."""
    passed, failed = [], []

    # Delta should be in [-1, 1]
    if label.get("delta_at_entry") is not None:
        delta = label["delta_at_entry"]
        if -1 <= delta <= 1:
            passed.append(f"delta_at_entry={delta:.4f} in range [-1, 1] ✓")
        else:
            failed.append(f"delta_at_entry={delta:.4f} out of range [-1, 1]")

    # Gamma should be >= 0
    if label.get("gamma_at_entry") is not None:
        gamma = label["gamma_at_entry"]
        if gamma >= 0:
            passed.append(f"gamma_at_entry={gamma:.6f} >= 0 ✓")
        else:
            failed.append(f"gamma_at_entry={gamma:.6f} should be >= 0")

    # IV should be > 0 and < 500% (sane range)
    if label.get("iv_at_entry") is not None:
        iv = label["iv_at_entry"]
        if 0 < iv < 5:  # 0-500%
            passed.append(f"iv_at_entry={iv:.4f} in sane range ✓")
        else:
            failed.append(f"iv_at_entry={iv:.4f} out of sane range (0, 5)")

    # IV rank should be 0-100
    if label.get("iv_rank_at_entry") is not None:
        iv_rank = label["iv_rank_at_entry"]
        if 0 <= iv_rank <= 100:
            passed.append(f"iv_rank_at_entry={iv_rank:.1f} in range [0, 100] ✓")
        else:
            failed.append(f"iv_rank_at_entry={iv_rank:.1f} out of range [0, 100]")

    return {"passed": passed, "failed": failed}


# ============================================================================
# SANITY CHECKS (BATCH)
# ============================================================================


async def run_sanity_checks() -> Dict[str, Any]:
    """Run batch sanity checks on all records."""
    results: Dict[str, Any] = {"passed": 0, "failed": 0, "issues": []}

    async def check(session: Any) -> List[Dict]:
        stmt = text(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE NOT ml_ready) as not_ready,
                COUNT(*) FILTER (WHERE ml_ready AND (delta_at_entry < -1 OR delta_at_entry > 1)) as bad_delta,
                COUNT(*) FILTER (WHERE ml_ready AND gamma_at_entry < 0) as bad_gamma,
                COUNT(*) FILTER (WHERE ml_ready AND (iv_rank_at_entry < 0 OR iv_rank_at_entry > 100)) as bad_iv_rank,
                COUNT(*) FILTER (
                    WHERE ml_ready AND (minutes_to_close < 0 OR minutes_to_close > """
            + str(MINUTES_TO_CLOSE_MAX)
            + """)
                ) as bad_mtc,
                COUNT(*) FILTER (WHERE ml_ready AND (entry_hour < 0 OR entry_hour > 23)) as bad_hour,
                COUNT(*) FILTER (WHERE ml_ready AND darkpool_volume_1h < 0) as bad_dp,
                COUNT(*) FILTER (WHERE ml_ready AND rvol_1h < 0) as bad_rvol
            FROM price_target_labels
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        return dict(row._mapping)

    stats = await db_query(check)

    checks = [
        ("delta_at_entry in [-1, 1]", stats["bad_delta"]),
        ("gamma_at_entry >= 0", stats["bad_gamma"]),
        ("iv_rank_at_entry in [0, 100]", stats["bad_iv_rank"]),
        (f"minutes_to_close in [0, {MINUTES_TO_CLOSE_MAX}]", stats["bad_mtc"]),
        ("entry_hour in [0, 23]", stats["bad_hour"]),
        ("darkpool_volume_1h >= 0", stats["bad_dp"]),
        ("rvol_1h >= 0", stats["bad_rvol"]),
    ]

    for check_name, bad_count in checks:
        if bad_count == 0:
            results["passed"] += 1
            logger.info(f"✓ {check_name}: PASSED")
        else:
            results["failed"] += 1
            results["issues"].append(f"✗ {check_name}: {bad_count} violations")
            logger.error(f"✗ {check_name}: {bad_count} violations")

    not_ready = int(stats.get("not_ready") or 0)
    if not_ready > 0:
        results["failed"] += 1
        issue = f"✗ ml_ready = false rows present: {not_ready}"
        results["issues"].append(issue)
        logger.error(issue)
    else:
        results["passed"] += 1
        logger.info("✓ ml_ready coverage: no incomplete rows")

    return results


# ============================================================================
# DATA SOURCE AUDIT
# ============================================================================

_AUDIT_SOURCE_SPECS: dict[str, dict[str, Any]] = {
    "silver_alpaca_bars": {
        "sql": "SELECT MIN(DATE(bar_start_ts_utc)), MAX(DATE(bar_start_ts_utc)), COUNT(DISTINCT ticker) FROM silver_alpaca_bars",
        "features": ["overnight_gap", "vwap", "underlying"],
        "heber_method": "read_bars",
        "row_count_as_tickers": False,
    },
    "silver_uw_flow": {
        "sql": "SELECT MIN(DATE(flow_ts_utc)), MAX(DATE(flow_ts_utc)), COUNT(DISTINCT ticker) FROM silver_uw_flow",
        "features": ["greeks", "iv", "rvol", "flow_aggression", "checkpoints"],
        "heber_method": "read_flow",
        "row_count_as_tickers": False,
    },
    "silver_uw_darkpool": {
        "sql": "SELECT MIN(DATE(dark_ts_utc)), MAX(DATE(dark_ts_utc)), COUNT(DISTINCT ticker) FROM silver_uw_darkpool",
        "features": ["darkpool_*"],
        "heber_method": "read_darkpool",
        "row_count_as_tickers": False,
    },
    "silver_greek_exposure": {
        "sql": "SELECT MIN(DATE(ts_utc)), MAX(DATE(ts_utc)), COUNT(DISTINCT ticker) FROM silver_greek_exposure",
        "features": ["gex", "vex"],
        "heber_method": "read_greek_exposure",
        "row_count_as_tickers": False,
    },
    "silver_max_pain": {
        "sql": "SELECT MIN(date), MAX(date), COUNT(DISTINCT ticker) FROM silver_max_pain",
        "features": ["max_pain_distance"],
        "heber_method": "read_max_pain",
        "row_count_as_tickers": False,
    },
    "silver_market_tide": {
        "sql": "SELECT MIN(DATE(ts_utc)), MAX(DATE(ts_utc)), COUNT(*) FROM silver_market_tide",
        "features": ["market_tide_30m", "market_tide_direction"],
        "heber_method": "read_market_tide",
        "row_count_as_tickers": True,
    },
    "silver_vix_data": {
        "sql": "SELECT MIN(DATE(ts_utc)), MAX(DATE(ts_utc)), COUNT(*) FROM silver_vix_data",
        "features": ["vix_at_entry", "vix_regime"],
        "heber_method": None,
        "tickers_constant": 1,
    },
    "silver_regime_history": {
        "sql": "SELECT MIN(DATE(ts_utc)), MAX(DATE(ts_utc)), COUNT(*) FROM silver_regime_history",
        "features": ["trend_regime", "vol_regime", "risk_regime", "session_regime"],
        "heber_method": None,
        "tickers_constant": 1,
    },
}

_AUDIT_SOURCE_ORDER = [
    "silver_alpaca_bars",
    "silver_uw_flow",
    "silver_uw_darkpool",
    "silver_greek_exposure",
    "silver_max_pain",
    "silver_market_tide",
    "silver_vix_data",
    "silver_regime_history",
]

_PREFER_HEBER_FALSE_VALUES = {"0", "false", "no", "off", "n"}
_SOURCE_TIME_COLUMNS = ["ts_event", "ts_utc", "bar_start_ts", "flow_ts_utc", "dark_ts_utc", "date"]
_SOURCE_TICKER_COLUMNS = ["ticker", "symbol", "instrument_key"]


def _prefer_heber_source_from_env() -> bool:
    raw = os.getenv("ORION_VALIDATE_FEATURES_PREFER_HEBER", "1").strip().lower()
    return raw not in _PREFER_HEBER_FALSE_VALUES


def _pick_first_existing_column(df: pd.DataFrame, columns: List[str]) -> Optional[str]:
    for column in columns:
        if column in df.columns:
            return column
    return None


def _label_date_bounds(
    min_date: Optional[date],
    max_date: Optional[date],
) -> Tuple[Optional[datetime], Optional[datetime]]:
    if min_date is None or max_date is None:
        return None, None
    return (
        datetime.combine(min_date, time.min, tzinfo=timezone.utc),
        datetime.combine(max_date, time.max, tzinfo=timezone.utc),
    )


def _summarize_heber_source_frame(
    df: pd.DataFrame,
    row_count_as_tickers: bool = False,
) -> Dict[str, Any]:
    if df.empty:
        return {"min_date": None, "max_date": None, "tickers": 0}

    time_column = _pick_first_existing_column(df, _SOURCE_TIME_COLUMNS)
    min_date = None
    max_date = None
    if time_column is not None:
        time_series = pd.to_datetime(df[time_column], utc=True, errors="coerce").dropna()
        if not time_series.empty:
            min_date = str(time_series.min().date())
            max_date = str(time_series.max().date())

    ticker_column = _pick_first_existing_column(df, _SOURCE_TICKER_COLUMNS)
    if ticker_column is None:
        tickers = int(len(df)) if row_count_as_tickers else 0
    else:
        ticker_series = df[ticker_column].dropna().astype(str)
        if ticker_column == "instrument_key":
            ticker_series = ticker_series.str.split(":").str[-1]
        tickers = int(ticker_series.nunique())

    return {"min_date": min_date, "max_date": max_date, "tickers": tickers}


def _heber_read_kwargs(
    source: str,
    label_start_ts: Optional[datetime],
    label_end_ts: Optional[datetime],
) -> Dict[str, Any]:
    asof_time = datetime.now(timezone.utc)
    if source == "silver_alpaca_bars":
        return {
            "symbols": [],
            "asof_time": asof_time,
            "start_time": label_start_ts,
            "end_time": label_end_ts,
            "timeframe": "1m",
        }
    if source == "silver_market_tide":
        return {
            "asof_time": asof_time,
            "start_time": label_start_ts,
        }
    return {
        "asof_time": asof_time,
        "start_time": label_start_ts,
    }


async def _fetch_source_summary_from_heber(
    *,
    source: str,
    label_start_ts: Optional[datetime],
    label_end_ts: Optional[datetime],
) -> Optional[Dict[str, Any]]:
    spec = _AUDIT_SOURCE_SPECS[source]
    method_name = spec.get("heber_method")
    if not method_name:
        return None

    reader = get_heber_reader()
    method = getattr(reader, method_name, None)
    if method is None:
        return None

    kwargs = _heber_read_kwargs(source, label_start_ts, label_end_ts)
    try:
        df = await asyncio.to_thread(method, **kwargs)
    except Exception as exc:
        logger.warning(
            "audit_source_heber_read_failed",
            source=source,
            method=method_name,
            error=str(exc),
        )
        return None

    summary = _summarize_heber_source_frame(df, row_count_as_tickers=bool(spec.get("row_count_as_tickers", False)))
    if summary["min_date"] is None or summary["max_date"] is None:
        return None

    summary["backend"] = "heber"
    return summary


async def _fetch_source_summary_from_local_db(*, source: str) -> Dict[str, Any]:
    spec = _AUDIT_SOURCE_SPECS[source]

    async def audit(session: Any) -> Any:
        result = await session.execute(text(spec["sql"]))
        return result.fetchone()

    row = await db_query(audit)
    min_date = str(row[0]) if row and row[0] else None
    max_date = str(row[1]) if row and row[1] else None
    tickers_constant = spec.get("tickers_constant")
    if tickers_constant is not None:
        tickers = int(tickers_constant)
    else:
        tickers = int(row[2]) if row and row[2] else 0

    return {
        "min_date": min_date,
        "max_date": max_date,
        "tickers": tickers,
        "backend": "local_db",
    }


async def _fetch_source_summary(
    *,
    source: str,
    label_start_ts: Optional[datetime],
    label_end_ts: Optional[datetime],
    prefer_heber: bool,
) -> Dict[str, Any]:
    if prefer_heber:
        heber_summary = await _fetch_source_summary_from_heber(
            source=source,
            label_start_ts=label_start_ts,
            label_end_ts=label_end_ts,
        )
        if heber_summary is not None:
            return heber_summary
    return await _fetch_source_summary_from_local_db(source=source)


async def _load_label_period() -> Dict[str, Any]:
    async def query(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT MIN(DATE(entry_ts)) as min_date, MAX(DATE(entry_ts)) as max_date,
                   COUNT(DISTINCT ticker) as ticker_count
            FROM price_target_labels WHERE ml_ready
            """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        min_date = row[0] if row else None
        max_date = row[1] if row else None
        ticker_count = int(row[2]) if row and row[2] else 0
        return {
            "min_date_raw": min_date,
            "max_date_raw": max_date,
            "min_date": str(min_date) if min_date else None,
            "max_date": str(max_date) if max_date else None,
            "tickers": ticker_count,
        }

    return await db_query(query)


# Feature-to-source mapping for all 130+ features
FEATURE_SOURCE_MAPPING = {
    # Greeks - from silver_uw_flow
    "delta_at_entry": "silver_uw_flow",
    "gamma_at_entry": "silver_uw_flow",
    "iv_at_entry": "silver_uw_flow",
    "volume_at_entry": "silver_uw_flow",
    "open_interest_at_entry": "silver_uw_flow",
    # IV Rank - from silver_uw_flow history
    "iv_rank_at_entry": "silver_uw_flow",
    # Darkpool - from silver_uw_darkpool
    "darkpool_volume_1h": "silver_uw_darkpool",
    "darkpool_15m": "silver_uw_darkpool",
    "darkpool_30m": "silver_uw_darkpool",
    "darkpool_4h": "silver_uw_darkpool",
    "darkpool_1d": "silver_uw_darkpool",
    "darkpool_3d": "silver_uw_darkpool",
    "darkpool_1w": "silver_uw_darkpool",
    "darkpool_2w": "silver_uw_darkpool",
    "darkpool_4w": "silver_uw_darkpool",
    # Bars - from silver_alpaca_bars
    "overnight_gap_pct": "silver_alpaca_bars",
    "vwap_distance_pct": "silver_alpaca_bars",
    "underlying_at_entry": "silver_alpaca_bars",
    "underlying_at_1h": "silver_alpaca_bars",
    "price_change_5d_prior": "silver_alpaca_bars",
    # GEX/VEX - from silver_greek_exposure
    "gex_at_entry": "silver_greek_exposure",
    "vex_at_entry": "silver_greek_exposure",
    # Max Pain - from silver_max_pain
    "max_pain_distance_pct": "silver_max_pain",
    # Market Tide - from silver_market_tide
    "market_tide_30m": "silver_market_tide",
    "market_tide_direction": "silver_market_tide",
    # Regimes - from silver_vix_data / silver_regime_history
    "vix_at_entry": "silver_vix_data",
    "vix_regime_at_entry": "silver_vix_data",
    "trend_regime_at_entry": "silver_regime_history",
    "vol_regime_at_entry": "silver_regime_history",
    "risk_regime_at_entry": "silver_regime_history",
    "session_regime_at_entry": "silver_regime_history",
    # Time features - derived from entry_ts (no source table)
    "entry_hour": "derived",
    "entry_day_of_week": "derived",
    "entry_session": "derived",
    "minutes_to_close": "derived",
    # Flow aggression - from silver_uw_flow
    "ask_side_ratio": "silver_uw_flow",
    "sweep_ratio_1h": "silver_uw_flow",
    "same_ticker_premium_1h": "silver_uw_flow",
    "institutional_flow_1w": "silver_uw_flow",
    # RVOL - from silver_uw_flow
    "rvol_1h": "silver_uw_flow",
    "rvol_daily": "silver_uw_flow",
    "rvol_weekly": "silver_uw_flow",
    "rvol_30m": "silver_uw_flow",
    "rvol_3d": "silver_uw_flow",
    # Return checkpoints - from silver_uw_flow (option prices)
    "return_at_15m": "silver_uw_flow",
    "return_at_30m": "silver_uw_flow",
    "return_at_1h": "silver_uw_flow",
    "return_at_2h": "silver_uw_flow",
    "return_at_4h": "silver_uw_flow",
    "return_at_8h": "silver_uw_flow",
    "return_at_1d": "silver_uw_flow",
    "return_at_2d": "silver_uw_flow",
    "return_at_1w": "silver_uw_flow",
}


async def audit_data_sources() -> Dict[str, Any]:
    """Audit ALL source tables for the label period."""
    label_period = await _load_label_period()
    label_start_ts, label_end_ts = _label_date_bounds(
        label_period.get("min_date_raw"),
        label_period.get("max_date_raw"),
    )
    prefer_heber = _prefer_heber_source_from_env()

    sources: Dict[str, Dict[str, Any]] = {}
    for source in _AUDIT_SOURCE_ORDER:
        summary = await _fetch_source_summary(
            source=source,
            label_start_ts=label_start_ts,
            label_end_ts=label_end_ts,
            prefer_heber=prefer_heber,
        )
        summary["features"] = list(_AUDIT_SOURCE_SPECS[source]["features"])
        sources[source] = summary

    audit_results = {
        "label_period": {
            "min_date": label_period["min_date"],
            "max_date": label_period["max_date"],
            "tickers": label_period["tickers"],
        },
        "sources": sources,
    }

    logger.info("=" * 60)
    logger.info("DATA SOURCE AUDIT - Silver Coverage")
    logger.info("=" * 60)
    logger.info(
        f"Label period: {audit_results['label_period']['min_date']} to {audit_results['label_period']['max_date']}"
    )
    logger.info(f"Label tickers: {audit_results['label_period']['tickers']}")
    logger.info(f"Source preference: {'heber-first' if prefer_heber else 'local-db-only'}")
    logger.info("-" * 60)

    label_min = audit_results["label_period"]["min_date"]
    label_max = audit_results["label_period"]["max_date"]

    for source, info in audit_results["sources"].items():
        status = "✓" if info["min_date"] and info["max_date"] else "✗"
        features = ", ".join(info.get("features", []))
        backend = info.get("backend", "local_db")
        logger.info(f"{status} {source}")
        logger.info(
            f"    Period: {info['min_date']} to {info['max_date']} | Tickers: {info['tickers']} | Backend: {backend}"
        )
        logger.info(f"    Features: {features}")

        # Check coverage gaps
        if info["min_date"] and info["max_date"] and label_min and label_max:
            if info["min_date"] > label_min:
                logger.warning(f"    ⚠️ MISSING DATA: Starts {info['min_date']}, labels start {label_min}")
            if info["max_date"] < label_max:
                logger.warning(f"    ⚠️ MISSING DATA: Ends {info['max_date']}, labels end {label_max}")
        elif not info["min_date"]:
            logger.warning("    ⚠️ TABLE EMPTY - no data!")

    return audit_results


# ============================================================================
# MAIN
# ============================================================================


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Validate ML features")
    parser.add_argument("--spot-check", type=str, help="Event ID to spot-check")
    parser.add_argument("--sanity", action="store_true", help="Run batch sanity checks")
    parser.add_argument("--audit-sources", action="store_true", help="Audit data source coverage")
    parser.add_argument("--all", action="store_true", help="Run all validations")
    args = parser.parse_args()

    await init_db()

    if args.spot_check:
        logger.info(f"Spot-checking record: {args.spot_check}")
        results = await spot_check_record(args.spot_check)
        logger.info(f"Passed: {len(results['passed'])}")
        for p in results["passed"]:
            logger.info(f"  {p}")
        logger.info(f"Failed: {len(results['failed'])}")
        for f in results["failed"]:
            logger.error(f"  {f}")
        if results["warnings"]:
            logger.info(f"Warnings: {len(results['warnings'])}")
            for w in results["warnings"]:
                logger.warning(f"  {w}")

    if args.sanity or args.all:
        logger.info("Running sanity checks...")
        results = await run_sanity_checks()
        logger.info(f"Sanity checks: {results['passed']} passed, {results['failed']} failed")

    if args.audit_sources or args.all:
        logger.info("Auditing data sources...")
        await audit_data_sources()


if __name__ == "__main__":
    asyncio.run(main())
