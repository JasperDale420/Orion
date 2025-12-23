from datetime import datetime, timezone

from sqlalchemy import select

from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory
from orion.storage.models import SystemStatus

logger = setup_struct_logger("orion.core.circuit_breaker")


class CircuitBreaker:
    """
    Manages a global 'shut off' switch for the trading system.
    Backed by the SystemStatus table (DB).
    """

    KEY = "GLOBAL_CIRCUIT_BREAKER"

    def __init__(self):
        # We don't hold state in memory to ensure all services see the DB state.
        pass

    async def open(self, reason: str) -> None:
        """
        Trips the circuit breaker (Stops Trading).
        """
        async with async_session_factory() as session:
            # Upsert
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

            await session.commit()
            logger.critical(f"CIRCUIT BREAKER OPENED: {reason}")

    async def close(self) -> None:
        """
        Resets the circuit breaker (Resumes Trading).
        """
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == self.KEY)
            result = await session.execute(stmt)
            status_record = result.scalars().first()

            if status_record:
                status_record.status = "CLOSED"
                status_record.details = "Reset by system/operator"
                status_record.last_updated_utc = datetime.now(timezone.utc)
                await session.commit()
                logger.info("Circuit Breaker CLOSED (Reset). System Nominal.")

    async def is_open(self) -> bool:
        """
        Checks if trading should be halted.
        Returns True if OPEN (Halted).
        """
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == self.KEY)
            result = await session.execute(stmt)
            status_record = result.scalars().first()

            if status_record and status_record.status == "OPEN":
                return True
            return False

    async def get_state(self) -> dict:
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == self.KEY)
            result = await session.execute(stmt)
            status_record = result.scalars().first()

            if status_record:
                return {
                    "status": status_record.status,
                    "reason": status_record.details,
                    "last_updated": status_record.last_updated_utc,
                }
            return {"status": "CLOSED", "reason": "No record", "last_updated": None}
