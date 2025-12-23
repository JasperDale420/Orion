from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orion.jobs.dlq_consumer import DLQConsumer
from orion.storage.models_dlq import DeadLetterQueue


@pytest.fixture
def consumer():
    return DLQConsumer()


class MockAsyncSession:
    def __init__(self, result_scalars=None):
        self.result_scalars = result_scalars or []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def execute(self, stmt):
        result = MagicMock()
        result.scalars.return_value.all.return_value = self.result_scalars
        return result

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_dlq_run_once_no_items(consumer):
    mock_session = MockAsyncSession(result_scalars=[])

    with patch("orion.jobs.dlq_consumer.async_session_factory") as mock_factory:
        mock_factory.return_value = mock_session

        await consumer.run_once()

        # We can't assertions on execute call args easily with this class without spying
        # But we can verify no crash.


@pytest.mark.asyncio
async def test_dlq_replay_success(consumer):
    # Mock failed item
    mock_item = MagicMock(spec=DeadLetterQueue)
    mock_item.id = "failed_1"
    mock_item.status = "FAILED"
    mock_item.retry_count = 0
    mock_item.event_type = "ALPACA_BAR_1M"
    mock_item.payload = {"ticker": "AAPL", "c": 150.0}
    mock_item.timestamp_utc = datetime.now(timezone.utc)

    mock_session = MockAsyncSession(result_scalars=[mock_item])

    with patch("orion.jobs.dlq_consumer.async_session_factory") as mock_factory:
        mock_factory.return_value = mock_session

        # Mock FeatureEngine processing
        with patch.object(consumer.feature_engine, "process_alpaca_bars", return_value=[True]):
            with patch.object(consumer.feature_engine, "persist_signal_batch", new_callable=AsyncMock) as mock_persist:
                await consumer.run_once()

                assert mock_item.status == "REPLAYED"
                assert mock_persist.called
                assert mock_session.committed


@pytest.mark.asyncio
async def test_dlq_replay_failure(consumer):
    mock_item = MagicMock(spec=DeadLetterQueue)
    mock_item.id = "failed_2"
    mock_item.status = "FAILED"
    mock_item.retry_count = 0
    mock_item.event_type = "UNKNOWN_TYPE"
    mock_item.payload = {}

    mock_session = MockAsyncSession(result_scalars=[mock_item])

    with patch("orion.jobs.dlq_consumer.async_session_factory") as mock_factory:
        mock_factory.return_value = mock_session

        await consumer.run_once()

        assert mock_item.retry_count == 1
        assert "Retry" in mock_item.error_message
