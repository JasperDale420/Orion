"""
Tests for ExitClassifier.

Tests exit prediction logic and training data builder.
"""

import pytest

from orion.ml.exit_classifier import (
    ExitClassifier,
    ExitFeatures,
    ExitPrediction,
    get_exit_classifier,
)


class TestExitFeatures:
    """Tests for ExitFeatures dataclass."""

    def test_exit_features_creation(self) -> None:
        """Test creating exit features."""
        features = ExitFeatures(
            current_return_pct=25.0,
            time_held_hours=1.5,
            max_return_so_far=30.0,
            max_drawdown_so_far=5.0,
            premium_usd=100000,
            dte_at_entry=5,
            is_sweep=True,
            iv_rank_at_entry=75.0,
            vix_at_entry=18.0,
            trend_regime="BULLISH",
            vol_regime="NORMAL",
        )

        assert features.current_return_pct == 25.0
        assert features.is_sweep is True


class TestExitClassifier:
    """Tests for ExitClassifier class."""

    @pytest.fixture
    def classifier(self) -> ExitClassifier:
        """Create classifier instance."""
        return ExitClassifier()

    @pytest.fixture
    def profitable_position(self) -> ExitFeatures:
        """Position with good profit."""
        return ExitFeatures(
            current_return_pct=55.0,
            time_held_hours=2.0,
            max_return_so_far=60.0,
            max_drawdown_so_far=5.0,
            premium_usd=100000,
            dte_at_entry=5,
            is_sweep=True,
            iv_rank_at_entry=70.0,
            vix_at_entry=20.0,
            trend_regime="BULLISH",
            vol_regime="NORMAL",
        )

    @pytest.fixture
    def losing_position(self) -> ExitFeatures:
        """Position hitting stop loss."""
        return ExitFeatures(
            current_return_pct=-25.0,
            time_held_hours=1.0,
            max_return_so_far=5.0,
            max_drawdown_so_far=30.0,
            premium_usd=50000,
            dte_at_entry=3,
            is_sweep=False,
            iv_rank_at_entry=50.0,
            vix_at_entry=25.0,
            trend_regime="BEARISH",
            vol_regime="HIGH",
        )

    @pytest.fixture
    def zero_dte_position(self) -> ExitFeatures:
        """0DTE position with time decay concern."""
        return ExitFeatures(
            current_return_pct=15.0,
            time_held_hours=3.0,
            max_return_so_far=20.0,
            max_drawdown_so_far=5.0,
            premium_usd=75000,
            dte_at_entry=0,
            is_sweep=True,
            iv_rank_at_entry=80.0,
            vix_at_entry=22.0,
            trend_regime="NEUTRAL",
            vol_regime="NORMAL",
        )

    def test_classifier_initialization(self, classifier: ExitClassifier) -> None:
        """Test classifier initializes with heuristic mode."""
        assert classifier.use_heuristic is True
        assert classifier.model is None

    def test_predict_returns_exit_prediction(
        self,
        classifier: ExitClassifier,
        profitable_position: ExitFeatures,
    ) -> None:
        """Test predict returns ExitPrediction."""
        prediction = classifier.predict(profitable_position)

        assert isinstance(prediction, ExitPrediction)
        assert isinstance(prediction.should_exit, bool)
        assert 0 <= prediction.confidence <= 1
        assert prediction.reasoning

    def test_profitable_position_signals_exit(
        self,
        classifier: ExitClassifier,
        profitable_position: ExitFeatures,
    ) -> None:
        """Test 50%+ return triggers exit signal."""
        prediction = classifier.predict(profitable_position)

        assert prediction.should_exit is True
        assert "return" in prediction.reasoning.lower()

    def test_losing_position_signals_exit(
        self,
        classifier: ExitClassifier,
        losing_position: ExitFeatures,
    ) -> None:
        """Test stop loss triggers exit signal."""
        prediction = classifier.predict(losing_position)

        assert prediction.should_exit is True
        assert "stop" in prediction.reasoning.lower()

    def test_zero_dte_theta_decay(
        self,
        classifier: ExitClassifier,
        zero_dte_position: ExitFeatures,
    ) -> None:
        """Test 0DTE with time held triggers exit."""
        prediction = classifier.predict(zero_dte_position)

        assert prediction.should_exit is True
        assert "0dte" in prediction.reasoning.lower()

    def test_momentum_reversal(self, classifier: ExitClassifier) -> None:
        """Test momentum reversal triggers exit."""
        features = ExitFeatures(
            current_return_pct=-5.0,  # Now negative
            time_held_hours=2.0,
            max_return_so_far=25.0,  # Was up 25%
            max_drawdown_so_far=30.0,
            premium_usd=80000,
            dte_at_entry=2,
            is_sweep=True,
            iv_rank_at_entry=60.0,
            vix_at_entry=18.0,
            trend_regime="NEUTRAL",
            vol_regime="NORMAL",
        )

        prediction = classifier.predict(features)

        assert prediction.should_exit is True
        assert "reversal" in prediction.reasoning.lower()

    def test_no_exit_signal_for_normal_position(
        self,
        classifier: ExitClassifier,
    ) -> None:
        """Test normal position doesn't get exit signal."""
        features = ExitFeatures(
            current_return_pct=10.0,  # Modest gain
            time_held_hours=0.5,  # Short time
            max_return_so_far=12.0,
            max_drawdown_so_far=3.0,
            premium_usd=60000,
            dte_at_entry=5,
            is_sweep=False,
            iv_rank_at_entry=50.0,
            vix_at_entry=16.0,
            trend_regime="BULLISH",
            vol_regime="LOW",
        )

        prediction = classifier.predict(features)

        assert prediction.should_exit is False

    def test_predict_batch(self, classifier: ExitClassifier) -> None:
        """Test batch prediction."""
        features_list = [
            ExitFeatures(
                current_return_pct=60.0,
                time_held_hours=1.0,
                max_return_so_far=65.0,
                max_drawdown_so_far=5.0,
                premium_usd=100000,
                dte_at_entry=3,
                is_sweep=True,
                iv_rank_at_entry=70.0,
                vix_at_entry=20.0,
                trend_regime="BULLISH",
                vol_regime="NORMAL",
            ),
            ExitFeatures(
                current_return_pct=5.0,
                time_held_hours=0.5,
                max_return_so_far=8.0,
                max_drawdown_so_far=3.0,
                premium_usd=50000,
                dte_at_entry=7,
                is_sweep=False,
                iv_rank_at_entry=40.0,
                vix_at_entry=15.0,
                trend_regime="NEUTRAL",
                vol_regime="LOW",
            ),
        ]

        predictions = classifier.predict_batch(features_list)

        assert len(predictions) == 2
        assert predictions[0].should_exit is True  # High profit
        assert predictions[1].should_exit is False  # Normal


class TestGetExitClassifier:
    """Tests for singleton."""

    def test_get_exit_classifier_singleton(self) -> None:
        """Test singleton behavior."""
        c1 = get_exit_classifier()
        c2 = get_exit_classifier()

        assert c1 is c2
