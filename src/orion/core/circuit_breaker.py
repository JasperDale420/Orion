from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.models import SystemStatus

logger = setup_struct_logger("orion.core.circuit_breaker")


class CircuitBreaker:
    """
    Manages a global 'shut off' switch for the trading system.
    Backed by the SystemStatus table (DB).
    """

    KEY = "GLOBAL_CIRCUIT_BREAKER"

    def __init__(self) -> None:
        # We don't hold state in memory to ensure all services see the DB state.
        pass

    async def open(self, reason: str) -> None:
        """
        Trips the circuit breaker (Stops New Entries).

        Re-opening an already-open breaker keeps the FIRST reason and is a
        no-op. Callers on a hot path (the drawdown kill switch runs on every
        fill) may therefore call this repeatedly; only the transition logs.

        The transition is a single conditional UPDATE so two processes racing
        to trip the breaker can't both claim it: the loser's statement matches
        no row, so it neither overwrites the first reason nor re-logs. When
        there is no row at all yet, both can reach the insert and one loses on
        the primary key — that loser retries against the row the winner just
        committed rather than raising into the drawdown kill switch.
        """

        async def set_breaker(session: Any) -> bool:
            result = await session.execute(
                update(SystemStatus)
                .where(SystemStatus.key == self.KEY, SystemStatus.status != "OPEN")
                .values(status="OPEN", details=reason, last_updated_utc=datetime.now(UTC))
            )
            if result.rowcount:
                return True

            stmt = select(SystemStatus).where(SystemStatus.key == self.KEY)
            if (await session.execute(stmt)).scalars().first() is not None:
                return False  # Already open

            session.add(SystemStatus(key=self.KEY, status="OPEN", details=reason, last_updated_utc=datetime.now(UTC)))
            return True

        try:
            changed = await db_write(set_breaker)
        except IntegrityError:
            # Another process inserted the row between our UPDATE and our
            # INSERT. Re-run against it: the retry now finds a row, and an
            # already-OPEN one resolves to "no transition".
            changed = await db_write(set_breaker)

        if changed:
            logger.critical(f"CIRCUIT BREAKER OPENED: {reason}")

    async def close(self) -> None:
        """
        Resets the circuit breaker (Resumes Trading).
        """

        async def reset_breaker(session: Any) -> None:
            stmt = select(SystemStatus).where(SystemStatus.key == self.KEY)
            result = await session.execute(stmt)
            status_record = result.scalars().first()

            if status_record:
                status_record.status = "CLOSED"
                status_record.details = "Reset by system/operator"
                status_record.last_updated_utc = datetime.now(UTC)

        await db_write(reset_breaker)
        logger.info("Circuit Breaker CLOSED (Reset). System Nominal.")

    async def is_open(self) -> bool:
        """
        Reports whether NEW ENTRIES are halted. Returns True if OPEN.

        This is an entry gate only. Risk-reducing exits and closes must never
        consult it — a halted system still has to be able to get flat — so no
        exit path calls this method.

        The read is silent by design: it runs once per candidate and once per
        execution cycle, so anything logged here is logged thousands of times
        a day. State changes are logged by ``open()`` / ``close()``, and the
        dead-man watchdog alerts on a breaker left open.
        """

        async def check_status(session: Any) -> bool:
            stmt = select(SystemStatus).where(SystemStatus.key == self.KEY)
            result = await session.execute(stmt)
            status_record = result.scalars().first()
            return status_record is not None and status_record.status == "OPEN"

        return await db_query(check_status)

    async def get_state(self) -> dict[str, Any]:
        """Get current circuit breaker state."""

        async def fetch_state(session: Any) -> dict[str, Any]:
            stmt = select(SystemStatus).where(SystemStatus.key == self.KEY)
            result = await session.execute(stmt)
            status_record = result.scalars().first()
            if not status_record:
                return {"status": "CLOSED", "reason": "No record", "last_updated": None}
            return {
                "status": status_record.status,
                "reason": status_record.details,
                "last_updated": status_record.last_updated_utc,
            }

        return await db_query(fetch_state)


async def check_health_status() -> bool:
    """
    Legacy compatibility stub for check_health_status.
    Returns True if system is healthy (Circuit Closed), False otherwise.
    """
    cb = CircuitBreaker()
    is_open = await cb.is_open()
    return not is_open
