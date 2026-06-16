"""Tests for the default-dev-credential startup warning (config)."""

from unittest.mock import MagicMock, patch

import pytest

from orion.config import warn_on_default_dev_credentials


def _system(gateway_key):
    s = MagicMock()
    s.data_gateway_api_key = gateway_key
    return s


def _agent(ai_key):
    a = MagicMock()
    a.ai_gateway_key = ai_key
    return a


@pytest.mark.unit
def test_warns_on_default_gateway_key():
    with patch("orion.shared.logger.setup_struct_logger", return_value=MagicMock()):
        flagged = warn_on_default_dev_credentials(_system("gw_orion_trading_key_55555"), _agent("real-key"))
    assert flagged == ["data_gateway_api_key"]


@pytest.mark.unit
def test_warns_on_default_ai_gateway_key():
    with patch("orion.shared.logger.setup_struct_logger", return_value=MagicMock()):
        flagged = warn_on_default_dev_credentials(_system("real-key"), _agent("empire-ai-gateway-key"))
    assert flagged == ["ai_gateway_key"]


@pytest.mark.unit
def test_warns_on_both():
    with patch("orion.shared.logger.setup_struct_logger", return_value=MagicMock()):
        flagged = warn_on_default_dev_credentials(
            _system("gw_orion_trading_key_55555"), _agent("empire-ai-gateway-key")
        )
    assert flagged == ["data_gateway_api_key", "ai_gateway_key"]


@pytest.mark.unit
def test_silent_when_credentials_overridden():
    mock_log = MagicMock()
    with patch("orion.shared.logger.setup_struct_logger", return_value=mock_log):
        flagged = warn_on_default_dev_credentials(_system("prod-key"), _agent("prod-ai-key"))
    assert flagged == []
    mock_log.warning.assert_not_called()
