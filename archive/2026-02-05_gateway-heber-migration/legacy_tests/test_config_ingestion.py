from datetime import date
from unittest.mock import MagicMock, patch

from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.core.errors import ProviderError


def test_fetch_limit_config_usage():
    """
    Verify that UWFlowConnector uses the limit defined in system_settings.
    """
    # Patch system settings
    with patch("orion.config.system_settings") as mock_settings:
        mock_settings.uw_fetch_limit = 999

        # Instantiate Connector (mock api key to pass init check)
        connector = UWFlowConnector(api_key="test_key")

        # Mock Session
        connector.session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = [{"data": []}]
        mock_response.status_code = 200
        connector.session.get.return_value = mock_response
        mock_response.raise_for_status.return_value = None

        # Call fetch_flow_for_date
        try:
            connector.fetch_flow_for_date(date(2025, 1, 1))
        except ProviderError:
            pass
        except Exception:
            pass

        # Verify call args
        call_args = connector.session.get.call_args
        assert call_args is not None
        _, kwargs = call_args
        params = kwargs.get("params", {})

        assert params["limit"] == 999
