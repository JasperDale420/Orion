from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

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
        Trips the circuit breaker (Stops Trading).
        """

        async def set_breaker(session: Any) -> None:
            stmt = select(SystemStatus).where(SystemStatus.key == self.KEY)
            result = await session.execute(stmt)
            status_record = result.scalars().first()

            if status_record:
                if status_record.status == "OPEN":
                    return  # Already open
                status_record.status = "OPEN"
                status_record.details = reason
                status_record.last_updated_utc = datetime.now(timezone.utc)
            else:
                status_record = SystemStatus(
                    key=self.KEY, status="OPEN", details=reason, last_updated_utc=datetime.now(timezone.utc)
                )
                session.add(status_record)

        await db_write(set_breaker)
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
                status_record.last_updated_utc = datetime.now(timezone.utc)

        await db_write(reset_breaker)
        logger.info("Circuit Breaker CLOSED (Reset). System Nominal.")

    async def is_open(self) -> bool:
        """
        Checks if trading should be halted.
        Returns True if OPEN (Halted).
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
