"""Derived features computed at runtime from existing alert-level features.

These features are ratios/interactions of existing features — no new data needed.
Used by both pattern_miner (training) and feature_store (inference) to ensure
train/inference parity by construction.

Input feature names match FEATURE_COLUMNS from pattern_miner:
    iv, realized_vol_20d, vega, theta, gamma, delta, spot_price, contract_price
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# Canonical list of all derived feature names.
DERIVED_FEATURE_NAMES: list[str] = [
    "iv_vs_realized",
    "vega_theta_ratio",
    "gamma_delta_ratio",
    "dollar_gamma",
    "theta_premium_ratio",
    "ask_side_dominance",
    "aggressor_conviction",
    "max_pain_distance",
    "days_to_nearest_opex",
]


# ---------------------------------------------------------------------------
# Single-row computation (dict-based, used at inference time)
# ---------------------------------------------------------------------------


def _safe_get(row: dict[str, Any], key: str) -> float | None:
    """Extract a float value from a row dict, returning None for missing/NaN."""
    val = row.get(key)
    if val is None:
        return None
    try:
        fval = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(fval):
        return None
    return fval


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    """Divide two values, returning None when inputs are missing or denominator is zero."""
    if numerator is None or denominator is None:
        return None
    if denominator == 0.0:
        return None
    return numerator / denominator


def compute_derived_features(row: dict[str, Any]) -> dict[str, float | None]:
    """Compute all derived features from a single row of raw features.

    Works with both:
    - dict (from feature_store at inference time)
    - pd.Series (convert to dict first via .to_dict())

    All inputs expected to be float or None. Returns None for any feature
    where required inputs are missing.
    """
    # If handed a pandas Series, convert to dict for uniform access.
    if isinstance(row, pd.Series):
        row = row.to_dict()

    iv = _safe_get(row, "iv")
    realized_vol_20d = _safe_get(row, "realized_vol_20d")
    vega = _safe_get(row, "vega")
    theta = _safe_get(row, "theta")
    gamma = _safe_get(row, "gamma")
    delta = _safe_get(row, "delta")
    spot_price = _safe_get(row, "spot_price")
    contract_price = _safe_get(row, "contract_price")

    # P1: IV vs Realized
    iv_vs_realized = _safe_div(iv, realized_vol_20d)

    # P2: Greeks Interaction Ratios
    abs_vega = abs(vega) if vega is not None else None
    abs_theta = abs(theta) if theta is not None else None
    abs_delta = abs(delta) if delta is not None else None

    vega_theta_ratio = _safe_div(abs_vega, abs_theta)
    gamma_delta_ratio = _safe_div(gamma, abs_delta)

    # dollar_gamma = gamma * spot_price^2 * 0.01
    if gamma is not None and spot_price is not None:
        dollar_gamma: float | None = gamma * spot_price * spot_price * 0.01
    else:
        dollar_gamma = None

    theta_premium_ratio = _safe_div(abs_theta, contract_price)

    # P3: Flow-based features
    total_ask_side_prem = _safe_get(row, "total_ask_side_prem")
    total_bid_side_prem = _safe_get(row, "total_bid_side_prem")

    # ask_side_dominance = ask / (ask + bid), range 0-1
    ask_bid_total = None
    if total_ask_side_prem is not None and total_bid_side_prem is not None:
        ask_bid_total = total_ask_side_prem + total_bid_side_prem
    ask_side_dominance = _safe_div(total_ask_side_prem, ask_bid_total)

    # aggressor_conviction = |ask - bid| / (ask + bid), range 0-1
    if total_ask_side_prem is not None and total_bid_side_prem is not None:
        aggressor_num: float | None = abs(total_ask_side_prem - total_bid_side_prem)
    else:
        aggressor_num = None
    aggressor_conviction = _safe_div(aggressor_num, ask_bid_total)

    # P4: Max pain distance
    max_pain_strike = _safe_get(row, "max_pain_strike")
    # max_pain_distance = (spot_price - max_pain_strike) / spot_price
    if spot_price is not None and max_pain_strike is not None:
        max_pain_distance = _safe_div(spot_price - max_pain_strike, spot_price)
    else:
        max_pain_distance = None

    # P5: Days to nearest OPEX (capped at 5 for near-term focus)
    days_to_expiry = _safe_get(row, "days_to_expiry")
    if days_to_expiry is not None:
        days_to_nearest_opex: float | None = min(days_to_expiry, 5.0)
    else:
        days_to_nearest_opex = None

    return {
        "iv_vs_realized": iv_vs_realized,
        "vega_theta_ratio": vega_theta_ratio,
        "gamma_delta_ratio": gamma_delta_ratio,
        "dollar_gamma": dollar_gamma,
        "theta_premium_ratio": theta_premium_ratio,
        "ask_side_dominance": ask_side_dominance,
        "aggressor_conviction": aggressor_conviction,
        "max_pain_distance": max_pain_distance,
        "days_to_nearest_opex": days_to_nearest_opex,
    }


# ---------------------------------------------------------------------------
# Batch computation (vectorized, used at training time)
# ---------------------------------------------------------------------------


def compute_derived_features_batch(df: pd.DataFrame) -> pd.DataFrame:
    """Compute derived features for a DataFrame (training batch).

    Vectorized for performance using numpy. Adds all DERIVED_FEATURE_NAMES
    as new columns. Returns a copy — does not mutate the input.

    Produces identical results to calling compute_derived_features() row-by-row.
    """
    result = df.copy()

    if len(result) == 0:
        for name in DERIVED_FEATURE_NAMES:
            result[name] = pd.Series(dtype="float64")
        return result

    n = len(result)

    # Extract column as numpy float array; NaN-filled when column missing.
    def _col(name: str) -> np.ndarray:
        s = result.get(name)
        if s is None:
            return np.full(n, np.nan, dtype="float64")
        return pd.to_numeric(s, errors="coerce").to_numpy(dtype="float64", na_value=np.nan)

    # Extract columns as numpy float arrays (NaN for missing).
    iv = _col("iv")
    realized_vol_20d = _col("realized_vol_20d")
    vega = _col("vega")
    theta = _col("theta")
    gamma = _col("gamma")
    delta = _col("delta")
    spot_price = _col("spot_price")
    contract_price = _col("contract_price")

    abs_vega = np.abs(vega)
    abs_theta = np.abs(theta)
    abs_delta = np.abs(delta)

    # Safe division helper: NaN where denominator is 0 or NaN.
    def _vdiv(num: np.ndarray, den: np.ndarray) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            out = num / den
        # Replace inf (from 0/0 or x/0) with NaN.
        out[~np.isfinite(out)] = np.nan
        return out

    result["iv_vs_realized"] = _vdiv(iv, realized_vol_20d)
    result["vega_theta_ratio"] = _vdiv(abs_vega, abs_theta)
    result["gamma_delta_ratio"] = _vdiv(gamma, abs_delta)
    result["dollar_gamma"] = gamma * spot_price * spot_price * 0.01
    result["theta_premium_ratio"] = _vdiv(abs_theta, contract_price)

    # P3: Flow-based features
    total_ask_side_prem = _col("total_ask_side_prem")
    total_bid_side_prem = _col("total_bid_side_prem")

    ask_bid_total = total_ask_side_prem + total_bid_side_prem
    result["ask_side_dominance"] = _vdiv(total_ask_side_prem, ask_bid_total)
    result["aggressor_conviction"] = _vdiv(np.abs(total_ask_side_prem - total_bid_side_prem), ask_bid_total)

    # P4: Max pain distance
    max_pain_strike = _col("max_pain_strike")
    result["max_pain_distance"] = _vdiv(spot_price - max_pain_strike, spot_price)

    # P5: Days to nearest OPEX (capped at 5)
    days_to_expiry = _col("days_to_expiry")
    result["days_to_nearest_opex"] = np.minimum(days_to_expiry, 5.0)

    return result
