from __future__ import annotations

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

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query)

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert tickers == ["AAPL", "MSFT"]
    assert source == "heber"


@pytest.mark.asyncio
async def test_get_active_tickers_with_source_falls_back_to_local_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feature_enrichment._heber_reader,
        "read_flow",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("heber unavailable")),
    )

    async def _fake_db_query(_query_fn):
        return ["SPY", "QQQ"]

    monkeypatch.setattr(feature_enrichment, "db_query", _fake_db_query)

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert tickers == ["SPY", "QQQ"]
    assert source == "local_db"


@pytest.mark.asyncio
async def test_get_active_tickers_with_source_falls_back_to_static(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feature_enrichment._heber_reader,
        "read_flow",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("heber unavailable")),
    )

    async def _fail_db_query(_query_fn):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query)

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
