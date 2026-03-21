"""Tests for orion.ml.derived_features — runtime derived feature computation.

TDD tests written first, covering:
- Each derived feature with normal inputs
- Safe division (zero/None denominators)
- Batch/single parity
- Edge cases (empty DataFrame, all-None inputs)
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from orion.ml.derived_features import (
    DERIVED_FEATURE_NAMES,
    compute_derived_features,
    compute_derived_features_batch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**overrides: float | None) -> dict[str, float | None]:
    """Build a minimal feature dict with sensible defaults.

    Defaults are chosen so every derived feature is computable.
    """
    base: dict[str, float | None] = {
        "iv": 0.35,
        "realized_vol_20d": 0.25,
        "vega": 0.15,
        "theta": -0.05,
        "gamma": 0.02,
        "delta": 0.50,
        "spot_price": 150.0,
        "contract_price": 3.0,
        "total_ask_side_prem": 100.0,
        "total_bid_side_prem": 50.0,
        "max_pain_strike": 145.0,
        "days_to_expiry": 3.0,
    }
    base.update(overrides)
    return base


def _make_df(rows: list[dict[str, float | None]]) -> pd.DataFrame:
    """Build a DataFrame from a list of feature dicts."""
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# P1: IV vs Realized
# ---------------------------------------------------------------------------


class TestIvVsRealized:
    """iv_vs_realized = iv / realized_vol_20d"""

    def test_normal(self):
        row = _make_row(iv=0.35, realized_vol_20d=0.25)
        result = compute_derived_features(row)
        assert result["iv_vs_realized"] == pytest.approx(1.4)

    def test_zero_denominator(self):
        row = _make_row(iv=0.35, realized_vol_20d=0.0)
        result = compute_derived_features(row)
        assert result["iv_vs_realized"] is None

    def test_none_numerator(self):
        row = _make_row(iv=None, realized_vol_20d=0.25)
        result = compute_derived_features(row)
        assert result["iv_vs_realized"] is None

    def test_none_denominator(self):
        row = _make_row(iv=0.35, realized_vol_20d=None)
        result = compute_derived_features(row)
        assert result["iv_vs_realized"] is None


# ---------------------------------------------------------------------------
# P2: Greeks Interaction Ratios
# ---------------------------------------------------------------------------


class TestVegaThetaRatio:
    """vega_theta_ratio = abs(vega) / abs(theta)"""

    def test_normal(self):
        row = _make_row(vega=0.15, theta=-0.05)
        result = compute_derived_features(row)
        assert result["vega_theta_ratio"] == pytest.approx(3.0)

    def test_zero_theta(self):
        row = _make_row(vega=0.15, theta=0.0)
        result = compute_derived_features(row)
        assert result["vega_theta_ratio"] is None

    def test_negative_vega(self):
        row = _make_row(vega=-0.15, theta=-0.05)
        result = compute_derived_features(row)
        assert result["vega_theta_ratio"] == pytest.approx(3.0)


class TestGammaDeltaRatio:
    """gamma_delta_ratio = gamma / abs(delta)"""

    def test_normal(self):
        row = _make_row(gamma=0.02, delta=0.5)
        result = compute_derived_features(row)
        assert result["gamma_delta_ratio"] == pytest.approx(0.04)

    def test_zero_delta(self):
        row = _make_row(gamma=0.02, delta=0.0)
        result = compute_derived_features(row)
        assert result["gamma_delta_ratio"] is None

    def test_negative_delta(self):
        row = _make_row(gamma=0.02, delta=-0.5)
        result = compute_derived_features(row)
        assert result["gamma_delta_ratio"] == pytest.approx(0.04)


class TestDollarGamma:
    """dollar_gamma = gamma * spot_price * spot_price * 0.01"""

    def test_normal(self):
        row = _make_row(gamma=0.02, spot_price=150.0)
        result = compute_derived_features(row)
        # 0.02 * 150 * 150 * 0.01 = 4.5
        assert result["dollar_gamma"] == pytest.approx(4.5)

    def test_none_gamma(self):
        row = _make_row(gamma=None, spot_price=150.0)
        result = compute_derived_features(row)
        assert result["dollar_gamma"] is None

    def test_none_spot(self):
        row = _make_row(gamma=0.02, spot_price=None)
        result = compute_derived_features(row)
        assert result["dollar_gamma"] is None


class TestThetaPremiumRatio:
    """theta_premium_ratio = abs(theta) / contract_price"""

    def test_normal(self):
        row = _make_row(theta=-0.05, contract_price=3.0)
        result = compute_derived_features(row)
        # abs(-0.05) / 3.0 = 0.01666...
        assert result["theta_premium_ratio"] == pytest.approx(0.01667, rel=1e-3)

    def test_zero_contract_price(self):
        row = _make_row(theta=-0.05, contract_price=0.0)
        result = compute_derived_features(row)
        assert result["theta_premium_ratio"] is None

    def test_none_theta(self):
        row = _make_row(theta=None, contract_price=3.0)
        result = compute_derived_features(row)
        assert result["theta_premium_ratio"] is None


# ---------------------------------------------------------------------------
# All-None inputs
# ---------------------------------------------------------------------------


class TestAllNoneInputs:
    """When every input feature is None, every derived feature should be None."""

    def test_all_none(self):
        row = {
            "iv": None,
            "realized_vol_20d": None,
            "vega": None,
            "theta": None,
            "gamma": None,
            "delta": None,
            "spot_price": None,
            "contract_price": None,
            "total_ask_side_prem": None,
            "total_bid_side_prem": None,
            "max_pain_strike": None,
            "days_to_expiry": None,
        }
        result = compute_derived_features(row)
        for name in DERIVED_FEATURE_NAMES:
            assert result[name] is None, f"{name} should be None when all inputs are None"


# ---------------------------------------------------------------------------
# Batch / Single parity
# ---------------------------------------------------------------------------


class TestBatchMatchesSingle:
    """Batch vectorized computation must produce the same values as row-by-row."""

    def test_batch_matches_single(self):
        rows = [
            _make_row(
                iv=0.35,
                realized_vol_20d=0.25,
                vega=0.15,
                theta=-0.05,
                gamma=0.02,
                delta=0.5,
                spot_price=150.0,
                contract_price=3.0,
            ),
            _make_row(
                iv=0.50,
                realized_vol_20d=0.30,
                vega=0.20,
                theta=-0.10,
                gamma=0.03,
                delta=-0.7,
                spot_price=200.0,
                contract_price=5.0,
            ),
            _make_row(
                iv=None,
                realized_vol_20d=0.20,
                vega=0.10,
                theta=0.0,
                gamma=0.01,
                delta=0.0,
                spot_price=100.0,
                contract_price=1.0,
            ),
        ]

        df = _make_df(rows)
        batch_result = compute_derived_features_batch(df)

        for i, row in enumerate(rows):
            single_result = compute_derived_features(row)
            for name in DERIVED_FEATURE_NAMES:
                batch_val = batch_result[name].iloc[i]
                single_val = single_result[name]

                if single_val is None:
                    assert pd.isna(batch_val), f"Row {i}, feature {name}: expected NaN, got {batch_val}"
                else:
                    assert not pd.isna(batch_val), f"Row {i}, feature {name}: expected {single_val}, got NaN"
                    assert batch_val == pytest.approx(single_val, rel=1e-9), (
                        f"Row {i}, feature {name}: single={single_val}, batch={batch_val}"
                    )


# ---------------------------------------------------------------------------
# Batch edge cases
# ---------------------------------------------------------------------------


class TestBatchEdgeCases:
    """Edge cases for batch processing."""

    def test_empty_dataframe(self):
        df = pd.DataFrame(
            columns=["iv", "realized_vol_20d", "vega", "theta", "gamma", "delta", "spot_price", "contract_price"]
        )
        result = compute_derived_features_batch(df)
        assert len(result) == 0
        for name in DERIVED_FEATURE_NAMES:
            assert name in result.columns, f"Missing column: {name}"

    def test_single_row_dataframe(self):
        df = _make_df([_make_row()])
        result = compute_derived_features_batch(df)
        assert len(result) == 1
        for name in DERIVED_FEATURE_NAMES:
            assert name in result.columns

    def test_preserves_existing_columns(self):
        """Batch should add derived columns without removing existing ones."""
        df = _make_df([_make_row()])
        df["extra_col"] = 42
        result = compute_derived_features_batch(df)
        assert "extra_col" in result.columns
        assert result["extra_col"].iloc[0] == 42


# ---------------------------------------------------------------------------
# P3: Ask Side Dominance
# ---------------------------------------------------------------------------


class TestAskSideDominance:
    """ask_side_dominance = total_ask_side_prem / (total_ask_side_prem + total_bid_side_prem)"""

    def test_normal(self):
        row = _make_row(total_ask_side_prem=100.0, total_bid_side_prem=50.0)
        result = compute_derived_features(row)
        # 100 / (100 + 50) = 0.6667
        assert result["ask_side_dominance"] == pytest.approx(0.6667, rel=1e-3)

    def test_aggressive_buying(self):
        row = _make_row(total_ask_side_prem=900.0, total_bid_side_prem=100.0)
        result = compute_derived_features(row)
        assert result["ask_side_dominance"] == pytest.approx(0.9)

    def test_aggressive_selling(self):
        row = _make_row(total_ask_side_prem=100.0, total_bid_side_prem=900.0)
        result = compute_derived_features(row)
        assert result["ask_side_dominance"] == pytest.approx(0.1)

    def test_balanced(self):
        row = _make_row(total_ask_side_prem=500.0, total_bid_side_prem=500.0)
        result = compute_derived_features(row)
        assert result["ask_side_dominance"] == pytest.approx(0.5)

    def test_both_zero(self):
        row = _make_row(total_ask_side_prem=0.0, total_bid_side_prem=0.0)
        result = compute_derived_features(row)
        assert result["ask_side_dominance"] is None

    def test_none_ask(self):
        row = _make_row(total_ask_side_prem=None, total_bid_side_prem=50.0)
        result = compute_derived_features(row)
        assert result["ask_side_dominance"] is None

    def test_none_bid(self):
        row = _make_row(total_ask_side_prem=100.0, total_bid_side_prem=None)
        result = compute_derived_features(row)
        assert result["ask_side_dominance"] is None


# ---------------------------------------------------------------------------
# P3: Aggressor Conviction
# ---------------------------------------------------------------------------


class TestAggressorConviction:
    """aggressor_conviction = |ask - bid| / (ask + bid)"""

    def test_normal(self):
        row = _make_row(total_ask_side_prem=100.0, total_bid_side_prem=50.0)
        result = compute_derived_features(row)
        # |100 - 50| / (100 + 50) = 50/150 = 0.3333
        assert result["aggressor_conviction"] == pytest.approx(0.3333, rel=1e-3)

    def test_strong_conviction(self):
        row = _make_row(total_ask_side_prem=1000.0, total_bid_side_prem=10.0)
        result = compute_derived_features(row)
        # |1000 - 10| / (1000 + 10) = 990/1010 ~ 0.9802
        assert result["aggressor_conviction"] == pytest.approx(0.9802, rel=1e-3)

    def test_balanced_no_conviction(self):
        row = _make_row(total_ask_side_prem=500.0, total_bid_side_prem=500.0)
        result = compute_derived_features(row)
        assert result["aggressor_conviction"] == pytest.approx(0.0)

    def test_both_zero(self):
        row = _make_row(total_ask_side_prem=0.0, total_bid_side_prem=0.0)
        result = compute_derived_features(row)
        assert result["aggressor_conviction"] is None

    def test_none_inputs(self):
        row = _make_row(total_ask_side_prem=None, total_bid_side_prem=None)
        result = compute_derived_features(row)
        assert result["aggressor_conviction"] is None


# ---------------------------------------------------------------------------
# P4: Max Pain Distance
# ---------------------------------------------------------------------------


class TestMaxPainDistance:
    """max_pain_distance = (spot_price - max_pain_strike) / spot_price"""

    def test_spot_above_max_pain(self):
        row = _make_row(spot_price=150.0, max_pain_strike=140.0)
        result = compute_derived_features(row)
        # (150 - 140) / 150 = 0.0667
        assert result["max_pain_distance"] == pytest.approx(0.06667, rel=1e-3)

    def test_spot_below_max_pain(self):
        row = _make_row(spot_price=150.0, max_pain_strike=160.0)
        result = compute_derived_features(row)
        # (150 - 160) / 150 = -0.0667
        assert result["max_pain_distance"] == pytest.approx(-0.06667, rel=1e-3)

    def test_spot_at_max_pain(self):
        row = _make_row(spot_price=150.0, max_pain_strike=150.0)
        result = compute_derived_features(row)
        assert result["max_pain_distance"] == pytest.approx(0.0)

    def test_missing_max_pain_strike(self):
        row = _make_row(spot_price=150.0, max_pain_strike=None)
        result = compute_derived_features(row)
        assert result["max_pain_distance"] is None

    def test_missing_spot_price(self):
        row = _make_row(spot_price=None, max_pain_strike=140.0)
        result = compute_derived_features(row)
        assert result["max_pain_distance"] is None

    def test_zero_spot_price(self):
        row = _make_row(spot_price=0.0, max_pain_strike=140.0)
        result = compute_derived_features(row)
        assert result["max_pain_distance"] is None


# ---------------------------------------------------------------------------
# P5: Days to Nearest OPEX
# ---------------------------------------------------------------------------


class TestDaysToNearestOpex:
    """days_to_nearest_opex = min(days_to_expiry, 5.0)"""

    def test_below_cap(self):
        row = _make_row(days_to_expiry=3.0)
        result = compute_derived_features(row)
        assert result["days_to_nearest_opex"] == pytest.approx(3.0)

    def test_at_cap(self):
        row = _make_row(days_to_expiry=5.0)
        result = compute_derived_features(row)
        assert result["days_to_nearest_opex"] == pytest.approx(5.0)

    def test_above_cap(self):
        row = _make_row(days_to_expiry=30.0)
        result = compute_derived_features(row)
        assert result["days_to_nearest_opex"] == pytest.approx(5.0)

    def test_zero_days(self):
        row = _make_row(days_to_expiry=0.0)
        result = compute_derived_features(row)
        assert result["days_to_nearest_opex"] == pytest.approx(0.0)

    def test_missing(self):
        row = _make_row(days_to_expiry=None)
        result = compute_derived_features(row)
        assert result["days_to_nearest_opex"] is None


# ---------------------------------------------------------------------------
# Batch / Single parity — new features
# ---------------------------------------------------------------------------


class TestBatchMatchesSingleNewFeatures:
    """Batch vectorized computation must produce same values as row-by-row for new features."""

    def test_batch_matches_single_new_features(self):
        rows = [
            _make_row(
                total_ask_side_prem=100.0,
                total_bid_side_prem=50.0,
                max_pain_strike=145.0,
                days_to_expiry=3.0,
            ),
            _make_row(
                total_ask_side_prem=200.0,
                total_bid_side_prem=200.0,
                max_pain_strike=160.0,
                days_to_expiry=30.0,
            ),
            _make_row(
                total_ask_side_prem=None,
                total_bid_side_prem=50.0,
                max_pain_strike=None,
                days_to_expiry=None,
            ),
        ]

        df = _make_df(rows)
        batch_result = compute_derived_features_batch(df)

        new_features = [
            "ask_side_dominance",
            "aggressor_conviction",
            "max_pain_distance",
            "days_to_nearest_opex",
        ]

        for i, row in enumerate(rows):
            single_result = compute_derived_features(row)
            for name in new_features:
                batch_val = batch_result[name].iloc[i]
                single_val = single_result[name]

                if single_val is None:
                    assert pd.isna(batch_val), f"Row {i}, feature {name}: expected NaN, got {batch_val}"
                else:
                    assert not pd.isna(batch_val), f"Row {i}, feature {name}: expected {single_val}, got NaN"
                    assert batch_val == pytest.approx(single_val, rel=1e-9), (
                        f"Row {i}, feature {name}: single={single_val}, batch={batch_val}"
                    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestDerivedFeatureNamesConstant:
    """DERIVED_FEATURE_NAMES must have exactly 9 entries matching the spec."""

    def test_count(self):
        assert len(DERIVED_FEATURE_NAMES) == 9

    def test_expected_names(self):
        expected = {
            "iv_vs_realized",
            "vega_theta_ratio",
            "gamma_delta_ratio",
            "dollar_gamma",
            "theta_premium_ratio",
            "ask_side_dominance",
            "aggressor_conviction",
            "max_pain_distance",
            "days_to_nearest_opex",
        }
        assert set(DERIVED_FEATURE_NAMES) == expected

    def test_no_duplicates(self):
        assert len(DERIVED_FEATURE_NAMES) == len(set(DERIVED_FEATURE_NAMES))
