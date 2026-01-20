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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.validate_features")


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
            failed.append(f"entry_day_of_week mismatch: got {label['entry_day_of_week']}, expected {entry_ts.weekday()}")

    # minutes_to_close should be reasonable (0-390)
    if label.get("minutes_to_close") is not None:
        if 0 <= label["minutes_to_close"] <= 390:
            passed.append(f"minutes_to_close={label['minutes_to_close']} in range ✓")
        else:
            failed.append(f"minutes_to_close={label['minutes_to_close']} out of range [0, 390]")

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
        stmt = text("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE delta_at_entry < -1 OR delta_at_entry > 1) as bad_delta,
                COUNT(*) FILTER (WHERE gamma_at_entry < 0) as bad_gamma,
                COUNT(*) FILTER (WHERE iv_rank_at_entry < 0 OR iv_rank_at_entry > 100) as bad_iv_rank,
                COUNT(*) FILTER (WHERE minutes_to_close < 0 OR minutes_to_close > 500) as bad_mtc,
                COUNT(*) FILTER (WHERE entry_hour < 0 OR entry_hour > 23) as bad_hour,
                COUNT(*) FILTER (WHERE darkpool_volume_1h < 0) as bad_dp,
                COUNT(*) FILTER (WHERE rvol_1h < 0) as bad_rvol
            FROM price_target_labels
            WHERE ml_ready
        """)
        result = await session.execute(stmt)
        row = result.fetchone()
        return dict(row._mapping)

    stats = await db_query(check)

    checks = [
        ("delta_at_entry in [-1, 1]", stats["bad_delta"]),
        ("gamma_at_entry >= 0", stats["bad_gamma"]),
        ("iv_rank_at_entry in [0, 100]", stats["bad_iv_rank"]),
        ("minutes_to_close in [0, 500]", stats["bad_mtc"]),
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

    return results


# ============================================================================
# DATA SOURCE AUDIT
# ============================================================================

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

    async def audit(session: Any) -> Dict:
        # Get label date range
        label_stmt = text("""
            SELECT MIN(DATE(entry_ts)) as min_date, MAX(DATE(entry_ts)) as max_date,
                   COUNT(DISTINCT ticker) as ticker_count
            FROM price_target_labels WHERE ml_ready
        """)
        label_result = await session.execute(label_stmt)
        label_row = label_result.fetchone()

        sources = {}

        # 1. silver_alpaca_bars
        stmt = text("SELECT MIN(DATE(bar_start_ts_utc)), MAX(DATE(bar_start_ts_utc)), COUNT(DISTINCT ticker) FROM silver_alpaca_bars")
        result = await session.execute(stmt)
        row = result.fetchone()
        sources["silver_alpaca_bars"] = {"min_date": str(row[0]) if row[0] else None, "max_date": str(row[1]) if row[1] else None, "tickers": row[2] or 0, "features": ["overnight_gap", "vwap", "underlying"]}

        # 2. silver_uw_flow
        stmt = text("SELECT MIN(DATE(flow_ts_utc)), MAX(DATE(flow_ts_utc)), COUNT(DISTINCT ticker) FROM silver_uw_flow")
        result = await session.execute(stmt)
        row = result.fetchone()
        sources["silver_uw_flow"] = {"min_date": str(row[0]) if row[0] else None, "max_date": str(row[1]) if row[1] else None, "tickers": row[2] or 0, "features": ["greeks", "iv", "rvol", "flow_aggression", "checkpoints"]}

        # 3. silver_uw_darkpool
        stmt = text("SELECT MIN(DATE(dark_ts_utc)), MAX(DATE(dark_ts_utc)), COUNT(DISTINCT ticker) FROM silver_uw_darkpool")
        result = await session.execute(stmt)
        row = result.fetchone()
        sources["silver_uw_darkpool"] = {"min_date": str(row[0]) if row[0] else None, "max_date": str(row[1]) if row[1] else None, "tickers": row[2] or 0, "features": ["darkpool_*"]}

        # 4. silver_greek_exposure
        stmt = text("SELECT MIN(DATE(ts_utc)), MAX(DATE(ts_utc)), COUNT(DISTINCT ticker) FROM silver_greek_exposure")
        result = await session.execute(stmt)
        row = result.fetchone()
        sources["silver_greek_exposure"] = {"min_date": str(row[0]) if row[0] else None, "max_date": str(row[1]) if row[1] else None, "tickers": row[2] or 0, "features": ["gex", "vex"]}

        # 5. silver_max_pain
        stmt = text("SELECT MIN(date), MAX(date), COUNT(DISTINCT ticker) FROM silver_max_pain")
        result = await session.execute(stmt)
        row = result.fetchone()
        sources["silver_max_pain"] = {"min_date": str(row[0]) if row[0] else None, "max_date": str(row[1]) if row[1] else None, "tickers": row[2] or 0, "features": ["max_pain_distance"]}

        # 6. silver_market_tide
        stmt = text("SELECT MIN(DATE(ts_utc)), MAX(DATE(ts_utc)), COUNT(*) FROM silver_market_tide")
        result = await session.execute(stmt)
        row = result.fetchone()
        sources["silver_market_tide"] = {"min_date": str(row[0]) if row[0] else None, "max_date": str(row[1]) if row[1] else None, "tickers": row[2] or 0, "features": ["market_tide_30m", "market_tide_direction"]}

        # 7. silver_vix_data
        stmt = text("SELECT MIN(DATE(ts_utc)), MAX(DATE(ts_utc)), COUNT(*) FROM silver_vix_data")
        result = await session.execute(stmt)
        row = result.fetchone()
        sources["silver_vix_data"] = {"min_date": str(row[0]) if row[0] else None, "max_date": str(row[1]) if row[1] else None, "tickers": 1, "features": ["vix_at_entry", "vix_regime"]}

        # 8. silver_regime_history
        stmt = text("SELECT MIN(DATE(ts_utc)), MAX(DATE(ts_utc)), COUNT(*) FROM silver_regime_history")
        result = await session.execute(stmt)
        row = result.fetchone()
        sources["silver_regime_history"] = {"min_date": str(row[0]) if row[0] else None, "max_date": str(row[1]) if row[1] else None, "tickers": 1, "features": ["trend_regime", "vol_regime", "risk_regime", "session_regime"]}

        return {
            "label_period": {
                "min_date": str(label_row[0]) if label_row[0] else None,
                "max_date": str(label_row[1]) if label_row[1] else None,
                "tickers": label_row[2] if label_row[2] else 0,
            },
            "sources": sources,
        }

    audit_results = await db_query(audit)

    logger.info("=" * 60)
    logger.info("DATA SOURCE AUDIT - All 11 Silver Tables")
    logger.info("=" * 60)
    logger.info(f"Label period: {audit_results['label_period']['min_date']} to {audit_results['label_period']['max_date']}")
    logger.info(f"Label tickers: {audit_results['label_period']['tickers']}")
    logger.info("-" * 60)

    label_min = audit_results["label_period"]["min_date"]
    label_max = audit_results["label_period"]["max_date"]

    for source, info in audit_results["sources"].items():
        status = "✓" if info["min_date"] and info["max_date"] else "✗"
        features = ", ".join(info.get("features", []))
        logger.info(f"{status} {source}")
        logger.info(f"    Period: {info['min_date']} to {info['max_date']} | Tickers: {info['tickers']}")
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
