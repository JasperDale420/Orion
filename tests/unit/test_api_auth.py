"""
Tests for the API auth module.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from orion.api.auth import require_api_key


class TestRequireApiKey:
    """Tests for require_api_key dependency."""

    def test_missing_orion_api_key_env(self) -> None:
        """Should raise 500 if api_key not configured in system_settings."""
        with patch("orion.api.auth.system_settings") as mock_settings:
            mock_settings.api_key = ""
            with pytest.raises(HTTPException) as exc_info:
                require_api_key(x_api_key="any-key")
            assert exc_info.value.status_code == 500
            assert "not configured" in exc_info.value.detail

    def test_missing_x_api_key_header(self) -> None:
        """Should raise 401 if x-api-key header not provided."""
        with patch("orion.api.auth.system_settings") as mock_settings:
            mock_settings.api_key = "secret-key"
            with pytest.raises(HTTPException) as exc_info:
                require_api_key(x_api_key=None)
            assert exc_info.value.status_code == 401
            assert "Unauthorized" in exc_info.value.detail

    def test_invalid_x_api_key(self) -> None:
        """Should raise 401 if x-api-key doesn't match."""
        with patch("orion.api.auth.system_settings") as mock_settings:
            mock_settings.api_key = "secret-key"
            with pytest.raises(HTTPException) as exc_info:
                require_api_key(x_api_key="wrong-key")
            assert exc_info.value.status_code == 401
            assert "Unauthorized" in exc_info.value.detail

    def test_valid_x_api_key(self) -> None:
        """Should pass silently if x-api-key matches."""
        with patch("orion.api.auth.system_settings") as mock_settings:
            mock_settings.api_key = "secret-key"
            result = require_api_key(x_api_key="secret-key")
            assert result is None  # No exception, returns None
