from orion.config import RiskSettings
from orion.execution.risk.manager import RiskManager


def test_calculate_size_honors_legacy_usd_caps() -> None:
    cfg = RiskSettings(
        risk_per_trade_pct=0.05,
        max_order_size_pct=1.0,
        max_ticker_exposure_pct=1.0,
        max_order_size_usd=1000.0,
        max_ticker_exposure_usd=800.0,
    )
    rm = RiskManager(config=cfg)

    assert rm.calculate_size(entry_price=100.0, stop_loss_pct=0.02) == 8.0


def test_check_order_allows_risk_reducing_sell_even_above_order_size_cap() -> None:
    cfg = RiskSettings(max_order_size_pct=0.05, max_ticker_exposure_pct=0.10, enable_shorting=True)
    rm = RiskManager(config=cfg)
    rm.current_equity = 100000.0

    rm.positions["AAPL"] = {"qty": 120.0, "avg_entry": 100.0}
    rm.ticker_exposures["AAPL"] = 12000.0

    assert rm.check_order("AAPL", 80.0, 100.0, "SELL") is True
    assert rm.check_order("MSFT", 80.0, 100.0, "SELL") is False
