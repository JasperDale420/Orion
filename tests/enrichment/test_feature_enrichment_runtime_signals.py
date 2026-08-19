from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from orion import main_feature_enrichment as feature_enrichment
from orion.core.market_schedule import MarketSchedule
from orion.enrichment import heber_context


async def _noop_wait_for_db(*_args: object, **_kwargs: object) -> None:
    """Stub for the startup DB-readiness wait the service loop now performs
    before init_db — irrelevant to these loop-behaviour tests, and would trip
    its shutdown-abort on the pre-set shutdown_event."""
    return None


@pytest.mark.asyncio
async def test_get_active_tickers_with_source_falls_back_to_heber_when_bronze_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bronze (TimescaleDB) is the primary discovery source after the
    2026-04-22 OOM redesign; Heber flow is now a fallback that runs only
    when the bronze path raises. See docs/rca/feature_enrichment_crash_loop.md.
    """
    now = pd.Timestamp.now(tz="UTC")
    flow_df = pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT"],
            "ts_event": [now, now, now],
        }
    )

    monkeypatch.setattr(
        heber_context._heber_reader,
        "read_flow",
        lambda **_kwargs: flow_df,
    )

    async def _bronze_fails(_limit: int, lookback_hours: int = 24) -> list[str]:
        raise RuntimeError("bronze ticker discovery unavailable")

    monkeypatch.setattr(heber_context, "_get_active_tickers_from_bronze", _bronze_fails)

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert tickers == ["AAPL", "MSFT"]
    assert source == "heber"


@pytest.mark.asyncio
async def test_get_active_tickers_with_source_falls_back_to_static_without_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MarketSchedule, "is_market_open", lambda self, timestamp=None: True)
    monkeypatch.setattr(
        heber_context._heber_reader,
        "read_flow",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("heber unavailable")),
    )
    # Also fail the bars fallback so we reach static
    monkeypatch.setattr(heber_context, "_extract_tickers_from_bars", lambda limit: [])

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert source == "static_fallback"
    assert tickers[:2] == ["SPY", "QQQ"]


@pytest.mark.asyncio
async def test_get_active_tickers_with_source_falls_back_to_static(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MarketSchedule, "is_market_open", lambda self, timestamp=None: True)
    monkeypatch.setattr(
        heber_context._heber_reader,
        "read_flow",
        lambda **_kwargs: pd.DataFrame(),
    )
    # Also fail the bars fallback so we reach static
    monkeypatch.setattr(heber_context, "_extract_tickers_from_bars", lambda limit: [])

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert source == "static_fallback"
    assert tickers[:2] == ["SPY", "QQQ"]


async def _bronze_empty(_limit: int, lookback_hours: int = 24) -> list[str]:
    return []


@pytest.mark.asyncio
async def test_empty_bronze_while_market_closed_is_idle_not_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful-but-empty bronze query outside market hours is normal
    (weekend/overnight ageing past the 24h lookback), not a discovery outage.
    It must not feed the DEGRADED fence that blocks Monday-open entries."""
    monkeypatch.setattr(heber_context, "_get_active_tickers_from_bronze", _bronze_empty)
    monkeypatch.setattr(MarketSchedule, "is_market_open", lambda self, timestamp=None: False)

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert source == "market_closed_idle"
    assert tickers == ["SPY", "QQQ"]
    assert heber_context._is_discovery_degraded("market_closed_idle", streak=300, warn_streak=3) is False


@pytest.mark.asyncio
async def test_empty_bronze_while_market_open_still_static_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """During the session an empty bronze result is a real outage signal:
    the source stays static_fallback and the streak fence is unchanged."""
    monkeypatch.setattr(heber_context, "_get_active_tickers_from_bronze", _bronze_empty)
    monkeypatch.setattr(MarketSchedule, "is_market_open", lambda self, timestamp=None: True)

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert source == "static_fallback"
    assert tickers == ["SPY", "QQQ"]
    assert heber_context._is_discovery_degraded("static_fallback", streak=3, warn_streak=3) is True


@pytest.mark.asyncio
async def test_empty_bronze_calendar_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead exchange calendar cannot be trusted to say 'closed'; treat it as
    open so the degradation fence keeps its existing behaviour."""

    def _calendar_dead(self: MarketSchedule, timestamp: datetime | None = None) -> bool:
        raise RuntimeError("Cannot verify market hours without calendar")

    monkeypatch.setattr(heber_context, "_get_active_tickers_from_bronze", _bronze_empty)
    monkeypatch.setattr(MarketSchedule, "is_market_open", _calendar_dead)

    tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)

    assert source == "static_fallback"
    assert tickers == ["SPY", "QQQ"]


@pytest.mark.asyncio
async def test_weekend_idle_then_dead_feed_at_open_still_reaches_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closed-to-open transition with bronze empty throughout.

    Weekend cycles are idle and hold the streak at zero; once the market opens
    the fence arms from a clean streak, so a feed that is genuinely dead at the
    open is DEGRADED after warn_streak (3) discovery refreshes — the same
    window an intraday outage has always had, no longer pre-armed by the
    weekend, and no wider than before.
    """
    market_open = {"value": False}
    monkeypatch.setattr(heber_context, "_get_active_tickers_from_bronze", _bronze_empty)
    monkeypatch.setattr(MarketSchedule, "is_market_open", lambda self, timestamp=None: market_open["value"])
    warn_streak = 3
    streak = 0

    for _ in range(3):
        _tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)
        streak = feature_enrichment._note_ticker_source_streak(source, streak, warn_streak, tickers_count=2)
        assert source == "market_closed_idle"
        assert streak == 0
        assert heber_context._is_discovery_degraded(source, streak, warn_streak) is False

    market_open["value"] = True
    degraded_at: list[bool] = []
    for _ in range(warn_streak):
        _tickers, source = await feature_enrichment.get_active_tickers_with_source(limit=2)
        streak = feature_enrichment._note_ticker_source_streak(source, streak, warn_streak, tickers_count=2)
        assert source == "static_fallback"
        degraded_at.append(heber_context._is_discovery_degraded(source, streak, warn_streak))

    assert degraded_at == [False, False, True]


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
    assert warnings[-1]["event"] == "orion_feature_enrichment_loop_sleep_seconds_invalid"


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
    assert warnings[-1]["event"] == "orion_feature_enrichment_non_heber_warn_streak_invalid"


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
    # run_feature_loop now calls wait_for_db(cancel_event=shutdown_event) before
    # init_db; this test pre-sets shutdown_event to make the loop exit after one
    # pass, which would otherwise trip wait_for_db's "shutdown requested" abort.
    monkeypatch.setattr(feature_enrichment, "wait_for_db", _noop_wait_for_db)
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
    monkeypatch.setattr(feature_enrichment, "wait_for_db", _noop_wait_for_db)
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
        "event": "feature_enrichment_static_fallback_streak",
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


@pytest.mark.parametrize(
    "et_hour,weekday,expected",
    [
        (4, 1, False),  # 4 AM ET Tuesday — pre pre-market
        (6, 1, False),  # 6 AM ET Tuesday — pre 7 AM gate
        (7, 1, True),  # 7 AM ET Tuesday — gate opens
        (10, 1, True),  # 10 AM ET Tuesday — regular hours
        (16, 4, True),  # 4 PM ET Friday — regular close
        (19, 4, True),  # 7 PM ET Friday — within post-market window
        (20, 4, False),  # 8 PM ET Friday — gate closes
        (10, 5, False),  # 10 AM ET Saturday — weekend
        (10, 6, False),  # 10 AM ET Sunday — weekend
    ],
)
def test_is_extended_market_hours(et_hour: int, weekday: int, expected: bool) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    # Pick a date with the desired weekday (2026-04-27 is a Monday).
    base = datetime(2026, 4, 27, et_hour, 30, tzinfo=et)
    test_dt = base + timedelta(days=weekday)
    assert feature_enrichment._is_extended_market_hours(test_dt.astimezone(UTC)) is expected


def test_note_ticker_source_streak_resets_on_bronze_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """bronze_db is the canonical primary source post-Apr-22 OOM redesign;
    it must reset the non-heber streak so the warning doesn't fire on every
    cycle in the new architecture.
    """
    warnings: list[dict[str, object]] = []

    def _fake_warning(_msg: str, *args: object, extra: dict[str, object] | None = None, **_kw: object) -> None:
        if extra:
            warnings.append(extra)

    monkeypatch.setattr(feature_enrichment.logger, "warning", _fake_warning, raising=False)

    streak = feature_enrichment._note_ticker_source_streak(
        source="bronze_db",
        non_heber_streak=42,
        warn_streak=2,
        tickers_count=20,
    )
    assert streak == 0
    assert warnings == []


@pytest.mark.asyncio
async def test_persist_regime_snapshot_avoids_local_db_write(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail_db_write(_fn):
        raise AssertionError("local db_write should not be used")

    monkeypatch.setattr(heber_context, "db_write", _fail_db_write, raising=False)
    monkeypatch.setattr(heber_context, "_recent_regime_snapshots", [], raising=False)

    snapshot = SimpleNamespace(
        trend=SimpleNamespace(value="bull"),
        vol=SimpleNamespace(value="normal"),
        risk=SimpleNamespace(value="risk_on"),
        session=SimpleNamespace(value="regular"),
        vix_regime=SimpleNamespace(value="normal"),
        vix_level=18.2,
        vix_source=None,
        vix_observed_at=None,
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

    assert heber_context._recent_regime_snapshots
