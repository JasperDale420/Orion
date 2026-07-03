"""Tests for orion.shared.async_main entry-point helpers."""

import asyncio
import signal
from unittest.mock import AsyncMock, patch

import pytest

from orion.shared.async_main import run_entrypoint, run_service


# --------------------------------------------------------------------------
# run_service
# --------------------------------------------------------------------------


def test_run_service_clean_exit_when_factory_returns():
    """A factory that returns normally yields a clean (exit-0) run."""
    seen_event: list[asyncio.Event] = []

    async def factory(shutdown_event: asyncio.Event) -> None:
        seen_event.append(shutdown_event)
        # Return immediately — simulates a loop that observed shutdown.

    # init_database=False so we don't touch the DB.
    run_service("test.clean", factory, init_database=False)

    assert len(seen_event) == 1
    assert isinstance(seen_event[0], asyncio.Event)


def test_run_service_signal_sets_event_and_exits_cleanly():
    """A SIGTERM delivered while the factory loop runs sets the shutdown
    event, the loop observes it, and the process exits cleanly (no SystemExit).
    """

    async def factory(shutdown_event: asyncio.Event) -> None:
        # Raise the signal to ourselves; the helper's handler sets the event.
        signal.raise_signal(signal.SIGTERM)
        # Wait for the handler to fire and set the event.
        await asyncio.wait_for(shutdown_event.wait(), timeout=2.0)
        # Clean return on shutdown.

    # No SystemExit expected on a clean shutdown.
    run_service("test.signal", factory, init_database=False)


def test_run_service_fatal_exception_exits_nonzero():
    """A factory that raises exits with code 1 and logs a CRITICAL crash."""

    async def factory(shutdown_event: asyncio.Event) -> None:
        raise RuntimeError("boom")

    with patch("orion.shared.async_main.setup_struct_logger") as mock_logger_factory:
        mock_logger = mock_logger_factory.return_value
        with pytest.raises(SystemExit) as exc_info:
            run_service("test.fatal", factory, init_database=False)

    assert exc_info.value.code == 1
    # CRITICAL crash log emitted.
    assert mock_logger.critical.called
    crash_call = mock_logger.critical.call_args
    assert crash_call.kwargs["extra"]["event_type"] == "SERVICE_PROCESS_CRASHED"
    assert crash_call.kwargs["extra"]["service"] == "test.fatal"


def test_run_service_awaits_on_shutdown_on_clean_exit():
    """on_shutdown is awaited after a clean factory return."""
    on_shutdown = AsyncMock()

    async def factory(shutdown_event: asyncio.Event) -> None:
        return

    run_service("test.cleanup", factory, init_database=False, on_shutdown=on_shutdown)

    on_shutdown.assert_awaited_once()


def test_run_service_awaits_on_shutdown_even_on_crash():
    """on_shutdown runs even when the factory raises (finally semantics)."""
    on_shutdown = AsyncMock()

    async def factory(shutdown_event: asyncio.Event) -> None:
        raise ValueError("nope")

    with pytest.raises(SystemExit):
        run_service("test.cleanup_crash", factory, init_database=False, on_shutdown=on_shutdown)

    on_shutdown.assert_awaited_once()


def test_run_service_calls_init_db_when_enabled():
    """init_database=True invokes orion.storage.db.init_db before the factory."""
    call_order: list[str] = []

    async def fake_init_db() -> None:
        call_order.append("init_db")

    async def factory(shutdown_event: asyncio.Event) -> None:
        call_order.append("factory")

    with patch("orion.storage.db.init_db", side_effect=fake_init_db):
        run_service("test.initdb", factory, init_database=True)

    assert call_order == ["init_db", "factory"]


def test_run_service_skips_init_db_when_disabled():
    """init_database=False does not import/call init_db."""

    async def factory(shutdown_event: asyncio.Event) -> None:
        return

    with patch("orion.storage.db.init_db") as mock_init:
        run_service("test.noinitdb", factory, init_database=False)

    mock_init.assert_not_called()


# --------------------------------------------------------------------------
# run_entrypoint
# --------------------------------------------------------------------------


def test_run_entrypoint_runs_coro_to_completion():
    ran: list[bool] = []

    async def coro() -> None:
        ran.append(True)

    run_entrypoint("test.entry", coro())
    assert ran == [True]


def test_run_entrypoint_fatal_exception_exits_nonzero():
    async def coro() -> None:
        raise RuntimeError("crash")

    with patch("orion.shared.async_main.setup_struct_logger") as mock_logger_factory:
        mock_logger = mock_logger_factory.return_value
        with pytest.raises(SystemExit) as exc_info:
            run_entrypoint("test.entry_fatal", coro())

    assert exc_info.value.code == 1
    assert mock_logger.critical.called
    assert mock_logger.critical.call_args.kwargs["extra"]["event_type"] == "SERVICE_PROCESS_CRASHED"


def test_run_entrypoint_suppresses_keyboard_interrupt():
    async def coro() -> None:
        raise KeyboardInterrupt

    # Should not raise — KeyboardInterrupt is suppressed by default.
    run_entrypoint("test.entry_ki", coro())


def test_run_entrypoint_propagates_keyboard_interrupt_when_disabled():
    async def coro() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_entrypoint("test.entry_ki_raise", coro(), suppress_keyboard_interrupt=False)


# --------------------------------------------------------------------------
# Smoke-import: migrated entry points must not start a loop at import time.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "orion.main_execution",
        "orion.main_position_monitor",
        "orion.main_feature_enrichment",
        "orion.main_data_quality",
        "orion.ingestion.__main__",
    ],
)
def test_migrated_module_imports_without_running(module_name):
    """Importing a migrated entry point must not execute the service loop."""
    import importlib

    mod = importlib.import_module(module_name)
    assert mod is not None
