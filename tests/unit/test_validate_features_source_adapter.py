from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from orion.jobs import validate_features


def test_prefer_heber_source_from_env_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORION_VALIDATE_FEATURES_PREFER_HEBER", raising=False)
    assert validate_features._prefer_heber_source_from_env() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", "n"])
def test_prefer_heber_source_from_env_false_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("ORION_VALIDATE_FEATURES_PREFER_HEBER", value)
    assert validate_features._prefer_heber_source_from_env() is False


def test_normalize_source_id_no_longer_maps_legacy_aliases() -> None:
    assert validate_features._normalize_source_id("silver_uw_flow") == "silver_uw_flow"
    assert validate_features._normalize_source_id("silver_uw_darkpool") == "silver_uw_darkpool"
    assert validate_features._normalize_source_id("silver_alpaca_bars") == "silver_alpaca_bars"
    assert validate_features._normalize_source_id("flow_alerts") == "flow_alerts"


@pytest.mark.asyncio
async def test_fetch_source_summary_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_heber(*, source: str, label_start_ts: datetime | None, label_end_ts: datetime | None):
        assert source == "flow_alerts"
        assert label_start_ts is None
        assert label_end_ts is None
        return {
            "min_date": "2026-02-01",
            "max_date": "2026-02-05",
            "tickers": 3,
            "backend": "heber",
        }

    async def _fail_local(*, source: str):
        raise AssertionError("local DB fallback should not be called when Heber succeeds")

    monkeypatch.setattr(validate_features, "_fetch_source_summary_from_heber", _fake_heber)
    monkeypatch.setattr(validate_features, "_fetch_source_summary_from_local_db", _fail_local)

    summary = await validate_features._fetch_source_summary(
        source="flow_alerts",
        label_start_ts=None,
        label_end_ts=None,
        prefer_heber=True,
    )

    assert summary["backend"] == "heber"
    assert summary["tickers"] == 3


@pytest.mark.asyncio
async def test_fetch_source_summary_returns_empty_when_heber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _empty_heber(*, source: str, label_start_ts: datetime | None, label_end_ts: datetime | None):
        assert source == "flow_alerts"
        return None

    async def _fail_local(*, source: str):
        raise AssertionError("local DB fallback should not be called")

    monkeypatch.setattr(validate_features, "_fetch_source_summary_from_heber", _empty_heber)
    monkeypatch.setattr(validate_features, "_fetch_source_summary_from_local_db", _fail_local)

    summary = await validate_features._fetch_source_summary(
        source="flow_alerts",
        label_start_ts=None,
        label_end_ts=None,
        prefer_heber=True,
    )

    assert summary["backend"] in {"heber_unavailable", "source_unavailable"}
    assert summary["tickers"] == 0


@pytest.mark.asyncio
async def test_fetch_source_summary_rejects_legacy_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        validate_features,
        "_fetch_source_summary_from_heber",
        lambda **_: (_ for _ in ()).throw(AssertionError("Heber read should not run for unknown source ids")),
    )
    monkeypatch.setattr(
        validate_features,
        "_fetch_source_summary_from_local_db",
        lambda **_: (_ for _ in ()).throw(AssertionError("local DB fallback should not be called")),
    )

    summary = await validate_features._fetch_source_summary(
        source="silver_uw_flow",
        label_start_ts=None,
        label_end_ts=None,
        prefer_heber=True,
    )

    assert summary["backend"] == "source_unavailable"
    assert summary["tickers"] == 0


def test_feature_source_mapping_uses_canonical_source_ids() -> None:
    for feature, source in validate_features.FEATURE_SOURCE_MAPPING.items():
        if source == "derived":
            continue
        assert not source.startswith("silver_"), f"{feature} still points to legacy source id: {source}"


@pytest.mark.asyncio
async def test_validate_darkpool_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail_db_query(_fn):
        raise AssertionError("local DB fallback should not be used when Heber volume is available")

    monkeypatch.setattr(validate_features, "_get_darkpool_volume_from_heber_for_validation", lambda *_: 150)
    monkeypatch.setattr(validate_features, "db_query", _fail_db_query, raising=False)

    results = await validate_features.validate_darkpool(
        {"darkpool_volume_1h": 150},
        "AAPL",
        datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc),
    )

    assert results["failed"] == []
    assert any("matches" in msg for msg in results["passed"])


@pytest.mark.asyncio
async def test_validate_overnight_gap_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail_db_query(_fn):
        raise AssertionError("local DB fallback should not be used when Heber bars are available")

    monkeypatch.setattr(
        validate_features,
        "_get_overnight_gap_inputs_from_heber_for_validation",
        lambda **_: (110.0, 100.0),
    )
    monkeypatch.setattr(validate_features, "db_query", _fail_db_query, raising=False)

    results = await validate_features.validate_overnight_gap(
        {"overnight_gap_pct": 10.0},
        "AAPL",
        datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc),
    )

    assert results["failed"] == []
    assert any("matches raw data" in msg for msg in results["passed"])


@pytest.mark.asyncio
async def test_validate_overnight_gap_returns_empty_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validate_features, "_get_overnight_gap_inputs_from_heber_for_validation", lambda **_: None)
    monkeypatch.setattr(
        validate_features,
        "db_query",
        lambda _fn: (_ for _ in ()).throw(AssertionError("local DB fallback should not be called")),
        raising=False,
    )

    results = await validate_features.validate_overnight_gap(
        {"overnight_gap_pct": 5.0},
        "AAPL",
        datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc),
    )

    assert results["failed"] == []
    assert results["passed"] == []


@pytest.mark.asyncio
async def test_validate_darkpool_warns_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validate_features, "_get_darkpool_volume_from_heber_for_validation", lambda *_: None)
    monkeypatch.setattr(
        validate_features,
        "db_query",
        lambda _fn: (_ for _ in ()).throw(AssertionError("local DB fallback should not be called")),
        raising=False,
    )

    results = await validate_features.validate_darkpool(
        {"darkpool_volume_1h": 200},
        "AAPL",
        datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc),
    )

    assert results["passed"] == []
    assert results["failed"] == []
    assert any("no Heber" in msg for msg in results["warnings"])


def test_summarize_heber_frame_uses_instrument_key_and_ts_event() -> None:
    df = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY", "equity:SPY", "equity:QQQ"],
            "ts_event": [
                datetime(2026, 2, 3, 14, tzinfo=timezone.utc),
                datetime(2026, 2, 4, 14, tzinfo=timezone.utc),
                datetime(2026, 2, 5, 14, tzinfo=timezone.utc),
            ],
        }
    )

    summary = validate_features._summarize_heber_source_frame(df)

    assert summary["min_date"] == "2026-02-03"
    assert summary["max_date"] == "2026-02-05"
    assert summary["tickers"] == 2


@pytest.mark.asyncio
async def test_load_label_period_prefers_heber_gold_without_local_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes_df = pd.DataFrame(
        {
            "ts_event": [
                datetime(2026, 2, 3, 14, tzinfo=timezone.utc),
                datetime(2026, 2, 5, 15, tzinfo=timezone.utc),
            ],
            "underlying": ["AAPL", "MSFT"],
        }
    )

    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (asof_time, symbols)
            assert dataset == "labels_alert_barriers"
            return outcomes_df

    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be used")

    monkeypatch.setattr(validate_features, "get_heber_reader", lambda: _FakeReader())
    monkeypatch.setattr(validate_features, "db_query", _fail_db_query, raising=False)

    period = await validate_features._load_label_period()

    assert period["min_date"] == "2026-02-03"
    assert period["max_date"] == "2026-02-05"
    assert period["tickers"] == 2


@pytest.mark.asyncio
async def test_load_label_period_returns_empty_when_heber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (dataset, asof_time, symbols)
            raise RuntimeError("heber unavailable")

    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be used")

    monkeypatch.setattr(validate_features, "get_heber_reader", lambda: _FakeReader())
    monkeypatch.setattr(validate_features, "db_query", _fail_db_query, raising=False)

    period = await validate_features._load_label_period()

    assert period["min_date"] is None
    assert period["max_date"] is None
    assert period["tickers"] == 0


@pytest.mark.asyncio
async def test_spot_check_record_prefers_heber_gold_without_local_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (asof_time, symbols)
            if dataset == "labels_alert_barriers":
                return [
                    {
                        "alert_id": "evt-1",
                        "underlying": "AAPL",
                        "ts_event": "2026-02-11T15:00:00Z",
                    }
                ]
            if dataset == "meta_label_features":
                return [
                    {
                        "alert_id": "evt-1",
                        "hour_of_day": 15,
                        "day_of_week": 2,
                        "minutes_to_close": 120,
                        "overnight_gap_pct": 10.0,
                        "darkpool_1h": 50,
                        "delta": 0.4,
                        "gamma": 0.02,
                        "iv_rank": 55.0,
                        "iv": 0.5,
                    }
                ]
            raise AssertionError(f"unexpected dataset requested: {dataset}")

    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be used")

    monkeypatch.setattr(validate_features, "get_heber_reader", lambda: _FakeReader())
    monkeypatch.setattr(validate_features, "db_query", _fail_db_query, raising=False)
    monkeypatch.setattr(
        validate_features,
        "_get_overnight_gap_inputs_from_heber_for_validation",
        lambda **_: (110.0, 100.0),
    )
    monkeypatch.setattr(
        validate_features,
        "_get_darkpool_volume_from_heber_for_validation",
        lambda *_args: 50,
    )

    result = await validate_features.spot_check_record("evt-1")

    assert result["failed"] == []
    assert any("entry_hour" in item for item in result["passed"])
    assert any("overnight_gap_pct" in item for item in result["passed"])
    assert any("darkpool_1h" in item for item in result["passed"])


@pytest.mark.asyncio
async def test_spot_check_record_returns_not_found_when_event_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (dataset, asof_time, symbols)
            return []

    monkeypatch.setattr(validate_features, "get_heber_reader", lambda: _FakeReader())
    monkeypatch.setattr(
        validate_features,
        "db_query",
        lambda _fn: (_ for _ in ()).throw(AssertionError("local db_query should not be used")),
        raising=False,
    )

    result = await validate_features.spot_check_record("missing-event")

    assert any("Record not found" in msg for msg in result["failed"])
