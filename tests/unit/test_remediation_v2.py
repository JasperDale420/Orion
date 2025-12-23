import os
from unittest.mock import AsyncMock, MagicMock, patch

import joblib
import pandas as pd
import pytest
from orion.core.model_registry import ModelRegistry
from orion.core.solver_executor import SolverPipeline
from orion.core.solver_schema import SolverConfig
from orion.processing.feature_engine import FeatureEngine

# Mock data
DUMMY_MODEL_URI = "file:///tmp/dummy_model.joblib"
DUMMY_MODEL_PATH = "/tmp/dummy_model.joblib"


class DummyModel:
    def predict_proba(self, X):
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
async def test_model_registry_load(mock_joblib_model):
    """Verify ModelRegistry loads from disk."""
    ModelRegistry.clear_cache()
    model = ModelRegistry.get(DUMMY_MODEL_URI)
    assert model is not None
    assert hasattr(model, "predict_proba")

    # Verify Cache
    model2 = ModelRegistry.get(DUMMY_MODEL_URI)
    assert model is model2


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
async def test_feature_engine_hydration():
    """Verify hydration logic calls DB."""
    # Mocking async session
    mock_session = AsyncMock()
    mock_result = MagicMock()

    # Mock bar object
    bar = MagicMock()
    bar.ticker = "SPY"
    bar.bar_start_ts_utc = pd.Timestamp("2023-01-01 10:00:00", tz="UTC")
    bar.close = 100.0
    bar.open = 99.0
    bar.high = 101.0
    bar.low = 98.0
    bar.volume = 1000
    bar.vwap = 100.0

    mock_result.scalars.return_value.all.return_value = [bar]
    mock_session.execute.return_value = mock_result

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__.return_value = mock_session

    with patch("orion.storage.db.async_session_factory", mock_factory):
        fe = FeatureEngine()
        await fe.hydrate_history()

        assert "SPY" in fe.history
        assert len(fe.history["SPY"]) == 1
        assert fe.history["SPY"].iloc[0]["close"] == 100.0
