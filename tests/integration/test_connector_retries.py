from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError

from orion.config import SystemSettings
from orion.connectors.alpaca_trading_connector import AlpacaTradingConnector


@pytest.fixture
def mock_settings():
    s = SystemSettings()
    s.alpaca_api_key = "test_key"  # pragma: allowlist secret
    s.alpaca_secret_key = "test_secret"  # pragma: allowlist secret
    s.alpaca_paper = True
    return s


def test_connector_submit_order_retry_success(mock_settings):
    """
    Verify that submit_limit_order retries on ConnectionError and eventually succeeds.
    """
    # 1. Setup Mock Client
    with patch("orion.connectors.alpaca_trading_connector.TradingClient") as MockClient:  # noqa: N806
        client_instance = MockClient.return_value
        # Mock get_account (called in init)
        client_instance.get_account.return_value.buying_power = "100000"
        client_instance.get_account.return_value.currency = "USD"

        connector = AlpacaTradingConnector(mock_settings)

        # 2. Configure Side Effect: Fail twice, then succeed
        client_instance.submit_order.side_effect = [
            ConnectionError("Transient Failure 1"),
            ConnectionError("Transient Failure 2"),
            MagicMock(id="order_123"),
        ]

        # 3. Call Method
        order = connector.submit_limit_order("AAPL", 10, "buy", 150.0)

        # 4. Assertions
        assert order is not None
        assert order.id == "order_123"
        # Verify it was called 3 times
        assert client_instance.submit_order.call_count == 3


def test_connector_submit_order_retry_giveup(mock_settings):
    """
    Verify that submit_limit_order raises after max retries.
    """
    with patch("orion.connectors.alpaca_trading_connector.TradingClient") as MockClient:  # noqa: N806
        client_instance = MockClient.return_value
        client_instance.get_account.return_value.buying_power = "100000"

        connector = AlpacaTradingConnector(mock_settings)

        # Fail always
        client_instance.submit_order.side_effect = ConnectionError("Persistent Failure")

        # Should raise connection error after retries
        with pytest.raises(ConnectionError):
            connector.submit_limit_order("AAPL", 10, "buy", 150.0)

        # Check call count (5 attempts configured)
        # Tenacity default stop_after_attempt(5)
        assert client_instance.submit_order.call_count == 5


if __name__ == "__main__":
    import pytest

    pytest.main([__file__])
