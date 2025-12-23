import logging
import time
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import func

logger = logging.getLogger(__name__)


class CriticalHealthException(Exception):
    pass


class HealthMonitor:
    """
    PRD 9.1: Data health circuit breakers.
    Tracks ingestion lag and heartbeats. Raises exception if thresholds breached.
    """

    def __init__(self):
        self.last_heartbeat_ts: float = time.time()
        self.max_lag_seconds: float = 0.0

        # Thresholds (PRD 9.1)
        self.HEARTBEAT_THRESHOLD_SEC = 60.0
        self.LAG_THRESHOLD_SEC = 60.0

        # Metrics
        self.metrics: Dict[str, Any] = {}

    def update_heartbeat(self):
        self.last_heartbeat_ts = time.time()

    async def check_lag(self, event_ts_utc: datetime):
        """
        Check lag for a specific event timestamp.
        """
        if not event_ts_utc:
            return

        # Simple lag check: Now - EventTS
        # Ensure UTC comparison
        now = datetime.utcnow()
        if event_ts_utc.tzinfo:
            # naive comparison if one is aware and other is not is tricky
            # Let's assume event_ts_utc is naive UTC or convert
            event_ts_utc = event_ts_utc.replace(tzinfo=None)  # Force naive UTC for comparison with utcnow()

        lag = (now - event_ts_utc).total_seconds()
        self.max_lag_seconds = max(self.max_lag_seconds, lag)

        if lag > self.LAG_THRESHOLD_SEC:
            msg = f"CRITICAL: Event Lag {lag:.2f}s > Threshold {self.LAG_THRESHOLD_SEC}s"
            logger.error(msg)

            # Trigger Circuit Breaker
            from orion.core.circuit_breaker import CircuitBreaker

            cb = CircuitBreaker()
            await cb.open(msg)

            raise CriticalHealthException(msg)

    async def check_health(self):
        """
        Called periodically (e.g. at end of polling loop).
        """
        # Check Heartbeat
        now = time.time()
        time_since_hb = now - self.last_heartbeat_ts

        if time_since_hb > self.HEARTBEAT_THRESHOLD_SEC:
            msg = f"CRITICAL: Heartbeat missing for {time_since_hb:.2f}s > {self.HEARTBEAT_THRESHOLD_SEC}s"
            logger.error(msg)

            # Trigger Circuit Breaker
            from orion.core.circuit_breaker import CircuitBreaker

            cb = CircuitBreaker()
            await cb.open(msg)

            raise CriticalHealthException(msg)

        self.max_lag_seconds = 0.0  # Reset for next batch

    async def update_db_status(self, is_healthy: bool, details: str = ""):
        """
        Persists the current health status to the DB.
        """
        from sqlalchemy.dialects.postgresql import insert

        from orion.storage.db import async_session_factory
        from orion.storage.models import SystemStatus

        status_str = "HEALTHY" if is_healthy else "UNHEALTHY"

        try:
            async with async_session_factory() as session:
                stmt = insert(SystemStatus).values(key="global_health", status=status_str, details=details)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["key"],
                    set_={"status": status_str, "details": details, "last_updated_utc": func.now()},
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to update SystemStatus in DB: {e}")
