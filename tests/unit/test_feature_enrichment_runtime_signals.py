from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from orion import main_feature_enrichment as feature_enrichment


@pytest.mark.asyncio
async def test_get_active_tickers_with_source_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = pd.Timestamp.now(tz="UTC")
    flow_df = pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT"],
            "ts_event": [now, now, now],
        }
    )

    monkeypatch.setattr(
        feature_enrichment._heber_reader,
        "read_flow",
        lambda **_kwargs: flow_df,
    )

    async def _fail_db_query(_query_fn):
        raise AssertionError("db_query fallback should not be called when Heber returns tickers")

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query, raising=False)

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert tickers == ["AAPL", "MSFT"]
    assert source == "heber"


@pytest.mark.asyncio
async def test_get_active_tickers_with_source_falls_back_to_static_without_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feature_enrichment._heber_reader,
        "read_flow",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("heber unavailable")),
    )
    # Also fail the bars fallback so we reach static
    monkeypatch.setattr(feature_enrichment, "_extract_tickers_from_bars", lambda limit: [])

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert source == "static_fallback"
    assert tickers[:2] == ["SPY", "QQQ"]


@pytest.mark.asyncio
async def test_get_active_tickers_with_source_falls_back_to_static(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feature_enrichment._heber_reader,
        "read_flow",
        lambda **_kwargs: pd.DataFrame(),
    )
    # Also fail the bars fallback so we reach static
    monkeypatch.setattr(feature_enrichment, "_extract_tickers_from_bars", lambda limit: [])

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert source == "static_fallback"
    assert tickers[:2] == ["SPY", "QQQ"]


def test_note_fetch_count_warns_on_zero_write_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[dict[str, object]] = []

    def _fake_warning(_message: str, *args: object, extra: dict[str, object] | None = None, **_kwargs: object) -> None:
        if extra:
            warnings.append(extra)

    monkeypatch.setattr(feature_enrichment.logger, "warning", _fake_warning, raising=False)

    streaks: dict[str, int] = {}
    feature_enrichment._note_fetch_count(
        feed_name="iv_rank",
        count=0,
        zero_write_streaks=streaks,
        warn_streak=2,
        tickers_count=5,
    )
    assert not warnings

    feature_enrichment._note_fetch_count(
        feed_name="iv_rank",
        count=0,
        zero_write_streaks=streaks,
        warn_streak=2,
        tickers_count=5,
    )
    assert warnings[-1] == {
        "event": "feature_enrichment_zero_write_streak",
        "feed": "iv_rank",
        "count": 0,
        "streak": 2,
        "warn_streak": 2,
        "tickers_count": 5,
    }


def test_note_fetch_count_resets_streak_after_success() -> None:
    streaks = {"max_pain": 3}

    feature_enrichment._note_fetch_count(
        feed_name="max_pain",
        count=4,
        zero_write_streaks=streaks,
        warn_streak=2,
    )

    assert streaks["max_pain"] == 0


def test_log_ticker_source_transition_logs_on_change(monkeypatch: pytest.MonkeyPatch) -> None:
    infos: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []

    def _fake_info(_message: str, *args: object, extra: dict[str, object] | None = None, **_kwargs: object) -> None:
        if extra:
            infos.append(extra)

    def _fake_warning(_message: str, *args: object, extra: dict[str, object] | None = None, **_kwargs: object) -> None:
        if extra:
            warnings.append(extra)

    monkeypatch.setattr(feature_enrichment.logger, "info", _fake_info, raising=False)
    monkeypatch.setattr(feature_enrichment.logger, "warning", _fake_warning, raising=False)

    previous = feature_enrichment._log_ticker_source_transition(
        source="heber",
        previous_source=None,
        tickers_count=3,
    )
    assert previous == "heber"
    assert infos[-1]["source"] == "heber"

    previous = feature_enrichment._log_ticker_source_transition(
        source="local_db",
        previous_source=previous,
        tickers_count=3,
    )
    assert previous == "local_db"
    assert warnings[-1] == {
        "event": "feature_enrichment_ticker_source_changed",
        "previous_source": "heber",
        "source": "local_db",
        "tickers_count": 3,
    }


def test_loop_sleep_seconds_invalid_env_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[dict[str, object]] = []
    monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_LOOP_SLEEP_SECONDS", "abc")

    def _fake_warning(_message: str, *args: object, extra: dict[str, object] | None = None, **_kwargs: object) -> None:
        if extra:
            warnings.append(extra)

    monkeypatch.setattr(feature_enrichment.logger, "warning", _fake_warning, raising=False)

    value = feature_enrichment._loop_sleep_seconds()

    assert value == feature_enrichment.DEFAULT_LOOP_SLEEP_SECONDS
    assert warnings[-1]["event"] == "feature_enrichment_loop_sleep_seconds_invalid"


def test_note_loop_error_warns_at_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[dict[str, object]] = []

    def _fake_warning(_message: str, *args: object, extra: dict[str, object] | None = None, **_kwargs: object) -> None:
        if extra:
            warnings.append(extra)

    monkeypatch.setattr(feature_enrichment.logger, "warning", _fake_warning, raising=False)

    streak = feature_enrichment._note_loop_error(
        consecutive_error_streak=0,
        warn_streak=2,
        error=RuntimeError("boom"),
    )
    assert streak == 1
    assert warnings == []

    streak = feature_enrichment._note_loop_error(
        consecutive_error_streak=streak,
        warn_streak=2,
        error=RuntimeError("boom"),
    )
    assert streak == 2
    assert warnings[-1] == {
        "event": "feature_enrichment_loop_error_streak",
        "streak": 2,
        "warn_streak": 2,
        "error": "boom",
    }


def test_non_heber_warn_streak_threshold_invalid_env_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[dict[str, object]] = []
    monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_NON_HEBER_WARN_STREAK", "nope")

    def _fake_warning(_message: str, *args: object, extra: dict[str, object] | None = None, **_kwargs: object) -> None:
        if extra:
            warnings.append(extra)

    monkeypatch.setattr(feature_enrichment.logger, "warning", _fake_warning, raising=False)

    value = feature_enrichment._non_heber_warn_streak_threshold()

    assert value == feature_enrichment.DEFAULT_NON_HEBER_WARN_STREAK
    assert warnings[-1]["event"] == "feature_enrichment_non_heber_warn_streak_invalid"


def test_gateway_fetch_enabled_defaults_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH", raising=False)
    assert feature_enrichment._gateway_fetch_enabled() is False


def test_gateway_fetch_enabled_parses_truthy_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH", "true")
    assert feature_enrichment._gateway_fetch_enabled() is True


@pytest.mark.asyncio
async def test_run_feature_loop_skips_gateway_contract_when_gateway_fetch_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH", "false")

    async def _noop_init_db() -> None:
        return None

    def _should_not_call_gateway_contract() -> tuple[str, str]:
        raise AssertionError("gateway contract should not be required when gateway fetch is disabled")

    monkeypatch.setattr(feature_enrichment, "init_db", _noop_init_db)
    monkeypatch.setattr(feature_enrichment, "_gateway_runtime_contract", _should_not_call_gateway_contract)

    shutdown_event = asyncio.Event()
    shutdown_event.set()

    await feature_enrichment.run_feature_loop(shutdown_event)


@pytest.mark.asyncio
async def test_run_feature_loop_requires_gateway_contract_when_gateway_fetch_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH", "true")

    async def _noop_init_db() -> None:
        return None

    def _missing_gateway_contract() -> tuple[str, str]:
        raise ValueError("gateway credentials missing")

    monkeypatch.setattr(feature_enrichment, "init_db", _noop_init_db)
    monkeypatch.setattr(feature_enrichment, "_gateway_runtime_contract", _missing_gateway_contract)

    shutdown_event = asyncio.Event()
    shutdown_event.set()

    with pytest.raises(ValueError, match="gateway credentials missing"):
        await feature_enrichment.run_feature_loop(shutdown_event)


def test_note_ticker_source_streak_warns_on_non_heber_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[dict[str, object]] = []

    def _fake_warning(_message: str, *args: object, extra: dict[str, object] | None = None, **_kwargs: object) -> None:
        if extra:
            warnings.append(extra)

    monkeypatch.setattr(feature_enrichment.logger, "warning", _fake_warning, raising=False)

    streak = feature_enrichment._note_ticker_source_streak(
        source="local_db",
        non_heber_streak=0,
        warn_streak=2,
        tickers_count=5,
    )
    assert streak == 1
    assert warnings == []

    streak = feature_enrichment._note_ticker_source_streak(
        source="local_db",
        non_heber_streak=streak,
        warn_streak=2,
        tickers_count=5,
    )
    assert streak == 2
    assert warnings[-1] == {
        "event": "feature_enrichment_non_heber_streak",
        "source": "local_db",
        "streak": 2,
        "warn_streak": 2,
        "tickers_count": 5,
    }

    streak = feature_enrichment._note_ticker_source_streak(
        source="heber",
        non_heber_streak=streak,
        warn_streak=2,
        tickers_count=5,
    )
    assert streak == 0


@pytest.mark.asyncio
async def test_persist_regime_snapshot_avoids_local_db_write(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail_db_write(_fn):
        raise AssertionError("local db_write should not be used")

    monkeypatch.setattr(feature_enrichment, "db_write", _fail_db_write, raising=False)
    monkeypatch.setattr(feature_enrichment, "_recent_regime_snapshots", [], raising=False)

    snapshot = SimpleNamespace(
        trend=SimpleNamespace(value="bull"),
        vol=SimpleNamespace(value="normal"),
        risk=SimpleNamespace(value="risk_on"),
        session=SimpleNamespace(value="regular"),
        vix_regime=SimpleNamespace(value="normal"),
        vix_level=18.2,
        realized_vol=0.21,
        trend_strength=0.33,
        risk_score=0.45,
        confidence={"trend": 0.8},
    )

    await feature_enrichment.persist_regime_snapshot(
        ts=datetime(2026, 2, 11, 20, 0, tzinfo=UTC),
        snapshot=snapshot,
        ticker="SPY",
    )

    assert feature_enrichment._recent_regime_snapshots
