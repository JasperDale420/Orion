from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from orion.execution.position_manager import PositionManager
from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateTrade, StrategyDecision


def _candidate(
    *,
    candidate_id: str,
    ticker: str,
    ts: datetime,
    option_symbol: str | None = None,
    evidence: dict | None = None,
) -> CandidateTrade:
    return CandidateTrade(
        candidate_id=candidate_id,
        ticker=ticker,
        timestamp_utc=ts,
        rule_id="rule.test",
        direction="LONG",
        confidence=0.9,
        option_symbol=option_symbol,
        evidence=evidence or {},
    )


def _decision(*, candidate_id: str, ticker: str, ts: datetime, limit_price: float) -> StrategyDecision:
    return StrategyDecision(
        decision_id=str(uuid.uuid4()),
        candidate_id=candidate_id,
        timestamp_utc=ts,
        ticker=ticker,
        strategy_version_id="v1",
        decision="EXECUTE",
        executed_successfully="TRUE",
        execution_params={"limit_price": limit_price, "qty": 1},
    )


def test_add_position_uses_candidate_option_symbol_and_sets_entry_option_price() -> None:
    pm = PositionManager()
    now = datetime.now(timezone.utc)
    candidate = _candidate(
        candidate_id="c-add",
        ticker="AAPL",
        ts=now,
        option_symbol="AAPL260220C00200000",
        evidence={"option_chain": "LEGACY_SHOULD_NOT_WIN"},
    )
    decision = _decision(candidate_id="c-add", ticker="AAPL", ts=now, limit_price=2.35)

    pos = pm.add_position(candidate, decision, entry_context={})

    assert pos.option_chain == "AAPL260220C00200000"
    assert pos.entry_price == 2.35
    assert pos.entry_option_price == 2.35


def test_create_position_prefers_candidate_option_symbol_over_evidence() -> None:
    pm = PositionManager()
    now = datetime.now(timezone.utc)
    candidate = _candidate(
        candidate_id="c-create",
        ticker="MSFT",
        ts=now,
        option_symbol="MSFT260220P00350000",
        evidence={"option_chain": "MSFT_LEGACY_CHAIN"},
    )
    decision = _decision(candidate_id="c-create", ticker="MSFT", ts=now, limit_price=4.1)

    pos = pm._create_position_from_decision(decision, candidate)

    assert pos is not None
    assert pos.option_chain == "MSFT260220P00350000"
    assert pos.entry_option_price == 4.1


@pytest.mark.asyncio
async def test_initialize_loads_more_than_fifty_open_positions(setup_test_db) -> None:
    now = datetime.now(timezone.utc)
    rows = []
    for idx in range(55):
        cid = f"cid-{idx:03d}"
        ticker = f"T{idx:03d}"
        rows.append(
            _candidate(
                candidate_id=cid,
                ticker=ticker,
                ts=now,
                option_symbol=f"{ticker}260220C00100000",
            )
        )
        rows.append(_decision(candidate_id=cid, ticker=ticker, ts=now, limit_price=1.0 + idx / 100.0))

    async with async_session_factory() as session:
        session.add_all(rows)
        await session.commit()

    pm = PositionManager()
    await pm.initialize()

    open_positions = pm.get_open_positions()
    assert len(open_positions) == 55
