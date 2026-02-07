from orion.connectors.alpaca_stream_connector import AlpacaStreamConnector


def test_connector_uses_runtime_gateway_setting(monkeypatch) -> None:
    monkeypatch.setattr(
        "orion.connectors.alpaca_stream_connector.system_settings.orion_use_gateway",
        False,
        raising=False,
    )
    connector = AlpacaStreamConnector()
    assert connector._use_gateway is False


def test_explicit_gateway_flag_overrides_runtime_setting(monkeypatch) -> None:
    monkeypatch.setattr(
        "orion.connectors.alpaca_stream_connector.system_settings.orion_use_gateway",
        False,
        raising=False,
    )
    connector = AlpacaStreamConnector(use_gateway=True)
    assert connector._use_gateway is True
