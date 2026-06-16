"""Tests for ingestion silent-failure hardening.

Covers:
- Gateway WS degrade mode + recovery (_check_gateway_stream_health)
- Non-blocking Heber flow read (asyncio.to_thread offload)
- Cycle latency thresholds (_log_cycle_latency)
- Pipeline failures routed to DLQ (_run_pipeline)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

GATEWAY_PATCHES = [
    "orion.ingestion.service.HealthMonitor",
    "orion.ingestion.service.UniverseManager",
    "orion.ingestion.service.FeatureEngine",
    "orion.ingestion.service.RuleEngine",
    "orion.ingestion.service.xcals",
    "orion.ingestion.service.create_gateway_stream_client",
]


def _make_service():
    with (
        patch("orion.ingestion.service.HealthMonitor"),
        patch("orion.ingestion.service.UniverseManager"),
        patch("orion.ingestion.service.FeatureEngine"),
        patch("orion.ingestion.service.RuleEngine"),
        patch("orion.ingestion.service.xcals"),
        patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
    ):
        mock_factory.return_value = MagicMock()
        from orion.ingestion.service import IngestionService

        return IngestionService()


# ---------------------------------------------------------------------------
# Gateway WS degrade mode
# ---------------------------------------------------------------------------
class TestGatewayStreamHealth:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_no_degrade_during_initial_connection(self):
        """Stream not running and never connected => not degraded (initial backoff)."""
        svc = _make_service()
        svc._ws_ever_connected = False
        svc.gateway_stream.is_running = False
        svc.gateway_stream.restart = AsyncMock()

        with patch("orion.ingestion.service.send_discord_alert", new_callable=AsyncMock) as mock_alert:
            await svc._check_gateway_stream_health()

        assert svc.is_degraded is False
        mock_alert.assert_not_awaited()
        svc.gateway_stream.restart.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_enters_degraded_and_alerts_once(self):
        """A dead stream that had connected once degrades and alerts a single time."""
        svc = _make_service()
        svc._ws_ever_connected = True
        svc.gateway_stream.is_running = False
        # restart keeps failing so it stays degraded across cycles.
        svc.gateway_stream.restart = AsyncMock(return_value=False)

        with patch("orion.ingestion.service.send_discord_alert", new_callable=AsyncMock) as mock_alert:
            await svc._check_gateway_stream_health()
            await svc._check_gateway_stream_health()

        assert svc.is_degraded is True
        # Down alert fired exactly once (dedupe handled by alert layer; here the
        # service only calls send on the *transition* into degraded).
        mock_alert.assert_awaited_once()
        assert mock_alert.await_args.kwargs["dedupe_key"] == "gateway_ws_down"
        # Restart attempted every cycle.
        assert svc.gateway_stream.restart.await_count == 2

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_logs_error_every_degraded_cycle(self):
        svc = _make_service()
        svc._ws_ever_connected = True
        svc.gateway_stream.is_running = False
        svc.gateway_stream.restart = AsyncMock(return_value=False)

        with (
            patch("orion.ingestion.service.send_discord_alert", new_callable=AsyncMock),
            patch("orion.ingestion.service.logger") as mock_logger,
        ):
            await svc._check_gateway_stream_health()
            await svc._check_gateway_stream_health()

        error_events = [c.args[0] for c in mock_logger.error.call_args_list]
        assert error_events.count("gateway_ws_degraded") == 2

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_recovery_clears_degraded_and_alerts(self):
        svc = _make_service()
        svc._ws_ever_connected = True
        svc.gateway_stream.is_running = False
        svc.gateway_stream.restart = AsyncMock(return_value=True)

        with patch("orion.ingestion.service.send_discord_alert", new_callable=AsyncMock) as mock_alert:
            await svc._check_gateway_stream_health()

        assert svc.is_degraded is False
        dedupe_keys = [c.kwargs["dedupe_key"] for c in mock_alert.await_args_list]
        assert "gateway_ws_down" in dedupe_keys
        assert "gateway_ws_recovered" in dedupe_keys

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_healthy_stream_is_noop(self):
        svc = _make_service()
        svc._ws_ever_connected = True
        svc.gateway_stream.is_running = True
        svc.gateway_stream.restart = AsyncMock()

        with patch("orion.ingestion.service.send_discord_alert", new_callable=AsyncMock) as mock_alert:
            await svc._check_gateway_stream_health()

        assert svc.is_degraded is False
        mock_alert.assert_not_awaited()
        svc.gateway_stream.restart.assert_not_called()


# ---------------------------------------------------------------------------
# Non-blocking Heber read
# ---------------------------------------------------------------------------
class TestNonBlockingHeberRead:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_flow_poll_offloads_read_to_thread(self):
        import pandas as pd

        svc = _make_service()
        mock_reader = MagicMock()
        mock_reader.read_flow.return_value = pd.DataFrame()

        with (
            patch("orion.ingestion.service.get_heber_reader", return_value=mock_reader),
            patch("orion.ingestion.service.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
        ):
            mock_to_thread.return_value = pd.DataFrame()
            await svc._poll_heber_flow("trace-xyz")

        # The blocking pyarrow read must be dispatched via to_thread, not called inline.
        mock_to_thread.assert_awaited_once()
        assert mock_to_thread.await_args.args[0] is mock_reader.read_flow


# ---------------------------------------------------------------------------
# Cycle latency thresholds
# ---------------------------------------------------------------------------
class TestCycleLatency:
    @pytest.mark.unit
    def test_fast_cycle_no_log(self):
        svc = _make_service()
        with patch("orion.ingestion.service.logger") as mock_logger:
            svc._log_cycle_latency(2.0)
        mock_logger.warning.assert_not_called()
        mock_logger.error.assert_not_called()

    @pytest.mark.unit
    def test_slow_cycle_warns(self):
        from orion.ingestion.service import CYCLE_LATENCY_WARN_SECONDS

        svc = _make_service()
        with patch("orion.ingestion.service.logger") as mock_logger:
            svc._log_cycle_latency(CYCLE_LATENCY_WARN_SECONDS + 1)
        mock_logger.warning.assert_called_once()
        mock_logger.error.assert_not_called()

    @pytest.mark.unit
    def test_very_slow_cycle_errors(self):
        from orion.ingestion.service import CYCLE_LATENCY_ERROR_SECONDS

        svc = _make_service()
        with patch("orion.ingestion.service.logger") as mock_logger:
            svc._log_cycle_latency(CYCLE_LATENCY_ERROR_SECONDS + 1)
        mock_logger.error.assert_called_once()
        mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# Pipeline failure -> DLQ
# ---------------------------------------------------------------------------
class TestPipelineDLQ:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pipeline_failure_routed_to_dlq(self):
        svc = _make_service()
        ev = MagicMock()
        ev.event_id = "evt-1"
        feature_fn = MagicMock(side_effect=RuntimeError("feature boom"))

        with patch.object(svc, "_send_to_dlq", new_callable=AsyncMock) as mock_dlq:
            await svc._run_pipeline([ev], feature_fn, "UW")

        mock_dlq.assert_awaited_once()
        args, kwargs = mock_dlq.call_args
        assert isinstance(args[0], RuntimeError)
        assert args[1] == "UW_PIPELINE_ERROR"
        assert kwargs["payload"]["event_ids"] == ["evt-1"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pipeline_success_does_not_hit_dlq(self):
        svc = _make_service()
        svc.rule_engine = MagicMock()
        svc.rule_engine.process_signals = MagicMock(return_value=[])
        feature_fn = MagicMock(return_value=[MagicMock()])

        with (
            patch.object(svc, "_save_signals", new_callable=AsyncMock),
            patch.object(svc, "_send_to_dlq", new_callable=AsyncMock) as mock_dlq,
        ):
            await svc._run_pipeline([MagicMock()], feature_fn, "UW")

        mock_dlq.assert_not_awaited()
