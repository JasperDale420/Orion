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
        _get_market_context(ticker, entry_ts),  # New: rvol, overnight_gap, 52w_high
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
    market_context = results[9] if not isinstance(results[9], Exception) else {}

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
            "rvol_1h": market_context.get("rvol_1h"),
            "rvol_daily": market_context.get("rvol_daily"),
            "oi_change_1d": greeks.get("oi_change_1d"),
            "oi_change_pct": greeks.get("oi_change_pct"),
            "ask_side_ratio": flow_metrics.get("ask_side_ratio"),
            "sweep_ratio_1h": flow_metrics.get("sweep_ratio_1h"),
            "same_ticker_premium_1h": flow_metrics.get("same_ticker_premium_1h"),
            "sector_net_premium_1h": flow_metrics.get("sector_net_premium_1h"),
            # Market context
            "spy_correlation_5d": flow_metrics.get("spy_correlation_5d"),
            "spy_return_1h": flow_metrics.get("spy_return_1h"),
            "vwap_distance_pct": market_context.get("vwap_distance_pct"),
            "high_52w_distance_pct": market_context.get("high_52w_distance_pct"),
            "overnight_gap_pct": market_context.get("overnight_gap_pct"),
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
                SELECT COALESCE(SUM(size_shares), 0)
                FROM silver_uw_darkpool
                WHERE ticker = :ticker AND dark_ts_utc > :start_ts AND dark_ts_utc <= :entry_ts
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
                iv, volume_contract, open_interest, ticker, flow_ts_utc
            FROM silver_uw_flow
            WHERE event_id = :event_id
        """
        )
        result = await session.execute(stmt, {"event_id": event_id})
        row = result.fetchone()
        if row:
            iv = row[4]
            ticker = row[7]
            flow_ts = row[8]

            # Calculate iv_vs_hv_ratio if we have IV
            iv_vs_hv = None
            if iv and ticker and flow_ts:
                try:
                    # Get historical volatility from bars (20-day realized)
                    hv_stmt = text(
                        """
                        WITH daily_returns AS (
                            SELECT
                                DATE(bar_start_ts_utc) as day,
                                LN(MAX(close) / LAG(MAX(close)) OVER (ORDER BY DATE(bar_start_ts_utc))) as log_return
                            FROM silver_alpaca_bars
                            WHERE ticker = :ticker
                            AND bar_start_ts_utc > :start_ts AND bar_start_ts_utc <= :end_ts
                            GROUP BY DATE(bar_start_ts_utc)
                            ORDER BY day
                        )
                        SELECT STDDEV(log_return) * SQRT(252) as hv_20d
                        FROM daily_returns
                        WHERE log_return IS NOT NULL
                    """
                    )
                    start_ts = flow_ts - timedelta(days=30)
                    hv_result = await session.execute(
                        hv_stmt, {"ticker": ticker, "start_ts": start_ts, "end_ts": flow_ts}
                    )
                    hv_row = hv_result.fetchone()
                    if hv_row and hv_row[0] and hv_row[0] > 0:
                        iv_vs_hv = iv / hv_row[0]
                except Exception:
                    pass  # Silent fail for HV calculation

            # Calculate OI change from prior day
            oi_change_1d = None
            oi_change_pct = None
            current_oi = row[6]
            if current_oi and ticker and flow_ts:
                try:
                    oi_stmt = text(
                        """
                        SELECT open_interest
                        FROM silver_uw_flow
                        WHERE ticker = :ticker
                        AND option_chain = (
                            SELECT option_chain FROM silver_uw_flow WHERE event_id = :event_id
                        )
                        AND DATE(flow_ts_utc) < DATE(:flow_ts)
                        ORDER BY flow_ts_utc DESC
                        LIMIT 1
                    """
                    )
                    oi_result = await session.execute(
                        oi_stmt, {"ticker": ticker, "flow_ts": flow_ts, "event_id": event_id}
                    )
                    oi_row = oi_result.fetchone()
                    if oi_row and oi_row[0]:
                        prior_oi = oi_row[0]
                        oi_change_1d = current_oi - prior_oi
                        if prior_oi > 0:
                            oi_change_pct = (current_oi - prior_oi) / prior_oi * 100
                except Exception:
                    pass  # Silent fail for OI change

            return {
                "delta": row[0],
                "gamma": row[1],
                "theta": row[2],
                "vega": row[3],
                "iv": iv,
                "volume": row[5],
                "open_interest": row[6],
                "iv_vs_hv_ratio": iv_vs_hv,
                "oi_change_1d": oi_change_1d,
                "oi_change_pct": oi_change_pct,
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
    from sqlalchemy import text

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

    # Get flow ratios from recent 1h flow data
    try:
        start_ts = entry_ts - timedelta(hours=1)

        async def query_flow_ratios(session: Any) -> Dict[str, Any]:
            stmt = text(
                """
                SELECT
                    COUNT(CASE WHEN aggressor = 'ASK' THEN 1 END)::float /
                        NULLIF(COUNT(*), 0) as ask_ratio,
                    COUNT(CASE WHEN is_sweep::text = 'true' OR is_sweep::text = 'True' THEN 1 END)::float /
                        NULLIF(COUNT(*), 0) as sweep_ratio,
                    COALESCE(SUM(premium_usd), 0) as total_premium
                FROM silver_uw_flow
                WHERE ticker = :ticker
                AND flow_ts_utc > :start_ts AND flow_ts_utc <= :entry_ts
            """
            )
            res = await session.execute(stmt, {"ticker": ticker, "start_ts": start_ts, "entry_ts": entry_ts})
            row = res.fetchone()
            if row:
                return {
                    "ask_side_ratio": row[0],
                    "sweep_ratio_1h": row[1],
                    "same_ticker_premium_1h": row[2],
                }
            return {}

        flow_ratios = await db_query(query_flow_ratios)
        result.update(flow_ratios)
    except Exception as e:
        logger.debug(f"Flow ratios lookup failed: {e}")

    # Get SPY return for market context
    try:
        start_ts = entry_ts - timedelta(hours=1)

        async def query_spy_return(session: Any) -> Optional[float]:
            stmt = text(
                """
                WITH spy_prices AS (
                    SELECT close, bar_start_ts_utc
                    FROM silver_alpaca_bars
                    WHERE ticker = 'SPY'
                    AND bar_start_ts_utc BETWEEN :start_ts AND :entry_ts
                    ORDER BY bar_start_ts_utc
                )
                SELECT
                    (SELECT close FROM spy_prices ORDER BY bar_start_ts_utc DESC LIMIT 1) /
                    NULLIF((SELECT close FROM spy_prices ORDER BY bar_start_ts_utc ASC LIMIT 1), 0) - 1
                    as return_1h
            """
            )
            res = await session.execute(stmt, {"start_ts": start_ts, "entry_ts": entry_ts})
            row = res.fetchone()
            return row[0] if row and row[0] else None

        spy_ret = await db_query(query_spy_return)
        if spy_ret is not None:
            result["spy_return_1h"] = spy_ret
    except Exception as e:
        logger.debug(f"SPY return lookup failed: {e}")

    # Get sector net premium (for sector-level flow direction)
    if sector:
        try:
            start_ts = entry_ts - timedelta(hours=1)
            sector_tickers = [t for t, s in TICKER_SECTORS.items() if s == sector]

            if sector_tickers:

                async def query_sector_premium(session: Any) -> Dict[str, Any]:
                    # Use ANY for array matching
                    stmt = text(
                        """
                        SELECT
                            COALESCE(SUM(CASE WHEN aggressor = 'ASK' THEN premium_usd ELSE 0 END), 0) as call_premium,
                            COALESCE(SUM(CASE WHEN aggressor = 'BID' THEN premium_usd ELSE 0 END), 0) as put_premium
                        FROM silver_uw_flow
                        WHERE ticker = ANY(:tickers)
                        AND flow_ts_utc > :start_ts AND flow_ts_utc <= :entry_ts
                    """
                    )
                    res = await session.execute(
                        stmt, {"tickers": sector_tickers, "start_ts": start_ts, "entry_ts": entry_ts}
                    )
                    row = res.fetchone()
                    if row:
                        call_prem = row[0] or 0
                        put_prem = row[1] or 0
                        net = call_prem - put_prem
                        direction = "BULLISH" if net > 0 else "BEARISH" if net < 0 else "NEUTRAL"
                        return {
                            "sector_net_premium_1h": net,
                            "sector_flow_direction": direction,
                        }
                    return {}

                sector_flow = await db_query(query_sector_premium)
                result.update(sector_flow)
        except Exception as e:
            logger.debug(f"Sector premium lookup failed: {e}")

    return result


async def _get_market_context(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get market context features: rvol, overnight_gap, 52w_high distance."""
    from sqlalchemy import text

    result = {
        "rvol_1h": None,
        "rvol_daily": None,
        "overnight_gap_pct": None,
        "high_52w_distance_pct": None,
        "vwap_distance_pct": None,
    }

    # Get rvol (current volume / average volume)
    try:

        async def query_rvol(session: Any) -> Dict[str, Optional[float]]:
            stmt = text(
                """
                WITH current_vol AS (
                    SELECT COALESCE(SUM(volume), 0) as vol
                    FROM silver_alpaca_bars
                    WHERE ticker = :ticker
                    AND bar_start_ts_utc > :start_1h AND bar_start_ts_utc <= :entry_ts
                ),
                avg_vol AS (
                    SELECT COALESCE(AVG(daily_vol), 1) as avg_daily
                    FROM (
                        SELECT DATE(bar_start_ts_utc) as day, SUM(volume) as daily_vol
                        FROM silver_alpaca_bars
                        WHERE ticker = :ticker
                        AND bar_start_ts_utc > :start_20d AND bar_start_ts_utc <= :entry_ts
                        GROUP BY DATE(bar_start_ts_utc)
                    ) daily
                )
                SELECT
                    (SELECT vol FROM current_vol) / NULLIF((SELECT avg_daily FROM avg_vol) / 6.5, 0) as rvol_1h,
                    (SELECT vol FROM current_vol) * 6.5 / NULLIF((SELECT avg_daily FROM avg_vol), 0) as rvol_daily
            """
            )
            start_1h = entry_ts - timedelta(hours=1)
            start_20d = entry_ts - timedelta(days=20)
            res = await session.execute(
                stmt, {"ticker": ticker, "entry_ts": entry_ts, "start_1h": start_1h, "start_20d": start_20d}
            )
            row = res.fetchone()
            if row:
                return {"rvol_1h": row[0], "rvol_daily": row[1]}
            return {}

        rvol = await db_query(query_rvol)
        result.update(rvol)
    except Exception as e:
        logger.debug(f"RVOL lookup failed: {e}")

    # Get overnight gap
    try:

        async def query_overnight_gap(session: Any) -> Optional[float]:
            stmt = text(
                """
                WITH prev_close AS (
                    SELECT close FROM silver_alpaca_bars
                    WHERE ticker = :ticker
                    AND bar_start_ts_utc < DATE(:entry_date)
                    ORDER BY bar_start_ts_utc DESC LIMIT 1
                ),
                today_open AS (
                    SELECT open FROM silver_alpaca_bars
                    WHERE ticker = :ticker
                    AND DATE(bar_start_ts_utc) = DATE(:entry_date)
                    ORDER BY bar_start_ts_utc ASC LIMIT 1
                )
                SELECT
                    ((SELECT open FROM today_open) / NULLIF((SELECT close FROM prev_close), 0) - 1) * 100
            """
            )
            res = await session.execute(stmt, {"ticker": ticker, "entry_date": entry_ts})
            row = res.fetchone()
            return row[0] if row and row[0] else None

        overnight = await db_query(query_overnight_gap)
        if overnight is not None:
            result["overnight_gap_pct"] = overnight
    except Exception as e:
        logger.debug(f"Overnight gap lookup failed: {e}")

    # Get 52-week high distance
    try:

        async def query_52w_high(session: Any) -> Optional[float]:
            stmt = text(
                """
                WITH high_52w AS (
                    SELECT MAX(high) as max_high FROM silver_alpaca_bars
                    WHERE ticker = :ticker
                    AND bar_start_ts_utc > :start_52w AND bar_start_ts_utc <= :entry_ts
                ),
                current_price AS (
                    SELECT close FROM silver_alpaca_bars
                    WHERE ticker = :ticker AND bar_start_ts_utc <= :entry_ts
                    ORDER BY bar_start_ts_utc DESC LIMIT 1
                )
                SELECT
                    ((SELECT close FROM current_price) / NULLIF((SELECT max_high FROM high_52w), 0) - 1) * 100
            """
            )
            start_52w = entry_ts - timedelta(weeks=52)
            res = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts, "start_52w": start_52w})
            row = res.fetchone()
            return row[0] if row and row[0] else None

        high_dist = await db_query(query_52w_high)
        if high_dist is not None:
            result["high_52w_distance_pct"] = high_dist
    except Exception as e:
        logger.debug(f"52w high lookup failed: {e}")

    # Get VWAP distance (intraday VWAP vs current price)
    try:

        async def query_vwap(session: Any) -> Optional[float]:
            stmt = text(
                """
                WITH today_bars AS (
                    SELECT close, volume, high, low
                    FROM silver_alpaca_bars
                    WHERE ticker = :ticker
                    AND DATE(bar_start_ts_utc) = DATE(:entry_ts)
                ),
                vwap_calc AS (
                    SELECT
                        SUM((high + low + close) / 3 * volume) / NULLIF(SUM(volume), 0) as vwap
                    FROM today_bars
                ),
                current_price AS (
                    SELECT close FROM silver_alpaca_bars
                    WHERE ticker = :ticker AND bar_start_ts_utc <= :entry_ts
                    ORDER BY bar_start_ts_utc DESC LIMIT 1
                )
                SELECT
                    ((SELECT close FROM current_price) / NULLIF((SELECT vwap FROM vwap_calc), 0) - 1) * 100
            """
            )
            res = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts})
            row = res.fetchone()
            return row[0] if row and row[0] else None

        vwap_dist = await db_query(query_vwap)
        if vwap_dist is not None:
            result["vwap_distance_pct"] = vwap_dist
    except Exception as e:
        logger.debug(f"VWAP lookup failed: {e}")

    # Get SPY correlation (5-day rolling correlation of daily returns)
    try:

        async def query_spy_correlation(session: Any) -> Optional[float]:
            stmt = text(
                """
                WITH ticker_returns AS (
                    SELECT
                        DATE(bar_start_ts_utc) as day,
                        MAX(close) as close
                    FROM silver_alpaca_bars
                    WHERE ticker = :ticker
                    AND bar_start_ts_utc > :start_5d AND bar_start_ts_utc <= :entry_ts
                    GROUP BY DATE(bar_start_ts_utc)
                    ORDER BY day
                ),
                spy_returns AS (
                    SELECT
                        DATE(bar_start_ts_utc) as day,
                        MAX(close) as close
                    FROM silver_alpaca_bars
                    WHERE ticker = 'SPY'
                    AND bar_start_ts_utc > :start_5d AND bar_start_ts_utc <= :entry_ts
                    GROUP BY DATE(bar_start_ts_utc)
                    ORDER BY day
                ),
                combined AS (
                    SELECT
                        t.day,
                        t.close / NULLIF(LAG(t.close) OVER (ORDER BY t.day), 0) - 1 as ticker_ret,
                        s.close / NULLIF(LAG(s.close) OVER (ORDER BY s.day), 0) - 1 as spy_ret
                    FROM ticker_returns t
                    JOIN spy_returns s ON t.day = s.day
                )
                SELECT CORR(ticker_ret, spy_ret)
                FROM combined
                WHERE ticker_ret IS NOT NULL AND spy_ret IS NOT NULL
            """
            )
            start_5d = entry_ts - timedelta(days=7)  # 7 calendar days = ~5 trading
            res = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts, "start_5d": start_5d})
            row = res.fetchone()
            return row[0] if row and row[0] else None

        spy_corr = await db_query(query_spy_correlation)
        if spy_corr is not None:
            result["spy_correlation_5d"] = spy_corr
    except Exception as e:
        logger.debug(f"SPY correlation lookup failed: {e}")

    return result
