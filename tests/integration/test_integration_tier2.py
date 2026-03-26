"""Tests verifying Tier-2 feature wiring between Heber Gold pipelines and Orion ML.

These tests confirm that all new feature constants, dataset registrations,
and feature-store mappings are correctly connected.
"""

from __future__ import annotations

import pytest

from orion.ml.feature_store import (
    _ALERT_LEVEL_DATASETS,
    _GOLD_TO_FEATURE,
    _MARKET_LEVEL_DATASETS,
)
from orion.ml.pattern_miner import (
    ALERT_FLOW_CONTEXT_FEATURES,
    ALERT_SECTOR_FLOW_FEATURES,
    ALL_EQUITY_FEATURE_COLUMNS,
    EQUITY_DARKPOOL_FEATURES,
    EQUITY_FLOW_TOXICITY_FEATURES,
    EQUITY_GEX_REGIME_FEATURES,
    EQUITY_GOLD_DATASETS,
    EQUITY_MARKET_TIDE_FEATURES,
    EQUITY_OI_MOMENTUM_FEATURES,
    EQUITY_STRADDLE_FEATURES,
    EQUITY_TREND_SCAN_FEATURES,
    FEATURE_COLUMNS,
)


# ---------------------------------------------------------------------------
# FEATURE_COLUMNS completeness
# ---------------------------------------------------------------------------


class TestFeatureColumnsCompleteness:
    """All new features must appear in FEATURE_COLUMNS."""

    @pytest.mark.parametrize(
        "feature",
        [
            "net_gex",
            "gex_regime",
            "gex_flip_distance",
        ],
    )
    def test_gex_regime_features_in_feature_columns(self, feature: str):
        assert feature in FEATURE_COLUMNS, f"{feature} missing from FEATURE_COLUMNS"

    @pytest.mark.parametrize(
        "feature",
        [
            "flow_toxicity_1d",
            "toxicity_acceleration",
        ],
    )
    def test_flow_toxicity_features_in_feature_columns(self, feature: str):
        assert feature in FEATURE_COLUMNS, f"{feature} missing from FEATURE_COLUMNS"

    @pytest.mark.parametrize(
        "feature",
        [
            "oi_buildup_ratio",
            "new_position_signal",
            "oi_change_momentum_5d",
        ],
    )
    def test_oi_momentum_features_in_feature_columns(self, feature: str):
        assert feature in FEATURE_COLUMNS, f"{feature} missing from FEATURE_COLUMNS"

    @pytest.mark.parametrize(
        "feature",
        [
            "market_sentiment_score",
            "market_premium_momentum",
        ],
    )
    def test_market_tide_features_in_feature_columns(self, feature: str):
        assert feature in FEATURE_COLUMNS, f"{feature} missing from FEATURE_COLUMNS"

    @pytest.mark.parametrize(
        "feature",
        [
            "darkpool_notional_1d",
            "darkpool_premium_ratio",
            "darkpool_activity_zscore",
        ],
    )
    def test_darkpool_features_in_feature_columns(self, feature: str):
        assert feature in FEATURE_COLUMNS, f"{feature} missing from FEATURE_COLUMNS"

    @pytest.mark.parametrize(
        "feature",
        [
            "straddle_return_1m",
            "straddle_return_3m",
        ],
    )
    def test_straddle_features_in_feature_columns(self, feature: str):
        assert feature in FEATURE_COLUMNS, f"{feature} missing from FEATURE_COLUMNS"

    @pytest.mark.parametrize(
        "feature",
        [
            "trend_scan_horizon",
            "trend_scan_t_value",
        ],
    )
    def test_trend_scan_features_in_feature_columns(self, feature: str):
        assert feature in FEATURE_COLUMNS, f"{feature} missing from FEATURE_COLUMNS"

    @pytest.mark.parametrize(
        "feature",
        [
            "sector_flow_alignment",
            "sector_call_put_ratio",
        ],
    )
    def test_sector_flow_features_in_feature_columns(self, feature: str):
        assert feature in FEATURE_COLUMNS, f"{feature} missing from FEATURE_COLUMNS"

    @pytest.mark.parametrize(
        "feature",
        [
            "ask_side_dominance",
            "aggressor_conviction",
            "max_pain_distance",
            "days_to_nearest_opex",
        ],
    )
    def test_runtime_derived_features_in_feature_columns(self, feature: str):
        assert feature in FEATURE_COLUMNS, f"{feature} missing from FEATURE_COLUMNS"


# ---------------------------------------------------------------------------
# EQUITY_GOLD_DATASETS registration
# ---------------------------------------------------------------------------


class TestEquityGoldDatasetsRegistration:
    """All new Gold datasets must be registered in EQUITY_GOLD_DATASETS."""

    @pytest.mark.parametrize(
        "dataset",
        [
            "gex_regime_features",
            "flow_toxicity_features",
            "oi_momentum_features",
            "market_tide_context_features",
            "darkpool_features",
            "straddle_momentum_features",
            "trend_scan_features",
        ],
    )
    def test_new_datasets_registered(self, dataset: str):
        assert dataset in EQUITY_GOLD_DATASETS, f"{dataset} missing from EQUITY_GOLD_DATASETS"

    def test_gex_regime_dataset_maps_to_correct_features(self):
        assert EQUITY_GOLD_DATASETS["gex_regime_features"] == EQUITY_GEX_REGIME_FEATURES

    def test_flow_toxicity_dataset_maps_to_correct_features(self):
        assert EQUITY_GOLD_DATASETS["flow_toxicity_features"] == EQUITY_FLOW_TOXICITY_FEATURES

    def test_oi_momentum_dataset_maps_to_correct_features(self):
        assert EQUITY_GOLD_DATASETS["oi_momentum_features"] == EQUITY_OI_MOMENTUM_FEATURES

    def test_market_tide_dataset_maps_to_correct_features(self):
        assert EQUITY_GOLD_DATASETS["market_tide_context_features"] == EQUITY_MARKET_TIDE_FEATURES

    def test_darkpool_dataset_maps_to_correct_features(self):
        assert EQUITY_GOLD_DATASETS["darkpool_features"] == EQUITY_DARKPOOL_FEATURES

    def test_straddle_dataset_maps_to_correct_features(self):
        assert EQUITY_GOLD_DATASETS["straddle_momentum_features"] == EQUITY_STRADDLE_FEATURES

    def test_trend_scan_dataset_maps_to_correct_features(self):
        assert EQUITY_GOLD_DATASETS["trend_scan_features"] == EQUITY_TREND_SCAN_FEATURES


# ---------------------------------------------------------------------------
# _GOLD_TO_FEATURE mapping
# ---------------------------------------------------------------------------


class TestGoldToFeatureMapping:
    """Every new feature column must have an entry in _GOLD_TO_FEATURE."""

    _NEW_FEATURES = [
        "net_gex",
        "gex_regime",
        "gex_flip_distance",
        "flow_toxicity_1d",
        "toxicity_acceleration",
        "oi_buildup_ratio",
        "new_position_signal",
        "oi_change_momentum_5d",
        "market_sentiment_score",
        "market_premium_momentum",
        "darkpool_notional_1d",
        "darkpool_premium_ratio",
        "darkpool_activity_zscore",
        "straddle_return_1m",
        "straddle_return_3m",
        "trend_scan_horizon",
        "trend_scan_t_value",
        "sector_flow_alignment",
        "sector_call_put_ratio",
    ]

    @pytest.mark.parametrize("feature", _NEW_FEATURES)
    def test_feature_in_gold_to_feature(self, feature: str):
        assert feature in _GOLD_TO_FEATURE, f"{feature} missing from _GOLD_TO_FEATURE"
        assert _GOLD_TO_FEATURE[feature] == feature, f"_GOLD_TO_FEATURE[{feature}] != {feature}"


# ---------------------------------------------------------------------------
# Market-level and alert-level dataset sets
# ---------------------------------------------------------------------------


class TestDatasetLevelSets:
    """Market-level and alert-level datasets must include the new entries."""

    def test_market_level_contains_gex_regime(self):
        assert "gex_regime_features" in _MARKET_LEVEL_DATASETS

    def test_market_level_contains_market_tide_context(self):
        assert "market_tide_context_features" in _MARKET_LEVEL_DATASETS

    def test_market_level_still_contains_original(self):
        assert "market_regime_features" in _MARKET_LEVEL_DATASETS

    def test_market_level_has_three_entries(self):
        assert len(_MARKET_LEVEL_DATASETS) == 3

    def test_alert_level_contains_sector_flow(self):
        assert "sector_flow_features" in _ALERT_LEVEL_DATASETS

    def test_alert_level_still_contains_original(self):
        assert "flow_context_features" in _ALERT_LEVEL_DATASETS

    def test_alert_level_has_two_entries(self):
        assert len(_ALERT_LEVEL_DATASETS) == 2


# ---------------------------------------------------------------------------
# Feature group constant correctness
# ---------------------------------------------------------------------------


class TestFeatureGroupConstants:
    """New EQUITY_*_FEATURES and ALERT_*_FEATURES constants must exist with correct lengths."""

    def test_gex_regime_features_length(self):
        assert len(EQUITY_GEX_REGIME_FEATURES) == 3

    def test_flow_toxicity_features_length(self):
        assert len(EQUITY_FLOW_TOXICITY_FEATURES) == 2

    def test_oi_momentum_features_length(self):
        assert len(EQUITY_OI_MOMENTUM_FEATURES) == 3

    def test_market_tide_features_length(self):
        assert len(EQUITY_MARKET_TIDE_FEATURES) == 2

    def test_darkpool_features_length(self):
        assert len(EQUITY_DARKPOOL_FEATURES) == 3

    def test_straddle_features_length(self):
        assert len(EQUITY_STRADDLE_FEATURES) == 2

    def test_trend_scan_features_length(self):
        assert len(EQUITY_TREND_SCAN_FEATURES) == 2

    def test_sector_flow_features_length(self):
        assert len(ALERT_SECTOR_FLOW_FEATURES) == 2


# ---------------------------------------------------------------------------
# ALL_EQUITY_FEATURE_COLUMNS includes new groups
# ---------------------------------------------------------------------------


class TestAllEquityFeatureColumns:
    """ALL_EQUITY_FEATURE_COLUMNS must include every feature from new groups."""

    @pytest.mark.parametrize(
        "feature_list,name",
        [
            (EQUITY_GEX_REGIME_FEATURES, "EQUITY_GEX_REGIME_FEATURES"),
            (EQUITY_FLOW_TOXICITY_FEATURES, "EQUITY_FLOW_TOXICITY_FEATURES"),
            (EQUITY_OI_MOMENTUM_FEATURES, "EQUITY_OI_MOMENTUM_FEATURES"),
            (EQUITY_MARKET_TIDE_FEATURES, "EQUITY_MARKET_TIDE_FEATURES"),
            (EQUITY_DARKPOOL_FEATURES, "EQUITY_DARKPOOL_FEATURES"),
            (EQUITY_STRADDLE_FEATURES, "EQUITY_STRADDLE_FEATURES"),
            (EQUITY_TREND_SCAN_FEATURES, "EQUITY_TREND_SCAN_FEATURES"),
        ],
    )
    def test_group_included_in_all_equity(self, feature_list: list[str], name: str):
        for feat in feature_list:
            assert feat in ALL_EQUITY_FEATURE_COLUMNS, f"{feat} from {name} missing from ALL_EQUITY_FEATURE_COLUMNS"


# ---------------------------------------------------------------------------
# No duplicate features
# ---------------------------------------------------------------------------


class TestNoDuplicates:
    """Feature lists must not contain duplicates."""

    def test_feature_columns_no_duplicates(self):
        assert len(FEATURE_COLUMNS) == len(set(FEATURE_COLUMNS)), "FEATURE_COLUMNS has duplicates"

    def test_all_equity_feature_columns_no_duplicates(self):
        assert len(ALL_EQUITY_FEATURE_COLUMNS) == len(set(ALL_EQUITY_FEATURE_COLUMNS)), (
            "ALL_EQUITY_FEATURE_COLUMNS has duplicates"
        )
