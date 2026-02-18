"""
Heber Feature Utilities.

Reusable feature-extraction functions that query Heber silver datasets.
Extracted from main_price_target_labeler.py for use by flow_enricher,
backfill jobs, and any other consumer that needs ML feature vectors.
"""

import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from orion.heber.reader import HeberReader
from orion.labeler.constants import RISK_FREE_RATE
from orion.labeler.greeks import calculate_black_scholes_delta, calculate_black_scholes_gamma
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.data.heber_features")
_heber_reader = HeberReader()

_FEATURE_FALLBACK_COUNTS: Dict[str, int] = defaultdict(int)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _record_fallback(feature_name: str, error: Optional[Exception] = None, **context: Any) -> None:
    _FEATURE_FALLBACK_COUNTS[feature_name] += 1
    payload: Dict[str, Any] = {
        "feature": feature_name,
        "fallback_count": _FEATURE_FALLBACK_COUNTS[feature_name],
    }
    if error is not None:
        payload["error"] = str(error)
    payload.update(context)
    logger.warning("Heber feature fallback applied", extra={"event_type": "HEBER_FEATURE_FALLBACK", **payload})


def _pick_first_existing_column(df: pd.DataFrame, columns: List[str]) -> Optional[str]:
    for column in columns:
        if column in df.columns:
            return column
    return None


def _coerce_dt_utc(value: Any) -> Optional[datetime]:
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


def _coerce_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    return ts.date()


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "t"}
    return False


def _normalize_put_call(value: Any) -> Optional[str]:
    if value is None:
        return None
    put_call = str(value).strip().upper()
    if put_call in {"C", "CALL", "CALLS"}:
        return "C"
    if put_call in {"P", "PUT", "PUTS"}:
        return "P"
    return None


def _market_tide_direction(net_premium: float) -> str:
    return "BULLISH" if net_premium > 0 else "BEARISH" if net_premium < 0 else "NEUTRAL"


def _map_vix_proxy_to_regime(vix_proxy: float) -> str:
    if vix_proxy > 30:
        return "EXTREME"
    if vix_proxy > 20:
        return "ELEVATED"
    if vix_proxy > 12:
        return "NORMAL"
    return "LOW"


# ---------------------------------------------------------------------------
# GEX features
# ---------------------------------------------------------------------------


async def get_gex_at_entry(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get the closest GEX values before entry time."""
    heber_result = _get_gex_at_entry_from_heber(ticker, entry_ts)
    if heber_result is not None:
        return heber_result
    return {"gex": None, "vex": None}


def _get_gex_at_entry_from_heber(ticker: str, entry_ts: datetime) -> Optional[Dict[str, Any]]:
    try:
        df = _heber_reader.read_greek_exposure(
            symbols=[ticker],
            asof_time=entry_ts,
            start_time=entry_ts - timedelta(days=30),
        )
    except Exception as e:
        _record_fallback("gex_heber_lookup", e, ticker=ticker)
        return None

    if df.empty:
        return None

    ts_col = _pick_first_existing_column(df, ["ts_utc", "ts_event", "timestamp", "created_at"])
    gex_col = _pick_first_existing_column(df, ["gex_oi", "gex"])
    vex_col = _pick_first_existing_column(df, ["vex_oi", "vex"])
    if ts_col is None or gex_col is None:
        return None

    best_ts: Optional[datetime] = None
    best_values: Optional[Dict[str, Any]] = None
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


async def get_gex_rolling_averages(ticker: str, entry_ts: datetime, days: int = 20) -> Dict[str, Optional[float]]:
    """Get rolling average GEX/VEX values prior to entry time."""
    heber_result = _get_gex_rolling_averages_from_heber(ticker, entry_ts, days=days)
    if heber_result is not None:
        return heber_result
    return {"gex_rolling_avg": None, "vex_rolling_avg": None}


def _get_gex_rolling_averages_from_heber(
    ticker: str,
    entry_ts: datetime,
    days: int = 20,
) -> Optional[Dict[str, Optional[float]]]:
    lookback_days = max(days, 1)
    try:
        df = _heber_reader.read_greek_exposure(
            symbols=[ticker],
            asof_time=entry_ts,
            start_time=entry_ts - timedelta(days=lookback_days),
        )
    except Exception as e:
        _record_fallback("gex_rolling_avg_heber_lookup", e, ticker=ticker)
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


# ---------------------------------------------------------------------------
# Market tide
# ---------------------------------------------------------------------------


async def get_market_tide_before_entry(entry_ts: datetime, minutes: int = 30) -> Dict[str, Any]:
    """Get market tide sum for the period before entry."""
    heber_result = _get_market_tide_before_entry_from_heber(entry_ts, minutes)
    if heber_result is not None:
        return heber_result
    return {"net_premium": None, "direction": None}


def _get_market_tide_before_entry_from_heber(entry_ts: datetime, minutes: int = 30) -> Optional[Dict[str, Any]]:
    net_premium = _get_heber_market_tide_net_premium(entry_ts, minutes)
    if net_premium is None:
        return None
    return {"net_premium": net_premium, "direction": _market_tide_direction(net_premium)}


def _sum_market_tide_from_dataframe(df: pd.DataFrame, start_ts: datetime, entry_ts: datetime) -> Optional[float]:
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


def _get_heber_market_tide_net_premium(entry_ts: datetime, minutes: int = 30) -> Optional[float]:
    start_ts = entry_ts - timedelta(minutes=minutes)
    try:
        tide_df = _heber_reader.read_market_tide(asof_time=entry_ts, start_time=start_ts)
        net_from_tide = _sum_market_tide_from_dataframe(tide_df, start_ts, entry_ts)
        if net_from_tide is not None:
            return net_from_tide
    except Exception as e:
        _record_fallback("market_tide_heber_lookup", e)

    try:
        flow_df = _heber_reader.read_flow(asof_time=entry_ts, start_time=start_ts)
    except Exception as e:
        _record_fallback("market_tide_flow_fallback", e)
        return None
    return _sum_market_tide_from_dataframe(flow_df, start_ts, entry_ts)


# ---------------------------------------------------------------------------
# Window features (1h / 1d / 1w)
# ---------------------------------------------------------------------------


async def get_window_features_at_entry(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
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
        _record_fallback("window_features_heber_lookup", e, ticker=ticker)
        return {}

    flow_frame = _normalize_window_flow_frame(flow_df, ticker=ticker)
    if flow_frame.empty:
        return {}

    darkpool_frame = _normalize_window_darkpool_frame(darkpool_df, ticker=ticker)
    period_windows = {"1h": timedelta(hours=1), "1d": timedelta(days=1), "1w": timedelta(weeks=1)}

    features_by_period: Dict[str, Any] = {}
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

    frame = pd.DataFrame({"ts": ts_series, "ticker": ticker_series, "size": size_series}).dropna(subset=["ts"])
    if frame.empty:
        return frame
    return frame[frame["ticker"] == str(ticker).upper()]


# ---------------------------------------------------------------------------
# VIX proxy
# ---------------------------------------------------------------------------


def _get_heber_vix_proxy_snapshot_at_or_before(entry_ts: datetime) -> Optional[Dict[str, Any]]:
    """Best-effort Heber lookup for VIX proxy (VIXY) and prior-close delta."""
    ts_utc = entry_ts if entry_ts.tzinfo is not None else entry_ts.replace(tzinfo=timezone.utc)
    try:
        bars = _heber_reader.read_bars(
            symbols=["VIXY"],
            asof_time=datetime.now(timezone.utc),
            start_time=ts_utc - timedelta(days=7),
            end_time=ts_utc + timedelta(minutes=1),
        )
    except Exception as exc:
        _record_fallback("heber_vix_proxy_lookup", exc)
        return None

    if bars.empty:
        return None

    ts_col = next((col for col in ("ts_event", "bar_start_ts", "timestamp") if col in bars.columns), None)
    close_col = next((col for col in ("close", "c") if col in bars.columns), None)
    if ts_col is None or close_col is None:
        return None

    candidates: List[tuple[datetime, float]] = []
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
    return {"vix": latest_close, "vix_1d_change": vix_1d_change, "vix_regime": _map_vix_proxy_to_regime(latest_close)}


# ---------------------------------------------------------------------------
# Max pain distance
# ---------------------------------------------------------------------------


async def get_max_pain_distance(ticker: str, expiry_date: Optional[datetime], entry_ts: datetime) -> Optional[float]:
    """Get distance to max pain at entry time."""
    if not expiry_date:
        return None
    return _get_max_pain_distance_from_heber(ticker, expiry_date, entry_ts)


def _get_max_pain_distance_from_heber(ticker: str, expiry_date: Any, entry_ts: datetime) -> Optional[float]:
    entry_date = entry_ts.date()
    expiry = expiry_date.date() if isinstance(expiry_date, datetime) else expiry_date
    try:
        max_pain_df = _heber_reader.read_max_pain(
            symbols=[ticker],
            asof_time=entry_ts,
            start_time=entry_ts - timedelta(days=365),
        )
    except Exception as e:
        _record_fallback("max_pain_heber_lookup", e, ticker=ticker)
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

    best_ts: Optional[datetime] = None
    best_distance: Optional[float] = None
    for _, row in max_pain_df.iterrows():
        row_expiry = _coerce_date(row.get(expiry_col))
        if row_expiry != expiry:
            continue
        row_ts = _coerce_dt_utc(row.get(ts_col))
        if row_ts is None:
            row_date = _coerce_date(row.get(ts_col))
            if row_date is None:
                continue
            row_ts = datetime.combine(row_date, datetime.min.time(), tzinfo=timezone.utc)
        if row_ts.date() > entry_date:
            continue
        distance = _coerce_float(row.get(dist_col))
        if distance is None:
            continue
        if best_ts is None or row_ts > best_ts:
            best_ts = row_ts
            best_distance = distance
    return best_distance


# ---------------------------------------------------------------------------
# IV rank
# ---------------------------------------------------------------------------


async def get_iv_rank_at_entry(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Get IV rank at entry time with Heber-first sourcing."""
    heber_iv_rank = _get_iv_rank_from_heber(ticker, entry_ts)
    if heber_iv_rank is not None:
        return heber_iv_rank
    return _estimate_iv_rank_from_heber_flow(ticker, entry_ts)


def _get_iv_rank_from_heber(ticker: str, target_ts: datetime) -> Optional[float]:
    try:
        iv_rank_df = _heber_reader.read_iv_rank(
            symbols=[ticker],
            asof_time=target_ts,
            start_time=target_ts - timedelta(days=365),
        )
    except Exception as e:
        _record_fallback("iv_rank_heber_lookup", e, ticker=ticker)
        return None

    if iv_rank_df.empty:
        return None

    ts_col = _pick_first_existing_column(iv_rank_df, ["ts_utc", "ts_event", "timestamp", "created_at", "date"])
    iv_rank_col = _pick_first_existing_column(iv_rank_df, ["iv_rank", "iv_rank_pct"])
    if ts_col is None or iv_rank_col is None:
        return None

    best_ts: Optional[datetime] = None
    best_iv_rank: Optional[float] = None
    for _, row in iv_rank_df.iterrows():
        row_ts = _coerce_dt_utc(row.get(ts_col))
        if row_ts is None:
            row_date = _coerce_date(row.get(ts_col))
            if row_date is None:
                continue
            row_ts = datetime.combine(row_date, datetime.min.time(), tzinfo=timezone.utc)
        if row_ts > target_ts:
            continue
        iv_rank = _coerce_float(row.get(iv_rank_col))
        if iv_rank is None:
            continue
        if best_ts is None or row_ts > best_ts:
            best_ts = row_ts
            best_iv_rank = iv_rank
    return best_iv_rank


def _estimate_iv_rank_from_heber_flow(ticker: str, target_ts: datetime) -> Optional[float]:
    """Estimate IV rank from Heber flow IV history when IV-rank snapshots are unavailable."""
    target_utc = _coerce_dt_utc(target_ts)
    if target_utc is None:
        return None
    start_utc = target_utc - timedelta(days=30)
    try:
        flow_df = _heber_reader.read_flow(symbols=[ticker], asof_time=target_utc, start_time=start_utc)
    except Exception as e:
        _record_fallback("iv_rank_heber_flow_estimate", e, ticker=ticker)
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


# ---------------------------------------------------------------------------
# Regime at entry
# ---------------------------------------------------------------------------


async def get_regime_at_entry(entry_ts: datetime) -> Dict[str, Any]:
    """Get regime snapshot at entry time from Heber VIX proxy + market tide."""
    from orion.analysis.regime import MultiAxisRegimeDetector

    detector = MultiAxisRegimeDetector()
    vix_data = _get_heber_vix_proxy_snapshot_at_or_before(entry_ts) or {}
    tide_net = _get_heber_market_tide_net_premium(entry_ts, minutes=30)

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


# ---------------------------------------------------------------------------
# Underlying price helpers
# ---------------------------------------------------------------------------


def _get_heber_close_at_or_before(ticker: str, target_ts: datetime) -> Optional[float]:
    """Best-effort Heber bars lookup for the latest close at-or-before target time."""
    ts_utc = target_ts if target_ts.tzinfo is not None else target_ts.replace(tzinfo=timezone.utc)
    try:
        bars = _heber_reader.read_bars(
            symbols=[ticker],
            asof_time=datetime.now(timezone.utc),
            start_time=ts_utc - timedelta(days=7),
            end_time=ts_utc + timedelta(minutes=1),
        )
    except Exception as exc:
        _record_fallback("heber_bar_lookup", exc, ticker=ticker)
        return None

    if bars.empty:
        return None

    ts_col = next((col for col in ("ts_event", "bar_start_ts", "timestamp") if col in bars.columns), None)
    close_col = next((col for col in ("close", "c") if col in bars.columns), None)
    if ts_col is None or close_col is None:
        return None

    candidates: List[tuple[datetime, float]] = []
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


# ---------------------------------------------------------------------------
# Flow Greeks
# ---------------------------------------------------------------------------


async def get_flow_greeks(event_id: str) -> Dict[str, Optional[float]]:
    """Get Greeks from stored values, with Black-Scholes fallback."""
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

    if flow_data.get("delta_stored") is not None:
        result["delta"] = flow_data.get("delta_stored")
        result["gamma"] = flow_data.get("gamma_stored")
        result["theta"] = flow_data.get("theta_stored")
        result["vega"] = flow_data.get("vega_stored")
        result["rho"] = flow_data.get("rho_stored")
        result["iv_alpaca"] = flow_data.get("iv_alpaca_stored")
        return result

    S = float(flow_data.get("underlying_price") or 0)
    K = float(flow_data.get("strike") or 0)
    iv = flow_data.get("iv")
    sigma = float(iv) if iv else 0
    put_call = flow_data.get("put_call", "C")
    expiry = flow_data.get("expiry")
    flow_ts = flow_data.get("flow_ts")

    T = 0.0
    if expiry and flow_ts:
        if isinstance(expiry, str):
            try:
                expiry_dt = datetime.strptime(expiry, "%Y-%m-%d")
            except ValueError:
                expiry_dt = None
        else:
            expiry_dt = expiry
        if expiry_dt:
            expiry_date = expiry_dt.date() if hasattr(expiry_dt, "date") else expiry_dt
            flow_date = flow_ts.date() if hasattr(flow_ts, "date") else flow_ts
            days_to_expiry = (expiry_date - flow_date).days
            T = max(days_to_expiry / 365.0, 1 / 365.0)

    if S > 0 and K > 0 and sigma > 0 and T > 0:
        result["delta"] = calculate_black_scholes_delta(S, K, T, RISK_FREE_RATE, sigma, put_call)
        result["gamma"] = calculate_black_scholes_gamma(S, K, T, RISK_FREE_RATE, sigma)

    return result


def _get_flow_greeks_from_heber(event_id: str) -> Optional[Dict[str, Any]]:
    event_id_str = str(event_id)
    try:
        flow_df = _heber_reader.read_flow(
            asof_time=datetime.now(timezone.utc),
            start_time=datetime.now(timezone.utc) - timedelta(days=365),
        )
    except Exception as e:
        _record_fallback("flow_greeks_heber_lookup", e, event_id=event_id_str)
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

    def _first_value(columns: List[str]) -> Any:
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


# ---------------------------------------------------------------------------
# Darkpool metrics
# ---------------------------------------------------------------------------


async def get_darkpool_metrics(ticker: str, entry_ts: datetime) -> Dict[str, Optional[float]]:
    """Get darkpool metrics for all trade bucket windows."""
    results = await asyncio.gather(
        get_darkpool_volume(ticker, entry_ts, 15),
        get_darkpool_volume(ticker, entry_ts, 30),
        get_darkpool_volume(ticker, entry_ts, 60),
        get_darkpool_volume(ticker, entry_ts, 240),
        get_darkpool_volume(ticker, entry_ts, 1440),
        get_darkpool_volume(ticker, entry_ts, 4320),
        get_darkpool_volume(ticker, entry_ts, 10080),
        get_darkpool_volume(ticker, entry_ts, 20160),
        get_darkpool_volume(ticker, entry_ts, 40320),
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


async def get_darkpool_volume(ticker: str, entry_ts: datetime, window_minutes: int = 60) -> Optional[float]:
    """Get aggregate darkpool volume in a time window before entry."""
    heber_volume = _get_darkpool_volume_from_heber(ticker, entry_ts, window_minutes)
    if heber_volume is not None:
        return heber_volume
    return None


def _get_darkpool_volume_from_heber(ticker: str, entry_ts: datetime, window_minutes: int = 60) -> Optional[float]:
    start_ts = entry_ts - timedelta(minutes=window_minutes)
    try:
        darkpool_df = _heber_reader.read_darkpool(
            symbols=[ticker],
            start_time=start_ts,
            asof_time=entry_ts,
        )
    except Exception as e:
        _record_fallback("darkpool_heber_lookup", e, ticker=ticker)
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


# ---------------------------------------------------------------------------
# RVOL metrics
# ---------------------------------------------------------------------------


async def get_rvol_metrics(ticker: str, entry_ts: datetime) -> Dict[str, Optional[float]]:
    """Get relative volume metrics vs historical average."""
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


def _get_rvol_metrics_from_heber(ticker: str, entry_ts: datetime) -> Optional[Dict[str, Optional[float]]]:
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
        bars_df = _heber_reader.read_bars(symbols=[ticker], asof_time=entry_utc, start_time=lookback_4w)
    except Exception as e:
        _record_fallback("rvol_heber_lookup", e, ticker=ticker)
        return None

    if bars_df.empty:
        return None

    ts_col = _pick_first_existing_column(bars_df, ["ts_event", "bar_start_ts", "bar_start_ts_utc", "ts_utc"])
    volume_col = _pick_first_existing_column(bars_df, ["volume", "shares", "size"])
    if ts_col is None or volume_col is None:
        return None

    ts_series = pd.to_datetime(bars_df[ts_col], utc=True, errors="coerce")
    volume_series = pd.to_numeric(bars_df[volume_col], errors="coerce")
    df = pd.DataFrame({"ts": ts_series, "volume": volume_series}).dropna(subset=["ts", "volume"])
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


# ---------------------------------------------------------------------------
# Flow aggression
# ---------------------------------------------------------------------------


async def get_flow_aggression(ticker: str, entry_ts: datetime) -> Dict[str, Optional[float]]:
    """Get flow aggression metrics for this ticker in the last hour."""
    heber_result = _get_flow_aggression_from_heber(ticker, entry_ts)
    if heber_result is not None:
        return heber_result
    return {"ask_side_ratio": None, "sweep_ratio_1h": None, "same_ticker_premium_1h": None}


def _get_flow_aggression_from_heber(ticker: str, entry_ts: datetime) -> Optional[Dict[str, Optional[float]]]:
    entry_utc = _coerce_dt_utc(entry_ts)
    if entry_utc is None:
        return None
    start_utc = entry_utc - timedelta(hours=1)

    try:
        flow_df = _heber_reader.read_flow(symbols=[ticker], asof_time=entry_utc, start_time=start_utc)
    except Exception as e:
        _record_fallback("flow_aggression_heber", e, ticker=ticker)
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

    ticker_series = (
        flow_df[ticker_col].astype(str).str.upper().str.split(":").str[-1]
        if ticker_col
        else pd.Series([str(ticker).upper()] * len(flow_df))
    )
    sweep_series = flow_df[sweep_col].map(_is_truthy) if sweep_col else pd.Series([False] * len(flow_df))
    aggressor_series = (
        flow_df[aggressor_col].astype(str).str.upper() if aggressor_col else pd.Series([""] * len(flow_df))
    )

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


# ---------------------------------------------------------------------------
# Earnings proximity
# ---------------------------------------------------------------------------


async def get_earnings_proximity(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get days to/from earnings based on UW ticker info."""
    from orion.main_price_target_labeler import get_ticker_info

    info = await get_ticker_info(ticker)
    next_earnings = info.get("next_earnings_date")
    last_earnings = info.get("last_earnings_date")
    entry_date = entry_ts.date()

    if next_earnings:
        days_diff = (next_earnings - entry_date).days
        if days_diff < 0:
            return {"days_to_earnings": None, "is_post_earnings": True}
        else:
            return {"days_to_earnings": days_diff, "is_post_earnings": False}

    if last_earnings:
        if isinstance(last_earnings, str):
            from dateutil.parser import parse as parse_date

            try:
                last_earnings = parse_date(last_earnings).date()
            except Exception:
                return {"days_to_earnings": None, "is_post_earnings": None}

        days_since = (entry_date - last_earnings).days
        is_post = 0 <= days_since <= 7
        return {"days_to_earnings": None, "is_post_earnings": is_post}

    return {"days_to_earnings": None, "is_post_earnings": None}


# ---------------------------------------------------------------------------
# P2 features (OI change, IV vs HV)
# ---------------------------------------------------------------------------


async def get_p2_features(ticker: str, option_chain: str, entry_ts: datetime) -> Dict[str, Optional[float]]:
    """Get P2 ML features: OI change momentum and IV vs HV ratio."""
    heber_result = _get_p2_features_from_heber(ticker, option_chain, entry_ts)
    if heber_result is not None:
        return heber_result
    return {"oi_change_1d": None, "oi_change_pct": None, "iv_vs_hv_ratio": None, "hv_30d": None}


def _get_p2_features_from_heber(
    ticker: str,
    option_chain: str,
    entry_ts: datetime,
) -> Optional[Dict[str, Optional[float]]]:
    import statistics

    result: Dict[str, Optional[float]] = {
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
        flow_df = _heber_reader.read_flow(asof_time=entry_utc, start_time=entry_utc - timedelta(days=35))
    except Exception as e:
        _record_fallback("p2_flow_heber_lookup", e, ticker=ticker, option_chain=option_chain)
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
    flow_oi = pd.to_numeric(flow_df[oi_col], errors="coerce") if oi_col else pd.Series([np.nan] * len(flow_df))
    flow_iv = pd.to_numeric(flow_df[iv_col], errors="coerce") if iv_col else pd.Series([np.nan] * len(flow_df))

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

    current_oi: Optional[float] = None
    prior_oi: Optional[float] = None
    iv_value: Optional[float] = None
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
            symbols=[ticker], asof_time=entry_utc, start_time=entry_utc - timedelta(days=35)
        )
    except Exception as e:
        _record_fallback("p2_hv_heber_lookup", e, ticker=ticker)
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
            bar_ticker = (
                bars_df[bar_ticker_col].astype(str).str.upper().str.split(":").str[-1]
                if bar_ticker_col
                else pd.Series([str(ticker).upper()] * len(bars_df))
            )

            bars_norm = pd.DataFrame({"ts": bar_ts, "ticker": bar_ticker, "close": bar_close}).dropna(
                subset=["ts", "close"]
            )
            if not bars_norm.empty:
                bars_norm = bars_norm[
                    (bars_norm["ticker"] == str(ticker).upper())
                    & (bars_norm["ts"].dt.date >= (entry_date - timedelta(days=30)))
                    & (bars_norm["ts"].dt.date < entry_date)
                ].sort_values("ts")

                closes = [float(v) for v in bars_norm["close"].tolist() if v]
                if len(closes) >= 10:
                    returns = [
                        (closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0
                    ]
                    if len(returns) > 1:
                        result["hv_30d"] = statistics.stdev(returns) * (252**0.5) * 100

    if iv_value is not None and result["hv_30d"] is not None and result["hv_30d"] > 0:
        iv_pct = iv_value * 100 if iv_value < 2 else iv_value
        result["iv_vs_hv_ratio"] = iv_pct / result["hv_30d"]

    if any(v is not None for v in result.values()):
        return result
    return None


# ---------------------------------------------------------------------------
# P3 features (52w high distance, spread detection)
# ---------------------------------------------------------------------------


async def get_p3_features(ticker: str, option_chain: str, expiry: datetime, entry_ts: datetime) -> Dict[str, Any]:
    """Get P3 ML features: 52w high distance, spread detection, same-expiry trades."""
    heber_result = _get_p3_features_from_heber(ticker, expiry, entry_ts)
    if heber_result is not None:
        return heber_result
    return {"high_52w_distance_pct": None, "is_spread_leg": None, "same_expiry_trades_1h": None}


def _get_p3_features_from_heber(ticker: str, expiry: datetime, entry_ts: datetime) -> Optional[Dict[str, Any]]:
    result: Dict[str, Any] = {"high_52w_distance_pct": None, "is_spread_leg": None, "same_expiry_trades_1h": None}
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
            symbols=[ticker], asof_time=entry_utc, start_time=entry_utc - timedelta(days=370)
        )
    except Exception as e:
        _record_fallback("p3_bars_heber_lookup", e, ticker=ticker)
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
            ticker_series = (
                bars_df[ticker_col].astype(str).str.upper().str.split(":").str[-1]
                if ticker_col
                else pd.Series([ticker_upper] * len(bars_df))
            )

            normalized_bars = pd.DataFrame(
                {"ts": ts_series, "ticker": ticker_series, "high": high_series, "close": close_series}
            ).dropna(subset=["ts"])
            if not normalized_bars.empty:
                scoped = normalized_bars[
                    (normalized_bars["ticker"] == ticker_upper) & (normalized_bars["ts"] < entry_utc)
                ].sort_values("ts")
                if not scoped.empty:
                    history_52w = scoped[(scoped["ts"].dt.date >= lookback_52w) & (scoped["ts"].dt.date < entry_date)]
                    high_52w = (
                        pd.to_numeric(history_52w["high"], errors="coerce").max() if not history_52w.empty else None
                    )

                    current_price = _coerce_float(scoped.iloc[-1].get("close"))
                    if high_52w is not None and current_price is not None and high_52w > 0:
                        result["high_52w_distance_pct"] = ((float(high_52w) - current_price) / float(high_52w)) * 100.0

    if expiry_str:
        try:
            flow_df = _heber_reader.read_flow(
                symbols=[ticker], asof_time=entry_utc, start_time=lookback_1h - timedelta(minutes=5)
            )
        except Exception as e:
            _record_fallback("p3_flow_heber_lookup", e, ticker=ticker)
            flow_df = pd.DataFrame()

        if not flow_df.empty:
            flow_ts_col = _pick_first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
            flow_ticker_col = _pick_first_existing_column(flow_df, ["ticker", "symbol", "underlying", "instrument_key"])
            flow_expiry_col = _pick_first_existing_column(flow_df, ["expiry", "expiration", "exp_date"])
            if flow_ts_col is not None and flow_expiry_col is not None:
                flow_ts = pd.to_datetime(flow_df[flow_ts_col], utc=True, errors="coerce")
                flow_expiry = pd.to_datetime(flow_df[flow_expiry_col], errors="coerce").dt.date.astype(str)
                flow_ticker = (
                    flow_df[flow_ticker_col].astype(str).str.upper().str.split(":").str[-1]
                    if flow_ticker_col
                    else pd.Series([ticker_upper] * len(flow_df))
                )

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

    if any(v is not None for v in result.values()):
        return result
    return None


# ---------------------------------------------------------------------------
# Sector correlation features
# ---------------------------------------------------------------------------


async def get_sector_correlation_features(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
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


def _get_sector_correlation_features_from_heber(ticker: str, entry_ts: datetime) -> Optional[Dict[str, Any]]:
    result: Dict[str, Any] = {
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

    # Sector net premium from flow
    try:
        flow_df = _heber_reader.read_flow(asof_time=entry_utc, start_time=lookback_1h - timedelta(hours=1))
    except Exception as e:
        _record_fallback("sector_net_premium_1h_heber", e, ticker=ticker)
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
                ticker_rows = temp_df[temp_df["ticker"].str.upper() == ticker_upper]
                ticker_sector = None
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

    # SPY return 1h
    try:
        spy_bars_df = _heber_reader.read_bars(
            symbols=["SPY"], asof_time=entry_utc, start_time=lookback_1h - timedelta(hours=2)
        )
    except Exception as e:
        _record_fallback("spy_return_1h_heber", e, ticker=ticker)
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

    # SPY correlation 5d
    correlation_start = datetime.combine(lookback_5d, datetime.min.time(), tzinfo=timezone.utc)
    try:
        bars_df = _heber_reader.read_bars(symbols=[ticker, "SPY"], asof_time=entry_utc, start_time=correlation_start)
    except Exception as e:
        _record_fallback("spy_correlation_5d_heber", e, ticker=ticker)
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

    if any(v is not None for v in result.values()):
        return result
    return None
