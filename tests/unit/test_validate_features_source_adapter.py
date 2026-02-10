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


def test_normalize_source_id_legacy_alias_maps_to_canonical() -> None:
    assert validate_features._normalize_source_id("silver_uw_flow") == "flow_alerts"
    assert validate_features._normalize_source_id("silver_uw_darkpool") == "darkpool"
    assert validate_features._normalize_source_id("silver_alpaca_bars") == "bars"
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
async def test_fetch_source_summary_falls_back_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _empty_heber(*, source: str, label_start_ts: datetime | None, label_end_ts: datetime | None):
        assert source == "flow_alerts"
        return None

    async def _local(*, source: str):
        assert source == "flow_alerts"
        return {
            "min_date": "2026-01-01",
            "max_date": "2026-02-05",
            "tickers": 7,
            "backend": "local_db",
        }

    monkeypatch.setattr(validate_features, "_fetch_source_summary_from_heber", _empty_heber)
    monkeypatch.setattr(validate_features, "_fetch_source_summary_from_local_db", _local)

    summary = await validate_features._fetch_source_summary(
        source="flow_alerts",
        label_start_ts=None,
        label_end_ts=None,
        prefer_heber=True,
    )

    assert summary["backend"] == "local_db"
    assert summary["tickers"] == 7


@pytest.mark.asyncio
async def test_fetch_source_summary_accepts_legacy_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_heber(*, source: str, label_start_ts: datetime | None, label_end_ts: datetime | None):
        assert source == "flow_alerts"
        return {
            "min_date": "2026-02-01",
            "max_date": "2026-02-05",
            "tickers": 3,
            "backend": "heber",
        }

    monkeypatch.setattr(validate_features, "_fetch_source_summary_from_heber", _fake_heber)
    monkeypatch.setattr(validate_features, "_fetch_source_summary_from_local_db", lambda **_: None)

    summary = await validate_features._fetch_source_summary(
        source="silver_uw_flow",
        label_start_ts=None,
        label_end_ts=None,
        prefer_heber=True,
    )

    assert summary["backend"] == "heber"
    assert summary["tickers"] == 3


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
    monkeypatch.setattr(validate_features, "db_query", _fail_db_query)

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
    monkeypatch.setattr(validate_features, "db_query", _fail_db_query)

    results = await validate_features.validate_overnight_gap(
        {"overnight_gap_pct": 10.0},
        "AAPL",
        datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc),
    )

    assert results["failed"] == []
    assert any("matches raw data" in msg for msg in results["passed"])


@pytest.mark.asyncio
async def test_validate_overnight_gap_falls_back_to_local_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_db_query(fn):
        class _FakeResult:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        class _FakeSession:
            def __init__(self):
                self._count = 0

            async def execute(self, *_args, **_kwargs):
                self._count += 1
                if self._count == 1:
                    return _FakeResult((105.0,))
                return _FakeResult((100.0,))

        return await fn(_FakeSession())

    monkeypatch.setattr(validate_features, "_get_overnight_gap_inputs_from_heber_for_validation", lambda **_: None)
    monkeypatch.setattr(validate_features, "db_query", _fake_db_query)

    results = await validate_features.validate_overnight_gap(
        {"overnight_gap_pct": 5.0},
        "AAPL",
        datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc),
    )

    assert results["failed"] == []
    assert any("matches raw data" in msg for msg in results["passed"])


@pytest.mark.asyncio
async def test_validate_darkpool_falls_back_to_local_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_db_query(fn):
        class _FakeResult:
            def fetchone(self):
                return (200,)

        class _FakeSession:
            async def execute(self, *_args, **_kwargs):
                return _FakeResult()

        return await fn(_FakeSession())

    monkeypatch.setattr(validate_features, "_get_darkpool_volume_from_heber_for_validation", lambda *_: None)
    monkeypatch.setattr(validate_features, "db_query", _fake_db_query)

    results = await validate_features.validate_darkpool(
        {"darkpool_volume_1h": 200},
        "AAPL",
        datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc),
    )

    assert results["failed"] == []
    assert any("matches" in msg for msg in results["passed"])


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
