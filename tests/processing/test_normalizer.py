from orion.processing.normalizer import NormalizationEngine


def test_normalize_uw_flow():
    payload = {
        "ticker": "AAPL",
        "timestamp": "2023-10-27T10:00:00Z",
        "put_call": "C",
        "expiry": "2023-10-27",
        "strike_price": "180.0",
        "price": "1.50",
        "size": "100",
        "bid": "1.45",
        "ask": "1.55",
        "underlying_price": "179.50",
        "aggressor": "ASK",
        "sweep": True,
        "trade_type": "BLOCK",
        "open_interest": "5000",
        "volume": "1000",
    }

    normalized = NormalizationEngine.normalize_event("UW", "UW_FLOW", payload)

    assert normalized["ticker"] == "AAPL"
    assert normalized["call_put"] == "C"
    assert normalized["strike"] == 180.0
    assert normalized["premium_usd"] == 15000.0  # 1.50 * 100 * 100
    assert normalized["flags"]["is_sweep"] is True
    assert normalized["flags"]["is_block"] is True


def test_normalize_uw_flow_handles_string_booleans():
    payload = {
        "ticker": "AAPL",
        "timestamp": "2023-10-27T10:00:00Z",
        "put_call": "put",
        "expiry": "2023-10-27",
        "strike_price": "180.0",
        "price": "1.50",
        "size": "100",
        "has_sweep": "yes",
        "has_floor": "false",
        "has_multileg": "1",
    }

    normalized = NormalizationEngine.normalize_event("UW", "UW_FLOW", payload)

    assert normalized["put_call"] == "P"
    assert normalized["call_put"] == "P"
    assert normalized["flags"]["is_sweep"] is True
    assert normalized["flags"]["is_block"] is False
    assert normalized["flags"]["is_multi_leg"] is True


def test_normalize_uw_alert_shortens_put_call_to_single_letter():
    payload = {
        "ticker": "AAPL",
        "timestamp": "2023-10-27T10:00:00Z",
        "put_call": "CALL",
        "expiry": "2023-10-27",
        "strike_price": "180.0",
        "price": "1.50",
        "size": "100",
    }

    normalized = NormalizationEngine.normalize_event("UW", "UW_ALERT", payload)

    assert normalized["put_call"] == "C"


def test_normalize_alpaca_bar():
    payload = {
        "S": "SPY",  # Alpaca sometimes uses S or symbol
        "symbol": "SPY",
        "t": "2023-10-27T10:00:00Z",
        "o": 410.5,
        "h": 411.0,
        "l": 410.0,
        "c": 410.8,
        "v": 1000,
    }

    normalized = NormalizationEngine.normalize_event("ALPACA", "ALPACA_BAR_1M", payload)

    assert normalized["ticker"] == "SPY"
    assert normalized["close"] == 410.8
    assert normalized["volume"] == 1000
