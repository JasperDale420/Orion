from orion.config import RiskSettings as RiskConfig


def test_risk_manager_defaults(risk_manager_factory):
    rm = risk_manager_factory()
    from orion.config import risk_settings

    assert rm.config.max_daily_loss == risk_settings.max_daily_loss
    assert rm.check_order("AAPL", 10, 150.0, "buy") is True


def test_risk_manager_blocks_excessive_order_size(risk_manager_factory):
    config = RiskConfig(max_order_size_usd=5000.0)
    rm = risk_manager_factory(config)

    # 10 * 600 = 6000 > 5000
    assert rm.check_order("TSLA", 10, 600.0, "buy") is False


def test_risk_manager_blocks_daily_loss_limit(risk_manager_factory):
    config = RiskConfig(max_daily_loss=500.0)
    rm = risk_manager_factory(config)

    # Simulate a loss
    # Simulate a loss
    rm.current_daily_loss = 600.0

    assert rm.check_order("AMD", 10, 100.0, "buy") is False


def test_check_order_max_positions_allow_existing(risk_manager_factory):
    config = RiskConfig(max_positions=2)
    rm = risk_manager_factory(config)

    # Setup: 2 positions exist, one is AAPL
    rm.open_positions = 2
    rm.open_positions = 2
    rm.ticker_exposures = {"AAPL": 5000.0, "MSFT": 5000.0}
    rm.positions["AAPL"] = {"qty": 50, "avg_entry": 100.0}
    rm.positions["MSFT"] = {"qty": 50, "avg_entry": 100.0}

    # Adding to AAPL should be allowed (not a new position)
    assert rm.check_order("AAPL", 1, 100.0, "BUY") is True

    # Buying new ticker should be blocked
    assert rm.check_order("NVDA", 1, 100.0, "BUY") is False


def test_check_order_ticker_exposure_limit(risk_manager_factory):
    config = RiskConfig(max_ticker_exposure_usd=10000.0)
    rm = risk_manager_factory(config)

    # Pre-seed exposure
    rm.ticker_exposures = {"AAPL": 8000.0}
    rm.positions["AAPL"] = {"qty": 80, "avg_entry": 100.0}

    # Case 1: Add 1000 -> 9000 (OK)
    assert rm.check_order("AAPL", 10, 100.0, "BUY") is True

    # Case 2: Add 3000 -> 11000 (Fail)
    assert rm.check_order("AAPL", 30, 100.0, "BUY") is False


def test_check_order_max_positions_logic(risk_manager_factory):
    config = RiskConfig(max_positions=2)
    rm = risk_manager_factory(config)

    # 2 positions exist
    rm.ticker_exposures = {"AAPL": 1000, "MSFT": 1000}
    rm.positions["AAPL"] = {"qty": 10, "avg_entry": 100.0}
    rm.positions["MSFT"] = {"qty": 10, "avg_entry": 100.0}
    rm.open_positions = 2

    # Try buying new ticker
    assert rm.check_order("GOOG", 1, 100, "BUY") is False

    # Try adding to existing
    assert rm.check_order("AAPL", 1, 100, "BUY") is True


def test_risk_manager_blocks_max_positions(risk_manager_factory):
    config = RiskConfig(max_positions=2)
    rm = risk_manager_factory(config)

    rm.open_positions = 2
    rm.positions["A"] = {"qty": 1}
    rm.positions["B"] = {"qty": 1}
    assert rm.check_order("NVDA", 1, 100.0, "buy") is False


def test_check_order_allow_risk_reduction_at_limit(risk_manager_factory):
    """
    If we are already over the exposure limit (e.g. gap up),
    we should be allowed to SELL to reduce risk.
    """
    config = RiskConfig(max_ticker_exposure_usd=10000.0)
    rm = risk_manager_factory(config)

    # Pre-seed exposure ABOVE limit
    rm.ticker_exposures = {"AAPL": 12000.0}
    rm.positions["AAPL"] = {"qty": 120, "avg_entry": 100.0}

    # Case 1: Buy more? -> FAIL
    assert rm.check_order("AAPL", 10, 100.0, "BUY") is False

    # Case 2: Sell (reduce exposure)? -> PASS
    # Selling 10 * 100 = 1000 -> New Exp 11000 (still over, but reducing)
    # The fix should detect |11000| < |12000| and allow it.
    assert rm.check_order("AAPL", 10, 100.0, "SELL") is True


def test_check_order_flip_long_to_short(risk_manager_factory):
    """
    Test flipping from Net Long to Net Short.
    Start Long 5000. Sell 8000 -> Net Short -3000.
    """
    config = RiskConfig(max_ticker_exposure_usd=10000.0, enable_shorting=True, max_order_size_usd=50000.0)
    rm = risk_manager_factory(config)

    # Start Long
    rm.ticker_exposures = {"TSLA": 5000.0}
    rm.positions["TSLA"] = {"qty": 50, "avg_entry": 100.0}

    # Sell 8000 (Flip to -3000)
    # | -3000 | < 10000 -> PASS
    assert rm.check_order("TSLA", 80, 100.0, "SELL") is True

    # Now assume we flip to -15000 (Over limit)
    # Sell 20000 (Flip to -15000) -> FAIL
    assert rm.check_order("TSLA", 200, 100.0, "SELL") is False


def test_check_order_flip_short_to_long(risk_manager_factory):
    """
    Test flipping from Net Short to Net Long.
    Start Short -5000. Buy 8000 -> Net Long +3000.
    """
    config = RiskConfig(max_ticker_exposure_usd=10000.0, enable_shorting=True, max_order_size_usd=50000.0)
    rm = risk_manager_factory(config)

    # Start Short
    rm.ticker_exposures = {"TSLA": -5000.0}
    rm.positions["TSLA"] = {"qty": -50, "avg_entry": 100.0}

    # Buy 8000 (Flip to +3000) -> PASS
    assert rm.check_order("TSLA", 80, 100.0, "BUY") is True

    # Buy huge -> FAIL
    assert rm.check_order("TSLA", 200, 100.0, "BUY") is False
