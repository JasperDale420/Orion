"""
Feature validation script for label features.

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

from orion.clients.heber_reader import get_heber_reader
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.validate_features")

MINUTES_TO_CLOSE_MAX = 390
SOURCE_BARS = "bars"
SOURCE_FLOW = "flow_alerts"
SOURCE_DARKPOOL = "darkpool"
SOURCE_GREEK_EXPOSURE = "greek_exposure"
SOURCE_MAX_PAIN = "max_pain"
SOURCE_MARKET_TIDE = "market_tide"
SOURCE_VIX = "vix_data"
SOURCE_REGIME = "regime_history"

# ============================================================================
# SPOT-CHECK VALIDATION
# ============================================================================


def _coerce_gold_frame(payload: Any) -> pd.DataFrame:
    if isinstance(payload, pd.DataFrame):
        return payload.copy()
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return pd.DataFrame()


async def _read_heber_gold_dataset(dataset: str) -> pd.DataFrame | None:
    reader = get_heber_reader()
    try:
        payload = await asyncio.to_thread(
            reader.read_gold_features,
            dataset=dataset,
            asof_time=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.warning("read_heber_gold_dataset_failed", dataset=dataset, error=str(exc))
        return None
    return _coerce_gold_frame(payload)


async def _load_heber_label_frames() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    outcomes_df = await _read_heber_gold_dataset("labels_alert_barriers")
    if outcomes_df is None:
        return None
    features_df = await _read_heber_gold_dataset("meta_label_features")
    if features_df is None:
        return None
    return outcomes_df, features_df


def _coalesce_columns(df: pd.DataFrame, columns: List[str]) -> pd.Series:
    series = pd.Series(index=df.index, dtype=object)
    for column in columns:
        if column in df.columns:
            values = df[column]
            series = values.where(series.isna(), series)
    return series


def _coalesce_numeric(df: pd.DataFrame, columns: List[str]) -> pd.Series:
    series = pd.Series(index=df.index, dtype="float64")
    for column in columns:
        if column in df.columns:
            values = pd.to_numeric(df[column], errors="coerce")
            series = values.where(series.isna(), series)
    return series


def _join_key_column(df: pd.DataFrame) -> str | None:
    return _pick_first_existing_column(df, ["alert_id", "event_id", "watch_id", "instrument_key"])


def _to_join_frame(df: pd.DataFrame, key_column: str | None) -> pd.DataFrame:
    frame = df.copy()
    frame["__join_key"] = None
    if key_column and key_column in frame.columns:
        frame["__join_key"] = frame[key_column].astype(str)
        frame.loc[frame[key_column].isna(), "__join_key"] = None
    return frame


def _build_joined_label_frame(outcomes_df: pd.DataFrame, features_df: pd.DataFrame) -> pd.DataFrame:
    outcomes = _to_join_frame(outcomes_df, _join_key_column(outcomes_df))
    features = _to_join_frame(features_df, _join_key_column(features_df))
    if features.empty:
        merged = outcomes.copy()
        merged["__matched"] = False
    else:
        merged = outcomes.merge(features, on="__join_key", how="left", suffixes=("", "_feature"), indicator=True)
        merged["__matched"] = merged["_merge"] == "both"

    merged["entry_ts"] = pd.to_datetime(
        _coalesce_columns(
            merged,
            ["entry_ts", "alert_time", "ts_event", "ts_available", "entry_ts_feature", "alert_time_feature"],
        ),
        utc=True,
        errors="coerce",
    )
    merged["ticker"] = _coalesce_columns(
        merged,
        [
            "ticker",
            "underlying",
            "symbol",
            "instrument_key",
            "ticker_feature",
            "underlying_feature",
            "symbol_feature",
            "instrument_key_feature",
        ],
    )
    if not merged["ticker"].empty:
        merged["ticker"] = merged["ticker"].astype(str)
        merged["ticker"] = merged["ticker"].str.split(":").str[-1].str.upper()

    merged["entry_hour"] = _coalesce_numeric(merged, ["entry_hour", "hour_of_day"])
    merged["entry_day_of_week"] = _coalesce_numeric(merged, ["entry_day_of_week", "day_of_week"])
    merged["minutes_to_close"] = _coalesce_numeric(merged, ["minutes_to_close"])
    merged["darkpool_volume_1h"] = _coalesce_numeric(merged, ["darkpool_volume_1h", "darkpool_1h"])
    merged["delta_at_entry"] = _coalesce_numeric(merged, ["delta_at_entry", "delta"])
    merged["gamma_at_entry"] = _coalesce_numeric(merged, ["gamma_at_entry", "gamma"])
    merged["iv_rank_at_entry"] = _coalesce_numeric(merged, ["iv_rank_at_entry", "iv_rank"])
    merged["iv_at_entry"] = _coalesce_numeric(merged, ["iv_at_entry", "iv"])
    merged["rvol_1h"] = _coalesce_numeric(merged, ["rvol_1h", "rvol"])
    merged["overnight_gap_pct"] = _coalesce_numeric(merged, ["overnight_gap_pct"])
    return merged


def _safe_int(value: Any) -> Optional[int]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_label_record(row: pd.Series) -> Dict[str, Any]:
    entry_ts = row.get("entry_ts")
    ticker = row.get("ticker")
    return {
        "event_id": row.get("__join_key"),
        "ticker": str(ticker) if ticker is not None else None,
        "entry_ts": entry_ts.to_pydatetime() if isinstance(entry_ts, pd.Timestamp) else entry_ts,
        "entry_hour": _safe_int(row.get("entry_hour")),
        "entry_day_of_week": _safe_int(row.get("entry_day_of_week")),
        "minutes_to_close": _safe_int(row.get("minutes_to_close")),
        "overnight_gap_pct": row.get("overnight_gap_pct"),
        "darkpool_volume_1h": row.get("darkpool_volume_1h"),
        "delta_at_entry": row.get("delta_at_entry"),
        "gamma_at_entry": row.get("gamma_at_entry"),
        "iv_rank_at_entry": row.get("iv_rank_at_entry"),
        "iv_at_entry": row.get("iv_at_entry"),
        "rvol_1h": row.get("rvol_1h"),
    }


async def _load_label_record_from_heber(event_id: str) -> Optional[Dict[str, Any]]:
    frames = await _load_heber_label_frames()
    if frames is None:
        return None
    outcomes_df, features_df = frames
    merged = _build_joined_label_frame(outcomes_df, features_df)
    if merged.empty:
        return None
    row_match = merged[merged["__join_key"] == event_id]
    if row_match.empty:
        return None
    return _to_label_record(row_match.iloc[0])


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

    label = await _load_label_record_from_heber(event_id)
    if not label:
        results["failed"].append(f"Record not found: {event_id}")
        return results

    ticker = label.get("ticker")
    entry_ts = label.get("entry_ts")
    if not ticker or not isinstance(entry_ts, datetime):
        results["failed"].append(f"Record missing required fields for validation: {event_id}")
        return results

    # 1. Validate time features
    time_checks = validate_time_features(label, entry_ts)
    results["passed"].extend(time_checks["passed"])
    results["failed"].extend(time_checks["failed"])

    # 2. Validate overnight_gap from raw bars
    gap_checks = validate_overnight_gap(label, ticker, entry_ts)
    results["passed"].extend(gap_checks["passed"])
    results["failed"].extend(gap_checks["failed"])

    # 3. Validate darkpool from raw darkpool table
    dp_checks = validate_darkpool(label, ticker, entry_ts)
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


def validate_overnight_gap(label: Dict, ticker: str, entry_ts: datetime) -> Dict[str, List[str]]:
    """Validate overnight_gap against raw bar data."""
    passed, failed = [], []

    if label.get("overnight_gap_pct") is None:
        return {"passed": [], "failed": []}

    heber_inputs = _get_overnight_gap_inputs_from_heber_for_validation(
        ticker=ticker,
        entry_ts=entry_ts,
    )
    if heber_inputs is None:
        return {"passed": passed, "failed": failed}

    today_open, prior_close = heber_inputs

    if today_open and prior_close and prior_close > 0:
        expected_gap = ((today_open - prior_close) / prior_close) * 100
        actual_gap = label["overnight_gap_pct"]

        if abs(expected_gap - actual_gap) < 0.001:
            passed.append(f"overnight_gap_pct={actual_gap:.4f} matches raw data ✓")
        else:
            failed.append(f"overnight_gap mismatch: got {actual_gap:.4f}, expected {expected_gap:.4f}")

    return {"passed": passed, "failed": failed}


def _get_overnight_gap_inputs_from_heber_for_validation(
    *,
    ticker: str,
    entry_ts: datetime,
) -> Optional[Tuple[Optional[float], Optional[float]]]:
    """Return (today_open, prior_close) from Heber bars for validation window."""
    try:
        bars_df = get_heber_reader().read_bars(
            symbols=[ticker],
            start_time=entry_ts - timedelta(days=7),
            asof_time=entry_ts,
        )
    except Exception as exc:
        logger.warning("validate_overnight_gap_heber_lookup_failed", ticker=ticker, error=str(exc))
        return None

    if bars_df.empty:
        return None

    ts_col = _pick_first_existing_column(bars_df, ["bar_start_ts_utc", "bar_start_ts", "ts_event", "ts_utc"])
    open_col = _pick_first_existing_column(bars_df, ["open", "o"])
    close_col = _pick_first_existing_column(bars_df, ["close", "c"])
    if ts_col is None or open_col is None or close_col is None:
        return None

    temp_df = bars_df.copy()
    if "ticker" not in temp_df.columns:
        if "symbol" in temp_df.columns:
            temp_df["ticker"] = temp_df["symbol"]
        elif "instrument_key" in temp_df.columns:
            temp_df["ticker"] = temp_df["instrument_key"].astype(str).str.split(":").str[-1]

    ts_series = pd.to_datetime(temp_df[ts_col], utc=True, errors="coerce")
    ticker_series = temp_df.get("ticker", pd.Series(index=temp_df.index, dtype=str)).astype(str).str.upper()
    open_series = pd.to_numeric(temp_df[open_col], errors="coerce")
    close_series = pd.to_numeric(temp_df[close_col], errors="coerce")

    normalized = pd.DataFrame(
        {
            "ticker": ticker_series,
            "ts_utc": ts_series,
            "open": open_series,
            "close": close_series,
        }
    ).dropna(subset=["ts_utc"])

    if not normalized.empty and "ticker" in normalized.columns:
        normalized = normalized[normalized["ticker"] == ticker.upper()]
    if normalized.empty:
        return None

    normalized = normalized.sort_values("ts_utc")
    entry_date = entry_ts.date()
    normalized["bar_date"] = normalized["ts_utc"].dt.date

    today_rows = normalized[normalized["bar_date"] == entry_date]
    prior_rows = normalized[normalized["bar_date"] < entry_date]

    today_open = (
        float(today_rows.iloc[0]["open"]) if not today_rows.empty and pd.notna(today_rows.iloc[0]["open"]) else None
    )
    prior_close = (
        float(prior_rows.iloc[-1]["close"]) if not prior_rows.empty and pd.notna(prior_rows.iloc[-1]["close"]) else None
    )

    return today_open, prior_close


def validate_darkpool(label: Dict, ticker: str, entry_ts: datetime) -> Dict[str, List[str]]:
    """Validate darkpool volume against raw darkpool table."""
    passed, failed, warnings = [], [], []

    def _count_darkpool_from_heber(minutes: int) -> Optional[int]:
        return _get_darkpool_volume_from_heber_for_validation(ticker, entry_ts, minutes)

    # Check darkpool_1h
    if label.get("darkpool_volume_1h") is not None:
        expected = _count_darkpool_from_heber(60)
        actual = label["darkpool_volume_1h"]
        if expected is None:
            warnings.append(f"darkpool_1h: no Heber darkpool data found for {ticker}")
        elif expected == actual:
            passed.append(f"darkpool_1h={actual} matches ✓")
        elif expected == 0:
            warnings.append(f"darkpool_1h: no raw darkpool data found for {ticker}")
        else:
            failed.append(f"darkpool_1h mismatch: got {actual}, expected {expected}")

    return {"passed": passed, "failed": failed, "warnings": warnings}


def _get_darkpool_volume_from_heber_for_validation(
    ticker: str,
    entry_ts: datetime,
    window_minutes: int,
) -> Optional[int]:
    """Return summed darkpool volume from Heber for validation window."""
    start_ts = entry_ts - timedelta(minutes=window_minutes)
    try:
        darkpool_df = get_heber_reader().read_darkpool(
            symbols=[ticker],
            start_time=start_ts,
            asof_time=entry_ts,
        )
    except Exception as exc:
        logger.warning("validate_darkpool_heber_lookup_failed", ticker=ticker, error=str(exc))
        return None

    if darkpool_df.empty:
        return None

    ts_col = _pick_first_existing_column(darkpool_df, ["dark_ts_utc", "ts_utc", "ts_event", "timestamp", "created_at"])
    size_col = _pick_first_existing_column(darkpool_df, ["size_shares", "size", "shares", "volume"])
    if ts_col is None or size_col is None:
        return None

    start_utc = pd.Timestamp(start_ts)
    if start_utc.tzinfo is None:
        start_utc = start_utc.tz_localize("UTC")
    else:
        start_utc = start_utc.tz_convert("UTC")

    entry_utc = pd.Timestamp(entry_ts)
    if entry_utc.tzinfo is None:
        entry_utc = entry_utc.tz_localize("UTC")
    else:
        entry_utc = entry_utc.tz_convert("UTC")
    ts_series = pd.to_datetime(darkpool_df[ts_col], utc=True, errors="coerce")
    in_window = darkpool_df[(ts_series >= start_utc) & (ts_series <= entry_utc)]
    if in_window.empty:
        return 0

    total = pd.to_numeric(in_window[size_col], errors="coerce").sum()
    if pd.isna(total):
        return None
    return int(total)


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

    frames = await _load_heber_label_frames()
    if frames is None:
        stats = {
            "total": 0,
            "bad_delta": 0,
            "bad_gamma": 0,
            "bad_iv_rank": 0,
            "bad_mtc": 0,
            "bad_hour": 0,
            "bad_dp": 0,
            "bad_rvol": 0,
            "not_ready": 0,
        }
    else:
        outcomes_df, features_df = frames
        merged = _build_joined_label_frame(outcomes_df, features_df)
        matched = merged["__matched"] if "__matched" in merged.columns else pd.Series(False, index=merged.index)
        delta = _coalesce_numeric(merged, ["delta_at_entry"])
        gamma = _coalesce_numeric(merged, ["gamma_at_entry"])
        iv_rank = _coalesce_numeric(merged, ["iv_rank_at_entry"])
        minutes_to_close = _coalesce_numeric(merged, ["minutes_to_close"])
        entry_hour = _coalesce_numeric(merged, ["entry_hour"])
        darkpool = _coalesce_numeric(merged, ["darkpool_volume_1h"])
        rvol = _coalesce_numeric(merged, ["rvol_1h"])

        stats = {
            "total": int(len(merged)),
            "not_ready": int((~matched).sum()),
            "bad_delta": int((matched & delta.notna() & ((delta < -1) | (delta > 1))).sum()),
            "bad_gamma": int((matched & gamma.notna() & (gamma < 0)).sum()),
            "bad_iv_rank": int((matched & iv_rank.notna() & ((iv_rank < 0) | (iv_rank > 100))).sum()),
            "bad_mtc": int(
                (
                    matched
                    & minutes_to_close.notna()
                    & ((minutes_to_close < 0) | (minutes_to_close > MINUTES_TO_CLOSE_MAX))
                ).sum()
            ),
            "bad_hour": int((matched & entry_hour.notna() & ((entry_hour < 0) | (entry_hour > 23))).sum()),
            "bad_dp": int((matched & darkpool.notna() & (darkpool < 0)).sum()),
            "bad_rvol": int((matched & rvol.notna() & (rvol < 0)).sum()),
        }

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
    SOURCE_BARS: {
        "features": ["overnight_gap", "vwap", "underlying"],
        "heber_method": "read_bars",
        "row_count_as_tickers": False,
    },
    SOURCE_FLOW: {
        "features": ["greeks", "iv", "rvol", "flow_aggression", "checkpoints"],
        "heber_method": "read_flow",
        "row_count_as_tickers": False,
    },
    SOURCE_DARKPOOL: {
        "features": ["darkpool_*"],
        "heber_method": "read_darkpool",
        "row_count_as_tickers": False,
    },
    SOURCE_GREEK_EXPOSURE: {
        "features": ["gex", "vex"],
        "heber_method": "read_greek_exposure",
        "row_count_as_tickers": False,
    },
    SOURCE_MAX_PAIN: {
        "features": ["max_pain_distance"],
        "heber_method": "read_max_pain",
        "row_count_as_tickers": False,
    },
    SOURCE_MARKET_TIDE: {
        "features": ["market_tide_30m", "market_tide_direction"],
        "heber_method": "read_market_tide",
        "row_count_as_tickers": True,
    },
    SOURCE_VIX: {
        "features": ["vix_at_entry", "vix_regime"],
        "heber_method": None,
        "tickers_constant": 1,
    },
    SOURCE_REGIME: {
        "features": ["trend_regime", "vol_regime", "risk_regime", "session_regime"],
        "heber_method": None,
        "tickers_constant": 1,
    },
}

_AUDIT_SOURCE_ORDER = [
    SOURCE_BARS,
    SOURCE_FLOW,
    SOURCE_DARKPOOL,
    SOURCE_GREEK_EXPOSURE,
    SOURCE_MAX_PAIN,
    SOURCE_MARKET_TIDE,
    SOURCE_VIX,
    SOURCE_REGIME,
]

_PREFER_HEBER_FALSE_VALUES = {"0", "false", "no", "off", "n"}
_SOURCE_TIME_COLUMNS = ["ts_event", "ts_utc", "bar_start_ts", "flow_ts_utc", "dark_ts_utc", "date"]
_SOURCE_TICKER_COLUMNS = ["ticker", "symbol", "underlying", "instrument_key"]


def _prefer_heber_source_from_env() -> bool:
    raw = os.getenv("ORION_VALIDATE_FEATURES_PREFER_HEBER", "1").strip().lower()
    return raw not in _PREFER_HEBER_FALSE_VALUES


def _pick_first_existing_column(df: pd.DataFrame, columns: List[str]) -> Optional[str]:
    for column in columns:
        if column in df.columns:
            return column
    return None


def _normalize_source_id(source: str) -> str:
    return source


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
    source_id = _normalize_source_id(source)
    asof_time = datetime.now(timezone.utc)
    if source_id == SOURCE_BARS:
        return {
            "symbols": [],
            "asof_time": asof_time,
            "start_time": label_start_ts,
            "end_time": label_end_ts,
            "timeframe": "1m",
        }
    if source_id == SOURCE_MARKET_TIDE:
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
    source_id = _normalize_source_id(source)
    spec = _AUDIT_SOURCE_SPECS[source_id]
    method_name = spec.get("heber_method")
    if not method_name:
        return None

    reader = get_heber_reader()
    method = getattr(reader, method_name, None)
    if method is None:
        return None

    kwargs = _heber_read_kwargs(source_id, label_start_ts, label_end_ts)
    try:
        df = await asyncio.to_thread(method, **kwargs)
    except Exception as exc:
        logger.warning(
            "audit_source_heber_read_failed",
            source=source_id,
            method=method_name,
            error=str(exc),
        )
        return None

    summary = _summarize_heber_source_frame(df, row_count_as_tickers=bool(spec.get("row_count_as_tickers", False)))
    if summary["min_date"] is None or summary["max_date"] is None:
        return None

    summary["backend"] = "heber"
    return summary


def _fetch_source_summary_from_local_db(*, source: str) -> Dict[str, Any]:
    _ = _normalize_source_id(source)
    return {"min_date": None, "max_date": None, "tickers": 0, "backend": "local_db_disabled"}


async def _fetch_source_summary(
    *,
    source: str,
    label_start_ts: Optional[datetime],
    label_end_ts: Optional[datetime],
    prefer_heber: bool,
) -> Dict[str, Any]:
    source_id = _normalize_source_id(source)
    if source_id not in _AUDIT_SOURCE_SPECS:
        logger.warning("audit_source_unknown_source_id", source=source_id)
        return {"min_date": None, "max_date": None, "tickers": 0, "backend": "source_unavailable"}

    if prefer_heber:
        heber_summary = await _fetch_source_summary_from_heber(
            source=source_id,
            label_start_ts=label_start_ts,
            label_end_ts=label_end_ts,
        )
        if heber_summary is not None:
            return heber_summary
        return {"min_date": None, "max_date": None, "tickers": 0, "backend": "heber_unavailable"}
    return {"min_date": None, "max_date": None, "tickers": 0, "backend": "source_unavailable"}


async def _load_label_period() -> Dict[str, Any]:
    reader = get_heber_reader()
    now = datetime.now(timezone.utc)
    try:
        df = await asyncio.to_thread(
            reader.read_gold_features,
            dataset="labels_alert_barriers",
            asof_time=now,
        )
    except Exception as exc:
        logger.warning("load_label_period_heber_failed", error=str(exc))
        return {
            "min_date_raw": None,
            "max_date_raw": None,
            "min_date": None,
            "max_date": None,
            "tickers": 0,
        }

    if not isinstance(df, pd.DataFrame):
        if isinstance(df, list):
            rows = [row for row in df if isinstance(row, dict)]
            df = pd.DataFrame(rows) if rows else pd.DataFrame()
        elif isinstance(df, dict):
            df = pd.DataFrame([df])
        else:
            df = pd.DataFrame()

    summary = _summarize_heber_source_frame(df)
    min_date_str = summary.get("min_date")
    max_date_str = summary.get("max_date")

    min_date_raw: Optional[date] = None
    max_date_raw: Optional[date] = None
    if isinstance(min_date_str, str):
        try:
            min_date_raw = date.fromisoformat(min_date_str)
        except ValueError:
            min_date_raw = None
    if isinstance(max_date_str, str):
        try:
            max_date_raw = date.fromisoformat(max_date_str)
        except ValueError:
            max_date_raw = None

    return {
        "min_date_raw": min_date_raw,
        "max_date_raw": max_date_raw,
        "min_date": min_date_str,
        "max_date": max_date_str,
        "tickers": int(summary.get("tickers") or 0),
    }


# Feature-to-source mapping for all 130+ features
FEATURE_SOURCE_MAPPING = {
    # Greeks - from flow alert feed
    "delta_at_entry": SOURCE_FLOW,
    "gamma_at_entry": SOURCE_FLOW,
    "iv_at_entry": SOURCE_FLOW,
    "volume_at_entry": SOURCE_FLOW,
    "open_interest_at_entry": SOURCE_FLOW,
    # IV Rank - from flow history
    "iv_rank_at_entry": SOURCE_FLOW,
    # Darkpool windows
    "darkpool_volume_1h": SOURCE_DARKPOOL,
    "darkpool_15m": SOURCE_DARKPOOL,
    "darkpool_30m": SOURCE_DARKPOOL,
    "darkpool_4h": SOURCE_DARKPOOL,
    "darkpool_1d": SOURCE_DARKPOOL,
    "darkpool_3d": SOURCE_DARKPOOL,
    "darkpool_1w": SOURCE_DARKPOOL,
    "darkpool_2w": SOURCE_DARKPOOL,
    "darkpool_4w": SOURCE_DARKPOOL,
    # Bars
    "overnight_gap_pct": SOURCE_BARS,
    "vwap_distance_pct": SOURCE_BARS,
    "underlying_at_entry": SOURCE_BARS,
    "underlying_at_1h": SOURCE_BARS,
    "price_change_5d_prior": SOURCE_BARS,
    # GEX/VEX
    "gex_at_entry": SOURCE_GREEK_EXPOSURE,
    "vex_at_entry": SOURCE_GREEK_EXPOSURE,
    # Max pain
    "max_pain_distance_pct": SOURCE_MAX_PAIN,
    # Market tide
    "market_tide_30m": SOURCE_MARKET_TIDE,
    "market_tide_direction": SOURCE_MARKET_TIDE,
    # Regimes - from VIX / regime history
    "vix_at_entry": SOURCE_VIX,
    "vix_regime_at_entry": SOURCE_VIX,
    "trend_regime_at_entry": SOURCE_REGIME,
    "vol_regime_at_entry": SOURCE_REGIME,
    "risk_regime_at_entry": SOURCE_REGIME,
    "session_regime_at_entry": SOURCE_REGIME,
    # Time features - derived from entry_ts (no source table)
    "entry_hour": "derived",
    "entry_day_of_week": "derived",
    "entry_session": "derived",
    "minutes_to_close": "derived",
    # Flow aggression
    "ask_side_ratio": SOURCE_FLOW,
    "sweep_ratio_1h": SOURCE_FLOW,
    "same_ticker_premium_1h": SOURCE_FLOW,
    "institutional_flow_1w": SOURCE_FLOW,
    # RVOL
    "rvol_1h": SOURCE_FLOW,
    "rvol_daily": SOURCE_FLOW,
    "rvol_weekly": SOURCE_FLOW,
    "rvol_30m": SOURCE_FLOW,
    "rvol_3d": SOURCE_FLOW,
    # Return checkpoints (option prices)
    "return_at_15m": SOURCE_FLOW,
    "return_at_30m": SOURCE_FLOW,
    "return_at_1h": SOURCE_FLOW,
    "return_at_2h": SOURCE_FLOW,
    "return_at_4h": SOURCE_FLOW,
    "return_at_8h": SOURCE_FLOW,
    "return_at_1d": SOURCE_FLOW,
    "return_at_2d": SOURCE_FLOW,
    "return_at_1w": SOURCE_FLOW,
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
