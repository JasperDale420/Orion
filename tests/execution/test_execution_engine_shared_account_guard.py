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
