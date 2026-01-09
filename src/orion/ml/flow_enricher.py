"""
Flow Enrichment Module.

Enriches flow data with all features required for ML scoring.
Used by both the price target labeler (historical) and ML scorer (real-time).
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.flow_enricher")


async def enrich_flow_for_scoring(
    ticker: str,
    entry_ts: datetime,
    put_call: str,
    strike: Optional[float] = None,
    underlying_price: Optional[float] = None,
    dte: Optional[int] = None,
    premium_usd: Optional[float] = None,
    event_id: Optional[str] = None,
    option_chain: Optional[str] = None,
    aggressor: Optional[str] = None,
    is_sweep: bool = False,
) -> Dict[str, Any]:
    """
    Enrich a flow with all features required for ML scoring.

    This function queries the same data sources as the price_target_labeler
    to ensure feature parity between training and inference.

    Args:
        ticker: Underlying ticker symbol
        entry_ts: Timestamp of the flow
        put_call: 'C' or 'P'
        strike: Strike price
        underlying_price: Underlying price at entry
        dte: Days to expiry
        premium_usd: Premium in USD
        event_id: UW flow event ID (for Greeks lookup)
        option_chain: OCC option symbol
        aggressor: 'ASK' or 'BID'
        is_sweep: Whether flow is a sweep

    Returns:
        Dict with all enriched features matching pattern_miner.FEATURE_COLUMNS
    """
    # Start with basic flow data
    enriched = {
        "ticker": ticker,
        "premium_usd": premium_usd or 0,
        "dte": dte or 0,
        "put_call": put_call,
        "aggressor": aggressor,
        "is_sweep": is_sweep,
    }

    # Helper for empty greeks
    async def _empty_greeks() -> Dict[str, Any]:
        return {}

    # Parallel enrichment queries for speed
    tasks = [
        _get_gex_at_entry(ticker, entry_ts),
        _get_market_tide(entry_ts),
        _get_max_pain_distance(ticker, entry_ts, dte),
        _get_iv_rank(ticker, entry_ts),
        _get_darkpool_volumes(ticker, entry_ts),
        _get_regime(entry_ts),
        _get_flow_greeks(event_id) if event_id else _empty_greeks(),
        _get_vix(entry_ts),
        _get_flow_metrics(ticker, entry_ts),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Unpack results
    gex_data = results[0] if not isinstance(results[0], Exception) else {}
    tide_data = results[1] if not isinstance(results[1], Exception) else {}
    max_pain_pct = results[2] if not isinstance(results[2], Exception) else None
    iv_rank = results[3] if not isinstance(results[3], Exception) else None
    darkpool = results[4] if not isinstance(results[4], Exception) else {}
    regime = results[5] if not isinstance(results[5], Exception) else {}
    greeks = results[6] if not isinstance(results[6], Exception) else {}
    vix = results[7] if not isinstance(results[7], Exception) else None
    flow_metrics = results[8] if not isinstance(results[8], Exception) else {}

    # Merge all enriched features
    enriched.update(
        {
            # GEX/VEX
            "gex_at_entry": gex_data.get("gex"),
            "vex_at_entry": gex_data.get("vex"),
            # Market Tide
            "market_tide_30m": tide_data.get("net_premium"),
            "market_tide_direction": tide_data.get("direction"),
            # Max Pain
            "max_pain_distance_pct": max_pain_pct,
            # IV Rank
            "iv_rank_at_entry": iv_rank,
            # VIX
            "vix_at_entry": vix,
            # Darkpool
            "darkpool_volume_1h": darkpool.get("1h"),
            "darkpool_30m": darkpool.get("30m"),
            "darkpool_4h": darkpool.get("4h"),
            "darkpool_1d": darkpool.get("1d"),
            # Greeks
            "delta_at_entry": greeks.get("delta"),
            "gamma_at_entry": greeks.get("gamma"),
            "theta_at_entry": greeks.get("theta"),
            "vega_at_entry": greeks.get("vega"),
            "iv_at_entry": greeks.get("iv"),
            "iv_vs_hv_ratio": greeks.get("iv_vs_hv_ratio"),
            # Volume/OI
            "volume_at_entry": greeks.get("volume"),
            "open_interest_at_entry": greeks.get("open_interest"),
            # Flow metrics
            "rvol_1h": flow_metrics.get("rvol_1h"),
            "rvol_daily": flow_metrics.get("rvol_daily"),
            "oi_change_1d": flow_metrics.get("oi_change_1d"),
            "oi_change_pct": flow_metrics.get("oi_change_pct"),
            "ask_side_ratio": flow_metrics.get("ask_side_ratio"),
            "sweep_ratio_1h": flow_metrics.get("sweep_ratio_1h"),
            "same_ticker_premium_1h": flow_metrics.get("same_ticker_premium_1h"),
            "sector_net_premium_1h": flow_metrics.get("sector_net_premium_1h"),
            # Market context
            "spy_correlation_5d": flow_metrics.get("spy_correlation_5d"),
            "spy_return_1h": flow_metrics.get("spy_return_1h"),
            "vwap_distance_pct": flow_metrics.get("vwap_distance_pct"),
            "high_52w_distance_pct": flow_metrics.get("high_52w_distance_pct"),
            "overnight_gap_pct": flow_metrics.get("overnight_gap_pct"),
            # Timing
            "entry_hour": entry_ts.hour,
            "minutes_to_close": _get_minutes_to_close(entry_ts),
            "days_to_earnings": flow_metrics.get("days_to_earnings"),
            # Categorical
            "is_spread_leg": False,  # Not detectable from single flow
            "is_post_earnings": flow_metrics.get("is_post_earnings", False),
            "earnings_in_dte_window": flow_metrics.get("earnings_in_dte_window", False),
            "entry_session": _get_session(entry_ts),
            "entry_day_of_week": entry_ts.weekday(),
            "sector": flow_metrics.get("sector"),
            "industry": flow_metrics.get("industry"),
            # Regimes
            "vol_regime_at_entry": regime.get("vol_regime"),
            "risk_regime_at_entry": regime.get("risk_regime"),
            "session_regime_at_entry": regime.get("session_regime"),
            "trend_regime_at_entry": regime.get("trend_regime"),
            "vix_regime_at_entry": regime.get("vix_regime"),
            "sector_flow_direction": flow_metrics.get("sector_flow_direction"),
        }
    )

    return enriched


def _get_minutes_to_close(ts: datetime) -> int:
    """Get minutes until market close (16:00 ET = 21:00 UTC)."""
    close_hour = 21  # 4 PM ET in UTC
    if ts.hour >= close_hour:
        return 0
    return (close_hour - ts.hour) * 60 - ts.minute


def _get_session(ts: datetime) -> str:
    """Classify trading session."""
    hour = ts.hour
    if hour < 15:  # Before 10 AM ET
        return "OPEN"
    elif hour >= 19:  # After 2 PM ET
        return "CLOSE"
    return "MID"


async def _get_gex_at_entry(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get GEX/VEX at entry time."""
    from sqlalchemy import text

    async def query(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT gex_oi, vex_oi
            FROM silver_greek_exposure
            WHERE ticker = :ticker AND ts_utc <= :entry_ts
            ORDER BY ts_utc DESC LIMIT 1
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts})
        row = result.fetchone()
        return {"gex": row[0], "vex": row[1]} if row else {}

    try:
        return await db_query(query)
    except Exception as e:
        logger.debug(f"GEX lookup failed: {e}")
        return {}


async def _get_market_tide(entry_ts: datetime, minutes: int = 30) -> Dict[str, Any]:
    """Get market tide in window before entry."""
    from sqlalchemy import text

    start_ts = entry_ts - timedelta(minutes=minutes)

    async def query(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT COALESCE(SUM(net_call_premium), 0), COALESCE(SUM(net_put_premium), 0)
            FROM silver_market_tide
            WHERE ts_utc > :start_ts AND ts_utc <= :entry_ts
        """
        )
        result = await session.execute(stmt, {"start_ts": start_ts, "entry_ts": entry_ts})
        row = result.fetchone()
        if row:
            net = float(row[0] or 0) + float(row[1] or 0)
            direction = "BULLISH" if net > 0 else "BEARISH" if net < 0 else "NEUTRAL"
            return {"net_premium": net, "direction": direction}
        return {}

    try:
        return await db_query(query)
    except Exception as e:
        logger.debug(f"Market tide lookup failed: {e}")
        return {}


async def _get_max_pain_distance(ticker: str, entry_ts: datetime, dte: Optional[int] = None) -> Optional[float]:
    """Get distance to max pain."""
    from sqlalchemy import text

    if dte is None:
        return None

    expiry = entry_ts.date() + timedelta(days=dte)

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            SELECT distance_to_max_pain_pct FROM silver_max_pain
            WHERE ticker = :ticker AND expiry = :expiry AND date <= :entry_date
            ORDER BY date DESC LIMIT 1
        """
        )
        result = await session.execute(
            stmt,
            {
                "ticker": ticker,
                "expiry": expiry,
                "entry_date": entry_ts.date(),
            },
        )
        row = result.fetchone()
        return row[0] if row else None

    try:
        return await db_query(query)
    except Exception as e:
        logger.debug(f"Max pain lookup failed: {e}")
        return None


async def _get_iv_rank(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Get IV rank at entry."""
    from sqlalchemy import text

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            WITH iv_history AS (
                SELECT iv FROM silver_uw_flow
                WHERE ticker = :ticker
                AND flow_ts_utc BETWEEN :start_ts AND :entry_ts
                AND iv IS NOT NULL AND iv > 0
            )
            SELECT
                (SELECT iv FROM silver_uw_flow
                 WHERE ticker = :ticker AND flow_ts_utc <= :entry_ts
                 AND iv IS NOT NULL AND iv > 0
                 ORDER BY flow_ts_utc DESC LIMIT 1) as current_iv,
                MIN(iv) as min_iv,
                MAX(iv) as max_iv
            FROM iv_history
        """
        )
        start_ts = entry_ts - timedelta(days=30)
        result = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts, "start_ts": start_ts})
        row = result.fetchone()
        if row and row[0] and row[1] is not None and row[2] is not None:
            current_iv, min_iv, max_iv = row[0], row[1], row[2]
            if max_iv > min_iv:
                return min(100.0, max(0.0, (current_iv - min_iv) / (max_iv - min_iv) * 100))
            return 50.0
        return None

    try:
        return await db_query(query)
    except Exception as e:
        logger.debug(f"IV rank lookup failed: {e}")
        return None


async def _get_darkpool_volumes(ticker: str, entry_ts: datetime) -> Dict[str, Optional[float]]:
    """Get darkpool volumes for multiple windows."""
    from sqlalchemy import text

    volumes = {}
    windows = [("30m", 30), ("1h", 60), ("4h", 240), ("1d", 1440)]

    for name, minutes in windows:
        start_ts = entry_ts - timedelta(minutes=minutes)

        async def query(session: Any, st: datetime = start_ts) -> Optional[float]:
            stmt = text(
                """
                SELECT COALESCE(SUM(volume), 0)
                FROM silver_darkpool
                WHERE ticker = :ticker AND ts_utc > :start_ts AND ts_utc <= :entry_ts
            """
            )
            result = await session.execute(stmt, {"ticker": ticker, "start_ts": st, "entry_ts": entry_ts})
            row = result.fetchone()
            return row[0] if row and row[0] else None

        try:
            volumes[name] = await db_query(query)
        except Exception:
            volumes[name] = None

    return volumes


async def _get_regime(entry_ts: datetime) -> Dict[str, str]:
    """Get regime snapshot at entry."""
    from sqlalchemy import text

    from orion.analysis.regime import MultiAxisRegimeDetector

    detector = MultiAxisRegimeDetector()

    # Get VIX data
    async def query_vix(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT vix, vix_1d_change, vix_regime
            FROM silver_vix_data
            WHERE ts_utc <= :entry_ts
            ORDER BY ts_utc DESC LIMIT 1
        """
        )
        result = await session.execute(stmt, {"entry_ts": entry_ts})
        row = result.fetchone()
        if row and row[0]:
            return {"vix": row[0], "vix_1d_change": row[1], "vix_regime": row[2]}
        return {}

    try:
        vix_data = await db_query(query_vix)
        snapshot = detector.detect(
            ts=entry_ts,
            vix=vix_data.get("vix"),
            vix_1d_change=vix_data.get("vix_1d_change"),
        )
        return {
            "trend_regime": snapshot.trend.value,
            "vol_regime": snapshot.vol.value,
            "risk_regime": snapshot.risk.value,
            "session_regime": snapshot.session.value,
            "vix_regime": snapshot.vix_regime.value,
        }
    except Exception as e:
        logger.debug(f"Regime lookup failed: {e}")
        return {}


async def _get_flow_greeks(event_id: str) -> Dict[str, Optional[float]]:
    """Get Greeks from flow event."""
    from sqlalchemy import text

    async def query(session: Any) -> Dict[str, Optional[float]]:
        stmt = text(
            """
            SELECT
                delta_alpaca, gamma_alpaca, theta_alpaca, vega_alpaca,
                iv, volume_contract, open_interest
            FROM silver_uw_flow
            WHERE event_id = :event_id
        """
        )
        result = await session.execute(stmt, {"event_id": event_id})
        row = result.fetchone()
        if row:
            return {
                "delta": row[0],
                "gamma": row[1],
                "theta": row[2],
                "vega": row[3],
                "iv": row[4],
                "volume": row[5],
                "open_interest": row[6],
            }
        return {}

    try:
        return await db_query(query)
    except Exception as e:
        logger.debug(f"Greeks lookup failed: {e}")
        return {}


async def _get_vix(entry_ts: datetime) -> Optional[float]:
    """Get VIX at entry."""
    from sqlalchemy import text

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            SELECT vix FROM silver_vix_data
            WHERE ts_utc <= :entry_ts
            ORDER BY ts_utc DESC LIMIT 1
        """
        )
        result = await session.execute(stmt, {"entry_ts": entry_ts})
        row = result.fetchone()
        return row[0] if row else None

    try:
        return await db_query(query)
    except Exception as e:
        logger.debug(f"VIX lookup failed: {e}")
        return None


async def _get_flow_metrics(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get additional flow metrics - sector, earnings, flow ratios."""
    # These are more complex to compute in real-time
    # Return defaults for now - can be enhanced
    return {
        "rvol_1h": None,
        "rvol_daily": None,
        "oi_change_1d": None,
        "oi_change_pct": None,
        "ask_side_ratio": None,
        "sweep_ratio_1h": None,
        "same_ticker_premium_1h": None,
        "sector_net_premium_1h": None,
        "spy_correlation_5d": None,
        "spy_return_1h": None,
        "vwap_distance_pct": None,
        "high_52w_distance_pct": None,
        "overnight_gap_pct": None,
        "days_to_earnings": None,
        "is_post_earnings": False,
        "earnings_in_dte_window": False,
        "sector": None,
        "industry": None,
        "sector_flow_direction": None,
    }
