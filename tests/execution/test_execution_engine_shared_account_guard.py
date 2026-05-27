from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import orion.execution.execution_engine as execution_engine_module
from orion.execution.execution_engine import ExecutionEngine


class _RiskManagerStub:
    def __init__(self) -> None:
        self.current_equity = 0.0
        self.starting_equity = 0.0
        self.current_daily_loss = 0.0
        self.peak_equity = 100000.0
        self.positions = {"KEEP": {"qty": 1.0, "avg_entry": 10.0}}
        self.ticker_exposures = {"KEEP": 10.0}
        self.open_positions = 1

    async def evaluate_drawdown_kill_switch(self) -> None:
        return None


@pytest.mark.asyncio
async def test_fetch_orion_tickers_logs_and_returns_none_when_lookup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = ExecutionEngine.__new__(ExecutionEngine)
    mock_logger = MagicMock()

    async def _raise(_query_fn):
        raise RuntimeError("db offline")

    monkeypatch.setattr(execution_engine_module, "db_query", _raise)
    monkeypatch.setattr(execution_engine_module, "logger", mock_logger)

    tickers = await engine._fetch_orion_tickers()

    assert tickers is None
    mock_logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_sync_risk_from_gateway_skips_positions_when_orion_ticker_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine.risk_manager = _RiskManagerStub()

    client = MagicMock()
    client.get_account = AsyncMock(return_value={"equity": "100000", "last_equity": "100000"})
    client.get_positions = AsyncMock(
        return_value=[
            {
                "symbol": "OTHER",
                "qty": "5",
                "avg_entry_price": "10",
                "market_value": "50",
            }
        ]
    )

    mock_logger = MagicMock()

    monkeypatch.setattr(engine, "_check_gateway_available", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_get_gateway_client", lambda: client)
    monkeypatch.setattr(engine, "_fetch_orion_tickers", AsyncMock(return_value=None))
    monkeypatch.setattr(execution_engine_module, "logger", mock_logger)

    await engine._sync_risk_from_gateway()

    assert client.get_positions.await_count == 0
    assert engine.risk_manager.positions == {"KEEP": {"qty": 1.0, "avg_entry": 10.0}}
    assert engine.risk_manager.ticker_exposures == {"KEEP": 10.0}
    mock_logger.error.assert_called()


@pytest.mark.asyncio
async def test_sync_risk_resets_open_positions_when_broker_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the broker returns 0 orion-attributed positions, the risk
    manager's `open_positions` count must reset to 0 — NOT stay at
    whatever stale value was loaded from the persisted risk_state.

    Pre-fix (2026-05-21): an `if positions:` guard around the entire
    position-reset block meant an empty broker response skipped the
    `risk_manager.open_positions = ...` assignment. The DB-loaded
    stale value (5 today) persisted; every EXECUTE then failed
    preflight with "RISK REJECT: Max Positions 5 Reached" even
    though Orion had 0 open positions in reality.
    """
    engine = ExecutionEngine.__new__(ExecutionEngine)
    risk = _RiskManagerStub()
    # Simulate the stale state: 5 ghost positions persisted on the
    # manager from a prior session.
    risk.positions = {f"GHOST_{i}": {"qty": 1.0, "avg_entry": 10.0} for i in range(5)}
    risk.ticker_exposures = {f"GHOST_{i}": 10.0 for i in range(5)}
    risk.open_positions = 5
    risk._equity_seeded = True
    risk._peak_equity_seeded = True
    engine.risk_manager = risk

    client = MagicMock()
    client.get_account = AsyncMock(return_value={"equity": "100000", "last_equity": "100000"})
    # Broker returns empty positions list (Orion really has none right now)
    client.get_positions = AsyncMock(return_value=[])

    monkeypatch.setattr(engine, "_check_gateway_available", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_get_gateway_client", lambda: client)
    # orion_tickers lookup succeeds with empty set (no orion-prefixed orders today)
    monkeypatch.setattr(engine, "_fetch_orion_tickers", AsyncMock(return_value=set()))

    await engine._sync_risk_from_gateway()

    assert engine.risk_manager.open_positions == 0, (
        f"open_positions must reset to 0 when broker returns empty list; "
        f"got {engine.risk_manager.open_positions} — stale ghost count would "
        f"block every EXECUTE at the max_positions preflight gate"
    )
    assert engine.risk_manager.positions == {}, "stale ghost positions must be cleared from risk_manager.positions"
    assert engine.risk_manager.ticker_exposures == {}


@pytest.mark.asyncio
async def test_sync_risk_from_gateway_matches_options_per_occ_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-contract attribution from ``fills.ticker`` (which stores the
    full OCC contract for options). Originally written 2026-05-26 as
    ``test_sync_risk_from_gateway_matches_options_by_underlying`` —
    rewritten same day after codex review CRITICAL on commit 39174f8
    to test the per-OCC-contract contract, not the now-removed
    underlying-only matching. See sibling adapter test
    ``test_adapter_keeps_orion_options_positions_per_occ_contract``.
    """
    engine = ExecutionEngine.__new__(ExecutionEngine)
    risk = _RiskManagerStub()
    risk.positions = {}
    risk.ticker_exposures = {}
    risk.open_positions = 0
    risk._equity_seeded = True
    risk._peak_equity_seeded = True
    engine.risk_manager = risk

    client = MagicMock()
    client.get_account = AsyncMock(return_value={"equity": "100000", "last_equity": "100000"})
    client.get_positions = AsyncMock(
        return_value=[
            # Orion-owned option position (OCC contract Orion has filled)
            {
                "symbol": "AAPL260529P00315000",
                "qty": "10",
                "avg_entry_price": "5.50",
                "market_value": "6750",
            },
            # Sibling system's option position Orion has never filled.
            {
                "symbol": "CRWD260529C00500000",
                "qty": "3",
                "avg_entry_price": "8",
                "market_value": "2400",
            },
        ]
    )

    monkeypatch.setattr(engine, "_check_gateway_available", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_get_gateway_client", lambda: client)
    # Orion has filled the AAPL put — fills.ticker contains the full OCC.
    monkeypatch.setattr(engine, "_fetch_orion_tickers", AsyncMock(return_value={"AAPL260529P00315000"}))

    await engine._sync_risk_from_gateway()

    assert engine.risk_manager.open_positions == 1
    assert "AAPL260529P00315000" in engine.risk_manager.positions
    assert "CRWD260529C00500000" not in engine.risk_manager.positions


@pytest.mark.asyncio
async def test_sync_risk_from_gateway_excludes_sibling_options_on_same_underlying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review 2026-05-26 CRITICAL: per-OCC attribution, not
    per-underlying. If a sibling system opens an AAPL CALL today on a
    contract Orion has never filled, the executor's sync must NOT count
    it toward Orion's risk — even if Orion has filled a different AAPL
    contract historically. Companion to the adapter-side test
    ``test_adapter_excludes_other_systems_options_on_same_underlying``.
    """
    engine = ExecutionEngine.__new__(ExecutionEngine)
    risk = _RiskManagerStub()
    risk.positions = {}
    risk.ticker_exposures = {}
    risk.open_positions = 0
    risk._equity_seeded = True
    risk._peak_equity_seeded = True
    engine.risk_manager = risk

    client = MagicMock()
    client.get_account = AsyncMock(return_value={"equity": "100000", "last_equity": "100000"})
    client.get_positions = AsyncMock(
        return_value=[
            # Orion's put — has a matching fill.
            {
                "symbol": "AAPL260529P00315000",
                "qty": "10",
                "avg_entry_price": "5.50",
                "market_value": "6750",
            },
            # Sibling's call on the same underlying — no orion fill.
            {
                "symbol": "AAPL260529C00400000",
                "qty": "5",
                "avg_entry_price": "2.80",
                "market_value": "1600",
            },
        ]
    )

    monkeypatch.setattr(engine, "_check_gateway_available", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_get_gateway_client", lambda: client)
    # Only the put is in Orion's fills set; the call is not.
    monkeypatch.setattr(engine, "_fetch_orion_tickers", AsyncMock(return_value={"AAPL260529P00315000"}))

    await engine._sync_risk_from_gateway()

    assert engine.risk_manager.open_positions == 1, (
        "only the orion-filled AAPL put may count toward open_positions; "
        f"got {engine.risk_manager.open_positions} (would include the sibling call)"
    )
    assert "AAPL260529P00315000" in engine.risk_manager.positions
    assert "AAPL260529C00400000" not in engine.risk_manager.positions, (
        "sibling system's AAPL call (different OCC contract, same underlying) "
        "must NOT leak into Orion's risk-tracked positions"
    )


@pytest.mark.asyncio
async def test_sync_risk_resets_open_positions_when_broker_has_only_non_orion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same as above but broker returns non-orion positions (other
    Empire systems on shared account). Orion's count must still
    reset to 0 since none are attributable to Orion."""
    engine = ExecutionEngine.__new__(ExecutionEngine)
    risk = _RiskManagerStub()
    risk.positions = {"STALE": {"qty": 5.0, "avg_entry": 10.0}}
    risk.ticker_exposures = {"STALE": 50.0}
    risk.open_positions = 1
    risk._equity_seeded = True
    risk._peak_equity_seeded = True
    engine.risk_manager = risk

    client = MagicMock()
    client.get_account = AsyncMock(return_value={"equity": "100000", "last_equity": "100000"})
    client.get_positions = AsyncMock(
        return_value=[
            {"symbol": "CERBERUS_POS", "qty": "5", "avg_entry_price": "10", "market_value": "50"},
        ]
    )

    monkeypatch.setattr(engine, "_check_gateway_available", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_get_gateway_client", lambda: client)
    # No orion orders → empty ticker set → CERBERUS_POS filtered out
    monkeypatch.setattr(engine, "_fetch_orion_tickers", AsyncMock(return_value=set()))

    await engine._sync_risk_from_gateway()

    assert engine.risk_manager.open_positions == 0
    assert engine.risk_manager.positions == {}
