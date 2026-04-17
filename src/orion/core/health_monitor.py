import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func

from orion.config import system_settings

logger = logging.getLogger(__name__)


class CriticalHealthError(Exception):
    pass


class HealthMonitor:
    """
    PRD 9.1: Data health circuit breakers.
    Tracks ingestion lag and heartbeats. Raises exception if thresholds breached.
    """

    def __init__(self) -> None:
        self.last_heartbeat_ts: float = time.time()
        self.max_lag_seconds: float = 0.0

        # Thresholds (PRD 9.1)
        self.HEARTBEAT_THRESHOLD_SEC = 60.0
        self.LAG_THRESHOLD_SEC = float(system_settings.max_data_lag_seconds)

        # Metrics
        self.metrics: dict[str, Any] = {}

    def update_heartbeat(self) -> None:
        self.last_heartbeat_ts = time.time()

    async def check_lag(self, event_ts_utc: datetime) -> None:
        """
        Check lag for a specific event timestamp.
        """
        if not event_ts_utc:
            return

        # Simple lag check: Now - EventTS
        # Ensure UTC comparison
        now = datetime.now(UTC).replace(tzinfo=None)  # Force naive UTC for comparison
        if event_ts_utc.tzinfo:
            # naive comparison if one is aware and other is not is tricky
            # Let's assume event_ts_utc is naive UTC or convert
            event_ts_utc = event_ts_utc.replace(tzinfo=None)  # Force naive UTC for comparison with utcnow()

        lag = (now - event_ts_utc).total_seconds()
        self.max_lag_seconds = max(self.max_lag_seconds, lag)

        if lag > self.LAG_THRESHOLD_SEC:
            msg = f"CRITICAL: Event Lag {lag:.2f}s > Threshold {self.LAG_THRESHOLD_SEC}s"
            logger.error(msg)

            # In dev/backfill scenarios, laggy events can be expected (e.g., when polling with overlap/backfill).
            # Only trip the global circuit breaker if explicitly enabled.
            if system_settings.trip_circuit_breaker_on_lag:
                from orion.core.circuit_breaker import CircuitBreaker

                cb = CircuitBreaker()
                await cb.open(msg)

            raise CriticalHealthError(msg)

    async def check_health(self) -> None:
        """
        Called periodically (e.g. at end of polling loop).
        """
        # Check Heartbeat
        now = time.time()
        time_since_hb = now - self.last_heartbeat_ts

        if time_since_hb > self.HEARTBEAT_THRESHOLD_SEC:
            # Distinguish genuine stalls from machine sleep / long GC pauses.
            # A gap > 10 minutes almost certainly means the host suspended;
            # tripping the circuit breaker for that is counter-productive.
            MACHINE_SLEEP_THRESHOLD = 600.0  # 10 minutes
            if time_since_hb > MACHINE_SLEEP_THRESHOLD:
                logger.warning(
                    f"Heartbeat gap {time_since_hb:.0f}s likely caused by machine sleep; "
                    "resetting heartbeat instead of tripping circuit breaker",
                    extra={"event_type": "HEARTBEAT_SLEEP_SKIP", "gap_seconds": time_since_hb},
                )
                self.last_heartbeat_ts = now
            else:
                msg = f"CRITICAL: Heartbeat missing for {time_since_hb:.2f}s > {self.HEARTBEAT_THRESHOLD_SEC}s"
                logger.error(msg)

                # Trigger Circuit Breaker
                from orion.core.circuit_breaker import CircuitBreaker

                cb = CircuitBreaker()
                await cb.open(msg)

                raise CriticalHealthError(msg)

        self.max_lag_seconds = 0.0  # Reset for next batch

    async def update_db_status(self, is_healthy: bool, details: str = "") -> None:
        """
        Persists the current health status to the DB.
        """
        from sqlalchemy.dialects.postgresql import insert

        from orion.shared.db_utils import db_write
        from orion.storage.models import SystemStatus

        status_str = "HEALTHY" if is_healthy else "UNHEALTHY"

        async def update_health_status(session: Any) -> None:
            stmt = insert(SystemStatus).values(key="global_health", status=status_str, details=details)
            stmt = stmt.on_conflict_do_update(
                index_elements=["key"],
                set_={"status": status_str, "details": details, "last_updated_utc": func.now()},
            )
            await session.execute(stmt)

        try:
            await db_write(update_health_status)
        except Exception as e:
            logger.error(f"Failed to update SystemStatus in DB: {e}")
