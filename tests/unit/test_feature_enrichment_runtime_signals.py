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
