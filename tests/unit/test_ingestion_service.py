import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Common patches needed for constructing IngestionService with mocked gateway
GATEWAY_PATCHES = [
    "orion.ingestion.service.HealthMonitor",
    "orion.ingestion.service.UniverseManager",
    "orion.ingestion.service.FeatureEngine",
    "orion.ingestion.service.RuleEngine",
    "orion.ingestion.service.LakehouseWriter",
    "orion.ingestion.service.xcals",
    "orion.ingestion.service.create_gateway_stream_client",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bronze_event(**overrides):
    """Create a minimal BronzeEvent-like mock for testing."""
    event = MagicMock()
    event.event_id = overrides.get("event_id", str(uuid.uuid4()))
    event.source = overrides.get("source", "ALPACA")
    event.source_event_id = overrides.get("source_event_id", None)
    event.event_type = overrides.get("event_type", "ALPACA_BAR_1M")
    event.ticker = overrides.get("ticker", "AAPL")
    event.trading_date = overrides.get("trading_date", None)
    event.session = overrides.get("session", None)
    event.schema_version = overrides.get("schema_version", "v1")
    event.event_ts_utc = overrides.get("event_ts_utc", datetime.now(UTC))
    event.received_ts_utc = overrides.get("received_ts_utc", None)
    event.payload = overrides.get("payload", {"ticker": "AAPL", "close": 150.0})
    event.ingest = overrides.get("ingest", None)
    return event


# ---------------------------------------------------------------------------
# IngestionService.__init__
# ---------------------------------------------------------------------------
class TestIngestionServiceInit:
    """Tests for IngestionService constructor."""

    @pytest.mark.unit
    def test_constructor_sets_run_id(self):
        """Verify run_id is set on construction and injected into env."""
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            from orion.ingestion.service import IngestionService

            svc = IngestionService()

            assert svc.run_id is not None
            assert len(svc.run_id) > 0
            assert svc.shutdown_event is not None
            assert not svc.shutdown_event.is_set()

    @pytest.mark.unit
    def test_constructor_with_explicit_run_id(self, monkeypatch):
        """Verify ORION_RUN_ID env var is used when set."""
        monkeypatch.setenv("ORION_RUN_ID", "test-run-42")

        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            svc = IngestionService()
            assert svc.run_id == "test-run-42"

    @pytest.mark.unit
    def test_gateway_stream_client_initialized(self):
        """Gateway stream client is created via factory during construction."""
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            from orion.ingestion.service import IngestionService

            svc = IngestionService()
            mock_factory.assert_called_once()
            assert svc.gateway_stream is mock_client


# ---------------------------------------------------------------------------
# _enrich_temporal_data
# ---------------------------------------------------------------------------
class TestEnrichTemporalData:
    """Tests for IngestionService._enrich_temporal_data()."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    def test_sets_session_and_trading_date(self):
        svc = self._make_service()
        event = _make_bronze_event(event_ts_utc=datetime(2025, 3, 10, 15, 30, 0, tzinfo=UTC), session=None)

        with patch("orion.ingestion.service.derive_trading_date_and_session") as mock_derive:
            mock_derive.return_value = (date(2025, 3, 10), "REGULAR")
            svc._enrich_temporal_data(event)

        assert event.trading_date == date(2025, 3, 10)
        assert event.session == "REGULAR"

    @pytest.mark.unit
    def test_adds_utc_tzinfo_if_naive(self):
        svc = self._make_service()
        naive_dt = datetime(2025, 3, 10, 12, 0, 0)  # no tzinfo
        event = _make_bronze_event(event_ts_utc=naive_dt, session=None)

        with patch("orion.ingestion.service.derive_trading_date_and_session") as mock_derive:
            mock_derive.return_value = (date(2025, 3, 10), "REGULAR")
            svc._enrich_temporal_data(event)

        assert event.event_ts_utc.tzinfo is not None

    @pytest.mark.unit
    def test_sets_default_session_closed(self):
        svc = self._make_service()
        event = _make_bronze_event(event_ts_utc=None, session=None)

        svc._enrich_temporal_data(event)

        assert event.session == "CLOSED"

    @pytest.mark.unit
    def test_sets_received_ts_utc_when_none(self):
        svc = self._make_service()
        event = _make_bronze_event(received_ts_utc=None)

        svc._enrich_temporal_data(event)

        assert event.received_ts_utc is not None


# ---------------------------------------------------------------------------
# _tag_ingest_metadata
# ---------------------------------------------------------------------------
class TestTagIngestMetadata:
    """Tests for IngestionService._tag_ingest_metadata()."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    def test_sets_ingest_metadata_when_none(self):
        svc = self._make_service()
        event = _make_bronze_event(ingest=None)

        svc._tag_ingest_metadata(event, "trace-abc", "alpaca_market")

        assert event.ingest is not None
        assert event.ingest["connector"] == "alpaca_market"
        assert event.ingest["trace_id"] == "trace-abc"
        assert event.ingest["run_id"] == svc.run_id
        assert event.ingest["attempt"] == 1

    @pytest.mark.unit
    def test_does_not_overwrite_existing_ingest(self):
        svc = self._make_service()
        existing_ingest = {"connector": "original", "run_id": "old-run", "trace_id": "old-trace", "attempt": 3}
        event = _make_bronze_event(ingest=existing_ingest)

        svc._tag_ingest_metadata(event, "new-trace", "new-connector")

        # Should NOT overwrite because force_defaults is False by default
        assert event.ingest["connector"] == "original"

    @pytest.mark.unit
    def test_force_defaults_fills_missing_keys(self):
        svc = self._make_service()
        # Ingest exists but is missing some keys
        event = _make_bronze_event(ingest={"connector": "alpaca"})

        svc._tag_ingest_metadata(event, "trace-xyz", "unknown", force_defaults=True)

        assert event.ingest["connector"] == "alpaca"  # not overwritten
        assert event.ingest["run_id"] == svc.run_id  # filled in
        assert event.ingest["trace_id"] == "trace-xyz"  # filled in
        assert event.ingest["attempt"] == 1  # filled in


# ---------------------------------------------------------------------------
# _active_event_source_profile
# ---------------------------------------------------------------------------
class TestActiveEventSourceProfile:
    """Tests for _active_event_source_profile()."""

    def _make_service_with_gateway(self, gateway_running=True):
        """Create service with a mock gateway stream client."""
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_client = MagicMock()
            mock_client.is_running = gateway_running
            mock_client.subscribed_symbols = {"SPY", "QQQ"}
            mock_factory.return_value = mock_client
            from orion.ingestion.service import IngestionService

            svc = IngestionService()
            return svc

    @pytest.mark.unit
    def test_gateway_streaming_profile(self):
        svc = self._make_service_with_gateway(gateway_running=True)
        profile = svc._active_event_source_profile()
        assert profile["data_source"] == "gateway_stream"
        assert profile["gateway_connected"] is True
        assert "ALPACA_BAR_1M" in profile["produced_event_types"]

    @pytest.mark.unit
    def test_gateway_disconnected_profile(self):
        svc = self._make_service_with_gateway(gateway_running=False)
        profile = svc._active_event_source_profile()
        assert profile["data_source"] == "gateway_stream"
        assert profile["gateway_connected"] is False


# ---------------------------------------------------------------------------
# _check_eod_trigger
# ---------------------------------------------------------------------------
class TestCheckEodTrigger:
    """Tests for IngestionService._check_eod_trigger()."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    def test_triggers_eod_at_correct_time(self):
        svc = self._make_service()
        svc.eod_trigger_last_run = None

        # Simulate 01:05 UTC
        mock_now = datetime(2025, 3, 10, 1, 6, 0, tzinfo=UTC)
        with patch("orion.ingestion.service.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch.object(svc, "_run_eod_task", new_callable=AsyncMock) as mock_eod:
                # We need to mock asyncio.create_task since it requires a coroutine
                with patch("orion.ingestion.service.asyncio") as mock_asyncio:
                    mock_asyncio.create_task.return_value = MagicMock()
                    svc._check_eod_trigger()
                    mock_asyncio.create_task.assert_called_once()

        assert svc.eod_trigger_last_run == "2025-03-10"

    @pytest.mark.unit
    def test_does_not_trigger_if_already_run_today(self):
        svc = self._make_service()
        svc.eod_trigger_last_run = "2025-03-10"

        mock_now = datetime(2025, 3, 10, 1, 10, 0, tzinfo=UTC)
        with patch("orion.ingestion.service.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch("orion.ingestion.service.asyncio") as mock_asyncio:
                svc._check_eod_trigger()
                mock_asyncio.create_task.assert_not_called()

    @pytest.mark.unit
    def test_does_not_trigger_outside_window(self):
        svc = self._make_service()
        svc.eod_trigger_last_run = None

        # 10:00 UTC -- outside the 01:05+ window
        mock_now = datetime(2025, 3, 10, 10, 0, 0, tzinfo=UTC)
        with patch("orion.ingestion.service.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch("orion.ingestion.service.asyncio") as mock_asyncio:
                svc._check_eod_trigger()
                mock_asyncio.create_task.assert_not_called()


# ---------------------------------------------------------------------------
# _send_to_dlq (async)
# ---------------------------------------------------------------------------
class TestSendToDlq:
    """Tests for IngestionService._send_to_dlq()."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sends_dict_payload(self):
        svc = self._make_service()
        mock_writer = MagicMock()
        mock_writer.write_to_dlq = AsyncMock()

        with patch("orion.shared.dlq_utils.DLQWriter", mock_writer):
            await svc._send_to_dlq(
                error=ValueError("test error"),
                event_type="TEST_EVENT",
                payload={"key": "value"},
                trace_id="trace-123",
            )
            mock_writer.write_to_dlq.assert_called_once()
            call_kwargs = mock_writer.write_to_dlq.call_args
            assert call_kwargs[1].get("event_type") == "TEST_EVENT"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sends_string_payload(self):
        svc = self._make_service()
        mock_writer = MagicMock()
        mock_writer.write_to_dlq = AsyncMock()

        with patch("orion.shared.dlq_utils.DLQWriter", mock_writer):
            await svc._send_to_dlq(
                error=RuntimeError("fail"),
                event_type="CRITICAL",
                payload="string payload",
            )
            mock_writer.write_to_dlq.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sends_none_payload(self):
        svc = self._make_service()
        mock_writer = MagicMock()
        mock_writer.write_to_dlq = AsyncMock()

        with patch("orion.shared.dlq_utils.DLQWriter", mock_writer):
            await svc._send_to_dlq(
                error=RuntimeError("fail"),
                event_type="CRITICAL",
                payload=None,
            )
            mock_writer.write_to_dlq.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_stringifies_non_dict_non_string_payload(self):
        svc = self._make_service()
        mock_writer = MagicMock()
        mock_writer.write_to_dlq = AsyncMock()

        with patch("orion.shared.dlq_utils.DLQWriter", mock_writer):
            await svc._send_to_dlq(
                error=RuntimeError("fail"),
                event_type="WEIRD",
                payload=[1, 2, 3],  # list -- should be stringified
            )
            mock_writer.write_to_dlq.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_dlq_write_failure_logs_critical(self):
        svc = self._make_service()
        mock_writer = MagicMock()
        mock_writer.write_to_dlq = AsyncMock(side_effect=RuntimeError("DLQ broken"))

        with (
            patch("orion.shared.dlq_utils.DLQWriter", mock_writer),
            patch("orion.ingestion.service.logger") as mock_logger,
        ):
            await svc._send_to_dlq(
                error=RuntimeError("original"),
                event_type="TEST",
            )
            mock_logger.critical.assert_called_once()


# ---------------------------------------------------------------------------
# _persist_loop_crash (async)
# ---------------------------------------------------------------------------
class TestPersistLoopCrash:
    """Tests for IngestionService._persist_loop_crash()."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_persists_crash_to_db(self):
        svc = self._make_service()

        with patch("orion.ingestion.service.db_write", new_callable=AsyncMock) as mock_db_write:
            await svc._persist_loop_crash(RuntimeError("Main loop exploded"))
            mock_db_write.assert_called_once()
            # Verify the callable passed to db_write can be inspected
            persist_fn = mock_db_write.call_args[0][0]
            assert callable(persist_fn)


# ---------------------------------------------------------------------------
# stop (async)
# ---------------------------------------------------------------------------
class TestStop:
    """Tests for IngestionService.stop()."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_stop_closes_gateway_stream(self):
        svc = self._make_service()
        mock_stream = AsyncMock()
        mock_stream.is_running = True
        svc.gateway_stream = mock_stream

        await svc.stop()
        mock_stream.stop.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_stop_without_running_stream_succeeds(self):
        svc = self._make_service()
        mock_stream = MagicMock()
        mock_stream.is_running = False
        svc.gateway_stream = mock_stream

        # Should not raise
        await svc.stop()


# ---------------------------------------------------------------------------
# _poll_alpaca_events (async)
# ---------------------------------------------------------------------------
class TestGatewayStreamDrain:
    """Tests for gateway stream event draining in _run_cycle."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_client = MagicMock()
            mock_client.drain_events.return_value = []
            mock_client.subscribed_symbols = set()
            mock_factory.return_value = mock_client
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_drain_returns_empty_when_no_events(self):
        """Gateway drain returns empty list when no bar data has arrived."""
        svc = self._make_service()
        events = svc.gateway_stream.drain_events()
        assert events == []


# ---------------------------------------------------------------------------
# _process_features_and_rules (async)
# ---------------------------------------------------------------------------
class TestProcessFeaturesAndRules:
    """Tests for IngestionService._process_features_and_rules()."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_processes_uw_flow_events(self):
        svc = self._make_service()
        svc.feature_engine = MagicMock()
        svc.feature_engine.process_uw_flow = MagicMock()
        svc.feature_engine.process_uw_flow_events = MagicMock(return_value=[])
        svc.rule_engine = MagicMock()

        uw_event = _make_bronze_event(event_type="UW_FLOW")
        await svc._process_features_and_rules([uw_event])

        svc.feature_engine.process_uw_flow.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_processes_alpaca_bar_events(self):
        svc = self._make_service()
        svc.feature_engine = MagicMock()
        svc.feature_engine.process_uw_flow = MagicMock()
        svc.feature_engine.process_alpaca_bars = MagicMock(return_value=[])
        svc.rule_engine = MagicMock()

        bar_event = _make_bronze_event(event_type="ALPACA_BAR_1M")
        await svc._process_features_and_rules([bar_event])

        svc.feature_engine.process_uw_flow.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_feature_engine_error_does_not_crash(self):
        svc = self._make_service()
        svc.feature_engine = MagicMock()
        svc.feature_engine.process_uw_flow = MagicMock(side_effect=RuntimeError("engine broken"))
        svc.rule_engine = MagicMock()

        # Should not raise
        await svc._process_features_and_rules([_make_bronze_event()])


# ---------------------------------------------------------------------------
# _write_to_lakehouse (async)
# ---------------------------------------------------------------------------
class TestWriteToLakehouse:
    """Tests for IngestionService._write_to_lakehouse()."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_writes_events_successfully(self):
        svc = self._make_service()
        svc.lakehouse = MagicMock()
        svc.lakehouse.write_events = MagicMock()

        events = [_make_bronze_event(), _make_bronze_event()]
        await svc._write_to_lakehouse(events, "trace-1")

        svc.lakehouse.write_events.assert_called_once_with(events)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_lakehouse_error_sends_to_dlq(self):
        svc = self._make_service()
        svc.lakehouse = MagicMock()
        svc.lakehouse.write_events = MagicMock(side_effect=RuntimeError("write failed"))

        with patch.object(svc, "_send_to_dlq", new_callable=AsyncMock) as mock_dlq:
            await svc._write_to_lakehouse([_make_bronze_event()], "trace-2")
            mock_dlq.assert_called_once()


# ---------------------------------------------------------------------------
# _run_pipeline (async)
# ---------------------------------------------------------------------------
class TestRunPipeline:
    """Tests for IngestionService._run_pipeline()."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pipeline_with_signals_and_candidates(self):
        svc = self._make_service()
        svc.rule_engine = MagicMock()

        mock_signal = MagicMock()
        mock_candidate = MagicMock()

        feature_fn = MagicMock(return_value=[mock_signal])
        svc.rule_engine.process_signals = MagicMock(return_value=[mock_candidate])

        with (
            patch.object(svc, "_save_signals", new_callable=AsyncMock) as mock_save_signals,
            patch.object(svc, "_save_candidates", new_callable=AsyncMock) as mock_save_candidates,
        ):
            events = [_make_bronze_event()]
            await svc._run_pipeline(events, feature_fn, "Test")

            mock_save_signals.assert_called_once_with([mock_signal])
            mock_save_candidates.assert_called_once_with([mock_candidate])

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pipeline_no_signals(self):
        svc = self._make_service()

        feature_fn = MagicMock(return_value=[])  # no signals

        with patch.object(svc, "_save_signals", new_callable=AsyncMock) as mock_save_signals:
            await svc._run_pipeline([_make_bronze_event()], feature_fn, "Test")
            mock_save_signals.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pipeline_error_does_not_propagate(self):
        svc = self._make_service()

        feature_fn = MagicMock(side_effect=RuntimeError("pipeline failure"))

        # Should not raise
        await svc._run_pipeline([_make_bronze_event()], feature_fn, "Test")


# ---------------------------------------------------------------------------
# _update_health_status (async)
# ---------------------------------------------------------------------------
class TestUpdateHealthStatus:
    """Tests for IngestionService._update_health_status()."""

    def _make_service(self):
        with (
            patch("orion.ingestion.service.HealthMonitor"),
            patch("orion.ingestion.service.UniverseManager"),
            patch("orion.ingestion.service.FeatureEngine"),
            patch("orion.ingestion.service.RuleEngine"),
            patch("orion.ingestion.service.LakehouseWriter"),
            patch("orion.ingestion.service.xcals"),
            patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
        ):
            mock_factory.return_value = MagicMock()
            from orion.ingestion.service import IngestionService

            return IngestionService()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_healthy_status_update(self):
        svc = self._make_service()
        svc.health_monitor = MagicMock()
        svc.health_monitor.check_health = AsyncMock()
        svc.health_monitor.update_db_status = AsyncMock()

        await svc._update_health_status()

        svc.health_monitor.check_health.assert_called_once()
        svc.health_monitor.update_db_status.assert_called_once_with(True, "Nominal")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_critical_health_error_logs_and_updates(self):
        from orion.core.health_monitor import CriticalHealthError

        svc = self._make_service()
        svc.health_monitor = MagicMock()
        svc.health_monitor.check_health = AsyncMock(side_effect=CriticalHealthError("lag too high"))
        svc.health_monitor.update_db_status = AsyncMock()

        with patch("orion.ingestion.service.logger") as mock_logger:
            await svc._update_health_status()

            mock_logger.critical.assert_called_once()
            svc.health_monitor.update_db_status.assert_called_once_with(False, "lag too high")
