from datetime import UTC, datetime, timedelta

import pandas as pd

from orion.processing.normalizer import NormalizationEngine
from orion.shared.utils import make_json_safe


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


def test_normalize_uw_flow_reads_heber_silver_ts_event_as_print_time():
    """Heber Silver ``feed=flow_alerts`` rows carry the print time in
    ``ts_event`` (the only event-time column in the parquet schema — there is
    no ``flow_ts_utc`` / ``timestamp`` / ``created_at``). ``_heber_row_to_event``
    passes the row through ``make_json_safe`` so ``ts_event`` reaches the
    normalizer as an ISO string. The normalized ``flow_ts_utc`` MUST be that
    print time; falling through to ``parse_timestamptz(None)`` stamps the
    normalization wall-clock instead, so the rule-time signal-age gate sees
    ~0s for a print that is really minutes old (the 2026-08 born-stale
    "Preflight reject: Data Lag" pattern on the heber_flow connector).
    """
    print_ts = datetime.now(UTC) - timedelta(seconds=300)
    row = {
        "event_id": "ce8ad61267446842b5ddfe3f76848f0e",
        "provider": "unusual_whales",
        "feed": "flow_alerts",
        "instrument_type": "option",
        "instrument_key": "option:OCC:SPY260814C00640000",
        "symbol": "SPY",
        "ts_event": pd.Timestamp(print_ts),
        "ts_ingest": pd.Timestamp(print_ts + timedelta(seconds=2)),
        "ts_available": pd.Timestamp(print_ts + timedelta(seconds=26)),
        "underlying": "SPY",
        "occ_symbol": "SPY260814C00640000",
        "expiry": "2026-08-14",
        "strike": 640.0,
        "put_call": "C",
        "premium": 131572.0,
        "volume": 134441.0,
        "open_interest": 685.0,
        "aggressor": "ask",
        "is_sweep": True,
        "total_ask_side_prem": 131572.0,
        "total_bid_side_prem": 0.0,
    }
    payload = {k: make_json_safe(v) for k, v in row.items()}
    assert isinstance(payload["ts_event"], str)

    normalized = NormalizationEngine.normalize_event("UW", "UW_FLOW", payload)

    flow_ts = datetime.fromisoformat(normalized["flow_ts_utc"])
    assert abs((flow_ts - print_ts).total_seconds()) < 1.0
    age = (datetime.now(UTC) - flow_ts).total_seconds()
    assert 299.0 <= age <= 305.0


def test_normalize_uw_flow_prefers_gateway_timestamp_over_ts_event():
    """The Gateway push path stamps ``event_ts_utc`` from ``payload.timestamp``
    first; the normalizer must agree so the two delivery paths stay
    interchangeable."""
    payload = {
        "ticker": "SPY",
        "timestamp": "2026-08-14T14:30:00+00:00",
        "ts_event": "2026-08-14T14:25:00+00:00",
        "put_call": "C",
        "expiry": "2026-08-14",
        "strike": 640.0,
        "price": 1.0,
        "size": 10,
    }
    normalized = NormalizationEngine.normalize_event("UW", "UW_FLOW", payload)
    assert normalized["flow_ts_utc"].startswith("2026-08-14T14:30:00")


def test_normalize_uw_flow_accepts_flow_ts_utc():
    """Regression guard for the legacy ``flow_ts_utc`` payload key (Orion's own
    Silver column name; also what a re-normalized payload carries). Without a
    matching branch the strict parse_timestamptz call falls through to now()
    and the event's timestamp is silently replaced by the ingest time.
    """
    payload = {
        "ticker": "EWY",
        "flow_ts_utc": "2026-05-21T17:26:36+00:00",
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
