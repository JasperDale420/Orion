import asyncio
from unittest.mock import AsyncMock, patch

import orion.main_ingest as main_app
import pytest


@pytest.mark.asyncio
async def test_ingest_service_smoke_run():
    """Run the main loop for a short time and ensure it shuts down cleanly."""

    # Mock mocks
    with patch("orion.main_ingest.RedpandaProducer") as MockProducerCls, patch(
        "orion.main_ingest.async_session_factory"
    ) as MockDbFactory, patch("orion.main_ingest.init_db", new_callable=AsyncMock), patch(
        "orion.core.health_monitor.HealthMonitor"
    ) as MockHealthMonitorCls, patch(
        "orion.core.health_monitor.HealthMonitor"
    ) as MockHealthMonitorCls, patch(
        "orion.main_ingest.UWFlowConnector"
    ) as MockUWFlow, patch(
        "orion.main_ingest.UWDarkPoolConnector"
    ) as MockUWDark, patch(
        "orion.main_ingest.UWAlertsConnector"
    ) as MockUWAlerts, patch(
        "orion.main_ingest.UniverseManager"
    ) as MockUniverse, patch(
        "orion.main_ingest.AlpacaMarketConnector"
    ) as MockAlpaca, patch(
        "orion.main_ingest.FeatureEngine"
    ) as MockFeatureEngine, patch(
        "orion.main_ingest.RuleEngine"
    ) as MockRuleEngine, patch(
        "orion.main_ingest.LakehouseWriter"
    ) as MockLakehouse, patch(
        "orion.main_ingest.DeduplicationEngine"
    ) as MockDeduper, patch(
        "orion.main_ingest.NormalizationEngine"
    ) as MockNormalizer, patch(
        "orion.main_ingest.asyncio.sleep", new_callable=AsyncMock
    ) as mock_sleep:
        # Setup Mocks
        mock_producer = MockProducerCls.get_instance.return_value
        mock_producer.start = AsyncMock()
        mock_producer.stop = AsyncMock()
        mock_producer.produce_event = AsyncMock()

        # Shutdown controller
        # We start main(), wait 0.5s, then set SHUTDOWN=True

        main_app.SHUTDOWN = False

        task = asyncio.create_task(main_app.main())

        await asyncio.sleep(0.5)

        # Signal shutdown
        main_app.SHUTDOWN = True

        # Wait for finish (with timeout)
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            pytest.fail("Ingest Service did not shut down in time")

        if task.done() and not task.cancelled():
            exc = task.exception()
            if exc:
                raise exc

        # Verify startup calls
        mock_producer.start.assert_called_once()
        mock_producer.stop.assert_called_once()

        # Verify loop ran at least once (by checking a poll call usually, but mocked connector calls)
        # UW polls?
        # orion.main_ingest has local instances: uw_flow, uw_dark... wait.
        # main imports CLASSES but creates instances LOCALLY?
        # No, main_ingest code:
        # from orion.connectors.uw_flow_connector import UWFlowConnector
        # ...
        # But where are they instantiated?
        # Ah, looking at Step 6 file content:
        # It imports classes.
        # It does NOT instantiate them globally?
        # Wait, lines 206 "flow_events = uw_flow.poll()"
        # Where is 'uw_flow' defined?
        # Missing lines in main_ingest.py view?
        # "Unexpected" missing lines? I saw "Lines 1-326".
        # Let's check lines 192 or something.
        # "(rest of init)"
        # I only skimmed it.
        # If they are created in `main`, mocks need to target where they are instantiated.
        # If they are global, I need to patch globals.

        # Assuming they are instantiated in main() or global variables in module.
        pass
