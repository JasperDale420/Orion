from orion.config import RiskSettings


def get_strict_config():
    return RiskSettings(
        max_daily_loss=1000.0,
        max_positions=5,
        max_ticker_exposure_usd=5000.0,
        risk_per_trade_pct=0.01,
        enable_shorting=False,  # Strict
    )


def test_risk_blocks_short_when_disabled(risk_manager_factory):
    rm = risk_manager_factory(config=get_strict_config())

    # 1. Try to Sell SPY (Current Exposure = 0) -> Should Block
    allowed = rm.check_order("SPY", 10, 400.0, "sell")
    assert allowed is False, "Should block short opening"


def test_risk_allows_closing_long(risk_manager_factory):
    rm = risk_manager_factory(config=get_strict_config())

    # Simulate holding SPY
    rm.ticker_exposures["SPY"] = 4000.0
    rm.positions["SPY"] = {"qty": 10.0, "avg_entry": 400.0}

    # 2. Try to Sell SPY (Current Exposure > 0) -> Should Allow
    allowed = rm.check_order("SPY", 10, 400.0, "sell")
    assert allowed is True, "Should allow closing long"


def test_risk_allows_short_when_enabled(risk_manager_factory):
    cfg = get_strict_config()
    cfg.enable_shorting = True
    rm = risk_manager_factory(config=cfg)

    # 3. Try to Sell SPY (Exposure 0) -> Should Allow
    allowed = rm.check_order("SPY", 10, 400.0, "sell")
    assert allowed is True, "Should allow shorting if enabled"
