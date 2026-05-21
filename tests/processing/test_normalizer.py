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


def test_normalize_uw_flow_accepts_flow_ts_utc():
    """Bronze events produced by `_heber_row_to_event` carry the Heber Silver
    schema, which uses `flow_ts_utc` for the event timestamp — NOT `timestamp`
    or `created_at`. Without this support, the normalizer's strict
    parse_timestamptz call raises on None, the event is silently dropped,
    and zero UW_FLOW silver signals get persisted even though bronze is
    populating. This regression was caught in prod when Orion went 0-of-N
    EXECUTEs for hours despite UW data flowing.
    """
    payload = {
        "ticker": "EWY",
        "flow_ts_utc": "2026-05-21T17:26:36+00:00",  # Heber-derived ts
        "put_call": "P",
        "expiry": "2026-05-22",
        "strike": 175.0,
        "price": 0.0,
        "size_contracts": 210,
        "bid": 0.0,
        "ask": 0.0,
        "underlying_price": 0.0,
        "aggressor": "BID",
        "is_sweep": "true",
        "premium_usd": 15750.0,
        "volume_contract": 890.0,
        "open_interest": 951.0,
        "volume_oi_ratio": 0.94,
    }

    normalized = NormalizationEngine.normalize_event("UW", "UW_FLOW", payload)

    # Critical: must NOT have raised + must preserve the timestamp
    assert normalized["ticker"] == "EWY"
    assert normalized["flow_ts_utc"].startswith("2026-05-21T17:26:36")
    assert normalized["put_call"] == "P"
    assert normalized["flags"]["is_sweep"] is True


def test_normalize_uw_darkpool_accepts_dark_ts_utc():
    """Mirror of test_normalize_uw_flow_accepts_flow_ts_utc — same
    silent-now() bug existed for darkpool; codex review 2026-05-21
    flagged it as a sibling."""
    payload = {
        "ticker": "AAPL",
        "dark_ts_utc": "2026-05-21T17:26:36+00:00",
        "size": 1000,
        "price": 180.0,
    }
    normalized = NormalizationEngine.normalize_event("UW", "UW_DARKPOOL", payload)
    assert normalized["ticker"] == "AAPL"
    # darkpool normalizer outputs `dark_ts_utc` as the canonical key
    assert normalized["dark_ts_utc"].startswith("2026-05-21T17:26:36")


def test_normalize_uw_alert_accepts_alert_ts_utc():
    """Same shape for alerts."""
    payload = {
        "ticker": "AAPL",
        "alert_ts_utc": "2026-05-21T17:26:36+00:00",
        "put_call": "C",
        "alert_tags": ["unusual_volume"],
    }
    normalized = NormalizationEngine.normalize_event("UW", "UW_ALERT", payload)
    assert normalized["ticker"] == "AAPL"
    # alert normalizer uses alert_ts_utc as the timestamp key in output
    assert "alert_ts_utc" in normalized or "ts_utc" in normalized
