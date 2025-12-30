"""
Tests for the FeatureFlags module.
"""

import os
from unittest.mock import patch

from orion.core import feature_flags


class TestFeatureFlags:
    """Tests for FeatureFlags class."""

    def setup_method(self) -> None:
        """Reset singleton for each test."""
        feature_flags.FeatureFlags._instance = None
        feature_flags.FeatureFlags._cache = {}

    def test_defaults_exist(self) -> None:
        """FeatureFlags should have default values defined."""
        assert "ENABLE_PAPER_TRADING" in feature_flags.FeatureFlags.DEFAULTS
        assert "ENABLE_LIVE_TRADING" in feature_flags.FeatureFlags.DEFAULTS

    def test_singleton_pattern(self) -> None:
        """FeatureFlags should follow singleton pattern."""
        flags1 = feature_flags.FeatureFlags()
        flags2 = feature_flags.FeatureFlags()
        assert flags1 is flags2

    def test_is_enabled_returns_default(self) -> None:
        """is_enabled should return default value when not set."""
        flags = feature_flags.FeatureFlags()
        # ENABLE_PAPER_TRADING defaults to True
        assert flags.is_enabled("ENABLE_PAPER_TRADING") is True
        # ENABLE_LIVE_TRADING defaults to False
        assert flags.is_enabled("ENABLE_LIVE_TRADING") is False

    def test_is_enabled_with_custom_default(self) -> None:
        """is_enabled should use provided default if flag not defined."""
        flags = feature_flags.FeatureFlags()
        assert flags.is_enabled("UNDEFINED_FLAG", default=True) is True
        assert flags.is_enabled("UNDEFINED_FLAG", default=False) is False

    def test_set_flag(self) -> None:
        """set should update flag value."""
        flags = feature_flags.FeatureFlags()
        assert flags.is_enabled("ENABLE_LIVE_TRADING") is False
        flags.set("ENABLE_LIVE_TRADING", True)
        assert flags.is_enabled("ENABLE_LIVE_TRADING") is True

    def test_get_all_includes_defaults_and_overrides(self) -> None:
        """get_all should return merged defaults and cache."""
        flags = feature_flags.FeatureFlags()
        flags.set("CUSTOM_FLAG", True)
        all_flags = flags.get_all()
        assert "ENABLE_PAPER_TRADING" in all_flags
        assert "CUSTOM_FLAG" in all_flags
        assert all_flags["CUSTOM_FLAG"] is True

    @patch.dict(os.environ, {"ORION_FF_ENABLE_LIVE_TRADING": "true"})
    def test_load_from_env(self) -> None:
        """Flags should be loadable from environment variables."""
        flags = feature_flags.FeatureFlags()
        # Should be True from env, not False from default
        assert flags.is_enabled("ENABLE_LIVE_TRADING") is True

    @patch.dict(os.environ, {"ORION_FF_ENABLE_PAPER_TRADING": "0"})
    def test_load_from_env_false(self) -> None:
        """Env value '0' should set flag to False."""
        flags = feature_flags.FeatureFlags()
        assert flags.is_enabled("ENABLE_PAPER_TRADING") is False


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    def setup_method(self) -> None:
        """Reset singleton for each test."""
        feature_flags.FeatureFlags._instance = None
        feature_flags.FeatureFlags._cache = {}

    def test_is_enabled_function(self) -> None:
        """Module-level is_enabled should work."""
        # Reinitialize global instance
        feature_flags._flags = feature_flags.FeatureFlags()
        assert feature_flags.is_enabled("ENABLE_PAPER_TRADING") is True

    def test_set_flag_function(self) -> None:
        """Module-level set_flag should work."""
        feature_flags._flags = feature_flags.FeatureFlags()
        feature_flags.set_flag("TEST_FLAG", True)
        assert feature_flags.is_enabled("TEST_FLAG", default=False) is True

    def test_get_all_flags_function(self) -> None:
        """Module-level get_all_flags should work."""
        feature_flags._flags = feature_flags.FeatureFlags()
        all_flags = feature_flags.get_all_flags()
        assert isinstance(all_flags, dict)
        assert len(all_flags) > 0
