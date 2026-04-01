"""
Tests for MLFlowProcessor.

Tests flow processing and candidate generation logic.
"""

from datetime import UTC, datetime

import pytest

from orion.ml.flow_processor import MLFlowProcessor, process_flows_with_ml


class TestMLFlowProcessor:
    """Tests for MLFlowProcessor class."""

    @pytest.fixture(autouse=True)
    def _isolate_models(self, tmp_path):
        """Force heuristic scoring so tests don't depend on trained model files."""
        import orion.ml.scorer as scorer_mod

        orig = scorer_mod.MODEL_DIR
        scorer_mod.MODEL_DIR = tmp_path / "empty_models"
        # Reset singleton so next MLFlowProcessor gets a fresh scorer
        scorer_mod._scorer = None
        yield
        scorer_mod.MODEL_DIR = orig
        scorer_mod._scorer = None

    @pytest.fixture
    def processor(self) -> MLFlowProcessor:
        """Create a processor with default threshold."""
        return MLFlowProcessor(score_threshold=0.5)

    @pytest.fixture
    def high_score_flow(self) -> dict:
        """Flow that should score high."""
        return {
            "ticker": "AAPL",
            "flow_ts_utc": datetime.now(UTC),
            "premium_usd": 500000,
            "is_sweep": "true",
            "aggressor": "ASK",
            "put_call": "C",
            "underlying_price": 150.0,
            "option_price": 5.0,
            "strike": 155.0,
            "dte": 5,
            "volume_contract": 1000,
            "open_interest": 200,
            "size_contracts": 500,
        }

    @pytest.fixture
    def low_score_flow(self) -> dict:
        """Flow that should score low."""
        return {
            "ticker": "XYZ",
            "flow_ts_utc": datetime.now(UTC),
            "premium_usd": 5000,
            "is_sweep": "false",
            "aggressor": "MID",
            "put_call": "C",
            "underlying_price": 50.0,
            "option_price": 1.0,
            "strike": 55.0,
            "dte": 30,
            "volume_contract": 50,
            "open_interest": 500,
            "size_contracts": 10,
        }

    def test_processor_initialization(self, processor: MLFlowProcessor) -> None:
        """Test processor initializes with correct threshold."""
        assert processor.score_threshold == 0.5
        assert processor.scorer is not None

    def test_process_flows_empty_list(self, processor: MLFlowProcessor) -> None:
        """Test processing empty flow list."""
        candidates = processor.process_flows([])
        assert candidates == []

    def test_process_flows_generates_candidates_above_threshold(
        self,
        processor: MLFlowProcessor,
        high_score_flow: dict,
    ) -> None:
        """Test high-scoring flows generate candidates."""
        candidates = processor.process_flows([high_score_flow])

        assert len(candidates) >= 1
        candidate = candidates[0]
        assert candidate.ticker == "AAPL"
        assert candidate.rule_id == "ml_score"
        assert candidate.evidence["ml_score"] >= 0.5

    def test_process_flows_filters_below_threshold(
        self,
        processor: MLFlowProcessor,
        low_score_flow: dict,
    ) -> None:
        """Test low-scoring flows don't generate candidates."""
        candidates = processor.process_flows([low_score_flow])

        # Low premium flow should not generate candidate
        assert len(candidates) == 0

    def test_process_flows_mixed_batch(
        self,
        processor: MLFlowProcessor,
        high_score_flow: dict,
        low_score_flow: dict,
    ) -> None:
        """Test mixed batch only includes high-scoring flows."""
        candidates = processor.process_flows([high_score_flow, low_score_flow])

        # Should only have candidate for high-score flow
        assert len(candidates) >= 1
        assert all(c.ticker == "AAPL" for c in candidates)

    def test_flow_to_candidate_direction_bullish_call(
        self,
        processor: MLFlowProcessor,
    ) -> None:
        """Test bullish call gets LONG direction."""
        flow = {
            "ticker": "SPY",
            "flow_ts_utc": datetime.now(UTC),
            "put_call": "C",
            "aggressor": "ASK",
            "premium_usd": 500000,
            "underlying_price": 450.0,
            "option_price": 10.0,
            "is_sweep": "true",
        }

        candidate = processor._flow_to_candidate(flow, score=0.7)

        assert candidate is not None
        assert candidate.direction == "LONG"

    def test_flow_to_candidate_direction_bullish_put(
        self,
        processor: MLFlowProcessor,
    ) -> None:
        """Test bullish put (BID aggressor) gets LONG direction."""
        flow = {
            "ticker": "SPY",
            "flow_ts_utc": datetime.now(UTC),
            "put_call": "P",
            "aggressor": "BID",
            "premium_usd": 500000,
            "underlying_price": 450.0,
            "option_price": 10.0,
        }

        candidate = processor._flow_to_candidate(flow, score=0.7)

        assert candidate is not None
        assert candidate.direction == "LONG"

    def test_flow_to_candidate_missing_ticker_returns_none(
        self,
        processor: MLFlowProcessor,
    ) -> None:
        """Test flow without ticker returns None."""
        flow = {"premium_usd": 100000}

        candidate = processor._flow_to_candidate(flow, score=0.7)

        assert candidate is None

    def test_get_rule_tags(self, processor: MLFlowProcessor) -> None:
        """Test rule tag generation for explainability."""
        flow = {
            "premium_usd": 600000,
            "is_sweep": "true",
            "dte": 0,
            "volume_oi_ratio": 3.0,
        }

        tags = processor._get_rule_tags(flow, score=0.8)

        assert "whale_premium" in tags
        assert "sweep" in tags
        assert "0dte" in tags
        assert "unusual_volume" in tags

    def test_convenience_function(self, high_score_flow: dict) -> None:
        """Test process_flows_with_ml convenience function."""
        candidates = process_flows_with_ml([high_score_flow], threshold=0.5)

        assert len(candidates) >= 1

    def test_process_flows_bypass_mode_passes_through_candidates(
        self, low_score_flow: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bypass mode should keep all flows eligible for candidate generation."""

        class _BypassScorer:
            bypass_scoring = True

            def score_batch(self, flows: list[dict]) -> list[float]:
                return [-1.0] * len(flows)

        monkeypatch.setattr("orion.ml.flow_processor.get_scorer", lambda: _BypassScorer())

        processor = MLFlowProcessor(score_threshold=0.5)
        candidates = processor.process_flows([low_score_flow])

        assert len(candidates) == 1
        assert candidates[0].evidence["ml_score"] == 1.0

    @pytest.mark.asyncio
    async def test_process_flows_enriched_bypass_mode_passes_through_candidates(
        self, low_score_flow: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bypass mode should also skip scoring in the enriched path."""

        class _BypassScorer:
            bypass_scoring = True

            async def score_enriched(self, flow: dict) -> float:
                return -1.0

        monkeypatch.setattr("orion.ml.flow_processor.get_scorer", lambda: _BypassScorer())

        processor = MLFlowProcessor(score_threshold=0.5)
        candidates = await processor.process_flows_enriched([low_score_flow])

        assert len(candidates) == 1
        assert candidates[0].evidence["ml_score"] == 1.0
