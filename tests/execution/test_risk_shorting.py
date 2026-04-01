from orion.config import RiskSettings
from orion.execution.risk.manager import RiskManager


def test_risk_manager_shorting_disabled():
    cfg = RiskSettings()
    cfg.enable_shorting = False  # Explicitly disable
    cfg.max_order_size_pct = 0.10  # 10000 / 100000 = 10%

    rm = RiskManager(config=cfg)

    # Case 1: Sell to Open Short
    # Ticker Exposure = 0
    # Sell 1000 USD
    allowed = rm.check_order("AAPL", 10, 100.0, "SELL")

    assert allowed is False

    # Case 2: Sell to Close Long (Allowed)
    rm.ticker_exposures["AAPL"] = 2000.0  # Long 2000
    rm.positions["AAPL"] = {"qty": 20.0, "avg_entry": 100.0}
    allowed_close = rm.check_order("AAPL", 10, 100.0, "SELL")

    assert allowed_close is True


def test_risk_manager_shorting_flip():
    cfg = RiskSettings()
    cfg.enable_shorting = False

    rm = RiskManager(config=cfg)
    rm.ticker_exposures["AMD"] = 500.0  # Long 500
    rm.positions["AMD"] = {"qty": 50.0, "avg_entry": 10.0}

    # Try to sell 1000 USD (Net -500)
    # This flips from Long -> Short. Should be blocked if shorting disabled.
    allowed_flip = rm.check_order("AMD", 100, 10.0, "SELL")  # 100 * 10 = 1000

    assert allowed_flip is False


def test_risk_manager_shorting_enabled():
    cfg = RiskSettings()
    cfg.enable_shorting = True
    cfg.max_order_size_pct = 0.50  # 50000 / 100000 = 50%

    rm = RiskManager(config=cfg)

    # Sell to Open (Net -1000)
    allowed = rm.check_order("NVDA", 10, 100.0, "SELL")
    assert allowed is True
