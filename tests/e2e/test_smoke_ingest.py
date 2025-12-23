import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import orion.main_ingest as main_app
import pytest


@pytest.mark.asyncio
async def test_ingest_service_smoke_run():
    """Run the main loop for a short time and ensure it shuts down cleanly."""

    with ExitStack() as stack:
        # 1. Setup Class/Factory mocks
        MockProducerCls = stack.enter_context(patch("orion.main_ingest.RedpandaProducer"))
        stack.enter_context(patch("orion.main_ingest.async_session_factory"))
        stack.enter_context(patch("orion.main_ingest.init_db", new_callable=AsyncMock))
        MockHealthMonitorCls = stack.enter_context(patch("orion.core.health_monitor.HealthMonitor"))

        # 2. Setup Connector Mocks
        MockUWFlow = stack.enter_context(patch("orion.main_ingest.UWFlowConnector"))
        MockUWDark = stack.enter_context(patch("orion.main_ingest.UWDarkPoolConnector"))
        MockUWAlerts = stack.enter_context(patch("orion.main_ingest.UWAlertsConnector"))
        MockUniverse = stack.enter_context(patch("orion.main_ingest.UniverseManager"))
        stack.enter_context(patch("orion.main_ingest.AlpacaMarketConnector"))

        # 3. Setup Processing/Logic Mocks
        stack.enter_context(patch("orion.main_ingest.FeatureEngine"))
        stack.enter_context(patch("orion.main_ingest.RuleEngine"))
        stack.enter_context(patch("orion.main_ingest.LakehouseWriter"))
        MockDeduper = stack.enter_context(patch("orion.main_ingest.DeduplicationEngine"))
        stack.enter_context(patch("orion.main_ingest.NormalizationEngine"))

        # 4. Setup Persistence Mocks (CRITICAL: Must be AsyncMock)
        stack.enter_context(patch("orion.main_ingest.persist_bronze_events", new_callable=AsyncMock))
        stack.enter_context(patch("orion.main_ingest.persist_silver_from_bronze", new_callable=AsyncMock))
        stack.enter_context(patch("orion.main_ingest.persist_silver_signals", new_callable=AsyncMock))
        stack.enter_context(patch("orion.main_ingest.persist_candidates", new_callable=AsyncMock))

        # 5. Setup Asyncio Sleep
        stack.enter_context(patch("orion.main_ingest.asyncio.sleep", new_callable=AsyncMock))

        # --- Configure Mocks ---

        # Producer
        mock_producer = MockProducerCls.get_instance.return_value
        mock_producer.start = AsyncMock()
        mock_producer.stop = AsyncMock()
        mock_producer.produce_event = AsyncMock()
        mock_producer.produce_event.return_value = None

        # Connectors (Async Polls)
        # Note: We must ensure the INSTANCES returned by the constructors have async methods
        MockUWFlow.return_value.poll = AsyncMock(return_value=[])
        MockUWDark.return_value.fetch_events = AsyncMock(return_value=[])
        MockUWAlerts.return_value.fetch_events = AsyncMock(return_value=[])

        # Health Monitor
        MockHealthMonitorCls.return_value.check_lag = AsyncMock()
        MockHealthMonitorCls.return_value.check_health = AsyncMock()
        MockHealthMonitorCls.return_value.update_db_status = AsyncMock()
        MockUWAlerts.return_value.fetch_events = AsyncMock(return_value=[])

        # Universe Manager
        MockUniverse.return_value.hydrate_from_db = AsyncMock()
        # Deduplication
        MockDeduper.return_value.dedupe_batch = AsyncMock(return_value=[])

        # Feature Engine (Sync? Check usage. If sync, MagicMock is fine. If async, need fix)
        # main_ingest.py: "e.enrichment = feature_engine.process_event(...)" -> Sync.
        # But wait, "enriched = await feature_engine.process_uw_flow(events)"?
        # Let's mock it as AsyncMock just in case, or leave as MagicMock if validation says sync.
        # Previous errors were unrelated.

        # --- Run Test Logic ---

        main_app.SHUTDOWN = False
        task = asyncio.create_task(main_app.main())

        # Allow main loop to tick
        await asyncio.sleep(0.1)

        # Trigger Shutdown
        main_app.SHUTDOWN = True

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

        # Assertions
        mock_producer.start.assert_called_once()
        mock_producer.stop.assert_called_once()
