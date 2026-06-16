"""Dormant Alpaca SDK readiness checks (A4 of the 2026-06-11 redesign plan).

These prove the repo is ready for a dedicated Alpaca paper account the moment a
key frees up, with ZERO behavior change today:
  - alpaca-py is installed and the trading/data clients orion would use import.
  - The new SystemSettings fields default to a dormant, gateway-routed state.
  - ORION_BROKER_MODE=direct fails fast at settings load (no half-mode).
"""

import pytest

pytestmark = pytest.mark.unit


def test_alpaca_sdk_imports() -> None:
    """alpaca-py and the trading/data clients orion would use import (no network)."""
    import alpaca  # noqa: F401
    import alpaca.data  # noqa: F401
    from alpaca.data.historical.stock import StockHistoricalDataClient  # noqa: F401
    from alpaca.trading.client import TradingClient  # noqa: F401

    # Classes are importable symbols — instantiation would require credentials
    # and is intentionally NOT exercised here.
    assert TradingClient is not None
    assert StockHistoricalDataClient is not None


def test_dedicated_alpaca_settings_default_dormant() -> None:
    """Defaults keep orion gateway-routed with no dedicated credentials set."""
    from orion.config import SystemSettings

    settings = SystemSettings()
    assert settings.broker_mode == "gateway"
    assert settings.orion_alpaca_api_key is None
    assert settings.orion_alpaca_secret_key is None
    assert settings.orion_alpaca_paper is True


def test_broker_mode_direct_coerces_to_gateway(monkeypatch, caplog):
    """direct is scaffolded-not-implemented: it must NEVER crash the fleet
    (SystemSettings instantiates at import in every service incl. the dead-man
    watchdog) — it coerces to gateway with a CRITICAL log."""
    monkeypatch.setenv("ORION_BROKER_MODE", "direct")
    from orion.config import SystemSettings

    with caplog.at_level("CRITICAL", logger="orion.config"):
        settings = SystemSettings()
    assert settings.broker_mode == "gateway"
    assert any("scaffolded but not implemented" in r.message for r in caplog.records)


def test_broker_mode_invalid_coerces_to_gateway(monkeypatch, caplog):
    monkeypatch.setenv("ORION_BROKER_MODE", "bogus")
    from orion.config import SystemSettings

    with caplog.at_level("CRITICAL", logger="orion.config"):
        settings = SystemSettings()
    assert settings.broker_mode == "gateway"
    assert any("Unknown ORION_BROKER_MODE" in r.message for r in caplog.records)
