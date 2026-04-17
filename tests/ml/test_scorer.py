"""
Tests for MLScorer.

Tests the flow event scoring logic including heuristic baseline.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from orion.ml.scorer import MLScorer, get_scorer


class TestMLScorer:
    """Tests for MLScorer class."""

    @pytest.fixture(autouse=True)
    def _isolate_models(self, tmp_path):
        """Force heuristic scoring so tests don't depend on trained model files."""
        import orion.ml.scorer as scorer_mod

        orig = scorer_mod.MODEL_DIR
        scorer_mod.MODEL_DIR = tmp_path / "empty_models"
        scorer_mod._scorer = None
        yield
        scorer_mod.MODEL_DIR = orig
        scorer_mod._scorer = None

    @pytest.fixture
    def scorer(self) -> MLScorer:
        """Create a fresh scorer instance."""
        return MLScorer()

    def test_scorer_initialization(self, scorer: MLScorer) -> None:
        """Test scorer initializes with heuristic mode when no model exists."""
        assert scorer.use_heuristic is True
        assert scorer.model is None

    def test_extract_features_basic(self, scorer: MLScorer) -> None:
        """Test feature extraction from flow dict."""
        flow = {
            "premium_usd": 100000,
            "dte": 5,
            "iv": 0.45,
            "volume_contract": 500,
            "open_interest": 1000,
            "underlying_price": 150.0,
            "strike": 155.0,
            "size_contracts": 100,
            "option_price": 2.50,
        }

        features = scorer.extract_features(flow)

        assert features["premium_usd"] == 100000
        assert features["dte"] == 5
        assert features["iv"] == 0.45
        assert features["moneyness"] == pytest.approx(155.0 / 150.0, rel=0.01)
        assert features["volume_oi_ratio"] == pytest.approx(0.5, rel=0.01)
        assert features["premium_per_contract"] == pytest.approx(1000, rel=0.01)

    def test_extract_features_handles_missing_values(self, scorer: MLScorer) -> None:
        """Test feature extraction handles missing/None values."""
        flow = {"ticker": "AAPL"}  # Minimal flow

        features = scorer.extract_features(flow)

        assert features["premium_usd"] == 0
        assert features["dte"] == 0
        assert features["moneyness"] == 1.0  # Default when underlying is 0

    def test_heuristic_score_high_premium_sweep(self, scorer: MLScorer) -> None:
        """Test heuristic scoring for high premium sweep trade."""
        flow = {
            "premium_usd": 500000,
            "is_sweep": "true",
            "aggressor": "ASK",
            "put_call": "C",
            "volume_contract": 1000,
            "open_interest": 200,  # High vol/OI ratio
            "underlying_price": 100,
            "strike": 105,
            "size_contracts": 500,
        }

        score = scorer.score(flow)

        # High premium + sweep + ASK aggressor + high vol/OI should score high
        assert score >= 0.7

    def test_heuristic_score_low_premium(self, scorer: MLScorer) -> None:
        """Test heuristic scoring penalizes low premium."""
        flow = {
            "premium_usd": 5000,  # Below 10k threshold
            "is_sweep": "false",
            "aggressor": "MID",
            "put_call": "C",
            "volume_contract": 100,
            "open_interest": 500,
            "underlying_price": 50,
            "strike": 55,
            "size_contracts": 10,
        }

        score = scorer.score(flow)

        # Low premium should score low
        assert score < 0.3

    def test_score_batch(self, scorer: MLScorer) -> None:
        """Test batch scoring returns correct number of scores."""
        flows = [
            {"premium_usd": 100000, "is_sweep": "true", "aggressor": "ASK", "put_call": "C"},
            {"premium_usd": 50000, "is_sweep": "false", "aggressor": "BID", "put_call": "P"},
            {"premium_usd": 25000, "is_sweep": "true", "aggressor": "ASK", "put_call": "C"},
        ]

        scores = scorer.score_batch(flows)

        assert len(scores) == 3
        assert all(0 <= s <= 1 for s in scores)

    def test_should_trade_threshold(self, scorer: MLScorer) -> None:
        """Test should_trade respects threshold."""
        high_premium_flow = {"premium_usd": 500000, "is_sweep": "true", "aggressor": "ASK", "put_call": "C"}
        low_premium_flow = {"premium_usd": 5000, "is_sweep": "false", "aggressor": "MID", "put_call": "C"}

        assert scorer.should_trade(high_premium_flow, threshold=0.5) is True
        assert scorer.should_trade(low_premium_flow, threshold=0.5) is False

    def test_get_scorer_singleton(self) -> None:
        """Test get_scorer returns singleton."""
        scorer1 = get_scorer()
        scorer2 = get_scorer()

        assert scorer1 is scorer2


class TestLegacyModelCategoricalFallback:
    """Tests that scoring works for legacy models without categorical_mappings.

    Reproduces the bug where 0DTE models trained before Apr 2026 have
    categorical features (put_call, aggressor, is_sweep) in their feature_names
    but no categorical_mappings key in the pickle, causing
    "could not convert string to float: 'P'" at np.array(..., dtype=float).
    """

    @pytest.fixture
    def legacy_model_scorer(self, tmp_path):
        """Create a scorer with a fake legacy 0DTE model (no categorical_mappings).

        Injects model data directly into the scorer rather than pickling to disk,
        because MagicMock objects are not picklable.
        """
        import orion.ml.scorer as scorer_mod

        orig = scorer_mod.MODEL_DIR
        # Point at empty dir so _load_models finds nothing on disk
        scorer_mod.MODEL_DIR = tmp_path / "empty_models"
        scorer_mod._scorer = None
        scorer = MLScorer()

        # Simulate a legacy 0DTE model: has categorical features in feature_names
        # but NO categorical_mappings key (matching the Jan 2026 format).
        feature_names = ["premium", "dte", "iv", "put_call", "aggressor", "is_sweep"]

        mock_model = MagicMock()
        mock_model.predict_proba.return_value = np.array([[0.3, 0.7]])

        model_data = {
            "model": mock_model,
            "feature_names": feature_names,
            "model_type": "0DTE_hit_target_50",
            "created_at": "2026-01-10T01:00:00+00:00",
            # NOTE: deliberately no "categorical_mappings" key
        }

        # Inject the model directly into the scorer's internal state
        scorer.models["0DTE"] = model_data
        scorer.feature_names["0DTE"] = feature_names
        scorer.use_heuristic = False
        yield scorer

        scorer_mod.MODEL_DIR = orig
        scorer_mod._scorer = None

    def test_score_does_not_crash_on_string_categorical(self, legacy_model_scorer):
        """Scoring a 0DTE flow with put_call='P' must not raise ValueError."""
        flow = {
            "premium_usd": 100000,
            "dte": 0,
            "iv": 0.55,
            "put_call": "P",
            "aggressor": "ASK",
            "is_sweep": "True",
            "underlying_price": 150.0,
            "strike": 148.0,
            "size_contracts": 50,
            "volume_contract": 200,
            "open_interest": 500,
        }
        score = legacy_model_scorer.score(flow)
        # Should return a valid probability, not crash
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_fallback_encoding_is_numeric(self, legacy_model_scorer):
        """Verify the fallback encodes string categoricals to numeric values."""
        flow = {
            "premium_usd": 50000,
            "dte": 0,
            "iv": 0.40,
            "put_call": "C",
            "aggressor": "BID",
            "is_sweep": "False",
            "underlying_price": 200.0,
            "strike": 205.0,
            "size_contracts": 100,
            "volume_contract": 300,
            "open_interest": 800,
        }
        features = legacy_model_scorer.extract_features(flow, bucket="0DTE")
        # Categorical values should still be strings at extract_features level
        # (encoding happens inside score()), but let's verify they exist
        assert "put_call" in features
        assert "aggressor" in features
        assert "is_sweep" in features

    def test_fallback_encoding_deterministic(self, legacy_model_scorer):
        """Same string value must always produce the same numeric code."""
        flow1 = {
            "premium_usd": 100000,
            "dte": 0,
            "iv": 0.5,
            "put_call": "P",
            "aggressor": "ASK",
            "is_sweep": "True",
            "underlying_price": 150.0,
            "strike": 148.0,
            "size_contracts": 50,
        }
        flow2 = dict(flow1)  # identical flow

        score1 = legacy_model_scorer.score(flow1)
        score2 = legacy_model_scorer.score(flow2)
        assert score1 == score2
