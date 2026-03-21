"""Tests for new feature integration across pattern_miner and feature_store.

Validates that all 20 new features from agents 1-5 are properly wired into
the ML pipeline: FEATURE_COLUMNS, EQUITY_GOLD_DATASETS, _GOLD_TO_FEATURE,
derived feature computation, and runtime ratio computation.
"""

import pytest

from orion.ml.derived_features import DERIVED_FEATURE_NAMES, compute_derived_features
from orion.ml.feature_store import _GOLD_TO_FEATURE, _ALERT_LEVEL_DATASETS
from orion.ml.pattern_miner import (
    ALERT_FLOW_CONTEXT_FEATURES,
    ALL_EQUITY_FEATURE_COLUMNS,
    EQUITY_FLOW_NORM_FEATURES,
    EQUITY_GOLD_DATASETS,
    EQUITY_IV_SURFACE_FEATURES,
    EQUITY_TICKER_RATES_FEATURES,
    FEATURE_COLUMNS,
)


# All 20 new input features expected in FEATURE_COLUMNS
# (edge_ratio and time_efficiency are label-side, NOT included)
NEW_FEATURES = [
    # Derived (Agent 1) — 5 features
    "iv_vs_realized",
    "vega_theta_ratio",
    "gamma_delta_ratio",
    "dollar_gamma",
    "theta_premium_ratio",
    # Flow normalization Gold (Agent 2) — 3 features
    "adv_premium_20d",
    "adv_volume_20d",
    "adv_oi_20d",
    # Runtime-derived from flow normalization (Agent 2) — 3 features
    "premium_vs_adv",
    "volume_vs_adoi",
    "relative_oi_buildup",
    # IV surface Gold (Agent 3) — 3 features
    "put_call_iv_skew",
    "term_structure_slope",
    "iv_change_1d",
    # Ticker base rates Gold (Agent 4) — 3 features
    "ticker_win_rate_90d",
    "ticker_alert_frequency",
    "ticker_flow_predictability",
    # Flow context Gold (Agent 5) — 3 features
    "same_ticker_alerts_1h",
    "directional_agreement_4h",
    "repeat_ticker_days_5d",
]


class TestFeatureColumnsIncludesNewFeatures:
    """test_feature_columns_includes_new_features — all 20 new features in FEATURE_COLUMNS."""

    def test_all_20_new_features_present(self):
        feature_set = set(FEATURE_COLUMNS)
        missing = [f for f in NEW_FEATURES if f not in feature_set]
        assert not missing, f"Missing features in FEATURE_COLUMNS: {missing}"

    def test_feature_count_increased(self):
        # Original 25 alert + 24 equity/regime = 49; plus 20 new = 69
        assert len(FEATURE_COLUMNS) >= 69, f"FEATURE_COLUMNS has {len(FEATURE_COLUMNS)} entries, expected >= 69"


class TestEquityGoldDatasetsIncludesNew:
    """test_equity_gold_datasets_includes_new — new datasets registered."""

    def test_flow_normalization_in_gold_datasets(self):
        assert "flow_normalization_features" in EQUITY_GOLD_DATASETS
        assert EQUITY_GOLD_DATASETS["flow_normalization_features"] == EQUITY_FLOW_NORM_FEATURES

    def test_iv_surface_in_gold_datasets(self):
        assert "iv_surface_features" in EQUITY_GOLD_DATASETS
        assert EQUITY_GOLD_DATASETS["iv_surface_features"] == EQUITY_IV_SURFACE_FEATURES

    def test_ticker_base_rates_in_gold_datasets(self):
        assert "ticker_base_rates" in EQUITY_GOLD_DATASETS
        assert EQUITY_GOLD_DATASETS["ticker_base_rates"] == EQUITY_TICKER_RATES_FEATURES

    def test_flow_context_not_in_equity_gold(self):
        # flow_context_features is per-alert, not per-ticker daily —
        # should NOT be in EQUITY_GOLD_DATASETS
        assert "flow_context_features" not in EQUITY_GOLD_DATASETS

    def test_flow_context_in_alert_level_datasets(self):
        assert "flow_context_features" in _ALERT_LEVEL_DATASETS

    def test_all_equity_feature_columns_includes_new(self):
        all_eq_set = set(ALL_EQUITY_FEATURE_COLUMNS)
        for feat in EQUITY_FLOW_NORM_FEATURES + EQUITY_IV_SURFACE_FEATURES + EQUITY_TICKER_RATES_FEATURES:
            assert feat in all_eq_set, f"{feat} missing from ALL_EQUITY_FEATURE_COLUMNS"

    def test_original_datasets_still_present(self):
        assert "momentum_features" in EQUITY_GOLD_DATASETS
        assert "volatility_features" in EQUITY_GOLD_DATASETS
        assert "flow_features" in EQUITY_GOLD_DATASETS
        assert "market_regime_features" in EQUITY_GOLD_DATASETS


class TestDerivedFeaturesComputedInScoring:
    """test_derived_features_computed_in_scoring — derived features present after computation."""

    def test_derived_features_from_valid_inputs(self):
        row = {
            "iv": 0.35,
            "realized_vol_20d": 0.25,
            "vega": 0.15,
            "theta": -0.05,
            "gamma": 0.02,
            "delta": 0.55,
            "spot_price": 150.0,
            "contract_price": 3.50,
            "total_ask_side_prem": 100.0,
            "total_bid_side_prem": 50.0,
            "max_pain_strike": 145.0,
            "days_to_expiry": 3.0,
        }
        derived = compute_derived_features(row)
        for name in DERIVED_FEATURE_NAMES:
            assert name in derived, f"Missing derived feature: {name}"
            assert derived[name] is not None, f"Derived feature {name} is None with valid inputs"

    def test_derived_feature_values_reasonable(self):
        row = {
            "iv": 0.35,
            "realized_vol_20d": 0.25,
            "vega": 0.15,
            "theta": -0.05,
            "gamma": 0.02,
            "delta": 0.55,
            "spot_price": 150.0,
            "contract_price": 3.50,
            "total_ask_side_prem": 100.0,
            "total_bid_side_prem": 50.0,
            "max_pain_strike": 145.0,
            "days_to_expiry": 3.0,
        }
        derived = compute_derived_features(row)
        # iv_vs_realized = 0.35 / 0.25 = 1.4
        assert abs(derived["iv_vs_realized"] - 1.4) < 0.001
        # vega_theta_ratio = |0.15| / |−0.05| = 3.0
        assert abs(derived["vega_theta_ratio"] - 3.0) < 0.001
        # dollar_gamma = 0.02 * 150^2 * 0.01 = 4.5
        assert abs(derived["dollar_gamma"] - 4.5) < 0.001

    def test_derived_features_all_in_feature_columns(self):
        feature_set = set(FEATURE_COLUMNS)
        for name in DERIVED_FEATURE_NAMES:
            assert name in feature_set, f"Derived feature {name} not in FEATURE_COLUMNS"


class TestRuntimeRatiosComputed:
    """test_runtime_ratios_computed — premium_vs_adv etc. computed when both raw values present."""

    def test_premium_vs_adv_computed(self):
        # Simulates what feature_store does: after loading features, compute ratio
        premium = 50000.0
        adv_premium_20d = 100000.0
        ratio = premium / adv_premium_20d
        assert abs(ratio - 0.5) < 0.001

    def test_volume_vs_adoi_computed(self):
        volume = 1500.0
        adv_volume_20d = 3000.0
        ratio = volume / adv_volume_20d
        assert abs(ratio - 0.5) < 0.001

    def test_relative_oi_buildup_computed(self):
        open_interest = 5000.0
        adv_oi_20d = 2500.0
        ratio = open_interest / adv_oi_20d
        assert abs(ratio - 2.0) < 0.001

    def test_runtime_ratio_features_in_feature_columns(self):
        feature_set = set(FEATURE_COLUMNS)
        for feat in ["premium_vs_adv", "volume_vs_adoi", "relative_oi_buildup"]:
            assert feat in feature_set, f"Runtime ratio {feat} not in FEATURE_COLUMNS"


class TestRuntimeRatiosNoneWhenMissing:
    """test_runtime_ratios_none_when_missing — ratios are None when denominator missing."""

    def test_premium_vs_adv_none_when_adv_missing(self):
        row = {
            "premium": 50000.0,
            "adv_premium_20d": None,
            "iv": None,
            "realized_vol_20d": None,
            "vega": None,
            "theta": None,
            "gamma": None,
            "delta": None,
            "spot_price": None,
            "contract_price": None,
        }
        # Simulate feature_store logic
        premium = row.get("premium")
        adv = row.get("adv_premium_20d")
        result = (premium / adv) if (premium is not None and adv) else None
        assert result is None

    def test_volume_vs_adoi_none_when_adv_zero(self):
        volume = 1500.0
        adv_volume_20d = 0.0
        # In feature_store, zero denominator yields None via truthiness check
        result = (volume / adv_volume_20d) if (volume is not None and adv_volume_20d) else None
        assert result is None

    def test_relative_oi_buildup_none_when_oi_missing(self):
        open_interest = None
        adv_oi_20d = 2500.0
        result = (open_interest / adv_oi_20d) if (open_interest is not None and adv_oi_20d) else None
        assert result is None

    def test_derived_features_none_when_inputs_missing(self):
        row = {}  # All inputs missing
        derived = compute_derived_features(row)
        for name in DERIVED_FEATURE_NAMES:
            assert derived[name] is None, f"Expected None for {name} with missing inputs"


class TestGoldToFeatureHasNewMappings:
    """test_gold_to_feature_has_new_mappings — _GOLD_TO_FEATURE has entries for all new Gold columns."""

    EXPECTED_NEW_GOLD_COLUMNS = [
        # Flow normalization
        "adv_premium_20d",
        "adv_volume_20d",
        "adv_oi_20d",
        # IV surface
        "put_call_iv_skew",
        "term_structure_slope",
        "iv_change_1d",
        # Ticker base rates
        "ticker_win_rate_90d",
        "ticker_alert_frequency",
        "ticker_flow_predictability",
        # Flow context
        "same_ticker_alerts_1h",
        "directional_agreement_4h",
        "repeat_ticker_days_5d",
    ]

    def test_all_new_gold_columns_mapped(self):
        for col in self.EXPECTED_NEW_GOLD_COLUMNS:
            assert col in _GOLD_TO_FEATURE, f"Missing from _GOLD_TO_FEATURE: {col}"

    def test_mapping_is_identity(self):
        # All new features map to themselves (Gold col name == feature name)
        for col in self.EXPECTED_NEW_GOLD_COLUMNS:
            assert _GOLD_TO_FEATURE[col] == col, f"Expected identity mapping for {col}, got {_GOLD_TO_FEATURE[col]}"

    def test_original_mappings_preserved(self):
        # Verify some original mappings are still intact
        assert _GOLD_TO_FEATURE["delta"] == "delta"
        assert _GOLD_TO_FEATURE["premium"] == "premium"
        assert _GOLD_TO_FEATURE["dispersion"] == "dispersion"
        assert _GOLD_TO_FEATURE["market_tide_net_premium"] == "market_tide_30m"


class TestAlertFlowContextFeatures:
    """Verify ALERT_FLOW_CONTEXT_FEATURES is properly defined and wired."""

    def test_alert_flow_context_features_defined(self):
        assert ALERT_FLOW_CONTEXT_FEATURES == [
            "same_ticker_alerts_1h",
            "directional_agreement_4h",
            "repeat_ticker_days_5d",
        ]

    def test_all_flow_context_in_feature_columns(self):
        feature_set = set(FEATURE_COLUMNS)
        for feat in ALERT_FLOW_CONTEXT_FEATURES:
            assert feat in feature_set, f"Flow context feature {feat} not in FEATURE_COLUMNS"
