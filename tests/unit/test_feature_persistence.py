from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.processing.feature_engine import FeatureEngine
from orion.storage.models_silver import SilverSignal


@pytest.fixture
def mock_session():
    mock = AsyncMock()
    # Mock execute/scalars for fetch
    mock.execute.return_value.scalars.return_value.all.return_value = []
    return mock


@pytest.fixture
def feature_engine():
    return FeatureEngine()


@pytest.mark.asyncio
async def test_persist_signal_batch(feature_engine):
    with patch("orion.storage.db.async_session_factory") as mock_factory:
        mock_session_obj = AsyncMock()
        mock_factory.return_value.__aenter__.return_value = mock_session_obj

        signals = [
            SilverSignal(
                signal_id="sig1",
                ticker="AAPL",
                signal_ts_utc=datetime(2023, 1, 1, 10, 0),
                signal_type="OHLCV_1M",
                features={"close": 150.0},
            )
        ]

        await feature_engine.persist_signal_batch(signals, "v1_test")

        # Check if execute was called (insert)
        assert mock_session_obj.execute.called
        assert mock_session_obj.commit.called


@pytest.mark.asyncio
async def test_fetch_signal_batch(feature_engine):
    with patch("orion.storage.db.async_session_factory") as mock_factory:
        mock_session_obj = AsyncMock()
        mock_factory.return_value.__aenter__.return_value = mock_session_obj

        # Mock fetch result
        mock_row = MagicMock()
        mock_row.ticker = "AAPL"
        mock_row.event_ts_utc = datetime(2023, 1, 1, 10, 0)
        mock_row.features = {"close": 150.0}

        # Proper setup for AsyncMock return value chain
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_row]
        mock_session_obj.execute.return_value = mock_result

        signals = await feature_engine.fetch_signal_batch("AAPL", datetime(2023, 1, 1), datetime(2023, 1, 2), "v1_test")

        assert len(signals) == 1
        assert signals[0].ticker == "AAPL"
        assert signals[0].features["close"] == 150.0
        assert signals[0].signal_type == "GOLD_FEATURE"
