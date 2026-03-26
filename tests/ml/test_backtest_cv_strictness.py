from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from orion.processing.backtest_engine import BacktestEngine
from orion.storage.models_gold import CandidateTrade


def test_run_cv_raises_without_t1():
    """Verify run_cv raises ValueError if t1_times is missing."""
    engine = BacktestEngine()

    # Create valid candidates
    candidates = [
        CandidateTrade(
            candidate_id=f"c{i}",
            ticker="AAPL",
            timestamp_utc=datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i),
            evidence={},
        )
        for i in range(5)
    ]

    with pytest.raises(ValueError):
        engine.run_cv(candidates=candidates, price_data={}, solver_config=None, n_splits=3)


def test_run_cv_passes_with_t1():
    """Verify run_cv proceeds when t1_times is provided."""
    engine = BacktestEngine()

    candidates = [
        CandidateTrade(
            candidate_id=f"c{i}",
            ticker="AAPL",
            timestamp_utc=datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i),
            evidence={},
        )
        for i in range(10)
    ]

    # Matching t1 series (same length as candidates)
    t1 = pd.Series([c.timestamp_utc + timedelta(hours=1) for c in candidates])

    # Mock PurgedKFold
    with pytest.MonkeyPatch.context() as m:
        mock_kfold = MagicMock()
        # Mock split to return one fold: train=[0..4], test=[5..9]
        mock_kfold.split.return_value = [([0, 1, 2, 3, 4], [5, 6, 7, 8, 9])]

        m.setattr("orion.processing.backtest_engine.PurgedKFold", lambda **k: mock_kfold)

        # Should not raise ValueError
        try:
            res = engine.run_cv(candidates=candidates, price_data={}, solver_config=None, t1_times=t1, n_splits=2)
        except ValueError as e:
            pytest.fail(f"run_cv raised ValueError unexpectedly: {e}")
        assert "mean_sharpe" in res
