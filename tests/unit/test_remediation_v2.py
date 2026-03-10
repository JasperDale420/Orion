import os
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import joblib
import pandas as pd
import pytest

from orion.config import system_settings
from orion.core.solver_executor import SolverPipeline
from orion.core.solver_schema import SolverConfig
from orion.processing.feature_engine import FeatureEngine

# Mock data
DUMMY_MODEL_URI = "file:///tmp/dummy_model.joblib"
DUMMY_MODEL_PATH = "/tmp/dummy_model.joblib"


class DummyModel:
    def predict_proba(self, X):  # noqa: N803
        # Return [ [0.4, 0.6] ] -> 0.6 prob trade
        return [[0.4, 0.6]]


@pytest.fixture
def mock_joblib_model():
    model = DummyModel()
    joblib.dump(model, DUMMY_MODEL_PATH)
    yield model
    if os.path.exists(DUMMY_MODEL_PATH):
        os.remove(DUMMY_MODEL_PATH)


@pytest.mark.asyncio
async def test_solver_pipeline_ml_inference(mock_joblib_model):
    """Verify SolverPipeline calls the model."""
    pipeline = SolverPipeline()

    # Mock Candidate and SolverConfig
    candidate = MagicMock()
    candidate.confidence = 0.5
    candidate.ticker = "AAPL"
    candidate.rule_id = "test_rule"

    config_dict = {
        "version_id": "v1",
        "base_strategy_name": "test",
        "timeframe": "5m",
        "entry_logic": {"rules": [], "combination_method": "AND"},
        "exit_logic": {},
        "risk": {},
        "model": {"model_uri": DUMMY_MODEL_URI},
        "volatility_penalty_threshold": 0.05,
    }
    solver = SolverConfig(**config_dict)

    # Mock FeatureEngine to return features
    feature_engine = MagicMock()
    feature_engine.compute = AsyncMock(return_value={"rsi": 50, "session_volatility": 0.01})

    # model_uri points to real file at /tmp/dummy_model.joblib (via file:// URI)
    # solver_executor loads it directly with joblib — no registry mock needed
    p_take, weight, trace = await pipeline.execute(solver, candidate, feature_engine)

    assert p_take == 0.6  # From dummy model


@pytest.mark.asyncio
async def test_solver_pipeline_volatility_penalty():
    """Verify dynamic threshold penalty."""
    pipeline = SolverPipeline()
    candidate = MagicMock()
    candidate.confidence = 0.8
    candidate.rule_id = "*"

    config_dict = {
        "version_id": "v1",
        "base_strategy_name": "test",
        "timeframe": "5m",
        "entry_logic": {"rules": [], "combination_method": "AND"},
        "exit_logic": {},
        "risk": {},
        # No model -> fallback to confidence
        "volatility_penalty_threshold": 0.02,  # Strict
    }
    solver = SolverConfig(**config_dict)

    feature_engine = MagicMock()
    feature_engine.compute = AsyncMock(return_value={"session_volatility": 0.03})

    p_take, weight, trace = await pipeline.execute(solver, candidate, feature_engine)

    # Base 0.8 * 0.8 penalty = 0.64
    assert pytest.approx(p_take) == 0.64


@pytest.mark.asyncio
async def test_feature_engine_hydration_prefers_heber(monkeypatch: pytest.MonkeyPatch):
    """Verify hydration logic reads bars from Heber."""
    now = pd.Timestamp("2023-01-01 10:00:00", tz="UTC")
    monkeypatch.setattr(system_settings, "static_watchlist", ["SPY"])

    frame = pd.DataFrame(
        {
            "symbol": ["SPY", "SPY"],
            "bar_start_ts": [now - timedelta(minutes=1), now],
            "open": [99.0, 100.0],
            "high": [100.0, 101.0],
            "low": [98.0, 99.0],
            "close": [99.5, 100.5],
            "volume": [900.0, 1000.0],
            "vwap": [99.4, 100.4],
        }
    )

    reader = MagicMock()
    reader.read_bars.return_value = frame
    monkeypatch.setattr("orion.processing.feature_engine.get_heber_reader", lambda: reader)

    fe = FeatureEngine()
    await fe.hydrate_history()

    assert "SPY" in fe.history
    assert len(fe.history["SPY"]) == 2
    assert fe.history["SPY"].iloc[-1]["close"] == 100.5


@pytest.mark.asyncio
async def test_feature_engine_hydration_returns_without_local_db_when_heber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(system_settings, "static_watchlist", ["SPY"])

    reader = MagicMock()
    reader.read_bars.side_effect = RuntimeError("heber unavailable")
    monkeypatch.setattr("orion.processing.feature_engine.get_heber_reader", lambda: reader)

    fe = FeatureEngine()
    await fe.hydrate_history()

    assert fe.history == {}
