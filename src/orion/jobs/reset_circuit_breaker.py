"""Close a stuck global circuit breaker.

Operator command (safe to re-run):

    DB_URL="postgresql+asyncpg://…@localhost:5440/orion_db" \\
      uv run python -m orion.jobs.reset_circuit_breaker [--reason "..."] [--dry-run]

Background: the previous ``scripts/reset_circuit_breaker.py`` imported
``psycopg2``, which is not a declared project dependency, so it failed at
import with ``ModuleNotFoundError`` — the exact command the dead-man-watchdog
Discord alert and the circuit-breaker runbook pointed operators at. This uses
the project's own async ORM path (``orion.core.circuit_breaker.CircuitBreaker``)
instead of a raw ``psycopg2`` connection, and honors ``DB_URL``/``ORION_DB_URL``
the same way every other Orion service does.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from orion.core.circuit_breaker import CircuitBreaker
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger(__name__)

DEFAULT_REASON = "Manual reset via operator command"


async def run_reset(*, reason: str, dry_run: bool = False) -> dict[str, object]:
    """Close the global circuit breaker if it is OPEN. Returns the final state."""
    await init_db()
    cb = CircuitBreaker()
    state = await cb.get_state()
    print(f"Current state: {state}")

    if state["status"] != "OPEN":
        print("Circuit breaker is already CLOSED. Nothing to do.")
        return state

    if dry_run:
        print("[DRY RUN] Would reset circuit breaker to CLOSED.")
        return state

    await cb.close(reason)
    state = await cb.get_state()
    print(f"Reset complete: {state}")
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Close the Orion global circuit breaker.")
    parser.add_argument("--reason", default=DEFAULT_REASON, help="Reason recorded for the reset")
    parser.add_argument("--dry-run", action="store_true", help="Show current state without modifying")
    args = parser.parse_args(argv)
    asyncio.run(run_reset(reason=args.reason, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
