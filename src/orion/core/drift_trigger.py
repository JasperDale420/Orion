"""
Drift Trigger Module.

Provides a simple flag mechanism for EOD agent to signal high feature drift
to the pattern miner, triggering immediate model retraining.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.core.drift_trigger")

# PSI threshold for triggering retraining
DRIFT_THRESHOLD = 0.25

# Flag file location (in artifacts directory)
DRIFT_FLAG_PATH = os.path.join(os.environ.get("ORION_ARTIFACTS_DIR", "artifacts"), ".drift_trigger")


def set_drift_flag(psi_values: Dict[str, float], source: str = "eod_agent") -> bool:
    """
    Write drift trigger flag when high drift is detected.

    Args:
        psi_values: Dict of feature -> PSI value
        source: What triggered the flag (for logging)

    Returns:
        True if flag was set
    """
    # Find features above threshold
    high_drift_features = {k: v for k, v in psi_values.items() if v is not None and v > DRIFT_THRESHOLD}

    if not high_drift_features:
        return False

    max_psi = max(high_drift_features.values())

    flag_data = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "max_psi": max_psi,
        "high_drift_features": high_drift_features,
        "threshold": DRIFT_THRESHOLD,
    }

    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(DRIFT_FLAG_PATH), exist_ok=True)

        with open(DRIFT_FLAG_PATH, "w") as f:
            json.dump(flag_data, f, indent=2)

        logger.info(
            f"Drift flag set: max_psi={max_psi:.3f}, features={list(high_drift_features.keys())}",
            extra={
                "event": "drift_flag_set",
                "max_psi": max_psi,
                "feature_count": len(high_drift_features),
            },
        )
        return True

    except Exception as e:
        logger.error(f"Failed to write drift flag: {e}")
        return False


def check_drift_flag() -> Optional[Dict[str, Any]]:
    """
    Check if drift trigger flag exists.

    Returns:
        Flag data dict if flag exists, None otherwise
    """
    if not os.path.exists(DRIFT_FLAG_PATH):
        return None

    try:
        with open(DRIFT_FLAG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read drift flag: {e}")
        return None


def clear_drift_flag() -> bool:
    """
    Clear the drift trigger flag after mining completes.

    Returns:
        True if flag was cleared
    """
    if not os.path.exists(DRIFT_FLAG_PATH):
        return True

    try:
        os.remove(DRIFT_FLAG_PATH)
        logger.info("Drift flag cleared", extra={"event": "drift_flag_cleared"})
        return True
    except Exception as e:
        logger.error(f"Failed to clear drift flag: {e}")
        return False


def check_and_clear_drift_flag() -> Optional[Dict[str, Any]]:
    """
    Check for drift flag and clear it if found.

    Returns:
        Flag data if flag was found (and cleared), None otherwise
    """
    flag_data = check_drift_flag()
    if flag_data:
        clear_drift_flag()
    return flag_data
