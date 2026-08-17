"""Decision-time factor inputs, assembly, and the optional shadow gate."""

import json
import math
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from orion.execution.factor_inputs import (
    compute_candidate_factors,
    factor_gate_reason,
    fetch_daily_closes,
    fetch_prior_flow_prints,
)
from orion.storage.db import async_session_factory
from orion.storage.models import BronzeEvent
from orion.storage.models_gold import CandidateTrade, GoldTickerRollup

AS_OF = datetime(2026, 8, 14, 18, 0, 0, tzinfo=UTC)


def _flow_row(
    event_id: str,
    *,
    ticker: str,
    ts: datetime,
    put_call: str,
    aggressor: str,
    premium: float,
    received_ts: datetime | None = None,
):
    return BronzeEvent(
        event_id=event_id,
        source="unusual_whales",
        event_type="UW_FLOW",
        ticker=ticker,
        trading_date=ts.date(),
        session="REGULAR",
        event_ts_utc=ts,
        received_ts_utc=received_ts or ts,
        payload={
            "ticker": ticker,
            "put_call": put_call,
            "aggressor": aggressor,
            "premium_usd": premium,
        },
    )


def _candidate(**kwargs) -> CandidateTrade:
    base = {
        "candidate_id": "cand-1",
        "ticker": "AAPL",
        "timestamp_utc": AS_OF,
        "rule_id": "test_rule",
        "direction": "LONG",
        "evidence": {},
        "option_symbol": "AAPL260918C00150000",
        "option_type": "CALL",
        "strike_price": 150.0,
        "expiration_date": AS_OF + timedelta(days=35),
        "underlying_price": 140.0,
        "premium": 51_870.0,
    }
    base.update(kwargs)
    return CandidateTrade(**base)


# --- fetch_prior_flow_prints ------------------------------------------------


async def test_prior_flow_prints_are_strictly_prior_same_ticker_and_side_split():
    async with async_session_factory() as session:
        session.add_all(
            [
                _flow_row(
                    "a", ticker="AAPL", ts=AS_OF - timedelta(hours=1), put_call="C", aggressor="ASK", premium=1e4
                ),
                _flow_row(
                    "b", ticker="AAPL", ts=AS_OF - timedelta(hours=2), put_call="P", aggressor="BID", premium=2e4
                ),
                _flow_row(
                    "c", ticker="AAPL", ts=AS_OF - timedelta(hours=3), put_call="C", aggressor="MID", premium=3e4
                ),
                # Excluded: at/after the anchor, outside the window, other ticker.
                _flow_row("d", ticker="AAPL", ts=AS_OF, put_call="C", aggressor="ASK", premium=9e9),
                _flow_row(
                    "e", ticker="AAPL", ts=AS_OF - timedelta(hours=30), put_call="C", aggressor="ASK", premium=9e9
                ),
                _flow_row(
                    "f", ticker="MSFT", ts=AS_OF - timedelta(hours=1), put_call="C", aggressor="ASK", premium=9e9
                ),
            ]
        )
        await session.commit()

    prints = await fetch_prior_flow_prints("AAPL", AS_OF)

    assert prints is not None
    assert len(prints) == 3
    by_side = {(p["put_call"], p["ask_prem"], p["bid_prem"]) for p in prints}
    assert ("C", 1e4, 0.0) in by_side
    assert ("P", 0.0, 2e4) in by_side
    # A MID print has no classifiable side, so it contributes to neither.
    assert ("C", 0.0, 0.0) in by_side
    assert all(p["ticker"] == "AAPL" for p in prints)


async def test_prior_flow_prints_excludes_a_print_that_arrived_after_the_candidate():
    async with async_session_factory() as session:
        session.add_all(
            [
                # Printed 90 minutes before the candidate, but not received
                # until a minute after it — the candidate could not have seen it.
                _flow_row(
                    "late",
                    ticker="AAPL",
                    ts=AS_OF - timedelta(minutes=90),
                    put_call="C",
                    aggressor="ASK",
                    premium=9e9,
                    received_ts=AS_OF + timedelta(minutes=1),
                ),
                _flow_row(
                    "ontime",
                    ticker="AAPL",
                    ts=AS_OF - timedelta(minutes=90),
                    put_call="C",
                    aggressor="ASK",
                    premium=1e4,
                    received_ts=AS_OF - timedelta(minutes=89),
                ),
            ]
        )
        await session.commit()

    prints = await fetch_prior_flow_prints("AAPL", AS_OF)

    assert prints is not None
    assert [p["ask_prem"] for p in prints] == [1e4]


async def test_prior_flow_prints_returns_none_when_the_query_fails():
    with patch("orion.execution.factor_inputs.db_query", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await fetch_prior_flow_prints("AAPL", AS_OF) is None


async def test_prior_flow_prints_returns_none_without_an_anchor():
    assert await fetch_prior_flow_prints("AAPL", None) is None
    assert await fetch_prior_flow_prints("", AS_OF) is None


# --- fetch_daily_closes -----------------------------------------------------


async def test_daily_closes_exclude_the_in_progress_session_and_come_back_chronological():
    async with async_session_factory() as session:
        for offset, close in ((2, 100.0), (1, 101.0), (0, 999.0)):
            session.add(
                GoldTickerRollup(
                    ticker="AAPL",
                    period="1d",
                    timestamp_utc=(AS_OF - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=close,
                    volume=1.0,
                    vwap=1.0,
                )
            )
            # An intraday bar for the same ticker must not leak into daily closes.
            session.add(
                GoldTickerRollup(
                    ticker="AAPL",
                    period="5m",
                    timestamp_utc=AS_OF - timedelta(days=offset, minutes=5),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=500.0,
                    volume=1.0,
                    vwap=1.0,
                )
            )
        await session.commit()

    closes = await fetch_daily_closes("AAPL", AS_OF)

    # 999.0 is the bar stamped on the candidate's own session — using it would
    # be reading a close that has not happened yet.
    assert closes == [100.0, 101.0]


async def test_daily_closes_returns_none_when_the_query_fails():
    with patch("orion.execution.factor_inputs.db_query", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await fetch_daily_closes("AAPL", AS_OF) is None


# --- compute_candidate_factors ---------------------------------------------


async def test_compute_candidate_factors_returns_the_full_key_set_on_an_empty_db():
    factors = await compute_candidate_factors(_candidate(), {"bid": 1.90, "ask": 2.10}, now=AS_OF)

    assert set(factors) == {
        "f_prior_flow_align",
        "f_rv20",
        "f_vrp",
        "f_hujacobs",
        "f_abs_delta",
        "f_moneyness_std",
        "f_dte",
        "f_premium_usd",
        "f_spread_pct",
        "f_bucket",
    }
    assert factors["f_dte"] == 35
    assert factors["f_bucket"] == "POSITION"
    assert factors["f_spread_pct"] == pytest.approx(0.10)
    assert factors["f_premium_usd"] == pytest.approx(51_870.0)
    assert factors["f_prior_flow_align"] == 0.0
    assert factors["f_rv20"] is None
    assert factors["f_vrp"] is None


async def test_compute_candidate_factors_uses_prior_flow_and_daily_closes():
    async with async_session_factory() as session:
        session.add(
            _flow_row("x", ticker="AAPL", ts=AS_OF - timedelta(hours=1), put_call="C", aggressor="ASK", premium=8e4)
        )
        for offset in range(1, 21):
            session.add(
                GoldTickerRollup(
                    ticker="AAPL",
                    period="1d",
                    timestamp_utc=(AS_OF - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=100.0 if offset % 2 else 101.0,
                    volume=1.0,
                    vwap=1.0,
                )
            )
        await session.commit()

    factors = await compute_candidate_factors(
        _candidate(), {"bid": 1.90, "ask": 2.10, "iv": 0.35, "delta": -0.42}, now=AS_OF
    )

    assert factors["f_prior_flow_align"] == pytest.approx(80_000.0)
    assert factors["f_rv20"] is not None and factors["f_rv20"] > 0
    assert factors["f_vrp"] == pytest.approx(math.log(factors["f_rv20"] / 0.35))
    assert factors["f_hujacobs"] == pytest.approx(-factors["f_rv20"])
    assert factors["f_abs_delta"] == pytest.approx(0.42)


async def test_compute_candidate_factors_output_is_json_serialisable():
    factors = await compute_candidate_factors(_candidate(), {"bid": 1.90, "ask": 2.10}, now=AS_OF)
    # decision_trace_json is a PostgreSQL json column: NaN/inf/datetime would
    # break the whole decision persist, not just the factor record. allow_nan
    # False is what makes this match PostgreSQL's strictness — the permissive
    # default emits bare NaN/Infinity, which the column rejects.
    assert json.loads(json.dumps(factors, allow_nan=False)) == factors


async def test_compute_candidate_factors_never_raises_on_a_broken_candidate():
    broken = _candidate(timestamp_utc=None, expiration_date=None, option_type=None, premium=None, strike_price=None)
    factors = await compute_candidate_factors(broken, {}, now=AS_OF)
    assert factors["f_prior_flow_align"] is None
    assert factors["f_dte"] is None
    assert factors["f_bucket"] is None


async def test_compute_candidate_factors_survives_a_dead_database():
    with patch("orion.execution.factor_inputs.db_query", AsyncMock(side_effect=RuntimeError("db down"))):
        factors = await compute_candidate_factors(_candidate(), {"bid": 1.0, "ask": 1.1}, now=AS_OF)
    assert factors["f_prior_flow_align"] is None
    assert factors["f_rv20"] is None
    # Quote-only factors still land.
    assert factors["f_spread_pct"] is not None


# --- factor_gate_reason -----------------------------------------------------


def test_factor_gate_is_off_by_default():
    from orion.config import system_settings

    assert system_settings.factor_gates == {}
    assert factor_gate_reason({"f_vrp": -9.0}) is None


def test_factor_gate_blocks_a_value_below_min():
    reason = factor_gate_reason({"f_vrp": -0.812345}, {"f_vrp": {"min": -0.5}})
    assert reason == "Factor gate: f_vrp=-0.812 outside [-0.5,None]"


def test_factor_gate_blocks_a_value_above_max():
    reason = factor_gate_reason({"f_dte": 45}, {"f_dte": {"max": 30}})
    assert reason == "Factor gate: f_dte=45.000 outside [None,30]"


def test_factor_gate_passes_a_value_inside_the_band():
    assert factor_gate_reason({"f_vrp": 0.1}, {"f_vrp": {"min": -0.5, "max": 0.5}}) is None


def test_factor_gate_does_not_fire_on_a_missing_value():
    assert factor_gate_reason({"f_vrp": None}, {"f_vrp": {"min": -0.5}}) is None
    assert factor_gate_reason({}, {"f_vrp": {"min": -0.5}}) is None


def test_factor_gate_does_not_fire_on_a_non_numeric_factor():
    assert factor_gate_reason({"f_bucket": "SWING"}, {"f_bucket": {"min": 0.0}}) is None


def test_factor_gate_reads_settings_when_no_gates_are_passed():
    from orion.config import system_settings

    with patch.object(system_settings, "factor_gates", {"f_vrp": {"min": 0.0}}):
        assert factor_gate_reason({"f_vrp": -1.0}) == "Factor gate: f_vrp=-1.000 outside [0.0,None]"
