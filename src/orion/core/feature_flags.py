"""
Feature flags module for Orion.

Provides a centralized way to enable/disable features at runtime.
Flags can be configured via environment variables or database.
"""

from orion.shared.logger import setup_struct_logger
import os
from typing import Dict, Optional

logger = setup_struct_logger("orion.core.feature_flags")


class FeatureFlags:
    """
    Centralized feature flag management.

    Priority order:
    1. Environment variables (ORION_FF_<FLAG_NAME>)
    2. Database (feature_flags table)
    3. Defaults defined in code
    """

    # Default flag values
    DEFAULTS: Dict[str, bool] = {
        "ENABLE_PAPER_TRADING": True,
        "ENABLE_LIVE_TRADING": False,
        "ENABLE_DARKPOOL_CONNECTOR": True,
        "ENABLE_FLOW_CONNECTOR": True,
        "ENABLE_ALERTS_CONNECTOR": True,
        "ENABLE_RAG_SEARCH": True,
        "ENABLE_AGENT_ANALYSIS": True,
        "ENABLE_SIGNAL_ROUTING": True,
        "ENABLE_RISK_CHECKS": True,
        "ENABLE_AUDIT_LOGGING": True,
    }

    _instance: Optional["FeatureFlags"] = None
    _cache: Dict[str, bool] = {}

    def __new__(cls) -> "FeatureFlags":
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_from_env()
        return cls._instance

    def _load_from_env(self) -> None:
        """Load flags from environment variables."""
        for flag in self.DEFAULTS:
            env_key = f"ORION_FF_{flag}"
            env_val = os.getenv(env_key)
            if env_val is not None:
                self._cache[flag] = env_val.lower() in ("true", "1", "yes")
                logger.info(f"Feature flag {flag} = {self._cache[flag]} (from env)")

    def is_enabled(self, flag: str, default: Optional[bool] = None) -> bool:
        """
        Check if a feature flag is enabled.

        Args:
            flag: Flag name (e.g., "ENABLE_PAPER_TRADING")
            default: Override default if flag not defined

        Returns:
            True if enabled, False otherwise
        """
        # Check cache first
        if flag in self._cache:
            return self._cache[flag]

        # Check defaults
        if default is not None:
            return default
        return self.DEFAULTS.get(flag, False)

    def set(self, flag: str, value: bool) -> None:
        """
        Set a feature flag value at runtime.

        Args:
            flag: Flag name
            value: True to enable, False to disable
        """
        self._cache[flag] = value
        logger.info(f"Feature flag {flag} set to {value}")

    def get_all(self) -> Dict[str, bool]:
        """Get all flag values including defaults."""
        result = dict(self.DEFAULTS)
        result.update(self._cache)
        return result


# Global instance for convenience
_flags = FeatureFlags()


def is_enabled(flag: str, default: Optional[bool] = None) -> bool:
    """Check if a feature flag is enabled."""
    return _flags.is_enabled(flag, default)


def set_flag(flag: str, value: bool) -> None:
    """Set a feature flag value."""
    _flags.set(flag, value)


def get_all_flags() -> Dict[str, bool]:
    """Get all feature flags."""
    return _flags.get_all()
