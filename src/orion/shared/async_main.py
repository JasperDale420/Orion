"""Shared entry-point plumbing for Orion services.

Most ``main_*.py`` modules reimplement the same async-service startup/shutdown
boilerplate: create an ``asyncio.Event``, install SIGINT/SIGTERM handlers that
set it, optionally ``init_db()``, run a loop coroutine, and wrap ``__main__`` in
crash logging with a non-zero exit code on failure. This module centralizes that
plumbing.

Two helpers are provided because two genuinely different shapes exist:

* ``run_service`` — for loop-style services that take a shutdown ``asyncio.Event``
  and terminate when it is set. Owns signal handlers, optional ``init_db``,
  structured crash logging, and exit codes.
* ``run_entrypoint`` — for services that own their own shutdown lifecycle
  (e.g. a service class that installs its own signal handlers internally). Owns
  only ``asyncio.run`` plus crash logging — no signal handlers, no shutdown
  event, no ``init_db``.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import Awaitable, Callable
from functools import partial

from orion.shared.logger import setup_struct_logger


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
    logger: object,
) -> None:
    def handle_signal(sig: int) -> None:
        logger.info(f"Received signal {sig}. Shutting down...")  # type: ignore[attr-defined]
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, partial(handle_signal, sig))


def run_service(
    name: str,
    main_coro_factory: Callable[[asyncio.Event], Awaitable[None]],
    *,
    init_database: bool = True,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run a loop-style async service with standard shutdown plumbing.

    Owns: ``asyncio.run`` loop creation, SIGINT/SIGTERM handlers that set the
    shutdown event, optional ``init_db()``, structured crash logging, and exit
    codes (0 on clean shutdown, 1 on a fatal exception).

    Args:
        name: Logger name (e.g. ``"orion.feature_enrichment"``).
        main_coro_factory: Callable receiving the shutdown ``asyncio.Event`` and
            returning the service coroutine. It must return when the event is set.
        init_database: When True (default), call ``init_db()`` before the service
            coroutine. Services that init the DB themselves pass False.
        on_shutdown: Optional async cleanup awaited after the service coroutine
            returns (clean or crashing), e.g. lease release or buffer drain.
    """
    logger = setup_struct_logger(name)

    async def _runner() -> None:
        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        _install_signal_handlers(loop, shutdown_event, logger)

        if init_database:
            from orion.storage.db import init_db

            await init_db()

        try:
            await main_coro_factory(shutdown_event)
        finally:
            if on_shutdown is not None:
                await on_shutdown()

    try:
        asyncio.run(_runner())
    except KeyboardInterrupt:
        # Ctrl-C path — operator initiated, clean exit.
        logger.info(f"{name} stopped (KeyboardInterrupt).")
    except SystemExit:
        # argparse --help / arg errors and explicit sys.exit() must keep their
        # own exit code, not be reclassified as a crash.
        raise
    except BaseException as exc:
        # Any other escape is a crash. Emit a structured CRITICAL so log
        # aggregation sees it, then exit non-zero so docker/launchd restart
        # policies report failure rather than a "clean" exit.
        logger.critical(
            f"{name} crashed",
            extra={"event_type": "SERVICE_PROCESS_CRASHED", "service": name, "error": repr(exc)},
            exc_info=True,
        )
        sys.exit(1)


def run_entrypoint(
    name: str,
    coro: Awaitable[None],
    *,
    suppress_keyboard_interrupt: bool = True,
) -> None:
    """Run a self-managed async entry point with crash logging only.

    For services that own their own shutdown lifecycle (signal handlers,
    shutdown event) inside the coroutine. This wrapper provides only
    ``asyncio.run`` plus structured crash logging and a non-zero exit code on
    fatal failure.

    Args:
        name: Logger name.
        coro: The already-constructed service coroutine to run.
        suppress_keyboard_interrupt: When True (default), Ctrl-C exits cleanly.
    """
    logger = setup_struct_logger(name)
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        if not suppress_keyboard_interrupt:
            raise
        logger.info(f"{name} stopped (KeyboardInterrupt).")
    except SystemExit:
        # argparse --help / arg errors and explicit sys.exit() must keep their
        # own exit code, not be reclassified as a crash.
        raise
    except BaseException as exc:
        logger.critical(
            f"{name} crashed",
            extra={"event_type": "SERVICE_PROCESS_CRASHED", "service": name, "error": repr(exc)},
            exc_info=True,
        )
        sys.exit(1)
