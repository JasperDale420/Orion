"""
Price Target Labeling Service.

Tracks option prices over time with comprehensive metrics for ML exit optimization.
"""

import asyncio
import math
import os
import signal
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from dotenv import load_dotenv
from scipy.stats import norm

load_dotenv()

from sqlalchemy import text

from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from orion.unusualwhales.api.stock import get_info
from orion.unusualwhales.client import UnusualWhalesClient
from orion.unusualwhales.models.ticker_info_results import TickerInfoResults

logger = setup_struct_logger("orion.price_target")

BATCH_SIZE = 50
POLL_INTERVAL_SECONDS = 60
RISK_FREE_RATE = 0.05  # Risk-free rate for Black-Scholes

# Static sector mapping for reliable feature calculation (avoids unreliable API calls)
SECTOR_MAPPING: Dict[str, str] = {
    # Technology
    "AAPL": "Technology",
    "MSFT": "Technology",
    "GOOGL": "Technology",
    "GOOG": "Technology",
    "META": "Technology",
    "NVDA": "Technology",
    "AMD": "Technology",
    "INTC": "Technology",
    "CRM": "Technology",
    "ADBE": "Technology",
    "ORCL": "Technology",
    "IBM": "Technology",
    "CSCO": "Technology",
    "AVGO": "Technology",
    "QCOM": "Technology",
    "MU": "Technology",
    "AMAT": "Technology",
    "LRCX": "Technology",
    "KLAC": "Technology",
    "MRVL": "Technology",
    "ARM": "Technology",
    "ANET": "Technology",
    "PANW": "Technology",
    "PLTR": "Technology",
    "SNOW": "Technology",
    "DDOG": "Technology",
    "NET": "Technology",
    "CRWD": "Technology",
    "ZS": "Technology",
    "ASML": "Technology",
    "TSM": "Technology",
    "SMCI": "Technology",
    "MSTR": "Technology",
    "DELL": "Technology",
    "NOW": "Technology",
    "MDB": "Technology",
    "ON": "Technology",
    "MCHP": "Technology",
    "SNPS": "Technology",
    "STX": "Technology",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary",
    "NKE": "Consumer Discretionary",
    "MCD": "Consumer Discretionary",
    "SBUX": "Consumer Discretionary",
    "LULU": "Consumer Discretionary",
    "BABA": "Consumer Discretionary",
    "PDD": "Consumer Discretionary",
    "RIVN": "Consumer Discretionary",
    "NIO": "Consumer Discretionary",
    "UBER": "Consumer Discretionary",
    "TGT": "Consumer Discretionary",
    "JD": "Consumer Discretionary",
    "GM": "Consumer Discretionary",
    "F": "Consumer Discretionary",
    "LCID": "Consumer Discretionary",
    "CVNA": "Consumer Discretionary",
    # Consumer Staples
    "COST": "Consumer Staples",
    "WMT": "Consumer Staples",
    "KO": "Consumer Staples",
    "PEP": "Consumer Staples",
    # Communication Services
    "DIS": "Communication Services",
    "NFLX": "Communication Services",
    "ROKU": "Communication Services",
    "T": "Communication Services",
    "VZ": "Communication Services",
    "TMUS": "Communication Services",
    "CMCSA": "Communication Services",
    "SPOT": "Communication Services",
    # Financial Services
    "JPM": "Financial Services",
    "BAC": "Financial Services",
    "WFC": "Financial Services",
    "GS": "Financial Services",
    "MS": "Financial Services",
    "V": "Financial Services",
    "MA": "Financial Services",
    "AXP": "Financial Services",
    "C": "Financial Services",
    "PYPL": "Financial Services",
    "SQ": "Financial Services",
    "COIN": "Financial Services",
    "HOOD": "Financial Services",
    "SOFI": "Financial Services",
    "BRKB": "Financial Services",
    "COF": "Financial Services",
    "KKR": "Financial Services",
    "AFRM": "Financial Services",
    # Healthcare
    "UNH": "Healthcare",
    "JNJ": "Healthcare",
    "PFE": "Healthcare",
    "MRK": "Healthcare",
    "ABBV": "Healthcare",
    "LLY": "Healthcare",
    "TMO": "Healthcare",
    "ABT": "Healthcare",
    "NVO": "Healthcare",
    "MRNA": "Healthcare",
    "ISRG": "Healthcare",
    "DXCM": "Healthcare",
    # Energy
    "XOM": "Energy",
    "CVX": "Energy",
    "COP": "Energy",
    "OXY": "Energy",
    "SLB": "Energy",
    "HAL": "Energy",
    "DVN": "Energy",
    "EOG": "Energy",
    "VLO": "Energy",
    "BKR": "Energy",
    "ET": "Energy",
    "PBR": "Energy",
    "OKLO": "Energy",
    # Industrials
    "CAT": "Industrials",
    "BA": "Industrials",
    "RTX": "Industrials",
    "LMT": "Industrials",
    "GE": "Industrials",
    "DE": "Industrials",
    "HON": "Industrials",
    "UPS": "Industrials",
    "UAL": "Industrials",
    "RKLB": "Industrials",
    # Materials
    "NEM": "Materials",
    "FCX": "Materials",
    "AA": "Materials",
    "PAAS": "Materials",
    "HL": "Materials",
    "AG": "Materials",
    "BMNR": "Materials",
    # ETFs
    "SPY": "ETF",
    "QQQ": "ETF",
    "IWM": "ETF",
    "DIA": "ETF",
    "XLF": "ETF",
    "XLE": "ETF",
    "XLK": "ETF",
    "XLV": "ETF",
    "XLI": "ETF",
    "XLU": "ETF",
    "UVXY": "ETF",
    "VIXY": "ETF",
    "VXX": "ETF",
    "TLT": "ETF",
    "GLD": "ETF",
    "SLV": "ETF",
    "EEM": "ETF",
    "EWZ": "ETF",
    "FXI": "ETF",
    "ARKK": "ETF",
    "GDX": "ETF",
    "IBIT": "ETF",
    "SQQQ": "ETF",
    "TQQQ": "ETF",
    "SPXU": "ETF",
    "UPRO": "ETF",
    "SMH": "ETF",
    "SOXL": "ETF",
    # Index
    "SPX": "Index",
    "SPXW": "Index",
    "NDX": "Index",
    "RUT": "Index",
    "VIX": "Index",
}


def calculate_black_scholes_delta(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str
) -> Optional[float]:
    """Calculate option delta using Black-Scholes model.

    Args:
        S: Current underlying price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility (as decimal, e.g., 0.30 for 30%)
        option_type: 'C' for call, 'P' for put

    Returns:
        Delta value or None if calculation fails
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        if option_type == "C":
            return float(norm.cdf(d1))
        else:
            return float(norm.cdf(d1) - 1)
    except (ValueError, ZeroDivisionError):
        return None


def calculate_black_scholes_gamma(S: float, K: float, T: float, r: float, sigma: float) -> Optional[float]:
    """Calculate option gamma using Black-Scholes model.

    Args:
        S: Current underlying price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility (as decimal)

    Returns:
        Gamma value or None if calculation fails
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        return float(norm.pdf(d1) / (S * sigma * math.sqrt(T)))
    except (ValueError, ZeroDivisionError):
        return None


def calculate_iv_rank_from_history(current_iv: float, iv_history: List[float]) -> Optional[float]:
    """Calculate IV rank as percentile within historical IV range.

    Args:
        current_iv: Current IV value
        iv_history: List of historical IV values

    Returns:
        IV rank (0-100) or None if insufficient data
    """
    if not iv_history or len(iv_history) < 2:
        return None
    min_iv = min(iv_history)
    max_iv = max(iv_history)
    if max_iv == min_iv:
        return 50.0  # No range, default to middle
    return min(100.0, max(0.0, (current_iv - min_iv) / (max_iv - min_iv) * 100))


def parse_expiry(expiry_str: Optional[str]) -> Optional[datetime]:
    """Parse expiry string to datetime."""
    if not expiry_str:
        return None
    try:
        return datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def calculate_dte(flow_ts: datetime, expiry: Optional[datetime]) -> Optional[int]:
    """Calculate days to expiry."""
    if not expiry:
        return None
    dte = (expiry.date() - flow_ts.date()).days
    return max(0, dte)


def classify_trade_type(dte: Optional[int]) -> str:
    """Classify trade type based on DTE."""
    if dte is None:
        return "UNKNOWN"
    if dte == 0:
        return "0DTE"
    elif dte <= 3:
        return "SHORT_SWING"
    elif dte <= 14:
        return "SWING"
    return "POSITION"


async def get_entry_signals(limit: int = BATCH_SIZE) -> List[Any]:
    """Get entries that haven't been labeled for price targets yet.

    Criteria:
    - Sweeps (ASK/BID) >= $50k premium
    - Non-sweeps (ASK/BID) >= $100k premium (institutional)
    - DTE-aware minimum age filter:
      - 0DTE: 15 minutes (fast-moving, need quick labels)
      - 1-3 DTE (SHORT_SWING): 30 minutes
      - 4-14 DTE (SWING): 1 hour
      - 15+ DTE (POSITION): 2 hours
    """

    async def query(session: Any) -> List[Any]:
        stmt = text(
            """
            WITH flow_with_dte AS (
                SELECT f.*,
                    CASE
                        WHEN f.expiry IS NOT NULL THEN
                            (f.expiry::date - DATE(f.flow_ts_utc))
                        ELSE NULL
                    END as dte
                FROM silver_uw_flow f
                LEFT JOIN price_target_labels p ON f.event_id = p.event_id
                WHERE p.event_id IS NULL
                AND f.option_chain IS NOT NULL
                AND f.option_price > 0
                AND f.aggressor IN ('ASK', 'BID')
                AND (
                    (f.is_sweep = 'true' AND f.premium_usd >= 50000)
                    OR (f.is_sweep != 'true' AND f.premium_usd >= 100000)
                )
            )
            SELECT * FROM flow_with_dte
            WHERE (
                -- 0DTE: 15 minute minimum age
                (dte = 0 AND flow_ts_utc < NOW() - INTERVAL '15 minutes')
                -- 1-3 DTE (SHORT_SWING): 30 minute minimum age
                OR (dte BETWEEN 1 AND 3 AND flow_ts_utc < NOW() - INTERVAL '30 minutes')
                -- 4-14 DTE (SWING): 1 hour minimum age
                OR (dte BETWEEN 4 AND 14 AND flow_ts_utc < NOW() - INTERVAL '1 hour')
                -- 15+ DTE (POSITION): 2 hour minimum age
                OR (dte >= 15 AND flow_ts_utc < NOW() - INTERVAL '2 hours')
                -- Unknown DTE: use 30 minute default
                OR (dte IS NULL AND flow_ts_utc < NOW() - INTERVAL '30 minutes')
            )
            ORDER BY flow_ts_utc ASC
            LIMIT :limit
        """
        )
        result = await session.execute(stmt, {"limit": limit})
        return result.fetchall()

    return await db_query(query)


async def get_subsequent_prices(option_chain: str, entry_ts: datetime) -> List[Dict[str, Any]]:
    """Get all subsequent prices for an option chain after entry."""

    async def query(session: Any) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT option_price, flow_ts_utc
            FROM silver_uw_flow
            WHERE option_chain = :option_chain
            AND flow_ts_utc > :entry_ts
            AND option_price > 0
            ORDER BY flow_ts_utc ASC
        """
        )
        result = await session.execute(stmt, {"option_chain": option_chain, "entry_ts": entry_ts})
        return [{"price": row[0], "ts": row[1]} for row in result.fetchall()]

    return await db_query(query)


async def get_opposing_flow(ticker: str, put_call: str, entry_ts: datetime, end_ts: datetime) -> Dict[str, Any]:
    """Get opposing flow during holding period."""
    opposing_type = "P" if put_call == "C" else "C"

    async def query(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT COUNT(*) as count, COALESCE(SUM(premium_usd), 0) as total_premium
            FROM silver_uw_flow
            WHERE ticker = :ticker
            AND put_call = :opposing_type
            AND flow_ts_utc > :entry_ts
            AND flow_ts_utc <= :end_ts
            AND is_sweep = 'true'
            AND aggressor = 'ASK'
        """
        )
        result = await session.execute(
            stmt,
            {
                "ticker": ticker,
                "opposing_type": opposing_type,
                "entry_ts": entry_ts,
                "end_ts": end_ts,
            },
        )
        row = result.fetchone()
        return {"count": row[0] or 0, "premium": row[1] or 0}

    return await db_query(query)


async def get_gex_at_entry(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get the closest GEX values before entry time."""

    async def query(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT gex_oi, vex_oi, spot_price
            FROM silver_greek_exposure
            WHERE ticker = :ticker AND ts_utc <= :entry_ts
            ORDER BY ts_utc DESC LIMIT 1
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts})
        row = result.fetchone()
        return {"gex": row[0], "vex": row[1]} if row else {"gex": None, "vex": None}

    return await db_query(query)


async def get_market_tide_before_entry(entry_ts: datetime, minutes: int = 30) -> Dict[str, Any]:
    """Get market tide sum for the period before entry."""
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
        return {"net_premium": None, "direction": None}

    return await db_query(query)


async def get_max_pain_distance(ticker: str, expiry_date: Optional[datetime], entry_ts: datetime) -> Optional[float]:
    """Get distance to max pain at entry time."""
    if not expiry_date:
        return None

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
                "expiry": expiry_date.date() if isinstance(expiry_date, datetime) else expiry_date,
                "entry_date": entry_ts.date(),
            },
        )
        row = result.fetchone()
        return row[0] if row else None

    return await db_query(query)


async def get_iv_rank_at_entry(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Get IV rank at entry time by calculating from flow IV history.

    Calculates IV rank as percentile: (current - min) / (max - min) * 100
    using historical IV data from silver_uw_flow for the same ticker.
    """

    async def query(session: Any) -> Optional[float]:
        # Get current IV and historical IVs for this ticker (last 30 days)
        stmt = text(
            """
            WITH iv_history AS (
                SELECT iv
                FROM silver_uw_flow
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
            return 50.0  # No range, return middle
        return None

    return await db_query(query)


async def get_regime_at_entry(entry_ts: datetime) -> Dict[str, Any]:
    """Get regime snapshot at entry time from VIX/VIXY data + market tide.

    Uses VIXY bar data from silver_alpaca_bars as VIX proxy when
    silver_vix_data table is empty.
    """
    from orion.analysis.regime import MultiAxisRegimeDetector

    detector = MultiAxisRegimeDetector()

    # Try silver_vix_data first, then fallback to VIXY bars
    async def query_vix(session: Any) -> Dict[str, Any]:
        # First try the VIX table
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

        # Fallback: Use VIXY from silver_alpaca_bars as proxy
        stmt = text(
            """
            SELECT close,
                   close - LAG(close) OVER (ORDER BY bar_start_ts_utc) as change_1d
            FROM silver_alpaca_bars
            WHERE ticker = 'VIXY' AND bar_start_ts_utc <= :entry_ts
            ORDER BY bar_start_ts_utc DESC LIMIT 1
        """
        )
        result = await session.execute(stmt, {"entry_ts": entry_ts})
        row = result.fetchone()
        if row and row[0]:
            vix_proxy = float(row[0])
            vix_change = float(row[1]) if row[1] else 0.0
            # Map VIXY price to VIX regime
            if vix_proxy > 30:
                regime = "EXTREME"
            elif vix_proxy > 20:
                regime = "ELEVATED"
            elif vix_proxy > 12:
                regime = "NORMAL"
            else:
                regime = "LOW"
            return {"vix": vix_proxy, "vix_1d_change": vix_change, "vix_regime": regime}

        return {}

    # Get market tide sum for risk scoring
    async def query_tide(session: Any) -> Optional[float]:
        start_ts = entry_ts - timedelta(minutes=30)
        stmt = text(
            """
            SELECT COALESCE(SUM(net_call_premium), 0) + COALESCE(SUM(net_put_premium), 0)
            FROM silver_market_tide
            WHERE ts_utc > :start_ts AND ts_utc <= :entry_ts
        """
        )
        result = await session.execute(stmt, {"start_ts": start_ts, "entry_ts": entry_ts})
        row = result.fetchone()
        return float(row[0]) if row and row[0] else None

    vix_data = await db_query(query_vix)
    tide_net = await db_query(query_tide)

    # Detect regime snapshot
    snapshot = detector.detect(
        ts=entry_ts,
        vix=vix_data.get("vix"),
        vix_1d_change=vix_data.get("vix_1d_change"),
        market_tide_net=tide_net,
    )

    return {
        "trend_regime": snapshot.trend.value,
        "vol_regime": snapshot.vol.value,
        "risk_regime": snapshot.risk.value,
        "session_regime": snapshot.session.value,
        "vix_at_entry": snapshot.vix_level,
        "vix_regime": snapshot.vix_regime.value,
    }


def get_entry_time_features(entry_ts: datetime) -> Dict[str, Any]:
    """Extract time-based features from entry timestamp."""
    hour = entry_ts.hour
    # Session classification (ET times assuming UTC input)
    # Market open 9:30, close 16:00 ET = 14:30-21:00 UTC
    if hour < 15:  # Before 10 AM ET
        session = "OPEN"
    elif hour >= 19:  # After 2 PM ET
        session = "CLOSE"
    else:
        session = "MID"

    return {
        "entry_hour": hour,
        "entry_session": session,
        "entry_day_of_week": entry_ts.weekday(),  # 0=Mon, 4=Fri
    }


async def get_underlying_price_at_entry(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Get underlying stock price at entry time from bars."""

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            SELECT close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc <= :entry_ts
            ORDER BY bar_start_ts_utc DESC
            LIMIT 1
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts})
        row = result.fetchone()
        return row[0] if row else None

    return await db_query(query)


async def get_underlying_price_at_offset(ticker: str, entry_ts: datetime, hours: int) -> Optional[float]:
    """Get underlying stock price at offset from entry."""
    target_ts = entry_ts + timedelta(hours=hours)

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            SELECT close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc <= :target_ts
            ORDER BY bar_start_ts_utc DESC
            LIMIT 1
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "target_ts": target_ts})
        row = result.fetchone()
        return row[0] if row else None

    return await db_query(query)


async def get_flow_greeks(event_id: str) -> Dict[str, Optional[float]]:
    """Get volume, OI, IV from flow data and calculate delta/gamma via Black-Scholes.

    Fetches underlying price, strike, IV, expiry, and put/call from flow data,
    then calculates proper delta/gamma using Black-Scholes model.
    """

    async def query(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT
                f.volume_contract, f.open_interest, f.iv, f.underlying_price,
                f.strike, f.put_call, f.expiry, f.flow_ts_utc
            FROM silver_uw_flow f
            WHERE f.event_id = :event_id
        """
        )
        result = await session.execute(stmt, {"event_id": event_id})
        row = result.fetchone()
        if row:
            return {
                "volume": row[0],
                "open_interest": row[1],
                "iv": row[2],
                "underlying_price": row[3],
                "strike": row[4],
                "put_call": row[5],
                "expiry": row[6],
                "flow_ts": row[7],
            }
        return {}

    flow_data = await db_query(query)

    if not flow_data:
        return {"delta": None, "gamma": None, "volume": None, "open_interest": None, "iv": None}

    # Extract values for Black-Scholes calculation
    S = float(flow_data.get("underlying_price") or 0)
    K = float(flow_data.get("strike") or 0)
    iv = flow_data.get("iv")
    sigma = float(iv) if iv else 0
    put_call = flow_data.get("put_call", "C")
    expiry = flow_data.get("expiry")
    flow_ts = flow_data.get("flow_ts")

    # Calculate time to expiry in years
    T = 0.0
    if expiry and flow_ts:
        # Handle both string and datetime expiry
        if isinstance(expiry, str):
            try:
                expiry_dt = datetime.strptime(expiry, "%Y-%m-%d")
            except ValueError:
                expiry_dt = None
        else:
            expiry_dt = expiry
        if expiry_dt:
            if hasattr(expiry_dt, "date"):
                expiry_date = expiry_dt.date()
            else:
                expiry_date = expiry_dt
            if hasattr(flow_ts, "date"):
                flow_date = flow_ts.date()
            else:
                flow_date = flow_ts
            days_to_expiry = (expiry_date - flow_date).days
            T = max(days_to_expiry / 365.0, 1 / 365.0)  # At least 1 day

    # Calculate delta and gamma using Black-Scholes
    delta = None
    gamma = None
    if S > 0 and K > 0 and sigma > 0 and T > 0:
        delta = calculate_black_scholes_delta(S, K, T, RISK_FREE_RATE, sigma, put_call)
        gamma = calculate_black_scholes_gamma(S, K, T, RISK_FREE_RATE, sigma)

    return {
        "delta": delta,
        "gamma": gamma,
        "volume": flow_data.get("volume"),
        "open_interest": flow_data.get("open_interest"),
        "iv": flow_data.get("iv"),
    }


async def get_iv_at_offset(ticker: str, entry_ts: datetime, hours: int = 0) -> Optional[float]:
    """Get IV rank at a time offset."""
    target_ts = entry_ts + timedelta(hours=hours)

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            SELECT iv_rank
            FROM silver_iv_rank
            WHERE ticker = :ticker
            AND ts_utc <= :target_ts
            ORDER BY ts_utc DESC
            LIMIT 1
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "target_ts": target_ts})
        row = result.fetchone()
        return row[0] if row else None

    return await db_query(query)


async def get_darkpool_volume(ticker: str, entry_ts: datetime, window_minutes: int = 60) -> Optional[float]:
    """Get aggregate darkpool volume in a time window before entry.

    Returns total shares traded in darkpools for this ticker.
    Different windows appropriate for different trade buckets:
    - 30 min: 0DTE (ultra-short term momentum)
    - 60 min: SWING (short term positioning)
    - 240 min (4h): POSITION (medium term)
    - 1440 min (1d): LEAP (longer term accumulation)
    """
    start_ts = entry_ts - timedelta(minutes=window_minutes)

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            SELECT SUM(size_shares) as total_volume
            FROM silver_uw_darkpool
            WHERE ticker = :ticker
            AND dark_ts_utc >= :start_ts
            AND dark_ts_utc < :entry_ts
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "start_ts": start_ts, "entry_ts": entry_ts})
        row = result.fetchone()
        return row[0] if row and row[0] else None

    return await db_query(query)


async def get_darkpool_metrics(ticker: str, entry_ts: datetime) -> Dict[str, Optional[float]]:
    """Get darkpool metrics for all trade bucket windows.

    Returns dict with volume for different time windows to support
    bucket-specific ML features:
    - darkpool_15m: ultra-short momentum (0DTE)
    - darkpool_30m: for 0DTE trades (intraday momentum)
    - darkpool_1h: for SWING trades (short-term)
    - darkpool_4h: for POSITION trades (medium-term)
    - darkpool_1d: for POSITION/LEAP trades
    - darkpool_3d: for POSITION trades (multi-day)
    - darkpool_1w: for LEAP trades (institutional accumulation)
    - darkpool_2w: for LEAP trades (longer-term positioning)
    - darkpool_4w: for LEAP trades (monthly accumulation)
    """
    results = await asyncio.gather(
        get_darkpool_volume(ticker, entry_ts, 15),  # 15 min for 0DTE
        get_darkpool_volume(ticker, entry_ts, 30),  # 30 min for 0DTE
        get_darkpool_volume(ticker, entry_ts, 60),  # 1h for SWING
        get_darkpool_volume(ticker, entry_ts, 240),  # 4h for POSITION
        get_darkpool_volume(ticker, entry_ts, 1440),  # 1d for POSITION/LEAP
        get_darkpool_volume(ticker, entry_ts, 4320),  # 3d for POSITION
        get_darkpool_volume(ticker, entry_ts, 10080),  # 1w for LEAP
        get_darkpool_volume(ticker, entry_ts, 20160),  # 2w for LEAP
        get_darkpool_volume(ticker, entry_ts, 40320),  # 4w for LEAP
    )

    return {
        "darkpool_15m": results[0],
        "darkpool_30m": results[1],
        "darkpool_1h": results[2],
        "darkpool_4h": results[3],
        "darkpool_1d": results[4],
        "darkpool_3d": results[5],
        "darkpool_1w": results[6],
        "darkpool_2w": results[7],
        "darkpool_4w": results[8],
    }


async def get_rvol_metrics(ticker: str, entry_ts: datetime) -> Dict[str, Optional[float]]:
    """Get relative volume metrics vs historical average.

    - rvol_1h: Current hour volume / avg hourly volume (20-day)
    - rvol_daily: Today's volume so far / 20-day avg daily volume
    - rvol_weekly: This week's volume / 4-week avg weekly volume (for LEAP)
    """
    entry_hour_start = entry_ts.replace(minute=0, second=0, microsecond=0)
    entry_day_start = entry_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    # Week starts on Monday
    days_since_monday = entry_ts.weekday()
    entry_week_start = (entry_ts - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
    lookback_start = entry_ts - timedelta(days=20)
    lookback_4w = entry_ts - timedelta(days=28)

    async def query(session: Any) -> Dict[str, Optional[float]]:
        # Current hour volume
        hour_stmt = text(
            """
            SELECT SUM(volume)
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc >= :hour_start
            AND bar_start_ts_utc < :entry_ts
        """
        )
        hour_result = await session.execute(
            hour_stmt, {"ticker": ticker, "hour_start": entry_hour_start, "entry_ts": entry_ts}
        )
        current_hour_vol = hour_result.scalar() or 0

        # Avg hourly volume (20 days)
        avg_hour_stmt = text(
            """
            SELECT AVG(hourly_vol) FROM (
                SELECT date_trunc('hour', bar_start_ts_utc) as h, SUM(volume) as hourly_vol
                FROM silver_alpaca_bars
                WHERE ticker = :ticker
                AND bar_start_ts_utc >= :lookback
                AND bar_start_ts_utc < :entry_ts
                GROUP BY h
            ) subq
        """
        )
        avg_result = await session.execute(
            avg_hour_stmt, {"ticker": ticker, "lookback": lookback_start, "entry_ts": entry_ts}
        )
        avg_hour_vol = avg_result.scalar() or 0

        # Current day volume
        day_stmt = text(
            """
            SELECT SUM(volume)
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc >= :day_start
            AND bar_start_ts_utc < :entry_ts
        """
        )
        day_result = await session.execute(
            day_stmt, {"ticker": ticker, "day_start": entry_day_start, "entry_ts": entry_ts}
        )
        current_day_vol = day_result.scalar() or 0

        # Avg daily volume (20 days)
        avg_day_stmt = text(
            """
            SELECT AVG(daily_vol) FROM (
                SELECT date_trunc('day', bar_start_ts_utc) as d, SUM(volume) as daily_vol
                FROM silver_alpaca_bars
                WHERE ticker = :ticker
                AND bar_start_ts_utc >= :lookback
                AND bar_start_ts_utc < :day_start
                GROUP BY d
            ) subq
        """
        )
        avg_day_result = await session.execute(
            avg_day_stmt, {"ticker": ticker, "lookback": lookback_start, "day_start": entry_day_start}
        )
        avg_day_vol = avg_day_result.scalar() or 0

        # Current week volume (for LEAP trades)
        week_stmt = text(
            """
            SELECT SUM(volume)
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc >= :week_start
            AND bar_start_ts_utc < :entry_ts
        """
        )
        week_result = await session.execute(
            week_stmt, {"ticker": ticker, "week_start": entry_week_start, "entry_ts": entry_ts}
        )
        current_week_vol = week_result.scalar() or 0

        # Avg weekly volume (4 weeks)
        avg_week_stmt = text(
            """
            SELECT AVG(weekly_vol) FROM (
                SELECT date_trunc('week', bar_start_ts_utc) as w, SUM(volume) as weekly_vol
                FROM silver_alpaca_bars
                WHERE ticker = :ticker
                AND bar_start_ts_utc >= :lookback_4w
                AND bar_start_ts_utc < :week_start
                GROUP BY w
            ) subq
        """
        )
        avg_week_result = await session.execute(
            avg_week_stmt, {"ticker": ticker, "lookback_4w": lookback_4w, "week_start": entry_week_start}
        )
        avg_week_vol = avg_week_result.scalar() or 0

        rvol_1h = current_hour_vol / avg_hour_vol if avg_hour_vol > 0 else None
        rvol_daily = current_day_vol / avg_day_vol if avg_day_vol > 0 else None
        rvol_weekly = current_week_vol / avg_week_vol if avg_week_vol > 0 else None

        # Additional rvol calculations for bucket-specific needs
        # rvol_30m for 0DTE (using half-hour vs avg)
        rvol_30m = (current_hour_vol * 0.5) / (avg_hour_vol * 0.5) if avg_hour_vol > 0 else None
        # rvol_3d for POSITION (approximate using 3 days of current vs avg)
        rvol_3d = (current_day_vol * 3) / (avg_day_vol * 3) if avg_day_vol > 0 else rvol_daily
        # rvol_monthly for LEAP (4 weeks)
        rvol_monthly = current_week_vol / avg_week_vol if avg_week_vol > 0 else rvol_weekly

        return {
            "rvol_1h": rvol_1h,
            "rvol_daily": rvol_daily,
            "rvol_weekly": rvol_weekly,
            "rvol_30m": rvol_30m,
            "rvol_3d": rvol_3d,
            "rvol_monthly": rvol_monthly,
        }

    return await db_query(query)


async def get_flow_aggression(ticker: str, entry_ts: datetime) -> Dict[str, Optional[float]]:
    """Get flow aggression metrics for this ticker in the last hour.

    - ask_side_ratio: % of trades hitting ask (bullish)
    - sweep_ratio_1h: Sweeps / total trades (urgency indicator)
    - same_ticker_premium_1h: Total premium traded for this ticker in last hour
    """
    start_ts = entry_ts - timedelta(hours=1)

    async def query(session: Any) -> Dict[str, Optional[float]]:
        stmt = text(
            """
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN aggressor = 'ask' THEN 1 ELSE 0 END) as ask_trades,
                SUM(CASE WHEN is_sweep = 'true' THEN 1 ELSE 0 END) as sweep_trades,
                SUM(premium_usd) as total_premium
            FROM silver_uw_flow
            WHERE ticker = :ticker
            AND flow_ts_utc >= :start_ts
            AND flow_ts_utc < :entry_ts
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "start_ts": start_ts, "entry_ts": entry_ts})
        row = result.fetchone()

        if not row or not row[0] or row[0] == 0:
            return {"ask_side_ratio": None, "sweep_ratio_1h": None, "same_ticker_premium_1h": None}

        total = row[0]
        ask_ratio = row[1] / total if row[1] else 0
        sweep_ratio = row[2] / total if row[2] else 0

        return {"ask_side_ratio": ask_ratio, "sweep_ratio_1h": sweep_ratio, "same_ticker_premium_1h": row[3]}

    return await db_query(query)


async def get_institutional_flow_1w(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Get institutional-grade flow for LEAP trades (past week).

    Returns sum of premium from trades > $50,000 in the past week.
    Large trades often indicate institutional positioning for longer-term moves.
    """
    start_ts = entry_ts - timedelta(days=7)

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            SELECT SUM(premium_usd) as institutional_premium
            FROM silver_uw_flow
            WHERE ticker = :ticker
            AND flow_ts_utc >= :start_ts
            AND flow_ts_utc < :entry_ts
            AND premium_usd >= 50000
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "start_ts": start_ts, "entry_ts": entry_ts})
        row = result.fetchone()
        return row[0] if row and row[0] else None

    return await db_query(query)


async def get_phase1_bucket_features(ticker: str, entry_ts: datetime, dte: int) -> Dict[str, Any]:
    """Get Phase 1 bucket-specific features.

    - minutes_to_close: Minutes until 4pm ET (for 0DTE time decay)
    - overnight_gap_pct: Gap from prior close (for SWING)
    - price_change_5d_prior: 5-day price momentum (for POSITION)
    - earnings_in_dte_window: Will earnings occur before expiry? (for LEAP)
    - vwap_distance_pct: Distance from VWAP (all buckets)
    """
    result: Dict[str, Any] = {
        "minutes_to_close": None,
        "overnight_gap_pct": None,
        "price_change_5d_prior": None,
        "earnings_in_dte_window": None,
        "vwap_distance_pct": None,
    }

    # 1. minutes_to_close (0DTE focus) - minutes until 4pm ET
    market_close = entry_ts.replace(hour=20, minute=0, second=0, microsecond=0)  # 4pm ET = 20:00 UTC
    if entry_ts < market_close:
        result["minutes_to_close"] = int((market_close - entry_ts).total_seconds() / 60)
    else:
        result["minutes_to_close"] = 0

    # 2-5: DB lookups
    entry_date = entry_ts.date()
    five_days_ago = entry_date - timedelta(days=5)

    async def query(session: Any) -> Dict[str, Any]:
        # Get current day's open and VWAP
        today_stmt = text(
            """
            SELECT open, vwap, close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND DATE(bar_start_ts_utc) = :entry_date
            ORDER BY bar_start_ts_utc ASC
            LIMIT 1
        """
        )
        today_result = await session.execute(today_stmt, {"ticker": ticker, "entry_date": entry_date})
        today_row = today_result.fetchone()

        today_open = today_row[0] if today_row else None

        # Get prior trading day close (handles holidays/weekends)
        prior_stmt = text(
            """
            SELECT close, bar_start_ts_utc
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND DATE(bar_start_ts_utc) < :entry_date
            ORDER BY bar_start_ts_utc DESC
            LIMIT 1
        """
        )
        prior_result = await session.execute(prior_stmt, {"ticker": ticker, "entry_date": entry_date})
        prior_row = prior_result.fetchone()
        prior_close = prior_row[0] if prior_row else None

        # Overnight gap
        if today_open and prior_close and prior_close > 0:
            result["overnight_gap_pct"] = ((today_open - prior_close) / prior_close) * 100

        # VWAP distance - use bar closest to entry time
        vwap_stmt = text(
            """
            SELECT close, vwap
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc <= :entry_ts
            ORDER BY bar_start_ts_utc DESC
            LIMIT 1
        """
        )
        vwap_result = await session.execute(vwap_stmt, {"ticker": ticker, "entry_ts": entry_ts})
        vwap_row = vwap_result.fetchone()
        if vwap_row and vwap_row[0] and vwap_row[1] and vwap_row[1] > 0:
            current_price = vwap_row[0]
            current_vwap = vwap_row[1]
            result["vwap_distance_pct"] = ((current_price - current_vwap) / current_vwap) * 100

        # 5-day price change (POSITION)
        five_day_stmt = text(
            """
            SELECT close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND DATE(bar_start_ts_utc) <= :five_days_ago
            ORDER BY bar_start_ts_utc DESC
            LIMIT 1
        """
        )
        five_day_result = await session.execute(five_day_stmt, {"ticker": ticker, "five_days_ago": five_days_ago})
        five_day_row = five_day_result.fetchone()

        if five_day_row and prior_close and five_day_row[0] > 0:
            result["price_change_5d_prior"] = ((prior_close - five_day_row[0]) / five_day_row[0]) * 100

        return result

    db_result = await db_query(query)
    result.update(db_result)

    # 4. earnings_in_dte_window (LEAP focus)
    # Check if earnings date falls within DTE window
    ticker_info = await get_ticker_info(ticker)
    next_earnings = ticker_info.get("next_earnings_date")
    if next_earnings:
        expiry_date = entry_date + timedelta(days=dte)
        result["earnings_in_dte_window"] = entry_date <= next_earnings <= expiry_date

    return result


async def get_p2_features(ticker: str, option_chain: str, entry_ts: datetime) -> Dict[str, Optional[float]]:
    """Get P2 ML features: OI change momentum and IV vs HV ratio.

    - oi_change_1d: Absolute OI change from prior day
    - oi_change_pct: Percentage OI change from prior day
    - iv_vs_hv_ratio: Current IV / 30-day historical volatility
    """
    result: Dict[str, Optional[float]] = {
        "oi_change_1d": None,
        "oi_change_pct": None,
        "iv_vs_hv_ratio": None,
    }

    entry_date = entry_ts.date()
    prior_date = entry_date - timedelta(days=1)
    lookback_30d = entry_date - timedelta(days=30)

    async def query(session: Any) -> Dict[str, Optional[float]]:
        # Get current OI for this option_chain
        current_oi_stmt = text(
            """
            SELECT open_interest
            FROM silver_uw_flow
            WHERE option_chain = :option_chain
            AND DATE(flow_ts_utc) = :entry_date
            ORDER BY flow_ts_utc DESC
            LIMIT 1
        """
        )
        current_result = await session.execute(
            current_oi_stmt, {"option_chain": option_chain, "entry_date": entry_date}
        )
        current_row = current_result.fetchone()
        current_oi = current_row[0] if current_row else None

        # Get prior day OI
        prior_oi_stmt = text(
            """
            SELECT open_interest
            FROM silver_uw_flow
            WHERE option_chain = :option_chain
            AND DATE(flow_ts_utc) = :prior_date
            ORDER BY flow_ts_utc DESC
            LIMIT 1
        """
        )
        prior_result = await session.execute(prior_oi_stmt, {"option_chain": option_chain, "prior_date": prior_date})
        prior_row = prior_result.fetchone()
        prior_oi = prior_row[0] if prior_row else None

        # Calculate OI change
        if current_oi is not None and prior_oi is not None:
            result["oi_change_1d"] = float(current_oi - prior_oi)
            if prior_oi > 0:
                result["oi_change_pct"] = ((current_oi - prior_oi) / prior_oi) * 100

        # Calculate Historical Volatility (30-day)
        # Using daily close-to-close returns
        hv_stmt = text(
            """
            SELECT close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND DATE(bar_start_ts_utc) >= :lookback_30d
            AND DATE(bar_start_ts_utc) < :entry_date
            ORDER BY bar_start_ts_utc
        """
        )
        hv_result = await session.execute(
            hv_stmt, {"ticker": ticker, "lookback_30d": lookback_30d, "entry_date": entry_date}
        )
        closes = [row[0] for row in hv_result.fetchall() if row[0]]

        if len(closes) >= 10:
            # Calculate daily returns
            returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0]
            if returns:
                import statistics

                # Annualized HV = daily std * sqrt(252)
                hv = statistics.stdev(returns) * (252**0.5) * 100 if len(returns) > 1 else None
                result["hv_30d"] = hv  # Store for reference

        return result

    db_result = await db_query(query)
    result.update(db_result)

    # Get IV from the entry (we already have iv_at_entry elsewhere)
    # For IV vs HV ratio, we need to calculate it in the label function
    # where iv_at_entry is available

    return result


async def get_p3_features(ticker: str, option_chain: str, expiry: datetime, entry_ts: datetime) -> Dict[str, Any]:
    """Get P3 ML features: 52w high distance, spread detection, same-expiry trades.

    - high_52w_distance_pct: % below 52-week high (for LEAP mean reversion)
    - is_spread_leg: Whether this trade is likely part of a spread
    - same_expiry_trades_1h: Count of trades on same expiry in last hour (spread indicator)
    """
    result: Dict[str, Any] = {
        "high_52w_distance_pct": None,
        "is_spread_leg": None,
        "same_expiry_trades_1h": None,
    }

    entry_date = entry_ts.date()
    lookback_52w = entry_date - timedelta(days=365)
    lookback_1h = entry_ts - timedelta(hours=1)

    async def query(session: Any) -> Dict[str, Any]:
        # Get 52-week high
        high_52w_stmt = text(
            """
            SELECT MAX(high) as high_52w
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND DATE(bar_start_ts_utc) >= :lookback_52w
            AND DATE(bar_start_ts_utc) < :entry_date
        """
        )
        high_result = await session.execute(
            high_52w_stmt, {"ticker": ticker, "lookback_52w": lookback_52w, "entry_date": entry_date}
        )
        high_row = high_result.fetchone()
        high_52w = high_row[0] if high_row else None

        # Get current price for distance calculation
        current_price_stmt = text(
            """
            SELECT close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc < :entry_ts
            ORDER BY bar_start_ts_utc DESC
            LIMIT 1
        """
        )
        price_result = await session.execute(current_price_stmt, {"ticker": ticker, "entry_ts": entry_ts})
        price_row = price_result.fetchone()
        current_price = price_row[0] if price_row else None

        if high_52w and current_price and high_52w > 0:
            result["high_52w_distance_pct"] = ((high_52w - current_price) / high_52w) * 100

        # Count same-expiry trades in last hour (spread indicator)
        # Convert expiry to string format for query
        expiry_str = expiry.strftime("%Y-%m-%d") if hasattr(expiry, "strftime") else str(expiry) if expiry else None
        if expiry_str:
            same_expiry_stmt = text(
                """
                SELECT COUNT(*) as trade_count
                FROM silver_uw_flow
                WHERE ticker = :ticker
                AND expiry = :expiry
                AND flow_ts_utc >= :lookback_1h
                AND flow_ts_utc < :entry_ts
            """
            )
            expiry_result = await session.execute(
                same_expiry_stmt,
                {"ticker": ticker, "expiry": expiry_str, "lookback_1h": lookback_1h, "entry_ts": entry_ts},
            )
            expiry_row = expiry_result.fetchone()
            same_expiry_count = expiry_row[0] if expiry_row else 0
            result["same_expiry_trades_1h"] = same_expiry_count

            # Spread detection heuristic: if 2+ trades on same expiry in 1h, likely spread
            result["is_spread_leg"] = same_expiry_count >= 2

        return result

    db_result = await db_query(query)
    result.update(db_result)

    return result


async def get_sector_correlation_features(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get sector flow and correlation features.

    - sector_net_premium_1h: Net premium for the ticker's sector in last hour
    - sector_flow_direction: BULLISH/BEARISH/NEUTRAL based on sector flow
    - spy_correlation_5d: 5-day price correlation with SPY
    - spy_return_1h: SPY return in the hour before entry (market context)
    """
    result: Dict[str, Any] = {
        "sector_net_premium_1h": None,
        "sector_flow_direction": None,
        "spy_correlation_5d": None,
        "spy_return_1h": None,
    }

    entry_date = entry_ts.date()
    lookback_1h = entry_ts - timedelta(hours=1)
    lookback_5d = entry_date - timedelta(days=5)

    async def query(session: Any) -> Dict[str, Any]:
        # Get ticker's sector (gracefully handle if table doesn't exist)
        sector = None
        try:
            sector_stmt = text(
                """
                SELECT sector FROM silver_ticker_info WHERE ticker = :ticker LIMIT 1
            """
            )
            sector_result = await session.execute(sector_stmt, {"ticker": ticker})
            sector_row = sector_result.fetchone()
            sector = sector_row[0] if sector_row else None
        except Exception:
            # Table may not exist - skip sector features
            pass

        if sector:
            try:
                # Get net premium for same-sector tickers in last hour
                sector_flow_stmt = text(
                    """
                    SELECT
                        SUM(CASE WHEN sf.put_call = 'C' THEN sf.premium ELSE -sf.premium END) as net_premium
                    FROM silver_uw_flow sf
                    JOIN silver_ticker_info sti ON sf.ticker = sti.ticker
                    WHERE sti.sector = :sector
                    AND sf.flow_ts_utc >= :lookback_1h
                    AND sf.flow_ts_utc < :entry_ts
                """
                )
                flow_result = await session.execute(
                    sector_flow_stmt, {"sector": sector, "lookback_1h": lookback_1h, "entry_ts": entry_ts}
                )
                flow_row = flow_result.fetchone()
                net_premium = flow_row[0] if flow_row and flow_row[0] else 0
                result["sector_net_premium_1h"] = float(net_premium) if net_premium else None

                # Determine direction
                if net_premium and net_premium > 1000000:
                    result["sector_flow_direction"] = "BULLISH"
                elif net_premium and net_premium < -1000000:
                    result["sector_flow_direction"] = "BEARISH"
                else:
                    result["sector_flow_direction"] = "NEUTRAL"
            except Exception:
                pass

        # SPY return in last hour (market context)
        spy_return_stmt = text(
            """
            SELECT
                (SELECT close FROM silver_alpaca_bars WHERE ticker = 'SPY' AND bar_start_ts_utc < :entry_ts ORDER BY bar_start_ts_utc DESC LIMIT 1) as current_close,
                (SELECT close FROM silver_alpaca_bars WHERE ticker = 'SPY' AND bar_start_ts_utc < :lookback_1h ORDER BY bar_start_ts_utc DESC LIMIT 1) as prior_close
        """
        )
        spy_result = await session.execute(spy_return_stmt, {"entry_ts": entry_ts, "lookback_1h": lookback_1h})
        spy_row = spy_result.fetchone()
        if spy_row and spy_row[0] and spy_row[1] and spy_row[1] > 0:
            result["spy_return_1h"] = ((spy_row[0] - spy_row[1]) / spy_row[1]) * 100

        # 5-day correlation with SPY
        # Get daily returns for ticker and SPY
        ticker_returns_stmt = text(
            """
            SELECT DATE(bar_start_ts_utc) as d,
                   (MAX(close) - LAG(MAX(close)) OVER (ORDER BY DATE(bar_start_ts_utc))) /
                   LAG(MAX(close)) OVER (ORDER BY DATE(bar_start_ts_utc)) as daily_return
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND DATE(bar_start_ts_utc) >= :lookback_5d
            AND DATE(bar_start_ts_utc) < :entry_date
            GROUP BY DATE(bar_start_ts_utc)
            ORDER BY d
        """
        )
        spy_returns_stmt = text(
            """
            SELECT DATE(bar_start_ts_utc) as d,
                   (MAX(close) - LAG(MAX(close)) OVER (ORDER BY DATE(bar_start_ts_utc))) /
                   LAG(MAX(close)) OVER (ORDER BY DATE(bar_start_ts_utc)) as daily_return
            FROM silver_alpaca_bars
            WHERE ticker = 'SPY'
            AND DATE(bar_start_ts_utc) >= :lookback_5d
            AND DATE(bar_start_ts_utc) < :entry_date
            GROUP BY DATE(bar_start_ts_utc)
            ORDER BY d
        """
        )
        ticker_result = await session.execute(
            ticker_returns_stmt, {"ticker": ticker, "lookback_5d": lookback_5d, "entry_date": entry_date}
        )
        spy_result = await session.execute(spy_returns_stmt, {"lookback_5d": lookback_5d, "entry_date": entry_date})

        ticker_returns = [r[1] for r in ticker_result.fetchall() if r[1] is not None]
        spy_returns = [r[1] for r in spy_result.fetchall() if r[1] is not None]

        # Calculate correlation if we have enough data points
        if len(ticker_returns) >= 3 and len(spy_returns) >= 3:
            # Simple Pearson correlation
            n = min(len(ticker_returns), len(spy_returns))
            t_ret = ticker_returns[:n]
            s_ret = spy_returns[:n]

            t_mean = sum(t_ret) / n
            s_mean = sum(s_ret) / n

            numerator = sum((t - t_mean) * (s - s_mean) for t, s in zip(t_ret, s_ret))
            t_var = sum((t - t_mean) ** 2 for t in t_ret)
            s_var = sum((s - s_mean) ** 2 for s in s_ret)

            if t_var > 0 and s_var > 0:
                result["spy_correlation_5d"] = numerator / ((t_var * s_var) ** 0.5)

        return result

    db_result = await db_query(query)
    result.update(db_result)

    return result


# Ticker info cache to avoid repeated API calls
_ticker_info_cache: Dict[str, Dict[str, Any]] = {}
_uw_client: Optional[UnusualWhalesClient] = None


def _get_uw_client() -> UnusualWhalesClient:
    """Get or create UW client."""
    global _uw_client
    if _uw_client is None:
        api_key = os.getenv("UW_API_KEY")
        base_url = os.getenv("UW_BASE_URL", "https://api.unusualwhales.com")
        if not api_key:
            logger.warning("UW_API_KEY not set, ticker info lookups will fail")
            return None  # type: ignore
        _uw_client = UnusualWhalesClient(base_url=base_url, token=api_key)
    return _uw_client


async def get_ticker_info(ticker: str) -> Dict[str, Any]:
    """Fetch ticker info from UW API with caching.

    Uses both /api/stock/{ticker}/info and /api/earnings/{ticker} endpoints
    to maximize data coverage.
    """
    # Return cached if available
    if ticker in _ticker_info_cache:
        return _ticker_info_cache[ticker]

    # Initialize empty cache entry
    cache_entry: Dict[str, Any] = {
        "sector": None,
        "next_earnings_date": None,
        "announce_time": None,
        "last_earnings_date": None,
    }

    client = _get_uw_client()
    if client is None:
        _ticker_info_cache[ticker] = cache_entry
        return cache_entry

    from orion.unusualwhales.types import UNSET

    # Try /api/stock/{ticker}/info first
    try:
        response = await asyncio.to_thread(
            get_info.sync,
            ticker=ticker,
            client=client,
        )

        if isinstance(response, TickerInfoResults) and response.data:
            info = response.data

            cache_entry["sector"] = (
                info.sector.value if info.sector and not isinstance(info.sector, type(UNSET)) else None
            )
            cache_entry["next_earnings_date"] = (
                info.next_earnings_date
                if info.next_earnings_date and not isinstance(info.next_earnings_date, type(UNSET))
                else None
            )
            cache_entry["announce_time"] = (
                info.announce_time.value
                if info.announce_time and not isinstance(info.announce_time, type(UNSET))
                else None
            )

    except Exception as e:
        logger.debug(f"Failed to fetch ticker info for {ticker}: {e}")

    # If sector still None, try /api/earnings/{ticker} for sector from past earnings
    if cache_entry["sector"] is None:
        try:
            from orion.unusualwhales.api.earnings import get_ticker_earnings
            from orion.unusualwhales.models.earnings_results import EarningsResults

            earnings_response = await asyncio.to_thread(
                get_ticker_earnings.sync,
                ticker=ticker,
                client=client,
            )

            if isinstance(earnings_response, EarningsResults) and earnings_response.data:
                # Get most recent earnings
                sorted_earnings = sorted(
                    [e for e in earnings_response.data if e.report_date], key=lambda x: x.report_date, reverse=True
                )

                if sorted_earnings:
                    latest = sorted_earnings[0]
                    # Get sector from earnings data (it's a string in the response)
                    if latest.sector and not isinstance(latest.sector, type(UNSET)):
                        cache_entry["sector"] = str(latest.sector)
                    # Store last earnings date for is_post_earnings calculation
                    if latest.report_date and not isinstance(latest.report_date, type(UNSET)):
                        cache_entry["last_earnings_date"] = latest.report_date

        except Exception as e:
            logger.debug(f"Failed to fetch earnings for {ticker}: {e}")

    _ticker_info_cache[ticker] = cache_entry
    return cache_entry


async def get_sector_info(ticker: str) -> Dict[str, Optional[str]]:
    """Get sector from static mapping (reliable) or fallback to UW API.

    Uses static SECTOR_MAPPING for common tickers to ensure reliability.
    Falls back to UW API for less common tickers.
    """
    # Check static mapping first (reliable, no API calls)
    sector = SECTOR_MAPPING.get(ticker)
    if sector:
        return {"sector": sector, "industry": None}

    # Fallback to UW API for unknown tickers
    try:
        info = await get_ticker_info(ticker)
        return {"sector": info.get("sector"), "industry": None}
    except Exception:
        return {"sector": "Other", "industry": None}


async def get_earnings_proximity(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get days to/from earnings based on UW ticker info.

    Uses next_earnings_date if available, otherwise uses last_earnings_date
    from earnings history to determine if we're in post-earnings period.
    """
    info = await get_ticker_info(ticker)
    next_earnings = info.get("next_earnings_date")
    last_earnings = info.get("last_earnings_date")
    entry_date = entry_ts.date()

    # If we have next earnings date
    if next_earnings:
        days_diff = (next_earnings - entry_date).days

        if days_diff < 0:
            # next_earnings is in the past (stale data)
            return {"days_to_earnings": None, "is_post_earnings": True}
        else:
            return {"days_to_earnings": days_diff, "is_post_earnings": False}

    # If we only have last earnings date, calculate post-earnings window
    if last_earnings:
        # Parse if string
        if isinstance(last_earnings, str):
            from dateutil.parser import parse as parse_date

            try:
                last_earnings = parse_date(last_earnings).date()
            except Exception:
                return {"days_to_earnings": None, "is_post_earnings": None}

        days_since = (entry_date - last_earnings).days
        # Consider "post-earnings" window as 5 trading days after earnings
        is_post = 0 <= days_since <= 7
        return {"days_to_earnings": None, "is_post_earnings": is_post}

    return {"days_to_earnings": None, "is_post_earnings": None}


def get_price_at_offset_minutes(prices: List[Dict[str, Any]], entry_ts: datetime, minutes: int) -> Optional[float]:
    """Get price at a specific minutes offset from entry (for 0DTE)."""
    target_ts = entry_ts + timedelta(minutes=minutes)
    closest = None
    min_diff = timedelta(minutes=5)  # Accept within 5 min window for short timeframes

    for p in prices:
        diff = abs(p["ts"] - target_ts)
        if diff < min_diff:
            min_diff = diff
            closest = p["price"]
    return closest


def get_price_at_offset_days(prices: List[Dict[str, Any]], entry_ts: datetime, days: int) -> Optional[float]:
    """Get price at a specific days offset from entry (for SWING/POSITION)."""
    target_ts = entry_ts + timedelta(days=days)
    closest = None
    min_diff = timedelta(hours=4)  # Accept within 4h window for longer timeframes

    for p in prices:
        diff = abs(p["ts"] - target_ts)
        if diff < min_diff:
            min_diff = diff
            closest = p["price"]
    return closest


def get_price_at_offset(prices: List[Dict[str, Any]], entry_ts: datetime, hours: int) -> Optional[float]:
    """Get price at a specific time offset from entry."""
    target_ts = entry_ts + timedelta(hours=hours)
    closest = None
    min_diff = timedelta(minutes=30)  # Accept within 30 min window

    for p in prices:
        diff = abs(p["ts"] - target_ts)
        if diff < min_diff:
            min_diff = diff
            closest = p["price"]
    return closest


def calculate_volatility(prices: List[float]) -> Optional[float]:
    """Calculate price volatility (std dev of returns)."""
    if len(prices) < 3:
        return None
    try:
        returns = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
        return float(np.std(returns)) if returns else None
    except (ZeroDivisionError, ValueError):
        return None


async def label_entry(entry: Any) -> Optional[Dict[str, Any]]:
    """Label a single entry with comprehensive price target tracking."""
    option_chain = entry.option_chain
    entry_price = entry.option_price
    entry_ts = entry.flow_ts_utc
    ticker = entry.ticker
    put_call = entry.put_call

    if entry_price <= 0:
        return None

    prices = await get_subsequent_prices(option_chain, entry_ts)
    expiry = parse_expiry(entry.expiry)
    dte = calculate_dte(entry_ts, expiry)

    # Base label with nulls
    label = {
        "event_id": entry.event_id,
        "ticker": ticker,
        "option_chain": option_chain,
        "trade_type": classify_trade_type(dte),
        "entry_ts": entry_ts,
        "entry_option_price": entry_price,
        "expiry": expiry,
        "dte": dte,
        "premium_usd": entry.premium_usd,
        "aggressor": entry.aggressor,
        "put_call": put_call,
        "is_sweep": entry.is_sweep == "true" if isinstance(entry.is_sweep, str) else entry.is_sweep,
    }

    if not prices:
        # No subsequent data - still lookup entry features
        gex_data = await get_gex_at_entry(ticker, entry_ts)
        tide_data = await get_market_tide_before_entry(entry_ts, minutes=30)
        max_pain_dist = await get_max_pain_distance(ticker, expiry, entry_ts)
        iv_rank = await get_iv_rank_at_entry(ticker, entry_ts)

        for key in [
            "max_price_reached",
            "max_price_ts",
            "max_return_pct",
            "min_price_reached",
            "min_price_ts",
            "max_drawdown_pct",
            "hit_50_pct_ts",
            "hit_75_pct_ts",
            "hit_100_pct_ts",
            "hit_150_pct_ts",
            "hit_stop_20_pct_ts",
            "first_exit_ts",
            "first_exit_return_pct",
            "time_to_max_seconds",
            "time_to_50_pct_seconds",
            "time_to_75_pct_seconds",
            "time_to_100_pct_seconds",
            "time_to_150_pct_seconds",
            "time_to_stop_seconds",
            "holding_period_seconds",
            "max_drawdown_before_target",
            "min_distance_to_stop_pct",
            "price_volatility",
            "price_at_1h",
            "price_at_2h",
            "price_at_4h",
            "return_at_1h",
            "return_at_2h",
            "return_at_4h",
            "price_at_15m",
            "return_at_15m",
            "price_at_30m",
            "return_at_30m",
            "price_at_8h",
            "return_at_8h",
            "price_at_1d",
            "return_at_1d",
            "price_at_2d",
            "return_at_2d",
            "price_at_3d",
            "return_at_3d",
            "price_at_1w",
            "return_at_1w",
            "opposing_flow_count",
            "opposing_premium_total",
            "sentiment_shift_ts",
            "optimal_exit_return",
            "optimal_exit_ts",
            "final_return_pct",
        ]:
            label[key] = None
        label["first_exit_type"] = "NONE"
        label["last_tracked_ts"] = entry_ts
        label["gex_at_entry"] = gex_data["gex"]
        label["vex_at_entry"] = gex_data["vex"]
        label["market_tide_30m"] = tide_data["net_premium"]
        label["market_tide_direction"] = tide_data["direction"]
        label["max_pain_distance_pct"] = max_pain_dist
        label["iv_rank_at_entry"] = iv_rank
        # Darkpool metrics for all bucket windows
        dp_metrics = await get_darkpool_metrics(ticker, entry_ts)
        label["darkpool_volume_1h"] = dp_metrics.get("darkpool_1h")
        label["darkpool_15m"] = dp_metrics.get("darkpool_15m")
        label["darkpool_30m"] = dp_metrics.get("darkpool_30m")
        label["darkpool_4h"] = dp_metrics.get("darkpool_4h")
        label["darkpool_1d"] = dp_metrics.get("darkpool_1d")
        label["darkpool_3d"] = dp_metrics.get("darkpool_3d")
        label["darkpool_1w"] = dp_metrics.get("darkpool_1w")
        label["darkpool_2w"] = dp_metrics.get("darkpool_2w")
        label["darkpool_4w"] = dp_metrics.get("darkpool_4w")
        # P1 ML Features
        rvol = await get_rvol_metrics(ticker, entry_ts)
        label["rvol_1h"] = rvol.get("rvol_1h")
        label["rvol_daily"] = rvol.get("rvol_daily")
        label["rvol_weekly"] = rvol.get("rvol_weekly")
        label["rvol_30m"] = rvol.get("rvol_30m")
        label["rvol_3d"] = rvol.get("rvol_3d")
        label["rvol_monthly"] = rvol.get("rvol_monthly")
        flow_agg = await get_flow_aggression(ticker, entry_ts)
        label["ask_side_ratio"] = flow_agg.get("ask_side_ratio")
        label["sweep_ratio_1h"] = flow_agg.get("sweep_ratio_1h")
        label["same_ticker_premium_1h"] = flow_agg.get("same_ticker_premium_1h")
        label["institutional_flow_1w"] = await get_institutional_flow_1w(ticker, entry_ts)
        # Trade bucket based on DTE
        if dte <= 1:
            label["trade_bucket"] = "0DTE"
        elif dte <= 5:
            label["trade_bucket"] = "SWING"
        elif dte <= 30:
            label["trade_bucket"] = "POSITION"
        else:
            label["trade_bucket"] = "LEAP"
        # Phase 1 bucket-specific features
        phase1 = await get_phase1_bucket_features(ticker, entry_ts, dte)
        label["minutes_to_close"] = phase1.get("minutes_to_close")
        label["overnight_gap_pct"] = phase1.get("overnight_gap_pct")
        label["price_change_5d_prior"] = phase1.get("price_change_5d_prior")
        label["earnings_in_dte_window"] = phase1.get("earnings_in_dte_window")
        label["vwap_distance_pct"] = phase1.get("vwap_distance_pct")
        # Regime at entry
        regime_data = await get_regime_at_entry(entry_ts)
        label["trend_regime_at_entry"] = regime_data.get("trend_regime")
        label["vol_regime_at_entry"] = regime_data.get("vol_regime")
        label["risk_regime_at_entry"] = regime_data.get("risk_regime")
        label["session_regime_at_entry"] = regime_data.get("session_regime")
        label["vix_at_entry"] = regime_data.get("vix_at_entry")
        label["vix_regime_at_entry"] = regime_data.get("vix_regime")
        # New ML features - set to None for early return
        label["iv_at_entry"] = None
        label["iv_at_1h"] = None
        label["iv_change_1h_pct"] = None
        label["underlying_at_entry"] = None
        label["underlying_at_1h"] = None
        label["underlying_change_1h_pct"] = None
        label["delta_at_entry"] = None
        label["gamma_at_entry"] = None
        label["volume_at_entry"] = None
        label["open_interest_at_entry"] = None
        time_features = get_entry_time_features(entry_ts)
        label.update(time_features)
        label["days_to_earnings"] = None
        label["is_post_earnings"] = None
        label["sector"] = None
        label["industry"] = None
        return label

    # Track core metrics
    max_price = entry_price
    max_price_ts = entry_ts
    min_price = entry_price
    min_price_ts = entry_ts

    hit_50_ts = None
    hit_75_ts = None
    hit_100_ts = None
    hit_150_ts = None
    hit_stop_ts = None

    first_exit_type = "NONE"
    first_exit_ts = None
    first_exit_return = None

    # Track drawdown before hitting target
    max_drawdown_before_50 = 0.0
    min_distance_to_stop = 20.0  # Start at stop level

    all_prices = [entry_price] + [p["price"] for p in prices]

    for p in prices:
        price = p["price"]
        ts = p["ts"]
        return_pct = ((price - entry_price) / entry_price) * 100

        # Track extremes
        if price > max_price:
            max_price = price
            max_price_ts = ts
        if price < min_price:
            min_price = price
            min_price_ts = ts

        # Track drawdown before 50% target
        if hit_50_ts is None and return_pct < 0:
            max_drawdown_before_50 = min(max_drawdown_before_50, return_pct)

        # Track how close to stop
        if return_pct < 0 and return_pct > -20:
            distance_to_stop = 20.0 + return_pct  # Distance from -20%
            min_distance_to_stop = min(min_distance_to_stop, distance_to_stop)

        # Check targets
        if return_pct >= 50 and hit_50_ts is None:
            hit_50_ts = ts
            if first_exit_ts is None:
                first_exit_type = "TARGET_50"
                first_exit_ts = ts
                first_exit_return = return_pct

        if return_pct >= 75 and hit_75_ts is None:
            hit_75_ts = ts
            if first_exit_ts is None:
                first_exit_type = "TARGET_75"
                first_exit_ts = ts
                first_exit_return = return_pct

        if return_pct >= 100 and hit_100_ts is None:
            hit_100_ts = ts
            if first_exit_ts is None:
                first_exit_type = "TARGET_100"
                first_exit_ts = ts
                first_exit_return = return_pct

        if return_pct >= 150 and hit_150_ts is None:
            hit_150_ts = ts
            if first_exit_ts is None:
                first_exit_type = "TARGET_150"
                first_exit_ts = ts
                first_exit_return = return_pct

        if return_pct <= -20 and hit_stop_ts is None:
            hit_stop_ts = ts
            if first_exit_ts is None:
                first_exit_type = "STOP_20"
                first_exit_ts = ts
                first_exit_return = return_pct

    # Calculate derived metrics
    max_return_pct = ((max_price - entry_price) / entry_price) * 100
    max_drawdown_pct = ((min_price - entry_price) / entry_price) * 100
    last_tracked_ts = prices[-1]["ts"]
    final_return_pct = ((prices[-1]["price"] - entry_price) / entry_price) * 100

    # Timing metrics
    time_to_max = int((max_price_ts - entry_ts).total_seconds()) if max_price_ts != entry_ts else None
    time_to_50 = int((hit_50_ts - entry_ts).total_seconds()) if hit_50_ts else None
    time_to_75 = int((hit_75_ts - entry_ts).total_seconds()) if hit_75_ts else None
    time_to_100 = int((hit_100_ts - entry_ts).total_seconds()) if hit_100_ts else None
    time_to_150 = int((hit_150_ts - entry_ts).total_seconds()) if hit_150_ts else None
    time_to_stop = int((hit_stop_ts - entry_ts).total_seconds()) if hit_stop_ts else None
    holding_period = int((last_tracked_ts - entry_ts).total_seconds())

    # Price at checkpoints (original 1h/2h/4h)
    price_1h = get_price_at_offset(prices, entry_ts, 1)
    price_2h = get_price_at_offset(prices, entry_ts, 2)
    price_4h = get_price_at_offset(prices, entry_ts, 4)

    return_1h = ((price_1h - entry_price) / entry_price * 100) if price_1h else None
    return_2h = ((price_2h - entry_price) / entry_price * 100) if price_2h else None
    return_4h = ((price_4h - entry_price) / entry_price * 100) if price_4h else None

    # 0DTE checkpoints (5m, 10m, 15m, 30m) - ultra-short for 0DTE
    price_5m = get_price_at_offset_minutes(prices, entry_ts, 5)
    price_10m = get_price_at_offset_minutes(prices, entry_ts, 10)
    price_15m = get_price_at_offset_minutes(prices, entry_ts, 15)
    price_30m = get_price_at_offset_minutes(prices, entry_ts, 30)
    return_5m = ((price_5m - entry_price) / entry_price * 100) if price_5m else None
    return_10m = ((price_10m - entry_price) / entry_price * 100) if price_10m else None
    return_15m = ((price_15m - entry_price) / entry_price * 100) if price_15m else None
    return_30m = ((price_30m - entry_price) / entry_price * 100) if price_30m else None

    # SWING/POSITION checkpoints (8h, 1d, 2d, 3d, 1w)
    price_8h = get_price_at_offset(prices, entry_ts, 8)
    price_1d = get_price_at_offset_days(prices, entry_ts, 1)
    price_2d = get_price_at_offset_days(prices, entry_ts, 2)
    price_3d = get_price_at_offset_days(prices, entry_ts, 3)
    price_1w = get_price_at_offset_days(prices, entry_ts, 7)

    return_8h = ((price_8h - entry_price) / entry_price * 100) if price_8h else None
    return_1d = ((price_1d - entry_price) / entry_price * 100) if price_1d else None
    return_2d = ((price_2d - entry_price) / entry_price * 100) if price_2d else None
    return_3d = ((price_3d - entry_price) / entry_price * 100) if price_3d else None
    return_1w = ((price_1w - entry_price) / entry_price * 100) if price_1w else None

    # SWING EOD checkpoint - price at end of entry day (4pm ET = 20:00 UTC)
    eod_ts = entry_ts.replace(hour=20, minute=0, second=0, microsecond=0)
    price_eod = (
        get_price_at_offset_minutes(prices, entry_ts, int((eod_ts - entry_ts).total_seconds() / 60))
        if entry_ts < eod_ts
        else None
    )
    return_eod = ((price_eod - entry_price) / entry_price * 100) if price_eod else None

    # Volatility
    volatility = calculate_volatility(all_prices)

    # Opposing flow
    opposing = await get_opposing_flow(ticker, put_call, entry_ts, last_tracked_ts)

    # Build full label
    label.update(
        {
            "max_price_reached": max_price,
            "max_price_ts": max_price_ts,
            "max_return_pct": max_return_pct,
            "min_price_reached": min_price,
            "min_price_ts": min_price_ts,
            "max_drawdown_pct": max_drawdown_pct,
            "hit_50_pct_ts": hit_50_ts,
            "hit_75_pct_ts": hit_75_ts,
            "hit_100_pct_ts": hit_100_ts,
            "hit_150_pct_ts": hit_150_ts,
            "hit_stop_20_pct_ts": hit_stop_ts,
            "first_exit_type": first_exit_type,
            "first_exit_ts": first_exit_ts,
            "first_exit_return_pct": first_exit_return,
            "last_tracked_ts": last_tracked_ts,
            # Timing
            "time_to_max_seconds": time_to_max,
            "time_to_50_pct_seconds": time_to_50,
            "time_to_75_pct_seconds": time_to_75,
            "time_to_100_pct_seconds": time_to_100,
            "time_to_150_pct_seconds": time_to_150,
            "time_to_stop_seconds": time_to_stop,
            "holding_period_seconds": holding_period,
            # Price path
            "max_drawdown_before_target": max_drawdown_before_50 if max_drawdown_before_50 < 0 else None,
            "min_distance_to_stop_pct": min_distance_to_stop if min_distance_to_stop < 20 else None,
            "price_volatility": volatility,
            # Original checkpoints (1h/2h/4h)
            "price_at_1h": price_1h,
            "price_at_2h": price_2h,
            "price_at_4h": price_4h,
            "return_at_1h": return_1h,
            "return_at_2h": return_2h,
            "return_at_4h": return_4h,
            # 0DTE checkpoints (5m/10m/15m/30m)
            "price_at_5m": price_5m,
            "return_at_5m": return_5m,
            "price_at_10m": price_10m,
            "return_at_10m": return_10m,
            "price_at_15m": price_15m,
            "return_at_15m": return_15m,
            "price_at_30m": price_30m,
            "return_at_30m": return_30m,
            # SWING/POSITION checkpoints (8h/1d/2d/3d/1w)
            "price_at_8h": price_8h,
            "return_at_8h": return_8h,
            "price_at_1d": price_1d,
            "return_at_1d": return_1d,
            "price_at_2d": price_2d,
            "return_at_2d": return_2d,
            "price_at_3d": price_3d,
            "return_at_3d": return_3d,
            "price_at_1w": price_1w,
            "return_at_1w": return_1w,
            # SWING EOD checkpoint
            "price_at_eod": price_eod,
            "return_at_eod": return_eod,
            # Context
            "opposing_flow_count": opposing["count"],
            "opposing_premium_total": opposing["premium"],
            "sentiment_shift_ts": None,
            # Exit quality
            "optimal_exit_return": max_return_pct,
            "optimal_exit_ts": max_price_ts,
            "final_return_pct": final_return_pct,
        }
    )

    # Lookup entry features from feature tables
    gex_data = await get_gex_at_entry(ticker, entry_ts)
    tide_data = await get_market_tide_before_entry(entry_ts, minutes=30)
    max_pain_dist = await get_max_pain_distance(ticker, expiry, entry_ts)
    iv_rank = await get_iv_rank_at_entry(ticker, entry_ts)

    # Darkpool metrics for all bucket windows
    dp_metrics = await get_darkpool_metrics(ticker, entry_ts)
    label.update(
        {
            "gex_at_entry": gex_data["gex"],
            "vex_at_entry": gex_data["vex"],
            "market_tide_30m": tide_data["net_premium"],
            "market_tide_direction": tide_data["direction"],
            "max_pain_distance_pct": max_pain_dist,
            "iv_rank_at_entry": iv_rank,
            "darkpool_volume_1h": dp_metrics.get("darkpool_1h"),
            "darkpool_15m": dp_metrics.get("darkpool_15m"),
            "darkpool_30m": dp_metrics.get("darkpool_30m"),
            "darkpool_4h": dp_metrics.get("darkpool_4h"),
            "darkpool_1d": dp_metrics.get("darkpool_1d"),
            "darkpool_3d": dp_metrics.get("darkpool_3d"),
            "darkpool_1w": dp_metrics.get("darkpool_1w"),
            "darkpool_2w": dp_metrics.get("darkpool_2w"),
            "darkpool_4w": dp_metrics.get("darkpool_4w"),
        }
    )

    # P1 ML Features: relative volume and flow aggression
    rvol = await get_rvol_metrics(ticker, entry_ts)
    flow_agg = await get_flow_aggression(ticker, entry_ts)
    institutional_flow = await get_institutional_flow_1w(ticker, entry_ts)
    # Trade bucket based on DTE
    if dte <= 1:
        trade_bucket = "0DTE"
    elif dte <= 5:
        trade_bucket = "SWING"
    elif dte <= 30:
        trade_bucket = "POSITION"
    else:
        trade_bucket = "LEAP"

    label.update(
        {
            "rvol_1h": rvol.get("rvol_1h"),
            "rvol_daily": rvol.get("rvol_daily"),
            "rvol_weekly": rvol.get("rvol_weekly"),
            "rvol_30m": rvol.get("rvol_30m"),
            "rvol_3d": rvol.get("rvol_3d"),
            "rvol_monthly": rvol.get("rvol_monthly"),
            "ask_side_ratio": flow_agg.get("ask_side_ratio"),
            "sweep_ratio_1h": flow_agg.get("sweep_ratio_1h"),
            "same_ticker_premium_1h": flow_agg.get("same_ticker_premium_1h"),
            "institutional_flow_1w": institutional_flow,
            "trade_bucket": trade_bucket,
        }
    )

    # Phase 1 bucket-specific features
    phase1 = await get_phase1_bucket_features(ticker, entry_ts, dte)
    label.update(
        {
            "minutes_to_close": phase1.get("minutes_to_close"),
            "overnight_gap_pct": phase1.get("overnight_gap_pct"),
            "price_change_5d_prior": phase1.get("price_change_5d_prior"),
            "earnings_in_dte_window": phase1.get("earnings_in_dte_window"),
            "vwap_distance_pct": phase1.get("vwap_distance_pct"),
        }
    )

    # P2 features: OI change momentum and IV vs HV ratio
    p2 = await get_p2_features(ticker, option_chain, entry_ts)
    iv_at_entry = label.get("iv_at_entry")
    hv_30d = p2.get("hv_30d")
    iv_vs_hv = (iv_at_entry / hv_30d) if iv_at_entry and hv_30d and hv_30d > 0 else None
    label.update(
        {
            "oi_change_1d": p2.get("oi_change_1d"),
            "oi_change_pct": p2.get("oi_change_pct"),
            "iv_vs_hv_ratio": iv_vs_hv,
        }
    )

    # P3 features: 52w high distance, spread detection
    p3 = await get_p3_features(ticker, option_chain, expiry, entry_ts)
    label.update(
        {
            "high_52w_distance_pct": p3.get("high_52w_distance_pct"),
            "is_spread_leg": p3.get("is_spread_leg"),
            "same_expiry_trades_1h": p3.get("same_expiry_trades_1h"),
        }
    )

    # Sector flow and correlation features
    sector_corr = await get_sector_correlation_features(ticker, entry_ts)
    label.update(
        {
            "sector_net_premium_1h": sector_corr.get("sector_net_premium_1h"),
            "sector_flow_direction": sector_corr.get("sector_flow_direction"),
            "spy_correlation_5d": sector_corr.get("spy_correlation_5d"),
            "spy_return_1h": sector_corr.get("spy_return_1h"),
        }
    )

    # Lookup regime at entry
    regime_data = await get_regime_at_entry(entry_ts)
    label.update(
        {
            "trend_regime_at_entry": regime_data.get("trend_regime"),
            "vol_regime_at_entry": regime_data.get("vol_regime"),
            "risk_regime_at_entry": regime_data.get("risk_regime"),
            "session_regime_at_entry": regime_data.get("session_regime"),
            "vix_at_entry": regime_data.get("vix_at_entry"),
            "vix_regime_at_entry": regime_data.get("vix_regime"),
        }
    )

    # New ML features - Time features
    time_features = get_entry_time_features(entry_ts)
    label.update(time_features)

    # New ML features - Greeks and volume from flow
    greeks_data = await get_flow_greeks(entry.event_id)
    label.update(
        {
            "delta_at_entry": greeks_data.get("delta"),
            "gamma_at_entry": greeks_data.get("gamma"),
            "volume_at_entry": greeks_data.get("volume"),
            "open_interest_at_entry": greeks_data.get("open_interest"),
        }
    )

    # New ML features - Underlying price
    underlying_entry = await get_underlying_price_at_entry(ticker, entry_ts)
    underlying_1h = await get_underlying_price_at_offset(ticker, entry_ts, 1)
    underlying_change = None
    if underlying_entry and underlying_1h and underlying_entry > 0:
        underlying_change = ((underlying_1h - underlying_entry) / underlying_entry) * 100
    label.update(
        {
            "underlying_at_entry": underlying_entry,
            "underlying_at_1h": underlying_1h,
            "underlying_change_1h_pct": underlying_change,
        }
    )

    # New ML features - IV change (use IV from flow data)
    # Note: IV from silver_iv_rank often unavailable, so we use IV from flow
    iv_entry = greeks_data.get("iv")
    # For IV change, we'd need subsequent flow data - set to None for now
    label.update(
        {
            "iv_at_entry": iv_entry,
            "iv_at_1h": None,  # Would require looking up subsequent flow
            "iv_change_1h_pct": None,
        }
    )

    # New ML features - Sector/Industry (placeholder for now)
    sector_info = await get_sector_info(ticker)
    label.update(sector_info)

    # New ML features - Earnings (placeholder for now)
    earnings_info = await get_earnings_proximity(ticker, entry_ts)
    label.update(earnings_info)

    return label


async def persist_labels(labels: List[Dict[str, Any]]) -> int:
    """Persist labeled records to database."""
    if not labels:
        return 0

    async def write(session: Any) -> None:
        stmt = text(
            """
            INSERT INTO price_target_labels (
                event_id, ticker, option_chain, trade_type,
                entry_ts, entry_option_price, expiry, dte,
                premium_usd, aggressor, put_call, is_sweep,
                max_price_reached, max_price_ts, max_return_pct,
                min_price_reached, min_price_ts, max_drawdown_pct,
                hit_50_pct_ts, hit_75_pct_ts, hit_100_pct_ts, hit_150_pct_ts,
                hit_stop_20_pct_ts,
                first_exit_type, first_exit_ts, first_exit_return_pct,
                last_tracked_ts,
                time_to_max_seconds, time_to_50_pct_seconds,
                time_to_75_pct_seconds, time_to_100_pct_seconds, time_to_150_pct_seconds,
                time_to_stop_seconds, holding_period_seconds,
                max_drawdown_before_target, min_distance_to_stop_pct, price_volatility,
                price_at_1h, price_at_2h, price_at_4h,
                return_at_1h, return_at_2h, return_at_4h,
                price_at_5m, return_at_5m, price_at_10m, return_at_10m,
                price_at_15m, return_at_15m, price_at_30m, return_at_30m,
                price_at_8h, return_at_8h, price_at_eod, return_at_eod,
                price_at_1d, return_at_1d, price_at_2d, return_at_2d,
                price_at_3d, return_at_3d, price_at_1w, return_at_1w,
                opposing_flow_count, opposing_premium_total, sentiment_shift_ts,
                optimal_exit_return, optimal_exit_ts, final_return_pct,
                gex_at_entry, vex_at_entry, market_tide_30m, market_tide_direction,
                max_pain_distance_pct, iv_rank_at_entry, darkpool_volume_1h,
                darkpool_15m, darkpool_30m, darkpool_4h, darkpool_1d, darkpool_3d,
                darkpool_1w, darkpool_2w, darkpool_4w,
                trend_regime_at_entry, vol_regime_at_entry, risk_regime_at_entry,
                session_regime_at_entry, vix_at_entry, vix_regime_at_entry,
                iv_at_entry, iv_at_1h, iv_change_1h_pct,
                underlying_at_entry, underlying_at_1h, underlying_change_1h_pct,
                delta_at_entry, gamma_at_entry, volume_at_entry, open_interest_at_entry,
                entry_hour, entry_session, entry_day_of_week,
                days_to_earnings, is_post_earnings, sector, industry,
                rvol_1h, rvol_daily, rvol_weekly, rvol_30m, rvol_3d, rvol_monthly,
                ask_side_ratio, sweep_ratio_1h,
                same_ticker_premium_1h, institutional_flow_1w, trade_bucket,
                minutes_to_close, overnight_gap_pct, price_change_5d_prior,
                earnings_in_dte_window, vwap_distance_pct,
                oi_change_1d, oi_change_pct, iv_vs_hv_ratio,
                high_52w_distance_pct, is_spread_leg, same_expiry_trades_1h,
                sector_net_premium_1h, sector_flow_direction, spy_correlation_5d, spy_return_1h
            ) VALUES (
                :event_id, :ticker, :option_chain, :trade_type,
                :entry_ts, :entry_option_price, :expiry, :dte,
                :premium_usd, :aggressor, :put_call, :is_sweep,
                :max_price_reached, :max_price_ts, :max_return_pct,
                :min_price_reached, :min_price_ts, :max_drawdown_pct,
                :hit_50_pct_ts, :hit_75_pct_ts, :hit_100_pct_ts, :hit_150_pct_ts,
                :hit_stop_20_pct_ts,
                :first_exit_type, :first_exit_ts, :first_exit_return_pct,
                :last_tracked_ts,
                :time_to_max_seconds, :time_to_50_pct_seconds,
                :time_to_75_pct_seconds, :time_to_100_pct_seconds, :time_to_150_pct_seconds,
                :time_to_stop_seconds, :holding_period_seconds,
                :max_drawdown_before_target, :min_distance_to_stop_pct, :price_volatility,
                :price_at_1h, :price_at_2h, :price_at_4h,
                :return_at_1h, :return_at_2h, :return_at_4h,
                :price_at_5m, :return_at_5m, :price_at_10m, :return_at_10m,
                :price_at_15m, :return_at_15m, :price_at_30m, :return_at_30m,
                :price_at_8h, :return_at_8h, :price_at_eod, :return_at_eod,
                :price_at_1d, :return_at_1d, :price_at_2d, :return_at_2d,
                :price_at_3d, :return_at_3d, :price_at_1w, :return_at_1w,
                :opposing_flow_count, :opposing_premium_total, :sentiment_shift_ts,
                :optimal_exit_return, :optimal_exit_ts, :final_return_pct,
                :gex_at_entry, :vex_at_entry, :market_tide_30m, :market_tide_direction,
                :max_pain_distance_pct, :iv_rank_at_entry, :darkpool_volume_1h,
                :darkpool_15m, :darkpool_30m, :darkpool_4h, :darkpool_1d, :darkpool_3d,
                :darkpool_1w, :darkpool_2w, :darkpool_4w,
                :trend_regime_at_entry, :vol_regime_at_entry, :risk_regime_at_entry,
                :session_regime_at_entry, :vix_at_entry, :vix_regime_at_entry,
                :iv_at_entry, :iv_at_1h, :iv_change_1h_pct,
                :underlying_at_entry, :underlying_at_1h, :underlying_change_1h_pct,
                :delta_at_entry, :gamma_at_entry, :volume_at_entry, :open_interest_at_entry,
                :entry_hour, :entry_session, :entry_day_of_week,
                :days_to_earnings, :is_post_earnings, :sector, :industry,
                :rvol_1h, :rvol_daily, :rvol_weekly, :rvol_30m, :rvol_3d, :rvol_monthly,
                :ask_side_ratio, :sweep_ratio_1h,
                :same_ticker_premium_1h, :institutional_flow_1w, :trade_bucket,
                :minutes_to_close, :overnight_gap_pct, :price_change_5d_prior,
                :earnings_in_dte_window, :vwap_distance_pct,
                :oi_change_1d, :oi_change_pct, :iv_vs_hv_ratio,
                :high_52w_distance_pct, :is_spread_leg, :same_expiry_trades_1h,
                :sector_net_premium_1h, :sector_flow_direction, :spy_correlation_5d, :spy_return_1h
            )
            ON CONFLICT (event_id) DO NOTHING
        """
        )

        for label in labels:
            await session.execute(stmt, label)

    await db_write(write)
    return len(labels)


async def run_labeling_loop(shutdown_event: asyncio.Event) -> None:
    """Main labeling loop."""
    await init_db()

    logger.info("Starting Price Target Labeling Service (v2 - comprehensive metrics)...")

    total_labeled = 0

    while not shutdown_event.is_set():
        try:
            entries = await get_entry_signals(BATCH_SIZE)

            if entries:
                labels = []
                for entry in entries:
                    label = await label_entry(entry)
                    if label:
                        labels.append(label)

                if labels:
                    count = await persist_labels(labels)
                    total_labeled += count

                    hit_50 = sum(1 for label in labels if label.get("hit_50_pct_ts"))
                    stopped = sum(1 for label in labels if label.get("hit_stop_20_pct_ts"))
                    avg_holding = sum(label.get("holding_period_seconds", 0) or 0 for label in labels) / len(labels)

                    logger.info(
                        f"Labeled {count} entries | Total: {total_labeled} | "
                        f"Hit50: {hit_50} | Stopped: {stopped} | AvgHold: {avg_holding/60:.0f}min",
                        extra={
                            "event_type": "BATCH_LABELED",
                            "batch_size": count,
                            "hit_50_pct": hit_50,
                            "stopped_out": stopped,
                            "avg_holding_min": avg_holding / 60,
                        },
                    )
            else:
                logger.debug("No unlabeled entries found, waiting...")

        except Exception as e:
            logger.error(f"Labeling error: {e}", exc_info=True)
            await asyncio.sleep(5)
            continue

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass

    logger.info(f"Price Target Labeling stopped. Total: {total_labeled}")


async def backfill_missing_features(batch_size: int = 100) -> int:
    """Backfill missing ML features for existing records.

    Finds records with NULL values in key feature columns and updates them.
    Can be run periodically to catch any gaps.
    """
    await init_db()
    total_updated = 0

    async def get_records_to_backfill(session: Any) -> List[Dict[str, Any]]:
        """Get records with missing features."""
        stmt = text(
            """
            SELECT event_id, ticker, option_chain, entry_ts, expiry, dte
            FROM price_target_labels
            WHERE rvol_1h IS NULL
               OR sector_net_premium_1h IS NULL
               OR high_52w_distance_pct IS NULL
               OR spy_correlation_5d IS NULL
            ORDER BY entry_ts DESC
            LIMIT :batch_size
        """
        )
        result = await session.execute(stmt, {"batch_size": batch_size})
        rows = result.fetchall()
        return [
            {
                "event_id": r[0],
                "ticker": r[1],
                "option_chain": r[2],
                "entry_ts": r[3],
                "expiry": r[4],
                "dte": r[5],
            }
            for r in rows
        ]

    records = await db_query(get_records_to_backfill)

    while records:
        logger.info(f"Backfilling {len(records)} records...")

        for record in records:
            try:
                ticker = record["ticker"]
                option_chain = record["option_chain"]
                entry_ts = record["entry_ts"]
                expiry = record["expiry"]
                dte = record["dte"] or 0

                # Calculate all missing features
                rvol = await get_rvol_metrics(ticker, entry_ts)
                flow_agg = await get_flow_aggression(ticker, entry_ts)
                dp_metrics = await get_darkpool_metrics(ticker, entry_ts)
                phase1 = await get_phase1_bucket_features(ticker, entry_ts, dte)
                p2 = await get_p2_features(ticker, option_chain, entry_ts)
                p3 = await get_p3_features(ticker, option_chain, expiry, entry_ts)
                sector_corr = await get_sector_correlation_features(ticker, entry_ts)

                # Build update dict (iv_vs_hv computed in p2 features)
                updates = {
                    "rvol_1h": rvol.get("rvol_1h"),
                    "rvol_daily": rvol.get("rvol_daily"),
                    "rvol_weekly": rvol.get("rvol_weekly"),
                    "rvol_30m": rvol.get("rvol_30m"),
                    "rvol_3d": rvol.get("rvol_3d"),
                    "rvol_monthly": rvol.get("rvol_monthly"),
                    "ask_side_ratio": flow_agg.get("ask_side_ratio"),
                    "sweep_ratio_1h": flow_agg.get("sweep_ratio_1h"),
                    "same_ticker_premium_1h": flow_agg.get("same_ticker_premium_1h"),
                    "darkpool_15m": dp_metrics.get("darkpool_15m"),
                    "darkpool_3d": dp_metrics.get("darkpool_3d"),
                    "darkpool_4w": dp_metrics.get("darkpool_4w"),
                    "minutes_to_close": phase1.get("minutes_to_close"),
                    "overnight_gap_pct": phase1.get("overnight_gap_pct"),
                    "price_change_5d_prior": phase1.get("price_change_5d_prior"),
                    "vwap_distance_pct": phase1.get("vwap_distance_pct"),
                    "oi_change_1d": p2.get("oi_change_1d"),
                    "oi_change_pct": p2.get("oi_change_pct"),
                    "high_52w_distance_pct": p3.get("high_52w_distance_pct"),
                    "is_spread_leg": p3.get("is_spread_leg"),
                    "same_expiry_trades_1h": p3.get("same_expiry_trades_1h"),
                    "sector_net_premium_1h": sector_corr.get("sector_net_premium_1h"),
                    "sector_flow_direction": sector_corr.get("sector_flow_direction"),
                    "spy_correlation_5d": sector_corr.get("spy_correlation_5d"),
                    "spy_return_1h": sector_corr.get("spy_return_1h"),
                }

                # Update record - bind loop variables to avoid B023
                event_id = record["event_id"]

                async def update_record(session: Any, upd: Dict[str, Any] = updates, eid: str = event_id) -> None:
                    update_stmt = text(
                        """
                        UPDATE price_target_labels SET
                            rvol_1h = :rvol_1h,
                            rvol_daily = :rvol_daily,
                            rvol_weekly = :rvol_weekly,
                            rvol_30m = :rvol_30m,
                            rvol_3d = :rvol_3d,
                            rvol_monthly = :rvol_monthly,
                            ask_side_ratio = :ask_side_ratio,
                            sweep_ratio_1h = :sweep_ratio_1h,
                            same_ticker_premium_1h = :same_ticker_premium_1h,
                            darkpool_15m = :darkpool_15m,
                            darkpool_3d = :darkpool_3d,
                            darkpool_4w = :darkpool_4w,
                            minutes_to_close = :minutes_to_close,
                            overnight_gap_pct = :overnight_gap_pct,
                            price_change_5d_prior = :price_change_5d_prior,
                            vwap_distance_pct = :vwap_distance_pct,
                            oi_change_1d = :oi_change_1d,
                            oi_change_pct = :oi_change_pct,
                            high_52w_distance_pct = :high_52w_distance_pct,
                            is_spread_leg = :is_spread_leg,
                            same_expiry_trades_1h = :same_expiry_trades_1h,
                            sector_net_premium_1h = :sector_net_premium_1h,
                            sector_flow_direction = :sector_flow_direction,
                            spy_correlation_5d = :spy_correlation_5d,
                            spy_return_1h = :spy_return_1h
                        WHERE event_id = :event_id
                    """
                    )
                    await session.execute(update_stmt, {**upd, "event_id": eid})

                await db_write(update_record)
                total_updated += 1

                if total_updated % 50 == 0:
                    logger.info(f"Backfilled {total_updated} records so far...")

            except Exception as e:
                logger.error(f"Error backfilling {record['event_id']}: {e}")
                continue

        # Get next batch
        records = await db_query(get_records_to_backfill)

    logger.info(f"Backfill complete. Updated {total_updated} records.")
    return total_updated


async def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Price Target Labeler")
    parser.add_argument("--backfill", action="store_true", help="Backfill missing features for existing records")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for backfill (default: 100)")
    args = parser.parse_args()

    if args.backfill:
        logger.info("Starting backfill mode...")
        await backfill_missing_features(batch_size=args.batch_size)
        return

    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def handle_signal(sig: int) -> None:
        logger.info(f"Received signal {sig}. Shutting down...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    await run_labeling_loop(shutdown_event)


if __name__ == "__main__":
    asyncio.run(main())
