"""
Tests for MLScorer.

Tests the flow event scoring logic including heuristic baseline.
"""

import pytest
from orion.ml.scorer import MLScorer, get_scorer


class TestMLScorer:
    """Tests for MLScorer class."""

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
