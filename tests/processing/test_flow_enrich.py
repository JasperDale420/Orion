from datetime import datetime

import pytest

from orion.processing.flow_enrich import compute_dte, enrich_flow_payload, infer_aggressor

NOW = datetime(2026, 3, 10, 15, 0)


@pytest.mark.unit
class TestComputeDte:
    def test_future_expiry_counts_calendar_days(self) -> None:
        assert compute_dte("2026-03-17", NOW) == 7

    def test_timestamp_suffix_is_ignored(self) -> None:
        assert compute_dte("2026-03-17T00:00:00Z", NOW) == 7

    def test_past_expiry_is_clamped_to_zero(self) -> None:
        assert compute_dte("2026-03-01", NOW) == 0

    def test_unparseable_expiry_returns_none(self) -> None:
        assert compute_dte("not-a-date", NOW) is None


@pytest.mark.unit
class TestInferAggressor:
    def test_explicit_aggressor_is_uppercased(self) -> None:
        assert infer_aggressor({"aggressor": "ask", "total_bid_side_prem": 9}) == "ASK"

    def test_ask_heavy_premium_is_ask(self) -> None:
        assert infer_aggressor({"total_ask_side_prem": "500", "total_bid_side_prem": 100}) == "ASK"

    def test_bid_heavy_premium_is_bid(self) -> None:
        assert infer_aggressor({"total_ask_side_prem": 100, "total_bid_side_prem": 500}) == "BID"

    def test_tie_or_missing_premium_is_mid(self) -> None:
        assert infer_aggressor({"total_ask_side_prem": 100, "total_bid_side_prem": 100}) == "MID"
        assert infer_aggressor({}) == "MID"


@pytest.mark.unit
class TestEnrichFlowPayload:
    def test_derives_all_keys_in_place(self) -> None:
        payload = {
            "underlying": "AAPL",
            "put_call": "call",
            "premium": "12500.5",
            "expiry": "2026-03-20",
            "total_ask_side_prem": 10,
            "total_bid_side_prem": 1,
        }
        ticker = enrich_flow_payload(payload, NOW)
        assert ticker == "AAPL"
        assert payload["ticker"] == "AAPL"
        assert payload["put_call"] == "C"
        assert payload["premium_usd"] == 12500.5
        assert payload["dte"] == 10
        assert payload["aggressor_ind"] == "ASK"

    def test_symbol_fallback_and_missing_ticker(self) -> None:
        p1 = {"symbol": "TSLA"}
        assert enrich_flow_payload(p1, NOW) == "TSLA"
        p2: dict[str, object] = {}
        assert enrich_flow_payload(p2, NOW) == ""
        assert "ticker" not in p2

    def test_bad_expiry_sets_no_dte_and_empty_put_call_stays_empty(self) -> None:
        payload = {"put_call": "", "expiry": "garbage"}
        enrich_flow_payload(payload, NOW)
        assert payload["put_call"] == ""
        assert "dte" not in payload
