"""
Tests for the API auth module.
"""

import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from orion.api.auth import require_api_key


class TestRequireApiKey:
    """Tests for require_api_key dependency."""

    def test_missing_orion_api_key_env(self) -> None:
        """Should raise 500 if ORION_API_KEY not configured."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove ORION_API_KEY if it exists
            os.environ.pop("ORION_API_KEY", None)
            with pytest.raises(HTTPException) as exc_info:
                require_api_key(x_api_key="any-key")
            assert exc_info.value.status_code == 500
            assert "not configured" in exc_info.value.detail

    @patch.dict(os.environ, {"ORION_API_KEY": "secret-key"})
    def test_missing_x_api_key_header(self) -> None:
        """Should raise 401 if x-api-key header not provided."""
        with pytest.raises(HTTPException) as exc_info:
            require_api_key(x_api_key=None)
        assert exc_info.value.status_code == 401
        assert "Unauthorized" in exc_info.value.detail

    @patch.dict(os.environ, {"ORION_API_KEY": "secret-key"})
    def test_invalid_x_api_key(self) -> None:
        """Should raise 401 if x-api-key doesn't match."""
        with pytest.raises(HTTPException) as exc_info:
            require_api_key(x_api_key="wrong-key")
        assert exc_info.value.status_code == 401
        assert "Unauthorized" in exc_info.value.detail

    @patch.dict(os.environ, {"ORION_API_KEY": "secret-key"})
    def test_valid_x_api_key(self) -> None:
        """Should pass silently if x-api-key matches."""
        result = require_api_key(x_api_key="secret-key")
        assert result is None  # No exception, returns None
