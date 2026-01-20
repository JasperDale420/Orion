from unittest.mock import AsyncMock, patch

import pytest
from orion.storage.models import BronzeEvent
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_db_session_factory_mock():
    """Verify that we can mock the session interaction."""
    with patch("orion.storage.db.async_session_factory") as mock_factory:
        mock_session = AsyncMock(spec=AsyncSession)
        mock_factory.return_value.__aenter__.return_value = mock_session

        # Simulate usage
        async with mock_factory() as session:
            await session.execute("SELECT 1")
            await session.commit()

        # Verify calls
        assert session.execute.called
        assert session.commit.called


@pytest.mark.asyncio
async def test_save_events_mock():
    """Verify save_events_to_db logic using mocked DB."""
    # We need to import the function. It's in main_ingest.py
    # But main_ingest.py has global code that runs on import (which might fail if not mocked).
    # We mocked pandas_ta in conftest, so hopefully safe.
    # Also RedpandaProducer is used. We might need to mock that too.

    from orion.main_ingest import save_events_to_db

    events = [
        BronzeEvent(
            event_id="evt_1",
            source="TEST",
            event_type="TEST",
            payload={"foo": "bar"},
            # timestamps need to be handled if strictly constrained
        )
    ]
    # We mock out RedpandaProducer.get_instance().produce_event AND db session

    with (
        patch("orion.main_ingest.RedpandaProducer") as MockProducerCls,
        patch("orion.main_ingest.async_session_factory") as mock_db_factory,
    ):
        mock_producer = AsyncMock()
        mock_producer.produce_event = AsyncMock()
        MockProducerCls.get_instance = AsyncMock(return_value=mock_producer)

        mock_session = AsyncMock(spec=AsyncSession)
        mock_db_factory.return_value.__aenter__.return_value = mock_session

        await save_events_to_db(events)

        # Check Producer call
        assert mock_producer.produce_event.called

        # Check DB interact
        assert mock_session.execute.called
        assert mock_session.commit.called
