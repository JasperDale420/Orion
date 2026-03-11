"""Unified feature store for ML scoring.

Reads pre-computed features from Heber Gold — the same datasets used for
training — eliminating train/inference skew by construction.

Training (pattern_miner) reads:
  - meta_label_features       (alert-level: greeks, moneyness, returns, timing, …)
  - momentum_features         (equity-level: momentum_1d/5d/10d/20d, rsi, macd)
  - volatility_features       (equity-level: vol_5d/20d, atr_14, bb_width, …)
  - flow_features             (equity-level: premiums, sweep counts, …)
  - market_regime_features    (market-level: dispersion, vol_of_vol, breadth, yield curve)

This module reads the exact same datasets at inference time.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pandas as pd

from orion.clients.heber_reader import get_heber_reader
from orion.ml.derived_features import compute_derived_features
from orion.ml.pattern_miner import (
    ALERT_FLOW_CONTEXT_FEATURES,
    CATEGORICAL_COLUMNS,
    EQUITY_GOLD_DATASETS,
    FEATURE_COLUMNS,
)
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.feature_store")

# Column name mapping: meta_label_features Gold schema → pattern_miner FEATURE_COLUMNS.
# Training's _normalize_heber_features() does this same mapping for batch data.
# Keys = Gold column name, Values = training feature name.
_GOLD_TO_FEATURE: dict[str, str] = {
    "days_to_expiry": "days_to_expiry",
    "premium": "premium",
    "volume": "volume",
    "open_interest": "open_interest",
    "volume_oi_ratio": "volume_oi_ratio",
    "spot_price": "spot_price",
    "contract_price": "contract_price",
    "strike": "strike",
    "moneyness": "moneyness",
    "log_moneyness": "log_moneyness",
    "delta": "delta",
    "gamma": "gamma",
    "theta": "theta",
    "vega": "vega",
    "iv": "iv",
    "underlying_30d_return": "underlying_30d_return",
    "underlying_5d_return": "underlying_5d_return",
    "underlying_1d_return": "underlying_1d_return",
    "realized_vol_20d": "realized_vol_20d",
    "iv_rank": "iv_rank",
    "hour_of_day": "hour_of_day",
    "minute_of_hour": "minute_of_hour",
    "day_of_week": "day_of_week",
    "minutes_since_open": "minutes_since_open",
    "minutes_to_close": "minutes_to_close",
    # GEX / market structure (in Gold but not in FEATURE_COLUMNS — carry for confidence rules)
    "gex": "gex",
    "vex": "vex",
    "max_pain_distance_pct": "max_pain_distance_pct",
    "market_tide_net_premium": "market_tide_30m",
    "market_tide_direction": "market_tide_direction",
    # Categorical / flags
    "put_call": "put_call",
    "alert_type": "alert_type",
    "side": "side",
    "aggressor": "aggressor",
    "is_bullish": "is_bullish",
    "is_bearish": "is_bearish",
    "is_sweep": "is_sweep",
    "is_block": "is_block",
    "is_unusual": "is_unusual",
    # Market regime features (market-level, broadcast to all tickers)
    "dispersion": "dispersion",
    "vol_of_vol": "vol_of_vol",
    "breadth_proxy": "breadth_proxy",
    "yield_curve_slope": "yield_curve_slope",
    # Flow normalization features (equity-level)
    "adv_premium_20d": "adv_premium_20d",
    "adv_volume_20d": "adv_volume_20d",
    "adv_oi_20d": "adv_oi_20d",
    # IV surface features (equity-level)
    "put_call_iv_skew": "put_call_iv_skew",
    "term_structure_slope": "term_structure_slope",
    "iv_change_1d": "iv_change_1d",
    # Ticker base rates (equity-level)
    "ticker_win_rate_90d": "ticker_win_rate_90d",
    "ticker_alert_frequency": "ticker_alert_frequency",
    "ticker_flow_predictability": "ticker_flow_predictability",
    # Flow context (per-alert level)
    "same_ticker_alerts_1h": "same_ticker_alerts_1h",
    "directional_agreement_4h": "directional_agreement_4h",
    "repeat_ticker_days_5d": "repeat_ticker_days_5d",
}

# Boolean flag columns that need coercion
_BOOL_COLUMNS = frozenset({"is_bullish", "is_bearish", "is_sweep", "is_block", "is_unusual"})

# Datasets that are market-level (not per-ticker) — read without symbol filter
_MARKET_LEVEL_DATASETS = frozenset({"market_regime_features"})

# Datasets that are per-alert (not per-ticker daily) — need event_id-based lookup
_ALERT_LEVEL_DATASETS = frozenset({"flow_context_features"})


async def get_scoring_features(
    event_id: str,
    ticker: str,
    entry_ts: datetime,
) -> dict[str, Any]:
    """Load pre-computed features for a single flow event from Heber Gold.

    Reads the same Gold datasets used by pattern_miner for training:
    1. meta_label_features — alert-level features keyed by event_id
    2. equity Gold datasets — momentum, volatility, flow features by ticker + time

    Returns a flat dict with keys matching FEATURE_COLUMNS + CATEGORICAL_COLUMNS,
    suitable for passing directly into MLScorer._build_feature_map() merge.
    """
    reader = get_heber_reader()
    result: dict[str, Any] = {}

    # Parallel reads: alert-level features + equity-level features
    alert_task = asyncio.to_thread(
        reader.read_gold_features,
        dataset="meta_label_features",
        asof_time=entry_ts,
    )
    equity_task = _load_equity_gold_for_ticker(reader, ticker, entry_ts)

    alert_df, equity_features = await asyncio.gather(alert_task, equity_task, return_exceptions=True)

    # --- Alert-level features from meta_label_features ---
    if isinstance(alert_df, Exception):
        logger.warning(
            "Failed to read meta_label_features for scoring",
            extra={"event": "feature_store_alert_read_failed", "error": str(alert_df), "event_id": event_id},
        )
        alert_df = pd.DataFrame()

    if not isinstance(alert_df, pd.DataFrame):
        alert_df = pd.DataFrame()

    if not alert_df.empty:
        row = _find_event_row(alert_df, event_id, ticker, entry_ts)
        if row is not None:
            for gold_col, feature_name in _GOLD_TO_FEATURE.items():
                if gold_col in row.index:
                    val = row[gold_col]
                    if feature_name in _BOOL_COLUMNS:
                        result[feature_name] = _coerce_bool(val)
                    else:
                        result[feature_name] = _coerce_float(val)

            logger.debug(
                "Loaded alert-level features from Gold",
                extra={
                    "event": "feature_store_alert_loaded",
                    "event_id": event_id,
                    "feature_count": sum(1 for v in result.values() if v is not None),
                },
            )
        else:
            logger.info(
                "Event not found in meta_label_features — flow may be too fresh",
                extra={"event": "feature_store_event_not_found", "event_id": event_id, "ticker": ticker},
            )

    # --- Equity-level Gold features ---
    if isinstance(equity_features, Exception):
        logger.warning(
            "Failed to read equity Gold features",
            extra={"event": "feature_store_equity_read_failed", "error": str(equity_features), "ticker": ticker},
        )
        equity_features = {}

    if isinstance(equity_features, dict):
        for key, val in equity_features.items():
            if key in (FEATURE_COLUMNS + CATEGORICAL_COLUMNS) or key in _GOLD_TO_FEATURE.values():
                result[key] = val

    # --- Flow context features (per-alert, from flow_context_features Gold dataset) ---
    flow_ctx = await _load_alert_level_gold(reader, event_id, ticker, entry_ts, "flow_context_features")
    if isinstance(flow_ctx, dict):
        for col in ALERT_FLOW_CONTEXT_FEATURES:
            if col in flow_ctx:
                result[col] = _coerce_float(flow_ctx[col])

    # --- Derived features (computed from existing alert-level features) ---
    derived = compute_derived_features(result)
    result.update(derived)

    # --- Runtime-derived flow normalization ratios ---
    _premium = _coerce_float(result.get("premium"))
    _adv_prem = _coerce_float(result.get("adv_premium_20d"))
    result["premium_vs_adv"] = (_premium / _adv_prem) if (_premium is not None and _adv_prem) else None

    _volume = _coerce_float(result.get("volume"))
    _adv_vol = _coerce_float(result.get("adv_volume_20d"))
    result["volume_vs_adoi"] = (_volume / _adv_vol) if (_volume is not None and _adv_vol) else None

    _oi = _coerce_float(result.get("open_interest"))
    _adv_oi = _coerce_float(result.get("adv_oi_20d"))
    result["relative_oi_buildup"] = (_oi / _adv_oi) if (_oi is not None and _adv_oi) else None

    # Fill missing features with None (scorer handles defaults)
    all_features = set(FEATURE_COLUMNS + CATEGORICAL_COLUMNS) | set(_GOLD_TO_FEATURE.values())
    for feat in all_features:
        result.setdefault(feat, None)

    return result


def _find_event_row(df: pd.DataFrame, event_id: str, ticker: str, entry_ts: datetime) -> Any | None:
    """Find the matching row in meta_label_features for this event.

    Priority:
    1. Exact match by event_id / alert_id / watch_id
    2. Closest match by ticker + timestamp (for recently ingested flows)
    """
    # Try exact event_id match
    for id_col in ["event_id", "alert_id", "watch_id", "instrument_key"]:
        if id_col not in df.columns:
            continue
        matches = df[df[id_col].astype(str) == str(event_id)]
        if not matches.empty:
            return matches.iloc[0]

    # Fallback: closest row for this ticker by timestamp
    symbol_col = "symbol" if "symbol" in df.columns else None
    if symbol_col is None:
        for candidate in ["ticker", "underlying"]:
            if candidate in df.columns:
                symbol_col = candidate
                break

    if symbol_col is None:
        return None

    ticker_rows = df[df[symbol_col].astype(str).str.upper() == ticker.upper()]
    if ticker_rows.empty:
        return None

    # Find closest by timestamp
    ts_col = None
    for candidate in ["alert_time", "ts_event", "ts_available", "timestamp"]:
        if candidate in ticker_rows.columns:
            ts_col = candidate
            break

    if ts_col is None:
        return ticker_rows.iloc[-1]  # Last row as fallback

    ticker_rows = ticker_rows.copy()
    ticker_rows["_ts"] = pd.to_datetime(ticker_rows[ts_col], utc=True, errors="coerce")
    ticker_rows = ticker_rows.dropna(subset=["_ts"])
    if ticker_rows.empty:
        return None

    target_ts = pd.Timestamp(entry_ts)
    if target_ts.tzinfo is None:
        target_ts = target_ts.tz_localize("UTC")
    # Only consider rows at or before entry_ts (no future leakage)
    valid = ticker_rows[ticker_rows["_ts"] <= target_ts]
    if valid.empty:
        return None

    closest_idx = (target_ts - valid["_ts"]).abs().idxmin()
    return valid.loc[closest_idx]


async def _load_equity_gold_for_ticker(
    reader: Any,
    ticker: str,
    entry_ts: datetime,
) -> dict[str, Any]:
    """Load equity-level Gold features for a single ticker at entry_ts.

    Same logic as flow_enricher._get_equity_gold_features and
    pattern_miner._load_equity_gold_features, but for a single ticker.
    """
    result: dict[str, Any] = {}

    for dataset_name, feature_cols in EQUITY_GOLD_DATASETS.items():
        try:
            # Market-level datasets are not keyed by ticker — omit symbol filter
            symbols = None if dataset_name in _MARKET_LEVEL_DATASETS else [ticker]
            df = await asyncio.to_thread(
                reader.read_gold_features,
                dataset=dataset_name,
                asof_time=entry_ts,
                symbols=symbols,
            )
            if df is None or df.empty:
                continue

            # Find the most recent row
            ts_col = None
            for candidate in ["ts_event", "ts_available", "timestamp", "date"]:
                if candidate in df.columns:
                    ts_col = candidate
                    break

            if ts_col is not None:
                df[ts_col] = pd.to_datetime(df[ts_col], utc=True, errors="coerce")
                df = df.dropna(subset=[ts_col])
                if not df.empty:
                    df = df.sort_values(ts_col, ascending=False)

            if df.empty:
                continue

            latest = df.iloc[0]
            for col in feature_cols:
                if col in latest.index:
                    result[col] = _coerce_float(latest[col])
        except Exception as exc:
            logger.warning(
                f"Equity Gold dataset {dataset_name} unavailable for {ticker}: {exc}",
                extra={"event": "feature_store_equity_unavailable", "dataset": dataset_name, "ticker": ticker},
            )

    return result


async def _load_alert_level_gold(
    reader: Any,
    event_id: str,
    ticker: str,
    entry_ts: datetime,
    dataset: str,
) -> dict[str, Any]:
    """Load a per-alert Gold dataset (e.g. flow_context_features) by event_id.

    Similar to how meta_label_features is read — keyed by alert_id/event_id,
    not by ticker+date like equity-level datasets.
    """
    try:
        df = await asyncio.to_thread(
            reader.read_gold_features,
            dataset=dataset,
            asof_time=entry_ts,
        )
    except Exception as exc:
        logger.warning(
            f"Alert-level Gold dataset {dataset} unavailable: {exc}",
            extra={"event": "feature_store_alert_gold_unavailable", "dataset": dataset, "event_id": event_id},
        )
        return {}

    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return {}

    if not isinstance(df, pd.DataFrame):
        return {}

    row = _find_event_row(df, event_id, ticker, entry_ts)
    if row is None:
        return {}

    result: dict[str, Any] = {}
    for col in row.index:
        result[col] = row[col]
    return result


def _coerce_float(val: Any) -> float | None:
    """Coerce a value to float, returning None for NaN/None."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return f
    except (ValueError, TypeError):
        return None


def _coerce_bool(val: Any) -> int:
    """Coerce a value to 0/1 matching training's boolean encoding."""
    if val is None:
        return 0
    return 1 if str(val).strip().lower() in {"1", "true", "t", "yes", "y", "on"} else 0
