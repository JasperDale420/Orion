from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from aiokafka.errors import KafkaError
from orion.connectors.redpanda_producer import RedpandaProducer
from orion.storage.models import BronzeEvent

# Note: We need to use 'with patch' to mock the AIOKafkaProducer inside RedpandaProducer


@pytest.fixture
async def redpanda_producer():
    # Reset singleton
    RedpandaProducer._reset_instance()
    producer = RedpandaProducer.get_instance()
    yield producer
    await producer.stop()
    RedpandaProducer._reset_instance()


@pytest.mark.asyncio
async def test_redpanda_idempotence_config():
    """Verify that AIOKafkaProducer is initialized with idempotence=True and acks='all'."""

    # Reload module to bypass conftest global mock of get_instance
    import importlib

    import orion.connectors.redpanda_producer

    importlib.reload(orion.connectors.redpanda_producer)
    from orion.connectors.redpanda_producer import RedpandaProducer

    # Ensure fresh start
    RedpandaProducer._reset_instance()

    with patch("orion.connectors.redpanda_producer.AIOKafkaProducer") as MockKafka:
        mock_instance = AsyncMock()
        MockKafka.return_value = mock_instance

        producer = RedpandaProducer.get_instance()
        await producer.start()

        # Check constructor args
        MockKafka.assert_called_once()
        call_kwargs = MockKafka.call_args.kwargs

        assert call_kwargs.get("enable_idempotence") is True, "Idempotence must be enabled"
        assert call_kwargs.get("acks") == "all", "Acks must be 'all'"

    RedpandaProducer._reset_instance()


@pytest.mark.asyncio
async def test_redpanda_retry_logic():
    """Verify that send_and_wait retries on transient errors."""
    # Reload module to bypass conftest global mock
    import importlib

    import orion.connectors.redpanda_producer

    importlib.reload(orion.connectors.redpanda_producer)
    from orion.connectors.redpanda_producer import RedpandaProducer

    RedpandaProducer._reset_instance()

    with patch("orion.connectors.redpanda_producer.AIOKafkaProducer") as MockKafka:
        mock_kafka_instance = AsyncMock()
        MockKafka.return_value = mock_kafka_instance

        # Start the producer (fixture yields it, but we need to start it or ensure internal client is mock)
        # The fixture calls get_instance which calls __init__.
        # But we are patching AIOKafkaProducer inside this test scope.
        # If get_instance was called BEFORE this patch, the internal client is already created.
        # The fixture creates `producer` before yielding.
        # So we need to inject the mock into the existing producer instance.

        # Setup mock to raise Exception then succeed
        mock_kafka_instance.send_and_wait.side_effect = [
            KafkaError("Transient"),
            None,  # Success on retry
        ]

        producer = RedpandaProducer.get_instance()
        producer.client = mock_kafka_instance  # Inject mock client

        # Test
        await producer.produce_event("test_topic", "key", {"foo": "bar"})

        assert mock_kafka_instance.send_and_wait.call_count == 2, "Should have retried once"


@pytest.mark.asyncio
async def test_main_ingest_dlq_fallback():
    """Verify that main_ingest writes to DLQ if Redpanda fails exhaustively."""
    from orion.main_ingest import save_events_to_db

    # Create sample event
    event = BronzeEvent(
        event_id="test_evt_1",
        source="TEST",
        event_type="TEST_TYPE",
        payload={"data": 123},
        event_ts_utc=datetime.now(timezone.utc),
        received_ts_utc=datetime.now(timezone.utc),
        trading_date="2025-01-01",
        session="REG",
        schema_version="v1",
    )

    # Patch RedpandaProducer.get_instance to return a mock
    mock_producer = AsyncMock()
    mock_producer.produce_event.side_effect = Exception("Kafka Down")

    # Patch the RedpandaProducer imported in main_ingest to handle potential stale references due to reloads
    with (
        patch("orion.main_ingest.RedpandaProducer.get_instance", return_value=mock_producer),
        patch("orion.shared.dlq_utils.DLQWriter.write_to_dlq", new_callable=AsyncMock) as mock_dlq,
        patch("orion.main_ingest.persist_bronze_events", new_callable=AsyncMock),
        patch("orion.main_ingest.async_session_factory") as mock_session_factory,
    ):
        # Mock session context manager
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        await save_events_to_db([event])

        # Verify produce called
        assert mock_producer.produce_event.called

        # Verify DLQ called
        assert mock_dlq.called
        args, kwargs = mock_dlq.call_args
        assert kwargs["event_type"] == "REDPANDA_PRODUCE_FAILED"
        assert kwargs["event_id"] == "test_evt_1"
