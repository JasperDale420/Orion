"""Pre-trade day-trading-buying-power gate on opening orders.

RCA 2026-06-02: the shared Alpaca paper account exhausted day-trading buying
power (Alpaca 40310000) and rejected every new opening order. Orion fired 193
rejected buys in a single day hammering through it. The gate backs off early
when DTBP can't cover the order, but fails OPEN on any read/parse problem so a
missing field or transient blip never blocks trading.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from orion.execution.execution_engine import ExecutionEngine


def _engine_with_account(account_result) -> ExecutionEngine:
    engine = ExecutionEngine.__new__(ExecutionEngine)
    client = AsyncMock()
    if isinstance(account_result, Exception):
        client.get_account = AsyncMock(side_effect=account_result)
    else:
        client.get_account = AsyncMock(return_value=account_result)
    engine._get_gateway_client = lambda: client
    return engine


@pytest.mark.asyncio
async def test_blocks_when_dtbp_below_cost() -> None:
    engine = _engine_with_account({"daytrading_buying_power": "100"})
    assert await engine._has_daytrading_buying_power(500.0) is False


@pytest.mark.asyncio
async def test_allows_when_dtbp_covers_cost() -> None:
    engine = _engine_with_account({"daytrading_buying_power": "1000"})
    assert await engine._has_daytrading_buying_power(500.0) is True


@pytest.mark.asyncio
async def test_fails_open_when_field_absent() -> None:
    """If the account payload has no daytrading_buying_power field, do NOT
    gate — better to let Alpaca arbitrate than to block all trading on a
    field the Gateway may not surface."""
    engine = _engine_with_account({"buying_power": "1000"})
    assert await engine._has_daytrading_buying_power(999_999.0) is True


@pytest.mark.asyncio
async def test_fails_open_on_account_error() -> None:
    engine = _engine_with_account({"error": "gateway unavailable"})
    assert await engine._has_daytrading_buying_power(100.0) is True


@pytest.mark.asyncio
async def test_fails_open_on_exception() -> None:
    engine = _engine_with_account(RuntimeError("gateway down"))
    assert await engine._has_daytrading_buying_power(100.0) is True


def test_dtbp_backoff_arms_and_expires(monkeypatch) -> None:
    """After a confirmed broker 40310000 rejection, the reactive backoff blocks
    opening orders for the cooldown, then clears — so the proactive check's
    fail-open can't recreate the reject flood (adversarial review)."""
    import orion.execution.execution_engine as ee_mod

    engine = ExecutionEngine.__new__(ExecutionEngine)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ee_mod.time, "monotonic", lambda: clock["t"])

    assert engine._in_dtbp_backoff() is False  # nothing armed yet
    engine._note_dtbp_rejection()
    assert engine._in_dtbp_backoff() is True  # armed

    clock["t"] += engine._DTBP_BACKOFF_SECONDS - 1
    assert engine._in_dtbp_backoff() is True  # still within cooldown

    clock["t"] += 2
    assert engine._in_dtbp_backoff() is False  # cooldown elapsed


@pytest.mark.asyncio
async def test_account_is_cached_within_ttl() -> None:
    """The per-order gate must not hit the Gateway on every order — the
    account snapshot is cached for a short TTL."""
    engine = _engine_with_account({"daytrading_buying_power": "1000"})
    client = engine._get_gateway_client()
    await engine._has_daytrading_buying_power(10.0)
    await engine._has_daytrading_buying_power(10.0)
    await engine._has_daytrading_buying_power(10.0)
    assert client.get_account.await_count == 1
