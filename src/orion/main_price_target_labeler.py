"""
Price Target Labeling Service.

Tracks option prices over time with comprehensive metrics for ML exit optimization.
"""

import asyncio
import math
import os
import signal
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from scipy.stats import norm

load_dotenv()


from orion.clients.heber_reader import HeberReader
from orion.labeler import (
    BATCH_SIZE,
    RISK_FREE_RATE,
)
from orion.shared.logger import setup_struct_logger
from orion.unusualwhales.api.stock import get_info
from orion.unusualwhales.client import UnusualWhalesClient
from orion.unusualwhales.models.ticker_info_results import TickerInfoResults

logger = setup_struct_logger("orion.price_target")
_heber_reader = HeberReader()

_PRICE_TARGET_FALLBACK_COUNTS: dict[str, int] = defaultdict(int)


def _legacy_label_pipeline_control() -> tuple[bool, str, str]:
    specific_key = "ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER"
    specific_raw = os.getenv(specific_key)
    if specific_raw is not None:
        enabled = specific_raw.lower() not in {"0", "false", "no", "off", "n"}
        return enabled, specific_key, specific_raw

    global_key = "ORION_ENABLE_LEGACY_LABEL_PIPELINES"
    global_raw = os.getenv(global_key, "true")
    enabled = global_raw.lower() not in {"0", "false", "no", "off", "n"}
    return enabled, global_key, global_raw


def _legacy_label_pipelines_enabled() -> bool:
    enabled, _, _ = _legacy_label_pipeline_control()
    return enabled


def _record_price_target_fallback(feature_name: str, error: Exception | None = None, **context: Any) -> None:
    _PRICE_TARGET_FALLBACK_COUNTS[feature_name] += 1
    payload: dict[str, Any] = {
        "feature": feature_name,
        "fallback_count": _PRICE_TARGET_FALLBACK_COUNTS[feature_name],
    }
    if error is not None:
        payload["error"] = str(error)
    payload.update(context)
    logger.warning("Price-target fallback applied", extra={"event_type": "PRICE_TARGET_FALLBACK", **payload})


# Static sector mapping for reliable feature calculation (avoids unreliable API calls)
SECTOR_MAPPING: dict[str, str] = {
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
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,  # noqa: N803
) -> float | None:
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


def calculate_black_scholes_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float | None:  # noqa: N803
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


def calculate_iv_rank_from_history(current_iv: float, iv_history: list[float]) -> float | None:
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


def parse_expiry(expiry_str: str | None) -> datetime | None:
    """Parse expiry string to datetime."""
    if not expiry_str:
        return None
    try:
        return datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        return None


def calculate_dte(flow_ts: datetime, expiry: datetime | None) -> int | None:
    """Calculate days to expiry."""
    if not expiry:
        return None
    dte = (expiry.date() - flow_ts.date()).days
    return max(0, dte)


def classify_trade_type(dte: int | None) -> str:
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


async def get_entry_signals(limit: int = BATCH_SIZE) -> list[Any]:
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

    try:
        heber_entries = await _get_entry_signals_from_heber(limit)
    except Exception as e:
        _record_price_target_fallback("entry_signals_heber", e)
        heber_entries = []

    if heber_entries:
        return heber_entries

    return []


async def get_subsequent_prices(option_chain: str, entry_ts: datetime) -> list[dict[str, Any]]:
    """Get all subsequent prices for an option chain after entry."""
    heber_prices = _get_subsequent_prices_from_heber(option_chain, entry_ts)
    if heber_prices is not None:
        return heber_prices

    return []


def _build_backfill_cursor_clause(
    after_entry_ts: datetime | None,
    after_event_id: str | None,
    params: dict[str, Any],
) -> str:
    """Build keyset cursor predicate for backfill candidate queries."""
    if after_entry_ts is not None and after_event_id is not None:
        params["after_entry_ts"] = after_entry_ts
        params["after_event_id"] = after_event_id
        return """
          AND (entry_ts > :after_entry_ts
               OR (entry_ts = :after_entry_ts AND event_id > :after_event_id))
        """
    if after_entry_ts is not None:
        params["after_entry_ts"] = after_entry_ts
        return """
          AND entry_ts >= :after_entry_ts
        """
    return ""


async def get_velocity_backfill_candidates(
    limit: int = 1000,
    after_entry_ts: datetime | None = None,
    after_event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Legacy no-op; local velocity backfill candidate discovery is decommissioned."""
    _ = (limit, after_entry_ts, after_event_id)
    logger.warning(
        "Velocity backfill candidate lookup is decommissioned; local label backfill is disabled",
        extra={
            "event_type": "DEPRECATED_PIPELINE_DISABLED",
            "pipeline": "orion.main_price_target_labeler",
            "operation": "get_velocity_backfill_candidates",
        },
    )
    return []


async def get_checkpoint_backfill_candidates(
    limit: int = 1000,
    after_entry_ts: datetime | None = None,
    after_event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Legacy no-op; local checkpoint backfill candidate discovery is decommissioned."""
    _ = (limit, after_entry_ts, after_event_id)
    logger.warning(
        "Checkpoint backfill candidate lookup is decommissioned; local label backfill is disabled",
        extra={
            "event_type": "DEPRECATED_PIPELINE_DISABLED",
            "pipeline": "orion.main_price_target_labeler",
            "operation": "get_checkpoint_backfill_candidates",
        },
    )
    return []


def _pick_first_existing_column(df: pd.DataFrame, columns: list[str]) -> str | None:
    for column in columns:
        if column in df.columns:
            return column
    return None


def _extract_entry_signal_row(row: pd.Series) -> SimpleNamespace | None:
    def _first(keys: list[str]) -> Any:
        for key in keys:
            if key in row and pd.notna(row[key]):
                return row[key]
        return None

    event_id = _first(["event_id", "source_event_id", "id"])
    flow_ts = _coerce_dt_utc(_first(["flow_ts_utc", "ts_event", "timestamp", "created_at"]))
    option_chain = _first(["option_chain", "option_symbol", "contract"])
    option_price = _coerce_float(_first(["option_price", "price"]))
    premium = _coerce_float(_first(["premium_usd", "premium"]))
    aggressor = _first(["aggressor", "side"])
    expiry = _first(["expiry"])
    put_call = _first(["put_call", "type"])
    is_sweep = _first(["is_sweep", "sweep"])

    ticker = _first(["ticker", "symbol", "underlying"])
    if ticker is None and "instrument_key" in row and pd.notna(row["instrument_key"]):
        ticker = str(row["instrument_key"]).split(":")[-1]

    if event_id is None or flow_ts is None or option_chain is None or ticker is None:
        return None
    if option_price is None or option_price <= 0:
        return None
    if premium is None:
        return None
    if str(aggressor).upper() not in {"ASK", "BID"}:
        return None

    sweep = _is_truthy(is_sweep)
    if sweep and premium < 50000:
        return None
    if not sweep and premium < 100000:
        return None

    expiry_dt = parse_expiry(str(expiry)) if expiry is not None else None
    dte = calculate_dte(flow_ts, expiry_dt)
    age = datetime.now(UTC) - flow_ts
    min_age = _min_entry_age_for_dte(dte)
    if age < min_age:
        return None

    return SimpleNamespace(
        event_id=str(event_id),
        ticker=str(ticker),
        flow_ts_utc=flow_ts,
        expiry=str(expiry) if expiry is not None else None,
        option_chain=str(option_chain),
        option_price=option_price,
        premium_usd=premium,
        aggressor=str(aggressor).upper(),
        put_call=str(put_call) if put_call is not None else None,
        is_sweep=is_sweep,
    )


def _min_entry_age_for_dte(dte: int | None) -> timedelta:
    if dte == 0:
        return timedelta(minutes=15)
    if dte is not None and 1 <= dte <= 3:
        return timedelta(minutes=30)
    if dte is not None and 4 <= dte <= 14:
        return timedelta(hours=1)
    if dte is not None and dte >= 15:
        return timedelta(hours=2)
    return timedelta(minutes=30)


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "t"}
    return False


async def _get_labeled_price_target_event_ids(event_ids: list[str]) -> set[str]:
    """Legacy no-op; local labeled-event lookup is decommissioned."""
    if not event_ids:
        return set()
    logger.warning(
        "Labeled event lookup is decommissioned; local label storage is disabled",
        extra={
            "event_type": "DEPRECATED_PIPELINE_DISABLED",
            "pipeline": "orion.main_price_target_labeler",
            "operation": "get_labeled_price_target_event_ids",
        },
    )
    return set()


async def _get_entry_signals_from_heber(limit: int) -> list[Any]:
    now_utc = datetime.now(UTC)
    flow_df = _heber_reader.read_flow(
        asof_time=now_utc,
        start_time=now_utc - timedelta(days=365),
    )
    if flow_df.empty:
        return []

    candidates: list[SimpleNamespace] = []
    for _, row in flow_df.iterrows():
        normalized = _extract_entry_signal_row(row)
        if normalized is not None:
            candidates.append(normalized)

    if not candidates:
        return []

    candidates.sort(key=lambda item: item.flow_ts_utc)
    labeled_ids = await _get_labeled_price_target_event_ids([item.event_id for item in candidates])
    return [item for item in candidates if item.event_id not in labeled_ids][:limit]


def _get_subsequent_prices_from_heber(option_chain: str, entry_ts: datetime) -> list[dict[str, Any]] | None:
    try:
        flow_df = _heber_reader.read_flow(
            asof_time=datetime.now(UTC),
            start_time=entry_ts,
        )
    except Exception as e:
        _record_price_target_fallback("subsequent_prices_heber", e, option_chain=option_chain)
        return None

    if flow_df.empty:
        return []

    chain_col = _pick_first_existing_column(flow_df, ["option_chain", "option_symbol", "contract"])
    ts_col = _pick_first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
    price_col = _pick_first_existing_column(flow_df, ["option_price", "price"])
    if chain_col is None or ts_col is None or price_col is None:
        return None

    prices: list[dict[str, Any]] = []
    for _, row in flow_df.iterrows():
        if str(row.get(chain_col)) != option_chain:
            continue
        ts = _coerce_dt_utc(row.get(ts_col))
        price = _coerce_float(row.get(price_col))
        if ts is None or ts <= entry_ts:
            continue
        if price is None or price <= 0:
            continue
        prices.append({"price": price, "ts": ts})

    prices.sort(key=lambda item: item["ts"])
    return prices


async def get_real_checkpoint_prices(event_id: str) -> dict[str, dict[str, float | None]]:
    """Get real option prices and Greeks from Heber event-level checkpoint data."""
    return _get_real_checkpoint_prices_from_heber(event_id)


def _get_real_checkpoint_prices_from_heber(event_id: str) -> dict[str, dict[str, float | None]]:
    event_id_str = str(event_id)
    try:
        flow_df = _heber_reader.read_flow(
            asof_time=datetime.now(UTC),
            start_time=datetime.now(UTC) - timedelta(days=365),
        )
    except Exception as e:
        _record_price_target_fallback("checkpoint_quote_heber_lookup", e, event_id=event_id_str)
        return {}

    if flow_df.empty:
        return {}

    event_col = _pick_first_existing_column(flow_df, ["event_id", "source_event_id", "id"])
    checkpoint_col = _pick_first_existing_column(flow_df, ["checkpoint", "price_checkpoint", "checkpoint_name"])
    mid_col = _pick_first_existing_column(flow_df, ["mid_price", "option_mid_price", "quote_mid"])
    last_col = _pick_first_existing_column(flow_df, ["last_trade_price", "option_price", "price"])
    if event_col is None or checkpoint_col is None or (mid_col is None and last_col is None):
        return {}

    filtered = flow_df[flow_df[event_col].astype(str) == event_id_str]
    if filtered.empty:
        return {}

    delta_col = _pick_first_existing_column(filtered, ["delta", "delta_alpaca"])
    gamma_col = _pick_first_existing_column(filtered, ["gamma", "gamma_alpaca"])
    theta_col = _pick_first_existing_column(filtered, ["theta", "theta_alpaca"])
    vega_col = _pick_first_existing_column(filtered, ["vega", "vega_alpaca"])
    iv_col = _pick_first_existing_column(filtered, ["iv", "iv_alpaca", "implied_volatility"])

    data: dict[str, dict[str, float | None]] = {}

    def _clean_float(value: Any) -> float | None:
        parsed = _coerce_float(value)
        if parsed is None or pd.isna(parsed):
            return None
        return parsed

    for _, row in filtered.iterrows():
        checkpoint = str(row.get(checkpoint_col) or "").strip()
        if not checkpoint:
            continue

        mid_price = _clean_float(row.get(mid_col)) if mid_col is not None else None
        last_price = _clean_float(row.get(last_col)) if last_col is not None else None
        price = mid_price if mid_price is not None else last_price

        data[checkpoint] = {
            "price": price,
            "delta": _clean_float(row.get(delta_col)) if delta_col is not None else None,
            "gamma": _clean_float(row.get(gamma_col)) if gamma_col is not None else None,
            "theta": _clean_float(row.get(theta_col)) if theta_col is not None else None,
            "vega": _clean_float(row.get(vega_col)) if vega_col is not None else None,
            "iv": _clean_float(row.get(iv_col)) if iv_col is not None else None,
        }

    return data


async def get_opposing_flow(ticker: str, put_call: str, entry_ts: datetime, end_ts: datetime) -> dict[str, Any]:
    """Get opposing flow during holding period."""
    heber_result = _get_opposing_flow_from_heber(ticker, put_call, entry_ts, end_ts)
    if heber_result is not None:
        return heber_result

    return {"count": 0, "premium": 0.0}


def _get_opposing_flow_from_heber(
    ticker: str,
    put_call: str,
    entry_ts: datetime,
    end_ts: datetime,
) -> dict[str, Any] | None:
    opposing_type = "P" if str(put_call).upper() == "C" else "C"
    entry_utc = _coerce_dt_utc(entry_ts)
    end_utc = _coerce_dt_utc(end_ts)
    if entry_utc is None or end_utc is None:
        return None

    try:
        flow_df = _heber_reader.read_flow(
            asof_time=end_utc,
            start_time=entry_utc,
        )
    except Exception as e:
        _record_price_target_fallback("opposing_flow_heber_lookup", e, ticker=ticker)
        return None

    if flow_df.empty:
        return None

    ts_col = _pick_first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
    ticker_col = _pick_first_existing_column(flow_df, ["ticker", "symbol", "underlying", "instrument_key"])
    put_call_col = _pick_first_existing_column(flow_df, ["put_call", "type"])
    premium_col = _pick_first_existing_column(flow_df, ["premium_usd", "premium"])
    sweep_col = _pick_first_existing_column(flow_df, ["is_sweep", "sweep"])
    aggressor_col = _pick_first_existing_column(flow_df, ["aggressor", "side"])
    if ts_col is None or ticker_col is None or put_call_col is None or premium_col is None:
        return None

    ts_series = pd.to_datetime(flow_df[ts_col], utc=True, errors="coerce")
    premium_series = pd.to_numeric(flow_df[premium_col], errors="coerce")
    ticker_series = flow_df[ticker_col].astype(str).str.upper()
    put_call_series = flow_df[put_call_col].astype(str).str.upper()
    if sweep_col is not None:
        sweep_series = flow_df[sweep_col].map(_is_truthy)
    else:
        sweep_series = pd.Series([False] * len(flow_df))
    if aggressor_col is not None:
        aggressor_series = flow_df[aggressor_col].astype(str).str.upper()
    else:
        aggressor_series = pd.Series([""] * len(flow_df))

    temp_df = pd.DataFrame(
        {
            "ts": ts_series,
            "ticker": ticker_series,
            "put_call": put_call_series,
            "premium": premium_series,
            "is_sweep": sweep_series,
            "aggressor": aggressor_series,
        }
    ).dropna(subset=["ts", "premium"])

    if temp_df.empty:
        return None

    ticker_upper = str(ticker).upper()
    filtered = temp_df[
        (temp_df["ticker"] == ticker_upper)
        & (temp_df["put_call"] == opposing_type)
        & (temp_df["ts"] > entry_utc)
        & (temp_df["ts"] <= end_utc)
        & (temp_df["is_sweep"])
        & (temp_df["aggressor"] == "ASK")
    ]
    if filtered.empty:
        return {"count": 0, "premium": 0}

    return {
        "count": int(len(filtered)),
        "premium": float(filtered["premium"].sum()),
    }


async def get_gex_at_entry(ticker: str, entry_ts: datetime) -> dict[str, Any]:
    """Get the closest GEX values before entry time."""
    heber_result = _get_gex_at_entry_from_heber(ticker, entry_ts)
    if heber_result is not None:
        return heber_result

    return {"gex": None, "vex": None}


async def get_gex_rolling_averages(ticker: str, entry_ts: datetime, days: int = 20) -> dict[str, float | None]:
    """Get rolling average GEX/VEX values prior to entry time."""
    heber_result = _get_gex_rolling_averages_from_heber(ticker, entry_ts, days=days)
    if heber_result is not None:
        return heber_result

    return {"gex_rolling_avg": None, "vex_rolling_avg": None}


def _get_gex_rolling_averages_from_heber(
    ticker: str,
    entry_ts: datetime,
    days: int = 20,
) -> dict[str, float | None] | None:
    lookback_days = max(days, 1)
    try:
        df = _heber_reader.read_greek_exposure(
            symbols=[ticker],
            asof_time=entry_ts,
            start_time=entry_ts - timedelta(days=lookback_days),
        )
    except Exception as e:
        _record_price_target_fallback("gex_rolling_avg_heber_lookup", e, ticker=ticker)
        return None

    if df.empty:
        return None

    ts_col = _pick_first_existing_column(df, ["ts_utc", "ts_event", "timestamp", "created_at"])
    gex_col = _pick_first_existing_column(df, ["gex_oi", "gex"])
    vex_col = _pick_first_existing_column(df, ["vex_oi", "vex"])
    if ts_col is None or gex_col is None:
        return None

    entry_utc = _coerce_dt_utc(entry_ts)
    if entry_utc is None:
        return None

    start_utc = entry_utc - timedelta(days=lookback_days)
    ts_series = pd.to_datetime(df[ts_col], utc=True, errors="coerce")
    window = df[(ts_series > start_utc) & (ts_series <= entry_utc)]
    if window.empty:
        return None

    gex_avg = pd.to_numeric(window[gex_col], errors="coerce").mean()
    vex_avg = pd.to_numeric(window[vex_col], errors="coerce").mean() if vex_col is not None else None

    gex_val = float(gex_avg) if pd.notna(gex_avg) else None
    vex_val = float(vex_avg) if vex_avg is not None and pd.notna(vex_avg) else None
    if gex_val is None and vex_val is None:
        return None

    return {"gex_rolling_avg": gex_val, "vex_rolling_avg": vex_val}


async def get_window_features_at_entry(ticker: str, entry_ts: datetime) -> dict[str, Any]:
    """Build 1h/1d/1w window features directly from Heber silver datasets."""
    entry_utc = _coerce_dt_utc(entry_ts)
    if entry_utc is None:
        return {}

    longest_window = timedelta(weeks=1)
    try:
        flow_df = _heber_reader.read_flow(
            symbols=[ticker],
            asof_time=entry_utc,
            start_time=entry_utc - longest_window,
        )
        darkpool_df = _heber_reader.read_darkpool(
            symbols=[ticker],
            asof_time=entry_utc,
            start_time=entry_utc - longest_window,
        )
    except Exception as e:
        _record_price_target_fallback("window_features_heber_lookup", e, ticker=ticker)
        return {}

    flow_frame = _normalize_window_flow_frame(flow_df, ticker=ticker)
    if flow_frame.empty:
        return {}

    darkpool_frame = _normalize_window_darkpool_frame(darkpool_df, ticker=ticker)
    period_windows = {
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "1w": timedelta(weeks=1),
    }

    features_by_period: dict[str, Any] = {}
    for period, window_size in period_windows.items():
        start_utc = entry_utc - window_size
        flow_window = flow_frame[(flow_frame["ts"] > start_utc) & (flow_frame["ts"] <= entry_utc)]
        if flow_window.empty:
            continue

        premium_series = flow_window["premium"]
        put_call_series = flow_window["put_call"]
        sweep_series = flow_window["is_sweep"]
        aggressor_series = flow_window["aggressor"]

        call_premium = float(premium_series[put_call_series == "C"].sum())
        put_premium = float(premium_series[put_call_series == "P"].sum())
        total_premium = float(premium_series.sum())
        flow_count = int(len(flow_window))
        sweep_count = int(sweep_series.sum())
        ask_side = int((aggressor_series == "ASK").sum())

        darkpool_window = darkpool_frame[(darkpool_frame["ts"] > start_utc) & (darkpool_frame["ts"] <= entry_utc)]
        dp_volume = float(darkpool_window["size"].sum()) if not darkpool_window.empty else 0.0
        dp_count = int(len(darkpool_window))

        features_by_period[period] = {
            "flow_count": flow_count,
            "sweep_count": sweep_count,
            "dp_count": dp_count,
            "call_premium": call_premium,
            "put_premium": put_premium,
            "total_premium": total_premium,
            "dp_volume": dp_volume,
            "call_put_ratio": call_premium / put_premium if put_premium > 0 else None,
            "call_put_imbalance": (call_premium - put_premium) / total_premium if total_premium > 0 else 0.0,
            "sweep_ratio": sweep_count / flow_count if flow_count > 0 else 0.0,
            "ask_ratio": ask_side / flow_count if flow_count > 0 else 0.5,
        }

    return features_by_period


def _normalize_window_flow_frame(flow_df: pd.DataFrame, *, ticker: str) -> pd.DataFrame:
    if flow_df.empty:
        return pd.DataFrame(columns=["ts", "premium", "put_call", "is_sweep", "aggressor"])

    ts_col = _pick_first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
    premium_col = _pick_first_existing_column(flow_df, ["premium_usd", "premium"])
    put_call_col = _pick_first_existing_column(flow_df, ["put_call", "type", "option_type", "right"])
    ticker_col = _pick_first_existing_column(flow_df, ["ticker", "symbol", "underlying", "instrument_key"])
    sweep_col = _pick_first_existing_column(flow_df, ["is_sweep", "sweep"])
    aggressor_col = _pick_first_existing_column(flow_df, ["aggressor", "side"])
    if ts_col is None or premium_col is None:
        return pd.DataFrame(columns=["ts", "premium", "put_call", "is_sweep", "aggressor"])

    ts_series = pd.to_datetime(flow_df[ts_col], utc=True, errors="coerce")
    premium_series = pd.to_numeric(flow_df[premium_col], errors="coerce")

    if put_call_col is not None:
        put_call_series = flow_df[put_call_col].map(_normalize_put_call)
    else:
        put_call_series = pd.Series(index=flow_df.index, dtype=object)
    if sweep_col is not None:
        sweep_series = flow_df[sweep_col].map(_is_truthy)
    else:
        sweep_series = pd.Series([False] * len(flow_df))
    if aggressor_col is not None:
        aggressor_series = flow_df[aggressor_col].astype(str).str.upper()
    else:
        aggressor_series = pd.Series([""] * len(flow_df))

    if ticker_col is not None:
        ticker_series = flow_df[ticker_col].astype(str).str.upper().str.split(":").str[-1]
    else:
        ticker_series = pd.Series([str(ticker).upper()] * len(flow_df))

    frame = pd.DataFrame(
        {
            "ts": ts_series,
            "ticker": ticker_series,
            "premium": premium_series,
            "put_call": put_call_series,
            "is_sweep": sweep_series,
            "aggressor": aggressor_series,
        }
    ).dropna(subset=["ts", "premium"])

    if frame.empty:
        return frame

    return frame[frame["ticker"] == str(ticker).upper()]


def _normalize_window_darkpool_frame(darkpool_df: pd.DataFrame, *, ticker: str) -> pd.DataFrame:
    if darkpool_df.empty:
        return pd.DataFrame(columns=["ts", "size"])

    ts_col = _pick_first_existing_column(darkpool_df, ["dark_ts_utc", "ts_utc", "ts_event", "timestamp", "created_at"])
    size_col = _pick_first_existing_column(darkpool_df, ["size_shares", "size", "shares", "volume"])
    ticker_col = _pick_first_existing_column(darkpool_df, ["ticker", "symbol", "underlying", "instrument_key"])
    if ts_col is None or size_col is None:
        return pd.DataFrame(columns=["ts", "size"])

    ts_series = pd.to_datetime(darkpool_df[ts_col], utc=True, errors="coerce")
    size_series = pd.to_numeric(darkpool_df[size_col], errors="coerce").fillna(0.0)
    if ticker_col is not None:
        ticker_series = darkpool_df[ticker_col].astype(str).str.upper().str.split(":").str[-1]
    else:
        ticker_series = pd.Series([str(ticker).upper()] * len(darkpool_df))

    frame = pd.DataFrame(
        {
            "ts": ts_series,
            "ticker": ticker_series,
            "size": size_series,
        }
    ).dropna(subset=["ts"])
    if frame.empty:
        return frame
    return frame[frame["ticker"] == str(ticker).upper()]


async def get_market_tide_before_entry(entry_ts: datetime, minutes: int = 30) -> dict[str, Any]:
    """Get market tide sum for the period before entry."""
    heber_result = _get_market_tide_before_entry_from_heber(entry_ts, minutes)
    if heber_result is not None:
        return heber_result

    return {"net_premium": None, "direction": None}


def _get_gex_at_entry_from_heber(ticker: str, entry_ts: datetime) -> dict[str, Any] | None:
    try:
        df = _heber_reader.read_greek_exposure(
            symbols=[ticker],
            asof_time=entry_ts,
            start_time=entry_ts - timedelta(days=30),
        )
    except Exception as e:
        _record_price_target_fallback("gex_heber_lookup", e, ticker=ticker)
        return None

    if df.empty:
        return None

    ts_col = _pick_first_existing_column(df, ["ts_utc", "ts_event", "timestamp", "created_at"])
    gex_col = _pick_first_existing_column(df, ["gex_oi", "gex"])
    vex_col = _pick_first_existing_column(df, ["vex_oi", "vex"])
    if ts_col is None or gex_col is None:
        return None

    best_ts: datetime | None = None
    best_values: dict[str, Any] | None = None
    for _, row in df.iterrows():
        row_ts = _coerce_dt_utc(row.get(ts_col))
        if row_ts is None or row_ts > entry_ts:
            continue
        if best_ts is None or row_ts > best_ts:
            best_ts = row_ts
            best_values = {
                "gex": _coerce_float(row.get(gex_col)),
                "vex": _coerce_float(row.get(vex_col)) if vex_col is not None else None,
            }

    return best_values


def _get_market_tide_before_entry_from_heber(entry_ts: datetime, minutes: int = 30) -> dict[str, Any] | None:
    net_premium = _get_heber_market_tide_net_premium(entry_ts, minutes)
    if net_premium is None:
        return None

    return {"net_premium": net_premium, "direction": _market_tide_direction(net_premium)}


def _market_tide_direction(net_premium: float) -> str:
    return "BULLISH" if net_premium > 0 else "BEARISH" if net_premium < 0 else "NEUTRAL"


def _normalize_put_call(value: Any) -> str | None:
    if value is None:
        return None
    put_call = str(value).strip().upper()
    if put_call in {"C", "CALL", "CALLS"}:
        return "C"
    if put_call in {"P", "PUT", "PUTS"}:
        return "P"
    return None


def _sum_market_tide_from_dataframe(df: pd.DataFrame, start_ts: datetime, entry_ts: datetime) -> float | None:
    ts_col = _pick_first_existing_column(df, ["ts_utc", "flow_ts_utc", "ts_event", "timestamp", "created_at"])
    if ts_col is None:
        return None

    call_col = _pick_first_existing_column(df, ["net_call_premium"])
    put_col = _pick_first_existing_column(df, ["net_put_premium"])
    net_col = _pick_first_existing_column(df, ["net_premium"])
    premium_col = _pick_first_existing_column(df, ["premium_usd", "premium"])
    put_call_col = _pick_first_existing_column(df, ["put_call", "option_type", "right"])

    total_net = 0.0
    seen = False
    for _, row in df.iterrows():
        row_ts = _coerce_dt_utc(row.get(ts_col))
        if row_ts is None or row_ts <= start_ts or row_ts > entry_ts:
            continue

        if call_col is not None or put_col is not None:
            call_value = _coerce_float(row.get(call_col)) if call_col is not None else 0.0
            put_value = _coerce_float(row.get(put_col)) if put_col is not None else 0.0
            total_net += float(call_value or 0.0) + float(put_value or 0.0)
            seen = True
            continue

        if net_col is not None:
            net_value = _coerce_float(row.get(net_col))
            total_net += float(net_value or 0.0)
            seen = True
            continue

        if premium_col is None or put_call_col is None:
            return None

        premium = _coerce_float(row.get(premium_col))
        if premium is None or premium <= 0:
            continue
        put_call = _normalize_put_call(row.get(put_call_col))
        if put_call == "C":
            total_net += premium
            seen = True
        elif put_call == "P":
            total_net -= premium
            seen = True

    if not seen:
        return None
    return total_net


def _get_heber_market_tide_net_premium(entry_ts: datetime, minutes: int = 30) -> float | None:
    start_ts = entry_ts - timedelta(minutes=minutes)
    try:
        tide_df = _heber_reader.read_market_tide(
            asof_time=entry_ts,
            start_time=start_ts,
        )
        net_from_tide = _sum_market_tide_from_dataframe(tide_df, start_ts, entry_ts)
        if net_from_tide is not None:
            return net_from_tide
    except Exception as e:
        _record_price_target_fallback("market_tide_heber_lookup", e)

    try:
        flow_df = _heber_reader.read_flow(
            asof_time=entry_ts,
            start_time=start_ts,
        )
    except Exception as e:
        _record_price_target_fallback("market_tide_flow_fallback", e)
        return None

    return _sum_market_tide_from_dataframe(flow_df, start_ts, entry_ts)


def _map_vix_proxy_to_regime(vix_proxy: float) -> str:
    if vix_proxy > 30:
        return "EXTREME"
    if vix_proxy > 20:
        return "ELEVATED"
    if vix_proxy > 12:
        return "NORMAL"
    return "LOW"


def _get_heber_vix_proxy_snapshot_at_or_before(entry_ts: datetime) -> dict[str, Any] | None:
    """Best-effort Heber lookup for VIX proxy (VIXY) and prior-close delta."""
    ts_utc = entry_ts if entry_ts.tzinfo is not None else entry_ts.replace(tzinfo=UTC)
    try:
        bars = _heber_reader.read_bars(
            symbols=["VIXY"],
            asof_time=datetime.now(UTC),
            start_time=ts_utc - timedelta(days=7),
            end_time=ts_utc + timedelta(minutes=1),
        )
    except Exception as exc:
        _record_price_target_fallback("heber_vix_proxy_lookup", exc)
        return None

    if bars.empty:
        return None

    ts_col = next((col for col in ("ts_event", "bar_start_ts", "timestamp") if col in bars.columns), None)
    close_col = next((col for col in ("close", "c") if col in bars.columns), None)
    if ts_col is None or close_col is None:
        return None

    candidates: list[tuple[datetime, float]] = []
    for _, row in bars.iterrows():
        row_ts = _coerce_dt_utc(row.get(ts_col))
        row_close = _coerce_float(row.get(close_col))
        if row_ts is None or row_close is None:
            continue
        if row_ts <= ts_utc:
            candidates.append((row_ts, row_close))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    latest_close = candidates[0][1]
    prior_close = candidates[1][1] if len(candidates) > 1 else latest_close
    vix_1d_change = latest_close - prior_close

    return {
        "vix": latest_close,
        "vix_1d_change": vix_1d_change,
        "vix_regime": _map_vix_proxy_to_regime(latest_close),
    }


async def get_max_pain_distance(ticker: str, expiry_date: datetime | None, entry_ts: datetime) -> float | None:
    """Get distance to max pain at entry time."""
    if not expiry_date:
        return None

    heber_distance = _get_max_pain_distance_from_heber(ticker, expiry_date, entry_ts)
    return heber_distance


def _get_max_pain_distance_from_heber(
    ticker: str,
    expiry_date: Any,
    entry_ts: datetime,
) -> float | None:
    entry_date = entry_ts.date()
    expiry = expiry_date.date() if isinstance(expiry_date, datetime) else expiry_date
    try:
        max_pain_df = _heber_reader.read_max_pain(
            symbols=[ticker],
            asof_time=entry_ts,
            start_time=entry_ts - timedelta(days=365),
        )
    except Exception as e:
        _record_price_target_fallback("max_pain_heber_lookup", e, ticker=ticker)
        return None

    if max_pain_df.empty:
        return None

    expiry_col = _pick_first_existing_column(max_pain_df, ["expiry", "expiry_date", "expiration"])
    dist_col = _pick_first_existing_column(
        max_pain_df,
        ["distance_to_max_pain_pct", "max_pain_distance_pct", "distance_to_max_pain"],
    )
    ts_col = _pick_first_existing_column(max_pain_df, ["date", "ts_utc", "ts_event", "timestamp", "created_at"])
    if expiry_col is None or dist_col is None or ts_col is None:
        return None

    best_ts: datetime | None = None
    best_distance: float | None = None
    for _, row in max_pain_df.iterrows():
        row_expiry = _coerce_date(row.get(expiry_col))
        if row_expiry != expiry:
            continue

        row_ts = _coerce_dt_utc(row.get(ts_col))
        if row_ts is None:
            row_date = _coerce_date(row.get(ts_col))
            if row_date is None:
                continue
            row_ts = datetime.combine(row_date, datetime.min.time(), tzinfo=UTC)

        if row_ts.date() > entry_date:
            continue

        distance = _coerce_float(row.get(dist_col))
        if distance is None:
            continue

        if best_ts is None or row_ts > best_ts:
            best_ts = row_ts
            best_distance = distance

    return best_distance


async def get_iv_rank_at_entry(ticker: str, entry_ts: datetime) -> float | None:
    """Get IV rank at entry time with Heber-first sourcing."""
    heber_iv_rank = _get_iv_rank_from_heber(ticker, entry_ts)
    if heber_iv_rank is not None:
        return heber_iv_rank

    return _estimate_iv_rank_from_heber_flow(ticker, entry_ts)


def _estimate_iv_rank_from_heber_flow(ticker: str, target_ts: datetime) -> float | None:
    """Estimate IV rank from Heber flow IV history when IV-rank snapshots are unavailable."""
    target_utc = _coerce_dt_utc(target_ts)
    if target_utc is None:
        return None

    start_utc = target_utc - timedelta(days=30)

    try:
        flow_df = _heber_reader.read_flow(
            symbols=[ticker],
            asof_time=target_utc,
            start_time=start_utc,
        )
    except Exception as e:
        _record_price_target_fallback("iv_rank_heber_flow_estimate", e, ticker=ticker)
        return None

    if flow_df.empty:
        return None

    ts_col = _pick_first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
    iv_col = _pick_first_existing_column(flow_df, ["iv", "implied_volatility", "iv_alpaca"])
    if ts_col is None or iv_col is None:
        return None

    ticker_col = _pick_first_existing_column(flow_df, ["ticker", "symbol", "underlying", "instrument_key"])
    if ticker_col is not None:
        ticker_series = flow_df[ticker_col].astype(str).str.upper().str.split(":").str[-1]
    else:
        ticker_series = pd.Series([str(ticker).upper()] * len(flow_df))

    ts_series = pd.to_datetime(flow_df[ts_col], utc=True, errors="coerce")
    iv_series = pd.to_numeric(flow_df[iv_col], errors="coerce")

    temp_df = pd.DataFrame({"ts": ts_series, "ticker": ticker_series, "iv": iv_series}).dropna(subset=["ts", "iv"])
    if temp_df.empty:
        return None

    ticker_upper = str(ticker).upper()
    window = temp_df[(temp_df["ticker"] == ticker_upper) & (temp_df["ts"] > start_utc) & (temp_df["ts"] <= target_utc)]
    if window.empty:
        return None

    window = window.sort_values("ts")
    current_iv = float(window.iloc[-1]["iv"])
    min_iv = float(window["iv"].min())
    max_iv = float(window["iv"].max())
    if max_iv > min_iv:
        return min(100.0, max(0.0, (current_iv - min_iv) / (max_iv - min_iv) * 100.0))
    return 50.0


async def get_regime_at_entry(entry_ts: datetime) -> dict[str, Any]:
    """Get regime snapshot at entry time from Heber VIX proxy + market tide."""
    from orion.analysis.regime import MultiAxisRegimeDetector

    detector = MultiAxisRegimeDetector()

    vix_data = _get_heber_vix_proxy_snapshot_at_or_before(entry_ts) or {}
    tide_net = _get_heber_market_tide_net_premium(entry_ts, minutes=30)

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


def get_entry_time_features(entry_ts: datetime) -> dict[str, Any]:
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


async def get_underlying_price_at_entry(ticker: str, entry_ts: datetime) -> float | None:
    """Get underlying stock price at entry time from bars."""
    heber_price = _get_heber_close_at_or_before(ticker, entry_ts)
    if heber_price is not None:
        return heber_price

    return None


async def get_underlying_price_at_offset(ticker: str, entry_ts: datetime, hours: int) -> float | None:
    """Get underlying stock price at offset from entry."""
    target_ts = entry_ts + timedelta(hours=hours)
    heber_price = _get_heber_close_at_or_before(ticker, target_ts)
    if heber_price is not None:
        return heber_price

    return None


def _coerce_dt_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    return ts.date()


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_heber_close_at_or_before(ticker: str, target_ts: datetime) -> float | None:
    """Best-effort Heber bars lookup for the latest close at-or-before target time."""
    ts_utc = target_ts if target_ts.tzinfo is not None else target_ts.replace(tzinfo=UTC)
    try:
        bars = _heber_reader.read_bars(
            symbols=[ticker],
            asof_time=datetime.now(UTC),
            start_time=ts_utc - timedelta(days=7),
            end_time=ts_utc + timedelta(minutes=1),
        )
    except Exception as exc:
        _record_price_target_fallback("heber_bar_lookup", exc, ticker=ticker)
        return None

    if bars.empty:
        return None

    ts_col = next((col for col in ("ts_event", "bar_start_ts", "timestamp") if col in bars.columns), None)
    close_col = next((col for col in ("close", "c") if col in bars.columns), None)
    if ts_col is None or close_col is None:
        return None

    candidates: list[tuple[datetime, float]] = []
    for _, row in bars.iterrows():
        row_ts = _coerce_dt_utc(row.get(ts_col))
        row_close = _coerce_float(row.get(close_col))
        if row_ts is None or row_close is None:
            continue
        if row_ts <= ts_utc:
            candidates.append((row_ts, row_close))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


async def get_flow_greeks(event_id: str) -> dict[str, float | None]:
    """Get Greeks from stored values or Alpaca API, with Black-Scholes fallback.

    Priority:
    1. Stored Greeks from Heber flow alerts (captured at ingestion time)
    2. Alpaca API (for flows ingested before Greeks enrichment)
    3. Black-Scholes fallback (if Alpaca unavailable)
    """
    from orion.connectors.alpaca_option_greeks_connector import get_option_greeks

    result = {
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "rho": None,
        "volume": None,
        "open_interest": None,
        "iv": None,
        "iv_alpaca": None,
    }

    flow_data = _get_flow_greeks_from_heber(event_id)

    if not flow_data:
        return result

    result["volume"] = flow_data.get("volume")
    result["open_interest"] = flow_data.get("open_interest")
    result["iv"] = flow_data.get("iv")

    option_chain = flow_data.get("option_chain")

    # Priority 1: Use stored Greeks from Heber flow alerts (captured at ingestion)
    if flow_data.get("delta_stored") is not None:
        result["delta"] = flow_data.get("delta_stored")
        result["gamma"] = flow_data.get("gamma_stored")
        result["theta"] = flow_data.get("theta_stored")
        result["vega"] = flow_data.get("vega_stored")
        result["rho"] = flow_data.get("rho_stored")
        result["iv_alpaca"] = flow_data.get("iv_alpaca_stored")
        return result

    # Priority 2: Try Alpaca API (for flows ingested before Greeks enrichment)
    if option_chain:
        alpaca_greeks = await get_option_greeks(option_chain)
        if alpaca_greeks.get("delta") is not None:
            result["delta"] = alpaca_greeks.get("delta")
            result["gamma"] = alpaca_greeks.get("gamma")
            result["theta"] = alpaca_greeks.get("theta")
            result["vega"] = alpaca_greeks.get("vega")
            result["rho"] = alpaca_greeks.get("rho")
            result["iv_alpaca"] = alpaca_greeks.get("implied_volatility")
            return result

    # Fallback to Black-Scholes if Alpaca unavailable
    S = float(flow_data.get("underlying_price") or 0)  # noqa: N806
    K = float(flow_data.get("strike") or 0)  # noqa: N806
    iv = flow_data.get("iv")
    sigma = float(iv) if iv else 0
    put_call = flow_data.get("put_call", "C")
    expiry = flow_data.get("expiry")
    flow_ts = flow_data.get("flow_ts")

    # Calculate time to expiry in years
    T = 0.0  # noqa: N806
    if expiry and flow_ts:
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
            T = max(days_to_expiry / 365.0, 1 / 365.0)  # noqa: N806

    # Calculate delta and gamma using Black-Scholes
    if S > 0 and K > 0 and sigma > 0 and T > 0:
        result["delta"] = calculate_black_scholes_delta(S, K, T, RISK_FREE_RATE, sigma, put_call)
        result["gamma"] = calculate_black_scholes_gamma(S, K, T, RISK_FREE_RATE, sigma)

    return result


def _get_flow_greeks_from_heber(event_id: str) -> dict[str, Any] | None:
    event_id_str = str(event_id)
    try:
        flow_df = _heber_reader.read_flow(
            asof_time=datetime.now(UTC),
            start_time=datetime.now(UTC) - timedelta(days=365),
        )
    except Exception as e:
        _record_price_target_fallback("flow_greeks_heber_lookup", e, event_id=event_id_str)
        return None

    if flow_df.empty:
        return None

    event_col = _pick_first_existing_column(flow_df, ["event_id", "source_event_id", "id"])
    if event_col is None:
        return None

    event_series = flow_df[event_col].astype(str)
    filtered = flow_df[event_series == event_id_str]
    if filtered.empty:
        return None

    ts_col = _pick_first_existing_column(filtered, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
    if ts_col is not None:
        ts_series = pd.to_datetime(filtered[ts_col], utc=True, errors="coerce")
        filtered = filtered.assign(_ts=ts_series).sort_values("_ts")
        row = filtered.iloc[-1]
    else:
        row = filtered.iloc[-1]

    def _first_value(columns: list[str]) -> Any:
        for column in columns:
            if column in row and pd.notna(row[column]):
                return row[column]
        return None

    return {
        "volume": _coerce_float(_first_value(["volume_contract", "volume"])),
        "open_interest": _coerce_float(_first_value(["open_interest", "oi"])),
        "iv": _coerce_float(_first_value(["iv", "implied_volatility"])),
        "underlying_price": _coerce_float(_first_value(["underlying_price", "stock_price", "underlier_price"])),
        "strike": _coerce_float(_first_value(["strike", "strike_price"])),
        "put_call": _first_value(["put_call", "type"]),
        "expiry": _first_value(["expiry", "expiration", "exp_date"]),
        "flow_ts": _coerce_dt_utc(_first_value(["flow_ts_utc", "ts_event", "timestamp", "created_at"])),
        "option_chain": _first_value(["option_chain", "option_symbol", "contract", "contract_symbol"]),
        "delta_stored": _coerce_float(_first_value(["delta_alpaca", "delta"])),
        "gamma_stored": _coerce_float(_first_value(["gamma_alpaca", "gamma"])),
        "theta_stored": _coerce_float(_first_value(["theta_alpaca", "theta"])),
        "vega_stored": _coerce_float(_first_value(["vega_alpaca", "vega"])),
        "rho_stored": _coerce_float(_first_value(["rho_alpaca", "rho"])),
        "iv_alpaca_stored": _coerce_float(_first_value(["iv_alpaca"])),
    }


async def get_iv_at_offset(ticker: str, entry_ts: datetime, hours: int = 0) -> float | None:
    """Get IV rank at a time offset."""
    target_ts = entry_ts + timedelta(hours=hours)

    heber_iv_rank = _get_iv_rank_from_heber(ticker, target_ts)
    if heber_iv_rank is not None:
        return heber_iv_rank

    return _estimate_iv_rank_from_heber_flow(ticker, target_ts)


def _get_iv_rank_from_heber(ticker: str, target_ts: datetime) -> float | None:
    try:
        iv_rank_df = _heber_reader.read_iv_rank(
            symbols=[ticker],
            asof_time=target_ts,
            start_time=target_ts - timedelta(days=365),
        )
    except Exception as e:
        _record_price_target_fallback("iv_rank_heber_lookup", e, ticker=ticker)
        return None

    if iv_rank_df.empty:
        return None

    ts_col = _pick_first_existing_column(iv_rank_df, ["ts_utc", "ts_event", "timestamp", "created_at", "date"])
    iv_rank_col = _pick_first_existing_column(iv_rank_df, ["iv_rank", "iv_rank_pct"])
    if ts_col is None or iv_rank_col is None:
        return None

    best_ts: datetime | None = None
    best_iv_rank: float | None = None
    for _, row in iv_rank_df.iterrows():
        row_ts = _coerce_dt_utc(row.get(ts_col))
        if row_ts is None:
            row_date = _coerce_date(row.get(ts_col))
            if row_date is None:
                continue
            row_ts = datetime.combine(row_date, datetime.min.time(), tzinfo=UTC)

        if row_ts > target_ts:
            continue

        iv_rank = _coerce_float(row.get(iv_rank_col))
        if iv_rank is None:
            continue

        if best_ts is None or row_ts > best_ts:
            best_ts = row_ts
            best_iv_rank = iv_rank

    return best_iv_rank


async def get_darkpool_volume(ticker: str, entry_ts: datetime, window_minutes: int = 60) -> float | None:
    """Get aggregate darkpool volume in a time window before entry.

    Returns total shares traded in darkpools for this ticker.
    Different windows appropriate for different trade buckets:
    - 30 min: 0DTE (ultra-short term momentum)
    - 60 min: SWING (short term positioning)
    - 240 min (4h): POSITION (medium term)
    - 1440 min (1d): LEAP (longer term accumulation)
    """
    heber_volume = _get_darkpool_volume_from_heber(ticker, entry_ts, window_minutes)
    if heber_volume is not None:
        return heber_volume

    return None


def _get_darkpool_volume_from_heber(ticker: str, entry_ts: datetime, window_minutes: int = 60) -> float | None:
    start_ts = entry_ts - timedelta(minutes=window_minutes)

    try:
        darkpool_df = _heber_reader.read_darkpool(
            symbols=[ticker],
            start_time=start_ts,
            asof_time=entry_ts,
        )
    except Exception as e:
        _record_price_target_fallback("darkpool_heber_lookup", e, ticker=ticker)
        return None

    if darkpool_df.empty:
        return None

    ts_col = _pick_first_existing_column(darkpool_df, ["dark_ts_utc", "ts_utc", "ts_event", "timestamp", "created_at"])
    size_col = _pick_first_existing_column(darkpool_df, ["size_shares", "size", "shares", "volume"])
    if ts_col is None or size_col is None:
        return None

    ts_series = pd.to_datetime(darkpool_df[ts_col], utc=True, errors="coerce")
    start_utc = _coerce_dt_utc(start_ts)
    entry_utc = _coerce_dt_utc(entry_ts)
    if start_utc is None or entry_utc is None:
        return None
    in_window = darkpool_df[(ts_series >= start_utc) & (ts_series < entry_utc)]
    if in_window.empty:
        return None

    total = pd.to_numeric(in_window[size_col], errors="coerce").sum()
    if pd.isna(total) or float(total) == 0.0:
        return None

    return float(total)


async def get_darkpool_metrics(ticker: str, entry_ts: datetime) -> dict[str, float | None]:
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


async def get_rvol_metrics(ticker: str, entry_ts: datetime) -> dict[str, float | None]:
    """Get relative volume metrics vs historical average.

    - rvol_1h: Current hour volume / avg hourly volume (20-day)
    - rvol_daily: Today's volume so far / 20-day avg daily volume
    - rvol_weekly: This week's volume / 4-week avg weekly volume (for LEAP)
    """
    heber_rvol = _get_rvol_metrics_from_heber(ticker, entry_ts)
    if heber_rvol is not None:
        return heber_rvol

    return {
        "rvol_1h": None,
        "rvol_daily": None,
        "rvol_weekly": None,
        "rvol_30m": None,
        "rvol_3d": None,
        "rvol_monthly": None,
    }


def _get_rvol_metrics_from_heber(ticker: str, entry_ts: datetime) -> dict[str, float | None] | None:
    entry_utc = _coerce_dt_utc(entry_ts)
    if entry_utc is None:
        return None

    entry_hour_start = entry_utc.replace(minute=0, second=0, microsecond=0)
    entry_day_start = entry_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    days_since_monday = entry_utc.weekday()
    entry_week_start = (entry_utc - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    lookback_start = entry_utc - timedelta(days=20)
    lookback_4w = entry_utc - timedelta(days=28)

    try:
        bars_df = _heber_reader.read_bars(
            symbols=[ticker],
            asof_time=entry_utc,
            start_time=lookback_4w,
        )
    except Exception as e:
        _record_price_target_fallback("rvol_heber_lookup", e, ticker=ticker)
        return None

    if bars_df.empty:
        return None

    ts_col = _pick_first_existing_column(bars_df, ["ts_event", "bar_start_ts", "bar_start_ts_utc", "ts_utc"])
    volume_col = _pick_first_existing_column(bars_df, ["volume", "shares", "size"])
    if ts_col is None or volume_col is None:
        return None

    ts_series = pd.to_datetime(bars_df[ts_col], utc=True, errors="coerce")
    volume_series = pd.to_numeric(bars_df[volume_col], errors="coerce")
    df = pd.DataFrame({"ts": ts_series, "volume": volume_series})
    df = df.dropna(subset=["ts", "volume"])
    if df.empty:
        return None

    df = df[(df["ts"] >= lookback_4w) & (df["ts"] < entry_utc)]
    if df.empty:
        return None

    def _sum_range(start: datetime, end: datetime) -> float:
        window = df[(df["ts"] >= start) & (df["ts"] < end)]
        return float(window["volume"].sum()) if not window.empty else 0.0

    def _avg_grouped(start: datetime, end: datetime, freq: str) -> float:
        window = df[(df["ts"] >= start) & (df["ts"] < end)]
        if window.empty:
            return 0.0
        grouped = window.groupby(window["ts"].dt.floor(freq))["volume"].sum()
        return float(grouped.mean()) if not grouped.empty else 0.0

    def _avg_weekly(start: datetime, end: datetime) -> float:
        window = df[(df["ts"] >= start) & (df["ts"] < end)]
        if window.empty:
            return 0.0
        week_bucket = (window["ts"] - pd.to_timedelta(window["ts"].dt.weekday, unit="D")).dt.floor("D")
        grouped = window.groupby(week_bucket)["volume"].sum()
        return float(grouped.mean()) if not grouped.empty else 0.0

    current_hour_vol = _sum_range(entry_hour_start, entry_utc)
    avg_hour_vol = _avg_grouped(lookback_start, entry_utc, "h")
    current_day_vol = _sum_range(entry_day_start, entry_utc)
    avg_day_vol = _avg_grouped(lookback_start, entry_day_start, "d")
    current_week_vol = _sum_range(entry_week_start, entry_utc)
    avg_week_vol = _avg_weekly(lookback_4w, entry_week_start)

    rvol_1h = current_hour_vol / avg_hour_vol if avg_hour_vol > 0 else None
    rvol_daily = current_day_vol / avg_day_vol if avg_day_vol > 0 else None
    rvol_weekly = current_week_vol / avg_week_vol if avg_week_vol > 0 else None
    rvol_30m = (current_hour_vol * 0.5) / (avg_hour_vol * 0.5) if avg_hour_vol > 0 else None
    rvol_3d = (current_day_vol * 3) / (avg_day_vol * 3) if avg_day_vol > 0 else rvol_daily
    rvol_monthly = current_week_vol / avg_week_vol if avg_week_vol > 0 else rvol_weekly

    return {
        "rvol_1h": rvol_1h,
        "rvol_daily": rvol_daily,
        "rvol_weekly": rvol_weekly,
        "rvol_30m": rvol_30m,
        "rvol_3d": rvol_3d,
        "rvol_monthly": rvol_monthly,
    }


async def get_flow_aggression(ticker: str, entry_ts: datetime) -> dict[str, float | None]:
    """Get flow aggression metrics for this ticker in the last hour.

    - ask_side_ratio: % of trades hitting ask (bullish)
    - sweep_ratio_1h: Sweeps / total trades (urgency indicator)
    - same_ticker_premium_1h: Total premium traded for this ticker in last hour
    """
    heber_result = _get_flow_aggression_from_heber(ticker, entry_ts)
    if heber_result is not None:
        return heber_result

    return {"ask_side_ratio": None, "sweep_ratio_1h": None, "same_ticker_premium_1h": None}


def _get_flow_aggression_from_heber(ticker: str, entry_ts: datetime) -> dict[str, float | None] | None:
    entry_utc = _coerce_dt_utc(entry_ts)
    if entry_utc is None:
        return None
    start_utc = entry_utc - timedelta(hours=1)

    try:
        flow_df = _heber_reader.read_flow(
            symbols=[ticker],
            asof_time=entry_utc,
            start_time=start_utc,
        )
    except Exception as e:
        _record_price_target_fallback("flow_aggression_heber", e, ticker=ticker)
        return None

    if flow_df.empty:
        return None

    ts_col = _pick_first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
    premium_col = _pick_first_existing_column(flow_df, ["premium_usd", "premium"])
    if ts_col is None or premium_col is None:
        return None

    ticker_col = _pick_first_existing_column(flow_df, ["ticker", "symbol", "underlying", "instrument_key"])
    sweep_col = _pick_first_existing_column(flow_df, ["is_sweep", "sweep"])
    aggressor_col = _pick_first_existing_column(flow_df, ["aggressor", "side"])

    ts_series = pd.to_datetime(flow_df[ts_col], utc=True, errors="coerce")
    premium_series = pd.to_numeric(flow_df[premium_col], errors="coerce")

    if ticker_col is not None:
        ticker_series = flow_df[ticker_col].astype(str).str.upper().str.split(":").str[-1]
    else:
        ticker_series = pd.Series([str(ticker).upper()] * len(flow_df))

    if sweep_col is not None:
        sweep_series = flow_df[sweep_col].map(_is_truthy)
    else:
        sweep_series = pd.Series([False] * len(flow_df))

    if aggressor_col is not None:
        aggressor_series = flow_df[aggressor_col].astype(str).str.upper()
    else:
        aggressor_series = pd.Series([""] * len(flow_df))

    temp_df = pd.DataFrame(
        {
            "ts": ts_series,
            "ticker": ticker_series,
            "premium": premium_series,
            "is_sweep": sweep_series,
            "aggressor": aggressor_series,
        }
    ).dropna(subset=["ts", "premium"])

    if temp_df.empty:
        return None

    ticker_upper = str(ticker).upper()
    filtered = temp_df[(temp_df["ticker"] == ticker_upper) & (temp_df["ts"] >= start_utc) & (temp_df["ts"] < entry_utc)]

    if filtered.empty:
        return {"ask_side_ratio": None, "sweep_ratio_1h": None, "same_ticker_premium_1h": None}

    total = len(filtered)
    ask_count = int((filtered["aggressor"] == "ASK").sum())
    sweep_count = int(filtered["is_sweep"].sum())
    total_premium = float(filtered["premium"].sum())

    return {
        "ask_side_ratio": ask_count / total if total > 0 else None,
        "sweep_ratio_1h": sweep_count / total if total > 0 else None,
        "same_ticker_premium_1h": total_premium,
    }


async def get_institutional_flow_1w(ticker: str, entry_ts: datetime) -> float | None:
    """Get institutional-grade flow for LEAP trades (past week).

    Returns sum of premium from trades > $50,000 in the past week.
    Large trades often indicate institutional positioning for longer-term moves.
    """
    heber_result = _get_institutional_flow_1w_from_heber(ticker, entry_ts)
    if heber_result is not None:
        return heber_result

    return None


def _get_institutional_flow_1w_from_heber(ticker: str, entry_ts: datetime) -> float | None:
    entry_utc = _coerce_dt_utc(entry_ts)
    if entry_utc is None:
        return None
    start_utc = entry_utc - timedelta(days=7)

    try:
        flow_df = _heber_reader.read_flow(
            symbols=[ticker],
            asof_time=entry_utc,
            start_time=start_utc,
        )
    except Exception as e:
        _record_price_target_fallback("institutional_flow_1w_heber", e, ticker=ticker)
        return None

    if flow_df.empty:
        return None

    ts_col = _pick_first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
    premium_col = _pick_first_existing_column(flow_df, ["premium_usd", "premium"])
    if ts_col is None or premium_col is None:
        return None

    ticker_col = _pick_first_existing_column(flow_df, ["ticker", "symbol", "underlying", "instrument_key"])
    if ticker_col is not None:
        ticker_series = flow_df[ticker_col].astype(str).str.upper().str.split(":").str[-1]
    else:
        ticker_series = pd.Series([str(ticker).upper()] * len(flow_df))

    ts_series = pd.to_datetime(flow_df[ts_col], utc=True, errors="coerce")
    premium_series = pd.to_numeric(flow_df[premium_col], errors="coerce")

    temp_df = pd.DataFrame({"ts": ts_series, "ticker": ticker_series, "premium": premium_series}).dropna(
        subset=["ts", "premium"]
    )
    if temp_df.empty:
        return None

    ticker_upper = str(ticker).upper()
    filtered = temp_df[
        (temp_df["ticker"] == ticker_upper)
        & (temp_df["ts"] >= start_utc)
        & (temp_df["ts"] < entry_utc)
        & (temp_df["premium"] >= 50000)
    ]

    if filtered.empty:
        return None
    return float(filtered["premium"].sum())


async def get_phase1_bucket_features(ticker: str, entry_ts: datetime, dte: int) -> dict[str, Any]:
    """Get Phase 1 bucket-specific features.

    - minutes_to_close: Minutes until 4pm ET (for 0DTE time decay)
    - overnight_gap_pct: Gap from prior close (for SWING)
    - price_change_5d_prior: 5-day price momentum (for POSITION)
    - earnings_in_dte_window: Will earnings occur before expiry? (for LEAP)
    - vwap_distance_pct: Distance from VWAP (all buckets)
    """
    result: dict[str, Any] = {
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

    # 2-5: Market context lookups
    entry_date = entry_ts.date()
    heber_market = _get_phase1_bucket_features_from_heber(ticker, entry_ts)
    if heber_market is not None:
        result.update(heber_market)

    # 4. earnings_in_dte_window (LEAP focus)
    # Check if earnings date falls within DTE window
    ticker_info = await get_ticker_info(ticker)
    next_earnings = ticker_info.get("next_earnings_date")
    if next_earnings:
        expiry_date = entry_date + timedelta(days=dte)
        result["earnings_in_dte_window"] = entry_date <= next_earnings <= expiry_date

    return result


def _get_phase1_bucket_features_from_heber(ticker: str, entry_ts: datetime) -> dict[str, Any] | None:
    result: dict[str, Any] = {
        "overnight_gap_pct": None,
        "price_change_5d_prior": None,
        "vwap_distance_pct": None,
    }
    entry_utc = _coerce_dt_utc(entry_ts)
    if entry_utc is None:
        return None

    entry_date = entry_utc.date()
    five_days_ago = entry_date - timedelta(days=5)
    try:
        bars_df = _heber_reader.read_bars(
            symbols=[ticker],
            asof_time=entry_utc,
            start_time=entry_utc - timedelta(days=14),
        )
    except Exception as e:
        _record_price_target_fallback("phase1_bucket_heber_lookup", e, ticker=ticker)
        return None

    if bars_df.empty:
        return None

    ts_col = _pick_first_existing_column(bars_df, ["ts_event", "bar_start_ts", "bar_start_ts_utc", "ts_utc"])
    open_col = _pick_first_existing_column(bars_df, ["open", "o"])
    close_col = _pick_first_existing_column(bars_df, ["close", "c"])
    vwap_col = _pick_first_existing_column(bars_df, ["vwap", "vw"])
    symbol_col = _pick_first_existing_column(bars_df, ["symbol", "ticker", "underlying"])
    if ts_col is None or close_col is None:
        return None

    ts_series = pd.to_datetime(bars_df[ts_col], utc=True, errors="coerce")
    if open_col is not None:
        open_series = pd.to_numeric(bars_df[open_col], errors="coerce")
    else:
        open_series = pd.to_numeric(bars_df[close_col], errors="coerce")
    close_series = pd.to_numeric(bars_df[close_col], errors="coerce")
    if vwap_col is not None:
        vwap_series = pd.to_numeric(bars_df[vwap_col], errors="coerce")
    else:
        vwap_series = pd.Series([None] * len(bars_df))

    temp_df = pd.DataFrame(
        {
            "ts": ts_series,
            "open": open_series,
            "close": close_series,
            "vwap": vwap_series,
        }
    ).dropna(subset=["ts", "close"])
    if temp_df.empty:
        return None

    if symbol_col is not None:
        symbol_series = bars_df[symbol_col].astype(str).str.upper().str.split(":").str[-1]
        temp_df["symbol"] = symbol_series.values
        temp_df = temp_df[temp_df["symbol"] == str(ticker).upper()]
        if temp_df.empty:
            return None

    before_entry = temp_df[temp_df["ts"] <= entry_utc].sort_values("ts")
    if before_entry.empty:
        return None
    before_entry["day"] = before_entry["ts"].dt.date

    today_rows = before_entry[before_entry["day"] == entry_date]
    prior_rows = before_entry[before_entry["day"] < entry_date]

    today_open: float | None = None
    prior_close: float | None = None
    if not today_rows.empty:
        today_open = _coerce_float(today_rows.iloc[0]["open"])
    if not prior_rows.empty:
        prior_close = _coerce_float(prior_rows.iloc[-1]["close"])

    if today_open is not None and prior_close is not None and prior_close > 0:
        result["overnight_gap_pct"] = ((today_open - prior_close) / prior_close) * 100.0

    latest_row = before_entry.iloc[-1]
    latest_close = _coerce_float(latest_row["close"])
    latest_vwap = _coerce_float(latest_row["vwap"])
    if latest_close is not None and latest_vwap is not None and latest_vwap > 0:
        result["vwap_distance_pct"] = ((latest_close - latest_vwap) / latest_vwap) * 100.0

    five_day_rows = prior_rows[prior_rows["day"] <= five_days_ago]
    if not five_day_rows.empty and prior_close is not None:
        five_day_close = _coerce_float(five_day_rows.iloc[-1]["close"])
        if five_day_close is not None and five_day_close > 0:
            result["price_change_5d_prior"] = ((prior_close - five_day_close) / five_day_close) * 100.0

    if any(value is not None for value in result.values()):
        return result
    return None


async def get_p2_features(ticker: str, option_chain: str, entry_ts: datetime) -> dict[str, float | None]:
    """Get P2 ML features: OI change momentum and IV vs HV ratio."""
    heber_result = _get_p2_features_from_heber(ticker, option_chain, entry_ts)
    if heber_result is not None:
        return heber_result

    return {
        "oi_change_1d": None,
        "oi_change_pct": None,
        "iv_vs_hv_ratio": None,
        "hv_30d": None,
    }


def _get_p2_features_from_heber(ticker: str, option_chain: str, entry_ts: datetime) -> dict[str, float | None] | None:
    result: dict[str, float | None] = {
        "oi_change_1d": None,
        "oi_change_pct": None,
        "iv_vs_hv_ratio": None,
        "hv_30d": None,
    }
    entry_utc = _coerce_dt_utc(entry_ts)
    if entry_utc is None:
        return None

    entry_date = entry_utc.date()
    option_chain_upper = str(option_chain).upper()

    try:
        flow_df = _heber_reader.read_flow(
            asof_time=entry_utc,
            start_time=entry_utc - timedelta(days=35),
        )
    except Exception as e:
        _record_price_target_fallback("p2_flow_heber_lookup", e, ticker=ticker, option_chain=option_chain)
        return None

    if flow_df.empty:
        return None

    flow_ts_col = _pick_first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
    flow_chain_col = _pick_first_existing_column(
        flow_df, ["option_chain", "option_symbol", "contract_symbol", "instrument_key"]
    )
    oi_col = _pick_first_existing_column(flow_df, ["open_interest", "oi"])
    iv_col = _pick_first_existing_column(flow_df, ["iv", "implied_volatility"])
    if flow_ts_col is None or flow_chain_col is None:
        return None

    flow_ts = pd.to_datetime(flow_df[flow_ts_col], utc=True, errors="coerce")
    flow_chain = flow_df[flow_chain_col].astype(str).str.upper().str.split(":").str[-1]
    flow_oi = (
        pd.to_numeric(flow_df[oi_col], errors="coerce") if oi_col is not None else pd.Series([np.nan] * len(flow_df))
    )
    flow_iv = (
        pd.to_numeric(flow_df[iv_col], errors="coerce") if iv_col is not None else pd.Series([np.nan] * len(flow_df))
    )

    normalized_flow = pd.DataFrame(
        {"ts": flow_ts, "option_chain": flow_chain, "open_interest": flow_oi, "iv": flow_iv}
    ).dropna(subset=["ts"])
    if normalized_flow.empty:
        return None

    scoped_flow = normalized_flow[normalized_flow["option_chain"] == option_chain_upper].sort_values("ts")
    if scoped_flow.empty:
        return None

    current_day_rows = scoped_flow[scoped_flow["ts"].dt.date == entry_date]
    prior_rows = scoped_flow[scoped_flow["ts"] < entry_utc]

    current_oi: float | None = None
    prior_oi: float | None = None
    iv_value: float | None = None
    if not current_day_rows.empty:
        latest_current = current_day_rows.iloc[-1]
        current_oi = _coerce_float(latest_current.get("open_interest"))
        iv_value = _coerce_float(latest_current.get("iv"))
    if not prior_rows.empty:
        prior_oi = _coerce_float(prior_rows.iloc[-1].get("open_interest"))

    if current_oi is not None and prior_oi is not None:
        result["oi_change_1d"] = float(current_oi - prior_oi)
        if prior_oi > 0:
            result["oi_change_pct"] = ((current_oi - prior_oi) / prior_oi) * 100.0

    try:
        bars_df = _heber_reader.read_bars(
            symbols=[ticker],
            asof_time=entry_utc,
            start_time=entry_utc - timedelta(days=35),
        )
    except Exception as e:
        _record_price_target_fallback("p2_hv_heber_lookup", e, ticker=ticker)
        bars_df = pd.DataFrame()

    if not bars_df.empty:
        bar_ts_col = _pick_first_existing_column(
            bars_df, ["ts_event", "bar_start_ts", "bar_start_ts_utc", "ts_utc", "timestamp"]
        )
        bar_close_col = _pick_first_existing_column(bars_df, ["close", "c"])
        bar_ticker_col = _pick_first_existing_column(bars_df, ["ticker", "symbol", "underlying", "instrument_key"])
        if bar_ts_col is not None and bar_close_col is not None:
            bar_ts = pd.to_datetime(bars_df[bar_ts_col], utc=True, errors="coerce")
            bar_close = pd.to_numeric(bars_df[bar_close_col], errors="coerce")
            if bar_ticker_col is not None:
                bar_ticker = bars_df[bar_ticker_col].astype(str).str.upper().str.split(":").str[-1]
            else:
                bar_ticker = pd.Series([str(ticker).upper()] * len(bars_df))

            bars_norm = pd.DataFrame({"ts": bar_ts, "ticker": bar_ticker, "close": bar_close}).dropna(
                subset=["ts", "close"]
            )
            if not bars_norm.empty:
                bars_norm = bars_norm[
                    (bars_norm["ticker"] == str(ticker).upper())
                    & (bars_norm["ts"].dt.date >= (entry_date - timedelta(days=30)))
                    & (bars_norm["ts"].dt.date < entry_date)
                ].sort_values("ts")

                closes = [float(value) for value in bars_norm["close"].tolist() if value]
                if len(closes) >= 10:
                    returns = [
                        (closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0
                    ]
                    if len(returns) > 1:
                        import statistics

                        result["hv_30d"] = statistics.stdev(returns) * (252**0.5) * 100

    if iv_value is not None and result["hv_30d"] is not None and result["hv_30d"] > 0:
        iv_pct = iv_value * 100 if iv_value < 2 else iv_value
        result["iv_vs_hv_ratio"] = iv_pct / result["hv_30d"]

    if any(value is not None for value in result.values()):
        return result
    return None


async def get_p3_features(ticker: str, option_chain: str, expiry: datetime, entry_ts: datetime) -> dict[str, Any]:
    """Get P3 ML features: 52w high distance, spread detection, same-expiry trades.

    - high_52w_distance_pct: % below 52-week high (for LEAP mean reversion)
    - is_spread_leg: Whether this trade is likely part of a spread
    - same_expiry_trades_1h: Count of trades on same expiry in last hour (spread indicator)
    """
    heber_result = _get_p3_features_from_heber(ticker, expiry, entry_ts)
    if heber_result is not None:
        return heber_result

    return {
        "high_52w_distance_pct": None,
        "is_spread_leg": None,
        "same_expiry_trades_1h": None,
    }


def _get_p3_features_from_heber(ticker: str, expiry: datetime, entry_ts: datetime) -> dict[str, Any] | None:
    result: dict[str, Any] = {
        "high_52w_distance_pct": None,
        "is_spread_leg": None,
        "same_expiry_trades_1h": None,
    }
    entry_utc = _coerce_dt_utc(entry_ts)
    if entry_utc is None:
        return None

    entry_date = entry_utc.date()
    lookback_52w = entry_date - timedelta(days=365)
    lookback_1h = entry_utc - timedelta(hours=1)
    ticker_upper = str(ticker).upper()

    expiry_date = _coerce_date(expiry)
    expiry_str = expiry_date.isoformat() if expiry_date is not None else (str(expiry) if expiry else None)

    try:
        bars_df = _heber_reader.read_bars(
            symbols=[ticker],
            asof_time=entry_utc,
            start_time=entry_utc - timedelta(days=370),
        )
    except Exception as e:
        _record_price_target_fallback("p3_bars_heber_lookup", e, ticker=ticker)
        bars_df = pd.DataFrame()

    if not bars_df.empty:
        ts_col = _pick_first_existing_column(
            bars_df, ["ts_event", "bar_start_ts", "bar_start_ts_utc", "ts_utc", "timestamp"]
        )
        high_col = _pick_first_existing_column(bars_df, ["high", "h"])
        close_col = _pick_first_existing_column(bars_df, ["close", "c"])
        ticker_col = _pick_first_existing_column(bars_df, ["ticker", "symbol", "underlying", "instrument_key"])
        if ts_col is not None and high_col is not None and close_col is not None:
            ts_series = pd.to_datetime(bars_df[ts_col], utc=True, errors="coerce")
            high_series = pd.to_numeric(bars_df[high_col], errors="coerce")
            close_series = pd.to_numeric(bars_df[close_col], errors="coerce")
            if ticker_col is not None:
                ticker_series = bars_df[ticker_col].astype(str).str.upper().str.split(":").str[-1]
            else:
                ticker_series = pd.Series([ticker_upper] * len(bars_df))

            normalized_bars = pd.DataFrame(
                {"ts": ts_series, "ticker": ticker_series, "high": high_series, "close": close_series}
            )
            normalized_bars = normalized_bars.dropna(subset=["ts"])
            if not normalized_bars.empty:
                scoped = normalized_bars[
                    (normalized_bars["ticker"] == ticker_upper) & (normalized_bars["ts"] < entry_utc)
                ].sort_values("ts")
                if not scoped.empty:
                    history_52w = scoped[(scoped["ts"].dt.date >= lookback_52w) & (scoped["ts"].dt.date < entry_date)]
                    if not history_52w.empty:
                        high_52w = pd.to_numeric(history_52w["high"], errors="coerce").max()
                    else:
                        high_52w = None

                    current_price = _coerce_float(scoped.iloc[-1].get("close"))
                    if high_52w is not None and current_price is not None and high_52w > 0:
                        result["high_52w_distance_pct"] = ((float(high_52w) - current_price) / float(high_52w)) * 100.0

    if expiry_str:
        try:
            flow_df = _heber_reader.read_flow(
                symbols=[ticker],
                asof_time=entry_utc,
                start_time=lookback_1h - timedelta(minutes=5),
            )
        except Exception as e:
            _record_price_target_fallback("p3_flow_heber_lookup", e, ticker=ticker)
            flow_df = pd.DataFrame()

        if not flow_df.empty:
            flow_ts_col = _pick_first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
            flow_ticker_col = _pick_first_existing_column(flow_df, ["ticker", "symbol", "underlying", "instrument_key"])
            flow_expiry_col = _pick_first_existing_column(flow_df, ["expiry", "expiration", "exp_date"])
            if flow_ts_col is not None and flow_expiry_col is not None:
                flow_ts = pd.to_datetime(flow_df[flow_ts_col], utc=True, errors="coerce")
                flow_expiry = pd.to_datetime(flow_df[flow_expiry_col], errors="coerce").dt.date.astype(str)
                if flow_ticker_col is not None:
                    flow_ticker = flow_df[flow_ticker_col].astype(str).str.upper().str.split(":").str[-1]
                else:
                    flow_ticker = pd.Series([ticker_upper] * len(flow_df))

                normalized_flow = pd.DataFrame({"ts": flow_ts, "ticker": flow_ticker, "expiry": flow_expiry}).dropna(
                    subset=["ts", "expiry"]
                )
                if not normalized_flow.empty:
                    flow_window = normalized_flow[
                        (normalized_flow["ticker"] == ticker_upper)
                        & (normalized_flow["expiry"] == expiry_str)
                        & (normalized_flow["ts"] >= lookback_1h)
                        & (normalized_flow["ts"] < entry_utc)
                    ]
                    same_expiry_count = int(len(flow_window))
                    result["same_expiry_trades_1h"] = same_expiry_count
                    result["is_spread_leg"] = same_expiry_count >= 2

    if any(value is not None for value in result.values()):
        return result
    return None


async def get_sector_correlation_features(ticker: str, entry_ts: datetime) -> dict[str, Any]:
    """Get sector flow and correlation features."""
    heber_result = _get_sector_correlation_features_from_heber(ticker, entry_ts)
    if heber_result is not None:
        return heber_result

    return {
        "sector_net_premium_1h": None,
        "sector_flow_direction": None,
        "spy_correlation_5d": None,
        "spy_return_1h": None,
    }


def _get_sector_correlation_features_from_heber(ticker: str, entry_ts: datetime) -> dict[str, Any] | None:
    result: dict[str, Any] = {
        "sector_net_premium_1h": None,
        "sector_flow_direction": None,
        "spy_correlation_5d": None,
        "spy_return_1h": None,
    }
    entry_utc = _coerce_dt_utc(entry_ts)
    if entry_utc is None:
        return None

    ticker_upper = str(ticker).upper()
    entry_date = entry_utc.date()
    lookback_1h = entry_utc - timedelta(hours=1)
    lookback_5d = entry_date - timedelta(days=5)

    try:
        flow_df = _heber_reader.read_flow(
            asof_time=entry_utc,
            start_time=lookback_1h - timedelta(hours=1),
        )
    except Exception as e:
        _record_price_target_fallback("sector_net_premium_1h_heber", e, ticker=ticker)
        flow_df = pd.DataFrame()

    if not flow_df.empty:
        ts_col = _pick_first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
        ticker_col = _pick_first_existing_column(flow_df, ["ticker", "symbol", "underlying", "instrument_key"])
        put_call_col = _pick_first_existing_column(flow_df, ["put_call", "type"])
        premium_col = _pick_first_existing_column(flow_df, ["premium_usd", "premium"])
        sector_col = _pick_first_existing_column(flow_df, ["sector", "sector_name"])

        if ts_col and ticker_col and put_call_col and premium_col:
            ts_series = pd.to_datetime(flow_df[ts_col], utc=True, errors="coerce")
            premium_series = pd.to_numeric(flow_df[premium_col], errors="coerce")
            temp_df = pd.DataFrame(
                {
                    "ts": ts_series,
                    "ticker": flow_df[ticker_col].astype(str),
                    "put_call": flow_df[put_call_col].astype(str).str.upper(),
                    "premium": premium_series,
                }
            )
            if sector_col:
                temp_df["sector"] = flow_df[sector_col].astype(str)
            temp_df = temp_df.dropna(subset=["ts", "put_call", "premium"])
            flow_window = temp_df[(temp_df["ts"] >= lookback_1h) & (temp_df["ts"] < entry_utc)]

            if sector_col and not flow_window.empty:
                ticker_sector = None
                ticker_rows = temp_df[temp_df["ticker"].str.upper() == ticker_upper]
                if not ticker_rows.empty and "sector" in ticker_rows.columns:
                    sector_values = ticker_rows["sector"].dropna()
                    if not sector_values.empty:
                        ticker_sector = str(sector_values.iloc[-1])

                if ticker_sector:
                    flow_window = flow_window[flow_window["sector"] == ticker_sector]

            if not flow_window.empty:
                signed = np.where(flow_window["put_call"] == "C", flow_window["premium"], -flow_window["premium"])
                net_premium = float(np.nansum(signed))
                result["sector_net_premium_1h"] = net_premium if net_premium != 0 else None
                if net_premium > 1000000:
                    result["sector_flow_direction"] = "BULLISH"
                elif net_premium < -1000000:
                    result["sector_flow_direction"] = "BEARISH"
                else:
                    result["sector_flow_direction"] = "NEUTRAL"

    try:
        spy_bars_df = _heber_reader.read_bars(
            symbols=["SPY"],
            asof_time=entry_utc,
            start_time=lookback_1h - timedelta(hours=2),
        )
    except Exception as e:
        _record_price_target_fallback("spy_return_1h_heber", e, ticker=ticker)
        spy_bars_df = pd.DataFrame()

    if not spy_bars_df.empty:
        ts_col = _pick_first_existing_column(spy_bars_df, ["ts_event", "bar_start_ts", "bar_start_ts_utc", "ts_utc"])
        close_col = _pick_first_existing_column(spy_bars_df, ["close", "c"])
        if ts_col and close_col:
            ts_series = pd.to_datetime(spy_bars_df[ts_col], utc=True, errors="coerce")
            close_series = pd.to_numeric(spy_bars_df[close_col], errors="coerce")
            temp_df = pd.DataFrame({"ts": ts_series, "close": close_series}).dropna(subset=["ts", "close"])
            if not temp_df.empty:
                current_candidates = temp_df[temp_df["ts"] < entry_utc].sort_values("ts")
                prior_candidates = temp_df[temp_df["ts"] < lookback_1h].sort_values("ts")
                if not current_candidates.empty and not prior_candidates.empty:
                    current_close = float(current_candidates.iloc[-1]["close"])
                    prior_close = float(prior_candidates.iloc[-1]["close"])
                    if prior_close > 0:
                        result["spy_return_1h"] = ((current_close - prior_close) / prior_close) * 100.0

    correlation_start = datetime.combine(lookback_5d, datetime.min.time(), tzinfo=UTC)
    try:
        bars_df = _heber_reader.read_bars(
            symbols=[ticker, "SPY"],
            asof_time=entry_utc,
            start_time=correlation_start,
        )
    except Exception as e:
        _record_price_target_fallback("spy_correlation_5d_heber", e, ticker=ticker)
        bars_df = pd.DataFrame()

    if not bars_df.empty:
        ts_col = _pick_first_existing_column(bars_df, ["ts_event", "bar_start_ts", "bar_start_ts_utc", "ts_utc"])
        symbol_col = _pick_first_existing_column(bars_df, ["symbol", "ticker", "underlying"])
        close_col = _pick_first_existing_column(bars_df, ["close", "c"])
        if ts_col and symbol_col and close_col:
            ts_series = pd.to_datetime(bars_df[ts_col], utc=True, errors="coerce")
            close_series = pd.to_numeric(bars_df[close_col], errors="coerce")
            temp_df = pd.DataFrame(
                {
                    "ts": ts_series,
                    "symbol": bars_df[symbol_col].astype(str).str.upper(),
                    "close": close_series,
                }
            ).dropna(subset=["ts", "symbol", "close"])

            temp_df = temp_df[(temp_df["ts"] < entry_utc) & (temp_df["symbol"].isin({ticker_upper, "SPY"}))]
            if not temp_df.empty:
                temp_df["day"] = temp_df["ts"].dt.date
                temp_df = temp_df[(temp_df["day"] >= lookback_5d) & (temp_df["day"] < entry_date)]
                if not temp_df.empty:
                    daily = temp_df.sort_values("ts").groupby(["symbol", "day"], as_index=False)["close"].last()
                    pivot = daily.pivot(index="day", columns="symbol", values="close").sort_index()
                    if ticker_upper in pivot.columns and "SPY" in pivot.columns:
                        returns = pd.DataFrame(
                            {
                                "ticker_return": pivot[ticker_upper].pct_change(),
                                "spy_return": pivot["SPY"].pct_change(),
                            }
                        ).dropna()
                        if len(returns) >= 3:
                            corr = returns["ticker_return"].corr(returns["spy_return"])
                            if pd.notna(corr):
                                result["spy_correlation_5d"] = float(corr)

    if any(value is not None for value in result.values()):
        return result
    return None


# Ticker info cache to avoid repeated API calls
_ticker_info_cache: dict[str, dict[str, Any]] = {}
_uw_client: UnusualWhalesClient | None = None


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


async def get_ticker_info(ticker: str) -> dict[str, Any]:
    """Fetch ticker info from UW API with caching.

    Uses both /api/stock/{ticker}/info and /api/earnings/{ticker} endpoints
    to maximize data coverage.
    """
    # Return cached if available
    if ticker in _ticker_info_cache:
        return _ticker_info_cache[ticker]

    # Initialize empty cache entry
    cache_entry: dict[str, Any] = {
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


async def get_sector_info(ticker: str) -> dict[str, str | None]:
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
    except Exception as e:
        _record_price_target_fallback("sector_info_lookup", e, ticker=ticker)
        return {"sector": "Other", "industry": None}


async def get_earnings_proximity(ticker: str, entry_ts: datetime) -> dict[str, Any]:
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


def get_price_at_offset_minutes(prices: list[dict[str, Any]], entry_ts: datetime, minutes: int) -> float | None:
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


def get_price_at_offset_days(prices: list[dict[str, Any]], entry_ts: datetime, days: int) -> float | None:
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


def get_price_at_offset(prices: list[dict[str, Any]], entry_ts: datetime, hours: int) -> float | None:
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


def calculate_volatility(prices: list[float]) -> float | None:
    """Calculate price volatility (std dev of returns)."""
    if len(prices) < 3:
        return None
    try:
        returns = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
        return float(np.std(returns)) if returns else None
    except (ZeroDivisionError, ValueError):
        return None


# Checkpoint definitions for Greeks fetching
# (suffix, minutes_offset, hours_offset, days_offset)
CHECKPOINT_OFFSETS = {
    "5m": timedelta(minutes=5),
    "10m": timedelta(minutes=10),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "8h": timedelta(hours=8),
    "eod": timedelta(hours=6, minutes=30),  # ~6.5h trading day
    "1d": timedelta(days=1),
    "2d": timedelta(days=2),
    "3d": timedelta(days=3),
    "1w": timedelta(days=7),
    "2w": timedelta(days=14),
    "3w": timedelta(days=21),
    "4w": timedelta(days=28),
}


async def get_checkpoint_greeks(
    option_chain: str,
    ticker: str,
    entry_ts: datetime,
    entry_price: float,
    expiry: datetime | None,
    dte: int | None,
) -> dict[str, dict[str, float | None]]:
    """Fetch Greeks and underlying price at each checkpoint from Alpaca.

    Since Alpaca only provides current data, this only populates
    for checkpoints that are near 'now' (within 5 minutes).
    Historical checkpoints remain NULL and populate going forward.

    Returns:
        Dict mapping checkpoint suffix to Greeks + decay features + underlying
    """
    from orion.connectors.alpaca_option_greeks_connector import get_option_greeks

    now = datetime.now(UTC)
    results: dict[str, dict[str, float | None]] = {}

    for cp_suffix, offset in CHECKPOINT_OFFSETS.items():
        checkpoint_ts = entry_ts + offset

        # Initialize with NULLs
        cp_data: dict[str, float | None] = {
            "delta": None,
            "gamma": None,
            "theta": None,
            "vega": None,
            "iv": None,
            "dte": None,
            "theta_decay_pct": None,
            "time_value_pct": None,
            "underlying": None,
        }

        # Only fetch live data if checkpoint is within 5 minutes of now (real-time labeling)
        time_to_checkpoint = abs((now - checkpoint_ts).total_seconds())
        if time_to_checkpoint < 300:  # 5 minutes
            try:
                greeks = await get_option_greeks(option_chain)
                cp_data["delta"] = greeks.get("delta")
                cp_data["gamma"] = greeks.get("gamma")
                cp_data["theta"] = greeks.get("theta")
                cp_data["vega"] = greeks.get("vega")
                cp_data["iv"] = greeks.get("implied_volatility")

                # Fetch underlying price at checkpoint
                underlying = await get_underlying_price_at_entry(ticker, checkpoint_ts)
                cp_data["underlying"] = underlying
            except Exception as e:
                logger.debug(f"Error fetching data at {cp_suffix}: {e}")

        # Calculate time decay features (can be calculated without API)
        if expiry and dte is not None:
            # DTE at checkpoint
            cp_date = checkpoint_ts.date() if hasattr(checkpoint_ts, "date") else checkpoint_ts
            if isinstance(expiry, datetime):
                expiry_date = expiry.date()
            else:
                expiry_date = expiry
            dte_at_cp = (expiry_date - cp_date).days if cp_date else None
            cp_data["dte"] = dte_at_cp if dte_at_cp and dte_at_cp >= 0 else 0

            # Theta decay percentage (rough estimate: theta * days / entry_price)
            # Only calculate if we have entry theta
            if cp_data.get("theta") and entry_price and entry_price > 0:
                days_held = offset.total_seconds() / 86400  # Convert to days
                # Theta is daily decay, so cumulative decay ≈ theta * days
                theta_decay = abs(cp_data["theta"]) * days_held
                cp_data["theta_decay_pct"] = (theta_decay / entry_price) * 100

        results[cp_suffix] = cp_data

    return results


async def label_entry(entry: Any) -> dict[str, Any] | None:
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
            # 0DTE checkpoints
            "price_at_5m",
            "return_at_5m",
            "price_at_10m",
            "return_at_10m",
            "price_at_15m",
            "return_at_15m",
            "price_at_30m",
            "return_at_30m",
            # EOD checkpoint
            "price_at_eod",
            "return_at_eod",
            # Longer term checkpoints
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
            # POSITION extended checkpoints
            "price_at_2w",
            "return_at_2w",
            "price_at_3w",
            "return_at_3w",
            "price_at_4w",
            "return_at_4w",
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
        label["theta_at_entry"] = None
        label["vega_at_entry"] = None
        label["rho_at_entry"] = None
        label["iv_at_entry_alpaca"] = None
        label["volume_at_entry"] = None
        label["open_interest_at_entry"] = None
        time_features = get_entry_time_features(entry_ts)
        label.update(time_features)
        label["days_to_earnings"] = None
        label["is_post_earnings"] = None
        label["sector"] = None
        label["industry"] = None
        # P2 ML features - set to None for early return (no subsequent prices)
        label["oi_change_1d"] = None
        label["oi_change_pct"] = None
        label["iv_vs_hv_ratio"] = None
        label["high_52w_distance_pct"] = None
        label["is_spread_leg"] = None
        label["same_expiry_trades_1h"] = None
        # Sector correlation features
        sector_corr = await get_sector_correlation_features(ticker, entry_ts)
        label["sector_net_premium_1h"] = sector_corr.get("sector_net_premium_1h")
        label["sector_flow_direction"] = sector_corr.get("sector_flow_direction")
        label["spy_correlation_5d"] = sector_corr.get("spy_correlation_5d")
        label["spy_return_1h"] = sector_corr.get("spy_return_1h")
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

    # Price at checkpoints - prefer real Alpaca quotes when available
    # Fetch real quotes from silver_option_quotes (populated by option_quote_tracker)
    real_quotes = await get_real_checkpoint_prices(entry.event_id)

    # Helper to get price: prefer real quote, fallback to flow data
    def _get_checkpoint_price(checkpoint: str, flow_price_fn, *flow_args) -> float | None:
        """Get checkpoint price: real quote first, then flow fallback."""
        quote_data = real_quotes.get(checkpoint)
        if quote_data is not None and quote_data.get("price") is not None:
            return quote_data["price"]
        return flow_price_fn(*flow_args)

    # Helper to get Greeks at checkpoint
    def _get_checkpoint_greeks(checkpoint: str) -> dict[str, float | None]:
        """Get Greeks at checkpoint from Alpaca quote data."""
        quote_data = real_quotes.get(checkpoint)
        if quote_data is None:
            return {"delta": None, "gamma": None, "theta": None, "vega": None, "iv": None}
        return {
            "delta": quote_data.get("delta"),
            "gamma": quote_data.get("gamma"),
            "theta": quote_data.get("theta"),
            "vega": quote_data.get("vega"),
            "iv": quote_data.get("iv"),
        }

    # Original hourly checkpoints (1h/2h/4h)
    price_1h = _get_checkpoint_price("1h", get_price_at_offset, prices, entry_ts, 1)
    price_2h = _get_checkpoint_price("2h", get_price_at_offset, prices, entry_ts, 2)
    price_4h = _get_checkpoint_price("4h", get_price_at_offset, prices, entry_ts, 4)

    return_1h = ((price_1h - entry_price) / entry_price * 100) if price_1h else None
    return_2h = ((price_2h - entry_price) / entry_price * 100) if price_2h else None
    return_4h = ((price_4h - entry_price) / entry_price * 100) if price_4h else None

    # 0DTE checkpoints (5m, 10m, 15m, 30m) - ultra-short for 0DTE
    price_5m = get_price_at_offset_minutes(prices, entry_ts, 5)  # No real quote yet
    price_10m = get_price_at_offset_minutes(prices, entry_ts, 10)  # No real quote yet
    price_15m = _get_checkpoint_price("15m", get_price_at_offset_minutes, prices, entry_ts, 15)
    price_30m = _get_checkpoint_price("30m", get_price_at_offset_minutes, prices, entry_ts, 30)
    return_5m = ((price_5m - entry_price) / entry_price * 100) if price_5m else None
    return_10m = ((price_10m - entry_price) / entry_price * 100) if price_10m else None
    return_15m = ((price_15m - entry_price) / entry_price * 100) if price_15m else None
    return_30m = ((price_30m - entry_price) / entry_price * 100) if price_30m else None

    # SWING/POSITION checkpoints (8h, 1d, 2d, 3d, 1w)
    price_8h = _get_checkpoint_price("8h", get_price_at_offset, prices, entry_ts, 8)
    price_1d = _get_checkpoint_price("1d", get_price_at_offset_days, prices, entry_ts, 1)
    price_2d = get_price_at_offset_days(prices, entry_ts, 2)  # No real quote yet
    price_3d = get_price_at_offset_days(prices, entry_ts, 3)  # No real quote yet
    price_1w = get_price_at_offset_days(prices, entry_ts, 7)  # No real quote yet

    return_8h = ((price_8h - entry_price) / entry_price * 100) if price_8h else None
    return_1d = ((price_1d - entry_price) / entry_price * 100) if price_1d else None
    return_2d = ((price_2d - entry_price) / entry_price * 100) if price_2d else None
    return_3d = ((price_3d - entry_price) / entry_price * 100) if price_3d else None
    return_1w = ((price_1w - entry_price) / entry_price * 100) if price_1w else None

    # POSITION extended checkpoints (2w, 3w, 4w) - for longer holding periods
    price_2w = get_price_at_offset_days(prices, entry_ts, 14)
    price_3w = get_price_at_offset_days(prices, entry_ts, 21)
    price_4w = get_price_at_offset_days(prices, entry_ts, 28)

    return_2w = ((price_2w - entry_price) / entry_price * 100) if price_2w else None
    return_3w = ((price_3w - entry_price) / entry_price * 100) if price_3w else None
    return_4w = ((price_4w - entry_price) / entry_price * 100) if price_4w else None

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

    # Extract Greeks at each checkpoint from Alpaca quote data
    greeks_5m = _get_checkpoint_greeks("5m") if real_quotes else {}
    greeks_15m = _get_checkpoint_greeks("15m") if real_quotes else {}
    greeks_30m = _get_checkpoint_greeks("30m") if real_quotes else {}
    greeks_1h = _get_checkpoint_greeks("1h") if real_quotes else {}
    greeks_2h = _get_checkpoint_greeks("2h") if real_quotes else {}
    greeks_4h = _get_checkpoint_greeks("4h") if real_quotes else {}
    greeks_8h = _get_checkpoint_greeks("8h") if real_quotes else {}
    greeks_1d = _get_checkpoint_greeks("1d") if real_quotes else {}
    greeks_eod = _get_checkpoint_greeks("eod") if real_quotes else {}

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
            # POSITION extended checkpoints (2w/3w/4w)
            "price_at_2w": price_2w,
            "return_at_2w": return_2w,
            "price_at_3w": price_3w,
            "return_at_3w": return_3w,
            "price_at_4w": price_4w,
            "return_at_4w": return_4w,
            # SWING EOD checkpoint
            "price_at_eod": price_eod,
            "return_at_eod": return_eod,
            # Checkpoint Greeks from Alpaca (delta, gamma, theta, iv)
            "delta_at_5m": greeks_5m.get("delta"),
            "gamma_at_5m": greeks_5m.get("gamma"),
            "theta_at_5m": greeks_5m.get("theta"),
            "iv_at_5m": greeks_5m.get("iv"),
            "delta_at_15m": greeks_15m.get("delta"),
            "gamma_at_15m": greeks_15m.get("gamma"),
            "theta_at_15m": greeks_15m.get("theta"),
            "iv_at_15m": greeks_15m.get("iv"),
            "delta_at_30m": greeks_30m.get("delta"),
            "gamma_at_30m": greeks_30m.get("gamma"),
            "theta_at_30m": greeks_30m.get("theta"),
            "iv_at_30m": greeks_30m.get("iv"),
            "delta_at_1h": greeks_1h.get("delta"),
            "gamma_at_1h": greeks_1h.get("gamma"),
            "theta_at_1h": greeks_1h.get("theta"),
            "iv_at_1h": greeks_1h.get("iv"),
            "delta_at_2h": greeks_2h.get("delta"),
            "gamma_at_2h": greeks_2h.get("gamma"),
            "theta_at_2h": greeks_2h.get("theta"),
            "iv_at_2h": greeks_2h.get("iv"),
            "delta_at_4h": greeks_4h.get("delta"),
            "gamma_at_4h": greeks_4h.get("gamma"),
            "theta_at_4h": greeks_4h.get("theta"),
            "iv_at_4h": greeks_4h.get("iv"),
            "delta_at_8h": greeks_8h.get("delta"),
            "gamma_at_8h": greeks_8h.get("gamma"),
            "theta_at_8h": greeks_8h.get("theta"),
            "iv_at_8h": greeks_8h.get("iv"),
            "delta_at_1d": greeks_1d.get("delta"),
            "gamma_at_1d": greeks_1d.get("gamma"),
            "theta_at_1d": greeks_1d.get("theta"),
            "iv_at_1d": greeks_1d.get("iv"),
            "delta_at_eod": greeks_eod.get("delta"),
            "gamma_at_eod": greeks_eod.get("gamma"),
            "theta_at_eod": greeks_eod.get("theta"),
            "iv_at_eod": greeks_eod.get("iv"),
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

    # New ML features - Greeks and volume from flow (Alpaca API with Black-Scholes fallback)
    greeks_data = await get_flow_greeks(entry.event_id)
    label.update(
        {
            "delta_at_entry": greeks_data.get("delta"),
            "gamma_at_entry": greeks_data.get("gamma"),
            "theta_at_entry": greeks_data.get("theta"),
            "vega_at_entry": greeks_data.get("vega"),
            "rho_at_entry": greeks_data.get("rho"),
            "iv_at_entry_alpaca": greeks_data.get("iv_alpaca"),
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

    # Checkpoint Greeks - fetch live Greeks from Alpaca at each checkpoint
    # Only populates for checkpoints near "now" (within 5 minutes)
    # Historical labels will have NULLs and populate going forward
    try:
        checkpoint_greeks = await get_checkpoint_greeks(
            option_chain=option_chain,
            ticker=ticker,
            entry_ts=entry_ts,
            entry_price=entry_price,
            expiry=expiry,
            dte=dte,
        )
        for cp_suffix, cp_data in checkpoint_greeks.items():
            label[f"delta_at_{cp_suffix}"] = cp_data.get("delta")
            label[f"gamma_at_{cp_suffix}"] = cp_data.get("gamma")
            label[f"theta_at_{cp_suffix}"] = cp_data.get("theta")
            label[f"vega_at_{cp_suffix}"] = cp_data.get("vega")
            label[f"iv_at_{cp_suffix}"] = cp_data.get("iv")
            label[f"dte_at_{cp_suffix}"] = cp_data.get("dte")
            label[f"theta_decay_pct_at_{cp_suffix}"] = cp_data.get("theta_decay_pct")
            label[f"time_value_pct_at_{cp_suffix}"] = cp_data.get("time_value_pct")
            label[f"underlying_at_{cp_suffix}"] = cp_data.get("underlying")
    except Exception as e:
        logger.debug(f"Error fetching checkpoint Greeks: {e}")

    return label


async def persist_labels(labels: list[dict[str, Any]]) -> int:
    """Legacy no-op; local label persistence is decommissioned."""
    if not labels:
        return 0
    logger.warning(
        "Local price-target label persistence is decommissioned; labels must flow through heber.watch",
        extra={
            "event_type": "DEPRECATED_PIPELINE_DISABLED",
            "pipeline": "orion.main_price_target_labeler",
            "operation": "persist_labels",
        },
    )
    return 0


async def run_labeling_loop(shutdown_event: asyncio.Event) -> None:
    """Legacy no-op; local price-target labeling loop is decommissioned."""
    _ = shutdown_event
    logger.warning(
        "Local price-target labeling loop is decommissioned",
        extra={
            "event_type": "DEPRECATED_PIPELINE_DISABLED",
            "pipeline": "orion.main_price_target_labeler",
            "replacement_path": "heber.watch datasets (labels_alert_barriers/meta_label_features) after field mapping signoff",
        },
    )
    return


async def backfill_missing_features(batch_size: int = 100) -> int:
    """Legacy no-op; local ML feature backfill is decommissioned."""
    _ = batch_size
    logger.warning(
        "Local feature backfill is decommissioned; use heber.watch feature pipelines",
        extra={
            "event_type": "DEPRECATED_PIPELINE_DISABLED",
            "pipeline": "orion.main_price_target_labeler",
            "operation": "backfill_missing_features",
        },
    )
    return 0


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
