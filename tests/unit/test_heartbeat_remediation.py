from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.storage.models import SystemStatus

# Mock data
MOCK_FLOW_RESPONSE = [
    {
        "id": "123",
        "ticker": "AAPL",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "premium": 50000,
        "put_call": "C",
    }
]


@pytest.mark.asyncio
async def test_uw_flow_connector_poll_updates_db_heartbeat():
    # Patch the class method
    with patch.object(UWFlowConnector, "fetch_raw_events", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = MOCK_FLOW_RESPONSE

        # Mock DB Session
        with patch("orion.storage.db.async_session_factory") as mock_session_factory:
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__.return_value = mock_session

            # Setup mock for select(SystemStatus)
            mock_result = MagicMock()
            mock_record = SystemStatus(key="global_health", status="OLD_STATUS")
            mock_result.scalars.return_value.first.return_value = mock_record
            mock_session.execute.return_value = mock_result
            mock_session.commit = AsyncMock()

            # Mock Watermark functions to avoid interference with session.execute mock
            with (
                patch("orion.storage.watermarks.get_watermark", new_callable=AsyncMock) as mock_get_wm,
                patch("orion.storage.watermarks.upsert_watermark", new_callable=AsyncMock),
            ):
                mock_get_wm.return_value = None

                connector = UWFlowConnector(api_key="test_key")
                # Manually mock the instance method to ensure interception
                connector.fetch_raw_events = AsyncMock(return_value=MOCK_FLOW_RESPONSE)
                # Ensure we bypass any validation that might check api key via property

                # Execute Poll
                events = await connector.poll(lookback_seconds=60)

                # Verify Fetch was called
                assert connector.fetch_raw_events.call_count >= 1, "fetch_raw_events should be called"

                # Verify DB Heartbeat Update
                assert mock_record.status == "HEALTHY"
                assert mock_session.commit.call_count >= 1

                # Verify Event Parsing
                assert len(events) == 1
                assert events[0].payload["ticker"] == "AAPL"
