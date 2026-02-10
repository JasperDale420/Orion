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
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import text

from orion.clients.heber_reader import get_heber_reader
from orion.core.logging_config import setup_logging
from orion.shared.db_utils import db_query
from orion.storage.db import init_db

logger = logging.getLogger(__name__)

# Critical tickers that must have continuous data
CRITICAL_TICKERS = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "TSLA"]

# Market hours (Eastern Time, simplified as UTC-5)
MARKET_OPEN_HOUR = 14  # 9:30 ET = 14:30 UTC
MARKET_CLOSE_HOUR = 21  # 4:00 PM ET = 21:00 UTC
_PREFER_HEBER_FALSE_VALUES = {"0", "false", "no", "off", "n"}


# =============================================================================
# ALPACA BARS CHECKS
# =============================================================================


async def check_zero_valued_bars(lookback_hours: int = 24) -> List[Dict]:
    """Check for bars with invalid close values in recent data."""
    if _prefer_heber_source():
        heber_rows = await _check_zero_valued_bars_from_heber(lookback_hours=lookback_hours)
        if heber_rows is not None:
            return heber_rows

    async def query(session: Any) -> List[Dict]:
        stmt = text(
            """
            SELECT ticker, COUNT(*) as zero_count,
                   MIN(bar_start_ts_utc) as earliest,
                   MAX(bar_start_ts_utc) as latest
            FROM silver_alpaca_bars
            WHERE (close = 0 OR close IS NULL)
              AND bar_start_ts_utc > NOW() - INTERVAL ':hours hours'
            GROUP BY ticker
            ORDER BY zero_count DESC
        """.replace(":hours", str(lookback_hours))
        )
        result = await session.execute(stmt)
        return [{"ticker": r[0], "zero_count": r[1], "earliest": r[2], "latest": r[3]} for r in result.fetchall()]

    return await db_query(query)


async def check_data_staleness(stale_minutes: int = 15) -> List[Dict]:
    """Check for tickers with stale data during market hours."""
    now = datetime.now(timezone.utc)
    current_hour = now.hour

    # Only check during market hours
    if current_hour < MARKET_OPEN_HOUR or current_hour >= MARKET_CLOSE_HOUR:
        return []

    if _prefer_heber_source():
        heber_rows = await _check_data_staleness_from_heber(stale_minutes=stale_minutes)
        if heber_rows is not None:
            return heber_rows

    async def query(session: Any) -> List[Dict]:
        stmt = text(
            """
            SELECT ticker, MAX(bar_start_ts_utc) as last_bar,
                   EXTRACT(EPOCH FROM (NOW() - MAX(bar_start_ts_utc))) / 60 as minutes_ago
            FROM silver_alpaca_bars
            WHERE ticker = ANY(:tickers)
            GROUP BY ticker
            HAVING EXTRACT(EPOCH FROM (NOW() - MAX(bar_start_ts_utc))) / 60 > :stale_minutes
        """
        )
        result = await session.execute(stmt, {"tickers": CRITICAL_TICKERS, "stale_minutes": stale_minutes})
        return [{"ticker": r[0], "last_bar": r[1], "minutes_ago": r[2]} for r in result.fetchall()]

    return await db_query(query)


async def check_bar_gaps(ticker: str = "SPY", gap_minutes: int = 5) -> List[Dict]:
    """Check for gaps in bar data for a ticker."""
    if _prefer_heber_source():
        heber_rows = await _check_bar_gaps_from_heber(ticker=ticker, gap_minutes=gap_minutes)
        if heber_rows is not None:
            return heber_rows

    async def query(session: Any) -> List[Dict]:
        stmt = text(
            """
            WITH bar_gaps AS (
                SELECT
                    bar_start_ts_utc,
                    LAG(bar_start_ts_utc) OVER (ORDER BY bar_start_ts_utc) as prev_bar,
                    EXTRACT(EPOCH FROM (bar_start_ts_utc - LAG(bar_start_ts_utc) OVER (ORDER BY bar_start_ts_utc))) / 60 as gap_minutes
                FROM silver_alpaca_bars
                WHERE ticker = :ticker
                  AND bar_start_ts_utc > NOW() - INTERVAL '24 hours'
                  AND EXTRACT(HOUR FROM bar_start_ts_utc) >= :market_open
                  AND EXTRACT(HOUR FROM bar_start_ts_utc) < :market_close
            )
            SELECT bar_start_ts_utc, prev_bar, gap_minutes
            FROM bar_gaps
            WHERE gap_minutes > :gap_minutes
            ORDER BY bar_start_ts_utc DESC
            LIMIT 10
        """
        )
        result = await session.execute(
            stmt,
            {
                "ticker": ticker,
                "gap_minutes": gap_minutes,
                "market_open": MARKET_OPEN_HOUR,
                "market_close": MARKET_CLOSE_HOUR,
            },
        )
        return [{"bar_ts": r[0], "prev_bar": r[1], "gap_minutes": r[2]} for r in result.fetchall()]

    return await db_query(query)


async def get_bars_summary() -> Dict:
    """Get Alpaca bars data quality summary."""
    if _prefer_heber_source():
        heber_summary = await _get_bars_summary_from_heber()
        if heber_summary is not None:
            return heber_summary

    async def query(session: Any) -> Dict:
        stmt = text(
            """
            SELECT
                COUNT(*) as total_bars,
                SUM(CASE WHEN close > 0 THEN 1 ELSE 0 END) as valid_bars,
                SUM(CASE WHEN close = 0 OR close IS NULL THEN 1 ELSE 0 END) as invalid_bars,
                COUNT(DISTINCT ticker) as unique_tickers,
                MAX(bar_start_ts_utc) as latest_bar
            FROM silver_alpaca_bars
            WHERE bar_start_ts_utc > NOW() - INTERVAL '24 hours'
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        return {
            "total_bars_24h": row[0] or 0,
            "valid_bars": row[1] or 0,
            "invalid_bars": row[2] or 0,
            "unique_tickers": row[3] or 0,
            "latest_bar": row[4],
            "validity_pct": round(100 * (row[1] or 0) / row[0], 2) if row[0] and row[0] > 0 else 0,
        }

    return await db_query(query)


# =============================================================================
# UW FLOW CHECKS
# =============================================================================


async def get_flow_summary() -> Dict:
    """Get UW Flow data quality summary."""
    if _prefer_heber_source():
        heber_summary = await _get_flow_summary_from_heber()
        if heber_summary is not None:
            return heber_summary

    async def query(session: Any) -> Dict:
        stmt = text(
            """
            SELECT
                COUNT(*) as total_flows,
                COUNT(CASE WHEN premium_usd IS NOT NULL AND premium_usd > 0 THEN 1 END) as valid_premium,
                COUNT(CASE WHEN premium_usd IS NULL OR premium_usd = 0 THEN 1 END) as missing_premium,
                COUNT(DISTINCT ticker) as unique_tickers,
                MAX(flow_ts_utc) as latest_flow
            FROM silver_uw_flow
            WHERE flow_ts_utc > NOW() - INTERVAL '24 hours'
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        total = row[0] or 0
        valid = row[1] or 0
        return {
            "total_flows_24h": total,
            "valid_premium": valid,
            "missing_premium": row[2] or 0,
            "unique_tickers": row[3] or 0,
            "latest_flow": row[4],
            "validity_pct": round(100 * valid / total, 2) if total > 0 else 0,
            "backend": "local_db",
        }

    return await db_query(query)


async def check_flow_staleness(stale_minutes: int = 30) -> bool:
    """Check if flow data is stale during market hours."""
    now = datetime.now(timezone.utc)
    current_hour = now.hour

    if current_hour < MARKET_OPEN_HOUR or current_hour >= MARKET_CLOSE_HOUR:
        return False  # Outside market hours, no alert

    if _prefer_heber_source():
        heber_stale = await _check_flow_staleness_from_heber(stale_minutes=stale_minutes)
        if heber_stale is not None:
            return heber_stale

    async def query(session: Any) -> bool:
        stmt = text(
            """
            SELECT EXTRACT(EPOCH FROM (NOW() - MAX(flow_ts_utc))) / 60 as minutes_ago
            FROM silver_uw_flow
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        return row[0] is not None and row[0] > stale_minutes

    return await db_query(query)


# =============================================================================
# DARKPOOL CHECKS
# =============================================================================


async def get_darkpool_summary() -> Dict:
    """Get Darkpool data quality summary."""
    if _prefer_heber_source():
        heber_summary = await _get_darkpool_summary_from_heber()
        if heber_summary is not None:
            return heber_summary

    async def query(session: Any) -> Dict:
        stmt = text(
            """
            SELECT
                COUNT(*) as total_trades,
                COUNT(CASE WHEN size_shares IS NOT NULL AND size_shares > 0 THEN 1 END) as valid_trades,
                COUNT(CASE WHEN trade_price IS NULL OR trade_price = 0 THEN 1 END) as invalid_price,
                COUNT(DISTINCT ticker) as unique_tickers,
                MAX(dark_ts_utc) as latest_trade
            FROM silver_uw_darkpool
            WHERE dark_ts_utc > NOW() - INTERVAL '24 hours'
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        total = row[0] or 0
        valid = row[1] or 0
        return {
            "total_trades_24h": total,
            "valid_trades": valid,
            "invalid_price": row[2] or 0,
            "unique_tickers": row[3] or 0,
            "latest_trade": row[4],
            "validity_pct": round(100 * valid / total, 2) if total > 0 else 0,
            "backend": "local_db",
        }

    return await db_query(query)


async def check_darkpool_staleness(stale_minutes: int = 60) -> bool:
    """Check if darkpool data is stale during market hours."""
    now = datetime.now(timezone.utc)
    current_hour = now.hour

    if current_hour < MARKET_OPEN_HOUR or current_hour >= MARKET_CLOSE_HOUR:
        return False

    if _prefer_heber_source():
        heber_stale = await _check_darkpool_staleness_from_heber(stale_minutes=stale_minutes)
        if heber_stale is not None:
            return heber_stale

    async def query(session: Any) -> bool:
        stmt = text(
            """
            SELECT EXTRACT(EPOCH FROM (NOW() - MAX(dark_ts_utc))) / 60 as minutes_ago
            FROM silver_uw_darkpool
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        return row[0] is not None and row[0] > stale_minutes

    return await db_query(query)


# =============================================================================
# ML FEATURES (PRICE TARGET LABELS) CHECKS
# =============================================================================


async def get_ml_features_summary() -> Dict:
    """Get ML features population summary for ALL features in price_target_labels."""

    async def query(session: Any) -> Dict:
        # Check ALL nullable feature columns
        stmt = text(
            """
            SELECT
                COUNT(*) as total_labels,
                COUNT(*) FILTER (WHERE ml_ready) as ml_ready_count,
                -- Core identifiers (should be 100%)
                COUNT(event_id) as has_event_id,
                COUNT(ticker) as has_ticker,
                COUNT(entry_ts) as has_entry_ts,
                -- Greeks/Pricing (critical for ML)
                COUNT(delta_at_entry) as has_delta,
                COUNT(gamma_at_entry) as has_gamma,
                COUNT(iv_at_entry) as has_iv,
                COUNT(iv_rank_at_entry) as has_iv_rank,
                COUNT(underlying_at_entry) as has_underlying,
                -- Context features
                COUNT(sector) as has_sector,
                COUNT(vix_at_entry) as has_vix,
                COUNT(trend_regime_at_entry) as has_trend_regime,
                COUNT(vol_regime_at_entry) as has_vol_regime,
                COUNT(gex_at_entry) as has_gex,
                COUNT(market_tide_30m) as has_tide,
                COUNT(max_pain_distance_pct) as has_max_pain,
                -- Volume/Flow features
                COUNT(volume_at_entry) as has_volume,
                COUNT(open_interest_at_entry) as has_oi,
                COUNT(rvol_1h) as has_rvol,
                COUNT(darkpool_volume_1h) as has_darkpool,
                -- Time features
                COUNT(entry_hour) as has_entry_hour,
                COUNT(minutes_to_close) as has_minutes,
                COUNT(dte) as has_dte,
                -- Price checkpoints
                COUNT(return_at_1h) as has_return_1h,
                COUNT(return_at_2h) as has_return_2h,
                COUNT(return_at_4h) as has_return_4h,
                COUNT(return_at_eod) as has_return_eod,
                -- Misc features
                COUNT(spy_return_1h) as has_spy,
                COUNT(overnight_gap_pct) as has_gap,
                COUNT(vwap_distance_pct) as has_vwap,
                MAX(entry_ts) as latest_entry
            FROM price_target_labels
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        total = row[0] or 1

        # Build comprehensive coverage dict
        features = {
            "total_labels": row[0] or 0,
            "ml_ready_count": row[1] or 0,
        }

        # Feature categories with their indices
        feature_map = [
            ("event_id", 2),
            ("ticker", 3),
            ("entry_ts", 4),
            ("delta", 5),
            ("gamma", 6),
            ("iv", 7),
            ("iv_rank", 8),
            ("underlying", 9),
            ("sector", 10),
            ("vix", 11),
            ("trend_regime", 12),
            ("vol_regime", 13),
            ("gex", 14),
            ("market_tide", 15),
            ("max_pain", 16),
            ("volume", 17),
            ("oi", 18),
            ("rvol", 19),
            ("darkpool", 20),
            ("entry_hour", 21),
            ("minutes_to_close", 22),
            ("dte", 23),
            ("return_1h", 24),
            ("return_2h", 25),
            ("return_4h", 26),
            ("return_eod", 27),
            ("spy_return", 28),
            ("overnight_gap", 29),
            ("vwap", 30),
        ]

        for name, idx in feature_map:
            features[f"{name}_pct"] = round(100 * (row[idx] or 0) / total, 1)

        features["latest_entry"] = row[31]
        return features

    return await db_query(query)


async def check_recent_labels_features() -> Dict:
    """Check ML feature population for labels created in last 24 hours."""

    async def query(session: Any) -> Dict:
        stmt = text(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE ml_ready) as ml_ready,
                -- Critical features
                COUNT(delta_at_entry) as has_delta,
                COUNT(gamma_at_entry) as has_gamma,
                COUNT(iv_rank_at_entry) as has_iv_rank,
                COUNT(sector) as has_sector,
                COUNT(vix_at_entry) as has_vix,
                -- Other features
                COUNT(rvol_1h) as has_rvol,
                COUNT(spy_return_1h) as has_spy,
                COUNT(darkpool_volume_1h) as has_dp
            FROM price_target_labels
            WHERE entry_ts > NOW() - INTERVAL '24 hours'
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        total = row[0] or 1
        return {
            "recent_labels": row[0] or 0,
            "ml_ready": row[1] or 0,
            # Critical (these must stay near 100%)
            "delta_pct": round(100 * (row[2] or 0) / total, 1),
            "gamma_pct": round(100 * (row[3] or 0) / total, 1),
            "iv_rank_pct": round(100 * (row[4] or 0) / total, 1),
            "sector_pct": round(100 * (row[5] or 0) / total, 1),
            "vix_pct": round(100 * (row[6] or 0) / total, 1),
            # Others
            "rvol_pct": round(100 * (row[7] or 0) / total, 1),
            "spy_pct": round(100 * (row[8] or 0) / total, 1),
            "darkpool_pct": round(100 * (row[9] or 0) / total, 1),
        }

    return await db_query(query)


# =============================================================================
# MAIN RUNNER
# =============================================================================


async def run_quality_checks():
    """Run all data quality checks and log results."""
    await init_db()

    logger.info("=" * 60)
    logger.info("STARTING DATA QUALITY CHECKS")
    logger.info("=" * 60)

    results = {}

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

    # 5. UW Flow Summary
    flow_summary = await get_flow_summary()
    results["flow"] = flow_summary
    logger.info(
        f"[FLOW] 24h: {flow_summary['total_flows_24h']} flows, "
        f"{flow_summary['validity_pct']}% valid premium, "
        f"{flow_summary['unique_tickers']} tickers"
    )

    flow_stale = await check_flow_staleness(stale_minutes=30)
    if flow_stale:
        logger.warning("[FLOW] ALERT: Flow data is stale (>30 min)")
    else:
        logger.info("[FLOW] Flow data fresh ✓")

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

    # 8. Recent Labels Features
    recent = await check_recent_labels_features()
    results["recent_labels"] = recent
    if recent["recent_labels"] > 0:
        logger.info(f"[ML] Recent 24h: {recent['recent_labels']} labels, ML-ready: {recent['ml_ready']}")

        # Alert on recent label gaps
        for feat in ["delta", "gamma", "sector", "vix", "iv_rank"]:
            pct = recent.get(f"{feat}_pct", 100)
            if pct < critical_threshold:
                logger.warning(f"[ML] ALERT: Recent {feat} coverage at {pct}% (below {critical_threshold}%)")

    logger.info("=" * 60)
    logger.info("DATA QUALITY CHECKS COMPLETE")
    logger.info("=" * 60)

    return results


def _prefer_heber_source() -> bool:
    raw = os.getenv("ORION_DATA_QUALITY_CHECKER_PREFER_HEBER", "1").strip().lower()
    return raw not in _PREFER_HEBER_FALSE_VALUES


def _first_existing_column(df: pd.DataFrame, names: List[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


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


async def _read_heber_flow_24h() -> pd.DataFrame | None:
    now = datetime.now(timezone.utc)
    start = now - pd.Timedelta(hours=24)
    reader = get_heber_reader()
    try:
        return await asyncio.to_thread(reader.read_flow, start_time=start, asof_time=now)
    except Exception as exc:
        logger.warning("flow_heber_read_failed", extra={"event_type": "FLOW_HEBER_READ_FAILED", "error": str(exc)})
        return None


async def _read_heber_darkpool_24h() -> pd.DataFrame | None:
    now = datetime.now(timezone.utc)
    start = now - pd.Timedelta(hours=24)
    reader = get_heber_reader()
    try:
        return await asyncio.to_thread(reader.read_darkpool, start_time=start, asof_time=now)
    except Exception as exc:
        logger.warning(
            "darkpool_heber_read_failed",
            extra={"event_type": "DARKPOOL_HEBER_READ_FAILED", "error": str(exc)},
        )
        return None


async def _get_flow_summary_from_heber() -> Dict | None:
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
    minutes_ago = (datetime.now(timezone.utc) - latest).total_seconds() / 60.0
    return minutes_ago > stale_minutes


async def _get_darkpool_summary_from_heber() -> Dict | None:
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
    minutes_ago = (datetime.now(timezone.utc) - latest).total_seconds() / 60.0
    return minutes_ago > stale_minutes


async def _read_heber_bars_24h(symbols: List[str] | None = None, lookback_hours: int = 24) -> pd.DataFrame | None:
    now = datetime.now(timezone.utc)
    start = now - pd.Timedelta(hours=lookback_hours)
    reader = get_heber_reader()
    try:
        return await asyncio.to_thread(
            reader.read_bars,
            symbols=symbols or [],
            asof_time=now,
            start_time=start,
        )
    except Exception as exc:
        logger.warning("bars_heber_read_failed", extra={"event_type": "BARS_HEBER_READ_FAILED", "error": str(exc)})
        return None


def _coerce_bar_time_series(df: pd.DataFrame) -> pd.Series:
    time_col = _first_existing_column(df, ["ts_event", "bar_start_ts", "bar_start_ts_utc", "ts_utc"])
    if time_col is None:
        return pd.Series(index=df.index, dtype="datetime64[ns, UTC]")
    return pd.to_datetime(df[time_col], utc=True, errors="coerce")


async def _get_bars_summary_from_heber() -> Dict | None:
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


async def _check_zero_valued_bars_from_heber(lookback_hours: int) -> List[Dict] | None:
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


async def _check_data_staleness_from_heber(stale_minutes: int) -> List[Dict] | None:
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
    now = datetime.now(timezone.utc)
    stale_rows: List[Dict] = []
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


async def _check_bar_gaps_from_heber(ticker: str, gap_minutes: int) -> List[Dict] | None:
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
    bars["hour_utc"] = bars["bar_ts"].dt.hour
    bars = bars[(bars["hour_utc"] >= MARKET_OPEN_HOUR) & (bars["hour_utc"] < MARKET_CLOSE_HOUR)]
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
