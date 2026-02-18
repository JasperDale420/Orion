"""
Flow Enrichment Module.

Enriches flow data with all features required for ML scoring.
Used by both the price target labeler (historical) and ML scorer (real-time).
"""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from orion.data.heber_features import (
    get_darkpool_metrics as get_labeler_darkpool_metrics,
    get_earnings_proximity as get_labeler_earnings_proximity,
    get_flow_aggression as get_labeler_flow_aggression,
    get_flow_greeks as get_labeler_flow_greeks,
    get_gex_at_entry as get_labeler_gex_at_entry,
    get_gex_rolling_averages as get_labeler_gex_rolling_averages,
    get_iv_rank_at_entry as get_labeler_iv_rank_at_entry,
    get_market_tide_before_entry as get_labeler_market_tide_before_entry,
    get_max_pain_distance as get_labeler_max_pain_distance,
    get_p2_features as get_labeler_p2_features,
    get_regime_at_entry as get_labeler_regime_at_entry,
    get_rvol_metrics as get_labeler_rvol_metrics,
    get_sector_correlation_features as get_labeler_sector_correlation_features,
    get_window_features_at_entry as get_labeler_window_features_at_entry,
)
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.flow_enricher")

_FLOW_ENRICHER_FALLBACK_COUNTS: Dict[str, int] = defaultdict(int)


def _record_enricher_fallback(feature_name: str, error: Optional[Exception] = None, **context: Any) -> None:
    _FLOW_ENRICHER_FALLBACK_COUNTS[feature_name] += 1
    payload: Dict[str, Any] = {
        "feature": feature_name,
        "fallback_count": _FLOW_ENRICHER_FALLBACK_COUNTS[feature_name],
    }
    if error is not None:
        payload["error"] = str(error)
    payload.update(context)
    logger.warning("Flow enricher fallback applied", extra={"event_type": "FLOW_ENRICHER_FALLBACK", **payload})


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
    expiry: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Enrich a flow with all features required for ML scoring.

    This function attempts to mirror Heber's feature extraction logic.
    Legacy features (Darkpool, GEX, Market Tide) have been removed.

    Args:
        ticker: Underlying ticker symbol
        entry_ts: Timestamp of the flow
        put_call: 'C' or 'P'
        strike: Strike price
        underlying_price: Underlying price at entry
        dte: Days to expiry (if None, calculated from expiry)
        premium_usd: Premium in USD
        event_id: UW flow event ID (for Greeks lookup)
        option_chain: OCC option symbol
        aggressor: 'ASK' or 'BID'
        is_sweep: Whether flow is a sweep
        expiry: Expiry date (YYYY-MM-DD string or date object)

    Returns:
        Dict with all enriched features matching pattern_miner.FEATURE_COLUMNS
    """
    # Calculate DTE from expiry if not provided
    if dte is None and expiry is not None:
        try:
            from dateutil.parser import parse as parse_date

            if isinstance(expiry, str):
                expiry_date = parse_date(expiry).date()
            elif hasattr(expiry, "date"):
                expiry_date = expiry.date() if callable(expiry.date) else expiry
            else:
                expiry_date = expiry

            # Use entry_ts date for DTE calculation
            entry_date = entry_ts.date() if hasattr(entry_ts, "date") else entry_ts
            dte = (expiry_date - entry_date).days
            if dte < 0:
                dte = 0  # Expired options
        except Exception as e:
            logger.debug(f"Failed to calculate DTE from expiry {expiry}: {e}")
            dte = None

    # Helper for empty greeks/metrics
    async def _empty_greeks() -> Dict[str, Any]:
        return {}

    # Parallel enrichment queries only for supported features
    tasks = [
        _get_iv_rank(ticker, entry_ts),
        _get_flow_greeks(event_id, ticker=ticker, entry_ts=entry_ts, option_chain=option_chain)
        if event_id
        else _empty_greeks(),
        _get_market_context(ticker, entry_ts, dte=dte, option_chain=option_chain, expiry=expiry),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    task_names = [
        "iv_rank",
        "flow_greeks",
        "market_context",
    ]
    for task_name, result in zip(task_names, results, strict=True):
        if isinstance(result, Exception):
            _record_enricher_fallback(task_name, result, ticker=ticker, event_id=event_id)

    # Unpack results
    iv_rank = results[0] if not isinstance(results[0], Exception) else None
    greeks = results[1] if not isinstance(results[1], Exception) else {}
    market_context = results[2] if not isinstance(results[2], Exception) else {}

    # Compute moneyness
    moneyness = None
    log_moneyness = None
    if strike and underlying_price and underlying_price > 0:
        moneyness = strike / underlying_price
        import math

        try:
            log_moneyness = math.log(moneyness)
        except ValueError:
            pass

    # Compute volume/OI ratio
    vol = greeks.get("volume")
    oi = greeks.get("open_interest")
    vol_oi_ratio = None
    if vol is not None and oi is not None and oi > 0:
        vol_oi_ratio = vol / oi

    # Timing
    entry_hour = entry_ts.hour
    mins_to_close = _get_minutes_to_close(entry_ts)

    # Calculate minutes since open (9:30 AM ET)
    # Using naive calculation based on hour/minute (assuming ET or approximating)
    mins_from_midnight = entry_hour * 60 + entry_ts.minute
    market_open_mins = 9 * 60 + 30
    minutes_since_open = max(0, mins_from_midnight - market_open_mins)

    enriched = {
        # Entry context
        "premium_usd": premium_usd or 0,
        "dte": dte or 0,
        "moneyness": moneyness,
        "log_moneyness": log_moneyness,
        # Greeks
        "delta_at_entry": greeks.get("delta"),
        "gamma_at_entry": greeks.get("gamma"),
        "theta_at_entry": greeks.get("theta"),
        "vega_at_entry": greeks.get("vega"),
        "iv_at_entry": greeks.get("iv"),
        # Vol/OI
        "volume_at_entry": vol,
        "open_interest_at_entry": oi,
        "volume_oi_ratio": vol_oi_ratio,
        # Market context
        "underlying_1d_return": market_context.get("rvol_1d_return"),  # mapped from market context
        "underlying_5d_return": market_context.get("rvol_5d_return"),
        "underlying_30d_return": market_context.get("rvol_30d_return"),
        "realized_vol_20d": market_context.get("rvol_daily"),
        "iv_rank_at_entry": iv_rank,
        # Timing
        "entry_hour": entry_hour,
        "minutes_to_close": mins_to_close,
        "minutes_since_open": minutes_since_open,
        # Categoricals / Tags
        "put_call": put_call,
        "aggressor": aggressor,
        "is_sweep": is_sweep,
        "is_block": False,  # TODO: Detect if block
        "entry_day_of_week": entry_ts.weekday(),
        # Tags (defaults)
        "is_bullish": 0,
        "is_bearish": 0,
        "is_unusual": 0,
    }

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
    """Get GEX/VEX at entry time, plus rolling averages from shared labeler helper."""
    try:
        gex_snapshot = await get_labeler_gex_at_entry(ticker, entry_ts)
    except Exception as e:
        logger.debug(f"GEX snapshot lookup failed: {e}")
        return {}

    if not isinstance(gex_snapshot, dict):
        return {}

    gex = gex_snapshot.get("gex")
    vex = gex_snapshot.get("vex")
    if gex is None and vex is None:
        return {}

    try:
        rolling_avg = await get_labeler_gex_rolling_averages(ticker, entry_ts)
    except Exception as e:
        logger.debug(f"GEX rolling-average helper failed: {e}")
        rolling_avg = {"gex_rolling_avg": None, "vex_rolling_avg": None}

    return {
        "gex": gex,
        "vex": vex,
        "gex_rolling_avg": rolling_avg.get("gex_rolling_avg"),
        "vex_rolling_avg": rolling_avg.get("vex_rolling_avg"),
    }


async def _get_market_tide(entry_ts: datetime, minutes: int = 30) -> Dict[str, Any]:
    """Get market tide in window before entry."""
    try:
        result = await get_labeler_market_tide_before_entry(entry_ts, minutes=minutes)
        if isinstance(result, dict):
            return {
                "net_premium": result.get("net_premium"),
                "direction": result.get("direction"),
            }
        return {}
    except Exception as e:
        logger.debug(f"Market tide lookup failed: {e}")
        return {}


async def _get_max_pain_distance(ticker: str, entry_ts: datetime, dte: Optional[int] = None) -> Optional[float]:
    """Get distance to max pain."""
    if dte is None:
        return None

    expiry_date = datetime.combine(entry_ts.date() + timedelta(days=dte), datetime.min.time())
    if entry_ts.tzinfo is not None:
        expiry_date = expiry_date.replace(tzinfo=entry_ts.tzinfo)

    try:
        return await get_labeler_max_pain_distance(ticker, expiry_date, entry_ts)
    except Exception as e:
        logger.debug(f"Max pain lookup failed: {e}")
        return None


async def _get_iv_rank(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Get IV rank at entry."""
    try:
        return await get_labeler_iv_rank_at_entry(ticker, entry_ts)
    except Exception as e:
        logger.debug(f"IV rank lookup failed: {e}")
        return None


async def _get_darkpool_volumes(ticker: str, entry_ts: datetime) -> Dict[str, Optional[float]]:
    """Get darkpool volumes for multiple windows."""
    try:
        darkpool_metrics = await get_labeler_darkpool_metrics(ticker, entry_ts)
        if not isinstance(darkpool_metrics, dict):
            return {}
        return {
            "30m": darkpool_metrics.get("darkpool_30m"),
            "1h": darkpool_metrics.get("darkpool_1h"),
            "4h": darkpool_metrics.get("darkpool_4h"),
            "1d": darkpool_metrics.get("darkpool_1d"),
        }
    except Exception as e:
        _record_enricher_fallback("darkpool_volume_window", e, ticker=ticker)
        return {"30m": None, "1h": None, "4h": None, "1d": None}


async def _get_regime(entry_ts: datetime) -> Dict[str, str]:
    """Get regime snapshot at entry."""
    try:
        regime = await get_labeler_regime_at_entry(entry_ts)
        if isinstance(regime, dict):
            return {
                "trend_regime": regime.get("trend_regime"),
                "vol_regime": regime.get("vol_regime"),
                "risk_regime": regime.get("risk_regime"),
                "session_regime": regime.get("session_regime"),
                "vix_regime": regime.get("vix_regime"),
            }
        return {}
    except Exception as e:
        logger.debug(f"Regime lookup failed: {e}")
        return {}


async def _get_flow_greeks(
    event_id: str,
    *,
    ticker: Optional[str] = None,
    entry_ts: Optional[datetime] = None,
    option_chain: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """Get Greeks from flow event."""
    try:
        flow_greeks = await get_labeler_flow_greeks(event_id)
        if not isinstance(flow_greeks, dict) or not flow_greeks:
            return {}

        result: Dict[str, Optional[float]] = {
            "delta": flow_greeks.get("delta"),
            "gamma": flow_greeks.get("gamma"),
            "theta": flow_greeks.get("theta"),
            "vega": flow_greeks.get("vega"),
            "iv": flow_greeks.get("iv"),
            "volume": flow_greeks.get("volume"),
            "open_interest": flow_greeks.get("open_interest"),
            "iv_vs_hv_ratio": None,
            "oi_change_1d": None,
            "oi_change_pct": None,
        }

        if ticker and entry_ts and option_chain:
            try:
                p2 = await get_labeler_p2_features(ticker, option_chain, entry_ts)
                if isinstance(p2, dict):
                    result["iv_vs_hv_ratio"] = p2.get("iv_vs_hv_ratio")
                    result["oi_change_1d"] = p2.get("oi_change_1d")
                    result["oi_change_pct"] = p2.get("oi_change_pct")
            except Exception as e:
                _record_enricher_fallback("flow_enricher_p2_lookup", e, ticker=ticker, event_id=event_id)

        return result
    except Exception as e:
        logger.debug(f"Greeks lookup failed: {e}")
        return {}


async def _get_vix(entry_ts: datetime) -> Optional[float]:
    """Get VIX at entry."""
    try:
        regime = await get_labeler_regime_at_entry(entry_ts)
        if isinstance(regime, dict):
            return regime.get("vix_at_entry")
        return None
    except Exception as e:
        logger.debug(f"VIX lookup failed: {e}")
        return None


async def _get_flow_metrics(ticker: str, entry_ts: datetime, dte: Optional[int] = None) -> Dict[str, Any]:
    """Get additional flow metrics - sector, earnings, flow ratios."""
    # Sector mapping (same as labeler)
    TICKER_SECTORS = {
        "AAPL": "Technology",
        "MSFT": "Technology",
        "GOOGL": "Technology",
        "GOOG": "Technology",
        "META": "Technology",
        "AMZN": "Consumer Discretionary",
        "TSLA": "Consumer Discretionary",
        "NVDA": "Technology",
        "AMD": "Technology",
        "INTC": "Technology",
        "NFLX": "Communication Services",
        "CRM": "Technology",
        "SPY": "ETF",
        "QQQ": "ETF",
        "IWM": "ETF",
        "DIA": "ETF",
        "XLF": "ETF",
        "XLE": "ETF",
        "XLK": "ETF",
        "XLV": "ETF",
        "JPM": "Financial Services",
        "BAC": "Financial Services",
        "GS": "Financial Services",
        "V": "Financial Services",
        "MA": "Financial Services",
        "PYPL": "Financial Services",
        "UNH": "Healthcare",
        "JNJ": "Healthcare",
        "PFE": "Healthcare",
        "LLY": "Healthcare",
        "XOM": "Energy",
        "CVX": "Energy",
        "COP": "Energy",
        "OXY": "Energy",
        "BA": "Industrials",
        "CAT": "Industrials",
        "RTX": "Industrials",
        "LMT": "Industrials",
        "COST": "Consumer Staples",
        "WMT": "Consumer Staples",
        "KO": "Consumer Staples",
        "DIS": "Communication Services",
        "T": "Communication Services",
        "VZ": "Communication Services",
    }

    sector = TICKER_SECTORS.get(ticker)
    industry = sector  # Use sector as industry fallback

    # Query basic flow metrics from recent flows
    result = {
        "sector": sector,
        "industry": industry,
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
        "sector_flow_direction": None,
    }

    try:
        flow_aggression = await get_labeler_flow_aggression(ticker, entry_ts)
        if isinstance(flow_aggression, dict):
            result["ask_side_ratio"] = flow_aggression.get("ask_side_ratio")
            result["sweep_ratio_1h"] = flow_aggression.get("sweep_ratio_1h")
            result["same_ticker_premium_1h"] = flow_aggression.get("same_ticker_premium_1h")
    except Exception as e:
        logger.debug(f"Flow aggression lookup failed: {e}")

    try:
        sector_corr = await get_labeler_sector_correlation_features(ticker, entry_ts)
        if isinstance(sector_corr, dict):
            result["sector_net_premium_1h"] = sector_corr.get("sector_net_premium_1h")
            result["sector_flow_direction"] = sector_corr.get("sector_flow_direction")
            result["spy_correlation_5d"] = sector_corr.get("spy_correlation_5d")
            result["spy_return_1h"] = sector_corr.get("spy_return_1h")
    except Exception as e:
        logger.debug(f"Sector-correlation lookup failed: {e}")

    try:
        earnings = await get_labeler_earnings_proximity(ticker, entry_ts)
        if isinstance(earnings, dict):
            days_to_earnings = earnings.get("days_to_earnings")
            is_post_earnings = bool(earnings.get("is_post_earnings", False))
            result["days_to_earnings"] = days_to_earnings
            result["is_post_earnings"] = is_post_earnings

            if dte is not None and isinstance(days_to_earnings, (int, float)) and 0 <= days_to_earnings <= dte:
                result["earnings_in_dte_window"] = True
    except Exception as e:
        logger.debug(f"Earnings lookup failed: {e}")

    return result


def _coerce_expiry_datetime(expiry: Any, entry_ts: datetime, dte: Optional[int]) -> Optional[datetime]:
    """Normalize expiry input to datetime for delegation helpers."""
    expiry_dt: Optional[datetime] = None
    if isinstance(expiry, datetime):
        expiry_dt = expiry
    elif hasattr(expiry, "year") and hasattr(expiry, "month") and hasattr(expiry, "day"):
        expiry_dt = datetime(expiry.year, expiry.month, expiry.day)
    elif isinstance(expiry, str):
        try:
            parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            expiry_dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=entry_ts.tzinfo)
        except ValueError:
            expiry_dt = None

    if expiry_dt is None and dte is not None:
        expiry_dt = entry_ts + timedelta(days=dte)

    if expiry_dt and expiry_dt.tzinfo is None and entry_ts.tzinfo is not None:
        expiry_dt = expiry_dt.replace(tzinfo=entry_ts.tzinfo)

    return expiry_dt


async def _get_market_context(
    ticker: str,
    entry_ts: datetime,
    dte: Optional[int] = None,
    option_chain: Optional[str] = None,
    expiry: Optional[Any] = None,
) -> Dict[str, Any]:
    """Get market context features via labeler helpers for parity with training."""
    result = {
        "rvol_daily": None,
        "rvol_1d_return": 0.0,  # Placeholder, Heber provides this
        "rvol_5d_return": 0.0,
        "rvol_30d_return": 0.0,
    }

    try:
        rvol = await get_labeler_rvol_metrics(ticker, entry_ts)
        if isinstance(rvol, dict):
            result["rvol_daily"] = rvol.get("rvol_daily")
            # In a real implementation, we'd fetch returns here too
            # For now, we rely on Heber's pipeline to provide these
    except Exception as e:
        logger.debug(f"RVOL helper lookup failed: {e}")

    return result


async def _get_window_features(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get aggregated flow features from shared gold-window helper."""
    result: Dict[str, Any] = {}

    try:
        window_data = await get_labeler_window_features_at_entry(ticker, entry_ts)

        # Extract key features from each period
        for period in ["1h", "1d", "1w"]:
            if period in window_data:
                f = window_data[period]
                result[f"call_put_imbalance_{period}"] = f.get("call_put_imbalance")
                result[f"sweep_ratio_{period}"] = f.get("sweep_ratio")
                result[f"flow_count_{period}"] = f.get("flow_count")

                # Only include some features for certain periods to avoid feature bloat
                if period in ["1d", "1w"]:
                    result[f"dp_volume_{period}"] = f.get("dp_volume")
                    result[f"call_put_ratio_{period}"] = f.get("call_put_ratio")
                    result[f"total_premium_{period}"] = f.get("total_premium")

    except Exception as e:
        logger.debug(f"Window features lookup failed: {e}")

    return result
