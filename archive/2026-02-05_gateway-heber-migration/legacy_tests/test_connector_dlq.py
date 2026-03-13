from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from orion.connectors.uw_darkpool_connector import UWDarkPoolConnector
from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.storage import db
from orion.storage.models_dlq import DeadLetterQueue
from sqlalchemy import delete, select

# Mock response data
VALID_FLOW = {
    "id": "FLOW_TEST_1",
    "ticker": "AAPL",
    "timestamp": datetime.now(UTC).isoformat(),
    "premium": 1000,
    "strike_price": 150,
    "expiry": "2025-01-17",
    "put_call": "C",
}

# Malformed event (invalid timestamp)
MALFORMED_FLOW = {"id": "FLOW_TEST_BAD", "ticker": "MSFT", "timestamp": "NOT_A_TIMESTAMP", "premium": 500}


@pytest.fixture
async def cleanup_dlq():
    """Cleanup DLQ before and after test using isolated in-memory DB"""
    import orion.storage.db
    from orion.storage.db import Base
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    # Create isolated in-memory DB
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    # Patch the global engine/session factory
    # We must patch before creating tables so models are bound if needed (though Base is unbound)
    with (
        patch.object(orion.storage.db, "engine", test_engine),
        patch.object(orion.storage.db, "async_session_factory", test_session_factory),
    ):
        # Create Tables
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with test_session_factory() as session:
            await session.execute(delete(DeadLetterQueue))
            await session.commit()

        yield

        # Cleanup (optional for in-memory)


@pytest.mark.asyncio
async def test_uw_flow_dlq_integration(cleanup_dlq, mocker):
    """
    Verifies that UWFlowConnector processes valid events AND logs malformed events to DLQ.
    """
    # Mock network call
    connector = UWFlowConnector(api_key="verify_test_key")

    # Mocking sync session.get or fetch_flow?
    # fetch_flow is sync, wrapped in thread.
    # Easier to mock fetch_flow to return the mixed list

    # Mock fetch_raw_events directly as it's the external boundary for this test
    # ensuring it returns the mix of valid and invalid payloads.
    # Note: fetch_raw_events in source is async.
    mock_fetch = mocker.AsyncMock(return_value=[VALID_FLOW, MALFORMED_FLOW])
    mocker.patch.object(connector, "fetch_raw_events", side_effect=mock_fetch)

    # Act
    events = await connector.poll()

    # Assert
    assert len(events) == 1
    assert events[0].event_id == connector._generate_event_id(VALID_FLOW)

    # Verify DLQ
    async with db.async_session_factory() as session:
        stmt = select(DeadLetterQueue).where(DeadLetterQueue.source == "UWFlowConnector")
        result = await session.execute(stmt)
        entries = result.scalars().all()

        assert len(entries) == 1
        dlq_entry = entries[0]
        assert dlq_entry.event_type == "UW_FLOW_PARSE_ERROR"
        assert dlq_entry.payload == MALFORMED_FLOW
        # parse_timestamptz raises ValueError: "Failed to parse timestamp..."
        assert "Failed to parse timestamp" in str(dlq_entry.error_message)


@pytest.mark.asyncio
async def test_uw_darkpool_dlq_integration(cleanup_dlq, mocker):
    """
    Verifies UWDarkPoolConnector DLQ logic.
    """
    connector = UWDarkPoolConnector(api_key="verify_test_key", base_url="http://mock")

    mock_response = {
        "data": [
            {
                "id": "DARK_TEST_1",
                "ticker": "TSLA",
                "executed_at": datetime.now(UTC).isoformat(),
                "price": 200,
                "size": 100,
            },
            {"id": "DARK_TEST_BAD", "ticker": "NVDA", "executed_at": "GARBAGE_DATE", "price": 500},
        ]
    }

    # Patch the function imported in the module, not a property of the client
    # source: from orion.unusualwhales.api.darkpool import get_trades_by_date
    mock_sync = mocker.MagicMock(return_value=mock_response)
    mocker.patch("orion.connectors.uw_darkpool_connector.get_trades_by_date.sync", side_effect=mock_sync)

    # Act
    events = await connector.fetch_events()

    # Assert
    assert len(events) == 1
    assert events[0].ticker == "TSLA"  # Payload access

    # Verify DLQ
    async with db.async_session_factory() as session:
        stmt = select(DeadLetterQueue).where(DeadLetterQueue.source == "UWDarkPoolConnector")
        result = await session.execute(stmt)
        entries = result.scalars().all()

        assert len(entries) == 1
        assert entries[0].payload["ticker"] == "NVDA"
