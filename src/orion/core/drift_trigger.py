"""
Drift Trigger Module.

Provides a DB-backed flag mechanism for EOD agent to signal high feature drift
to the pattern miner, triggering immediate model retraining.

Uses the RuntimeConfig table instead of filesystem to survive container restarts.
"""

from datetime import UTC, datetime
from typing import Any

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.core.drift_trigger")

# PSI threshold for triggering retraining
DRIFT_THRESHOLD = 0.25

# RuntimeConfig key for the drift flag
DRIFT_CONFIG_KEY = "drift_trigger"


async def set_drift_flag(psi_values: dict[str, float], source: str = "eod_agent") -> bool:
    """
    Write drift trigger flag to RuntimeConfig when high drift is detected.

    Args:
        psi_values: Dict of feature -> PSI value
        source: What triggered the flag (for logging)

    Returns:
        True if flag was set
    """
    high_drift_features = {k: v for k, v in psi_values.items() if v is not None and v > DRIFT_THRESHOLD}

    if not high_drift_features:
        return False

    max_psi = max(high_drift_features.values())

    flag_data = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "source": source,
        "max_psi": max_psi,
        "high_drift_features": high_drift_features,
        "threshold": DRIFT_THRESHOLD,
    }

    try:
        from orion.storage.db import async_session_factory
        from orion.storage.models import RuntimeConfig

        async with async_session_factory() as session:
            existing = await session.get(RuntimeConfig, DRIFT_CONFIG_KEY)
            if existing:
                existing.value_json = flag_data
            else:
                session.add(RuntimeConfig(key=DRIFT_CONFIG_KEY, value_json=flag_data))
            await session.commit()

        logger.info(
            "drift_flag_set",
            max_psi=round(max_psi, 3),
            feature_count=len(high_drift_features),
            features=list(high_drift_features.keys()),
        )
        return True

    except Exception as e:
        logger.error("drift_flag_set_failed", error=str(e), exc_info=True)
        return False


async def check_drift_flag() -> dict[str, Any] | None:
    """
    Check if drift trigger flag exists in RuntimeConfig.

    Returns:
        Flag data dict if flag exists, None otherwise
    """
    try:
        from orion.storage.db import async_session_factory
        from orion.storage.models import RuntimeConfig

        async with async_session_factory() as session:
            row = await session.get(RuntimeConfig, DRIFT_CONFIG_KEY)
            return row.value_json if row else None

    except Exception as e:
        logger.warning("drift_flag_check_failed", error=str(e), exc_info=True)
        return None


async def clear_drift_flag() -> bool:
    """
    Clear the drift trigger flag after mining completes.

    Returns:
        True if flag was cleared (or did not exist)
    """
    try:
        from orion.storage.db import async_session_factory
        from orion.storage.models import RuntimeConfig

        async with async_session_factory() as session:
            row = await session.get(RuntimeConfig, DRIFT_CONFIG_KEY)
            if row:
                await session.delete(row)
                await session.commit()
                logger.info("drift_flag_cleared")
        return True

    except Exception as e:
        logger.error("drift_flag_clear_failed", error=str(e), exc_info=True)
        return False


async def check_and_clear_drift_flag() -> dict[str, Any] | None:
    """
    Check for drift flag and clear it if found.

    Returns:
        Flag data if flag was found (and cleared), None otherwise
    """
    flag_data = await check_drift_flag()
    if flag_data:
        await clear_drift_flag()
    return flag_data
