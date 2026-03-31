from unittest.mock import AsyncMock

import pytest

from orion.execution.fill_processor import FillProcessor


@pytest.mark.asyncio
async def test_partial_fills_are_processed_incrementally(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = FillProcessor()
    risk_manager = AsyncMock()
    risk_manager.process_fill = AsyncMock()
    risk_manager.update_sector_exposure = AsyncMock()
    remove_pending_fn = AsyncMock()
    is_processed_mock = AsyncMock(return_value=False)
    mark_processed_mock = AsyncMock()
    persist_fill_mock = AsyncMock()

    fill_one = {
        "id": "broker-1",
        "client_order_id": "orion_123",
        "symbol": "TEST",
        "filled_qty": 4.0,
        "qty": 10.0,
        "filled_avg_price": 2.5,
        "side": "buy",
    }
    fill_two = {
        "id": "broker-1",
        "client_order_id": "orion_123",
        "symbol": "TEST",
        "filled_qty": 10.0,
        "qty": 10.0,
        "filled_avg_price": 2.5,
        "side": "buy",
    }

    monkeypatch.setattr("orion.execution.fill_processor.is_fill_processed", is_processed_mock)
    monkeypatch.setattr("orion.execution.fill_processor.mark_fill_processed", mark_processed_mock)
    monkeypatch.setattr("orion.execution.fill_processor.persist_fill_record", persist_fill_mock)

    await processor.process_single_fill(fill_one, risk_manager, remove_pending_fn)
    await processor.process_single_fill(fill_two, risk_manager, remove_pending_fn)

    assert risk_manager.process_fill.await_args_list[0].args[1] == 4.0
    assert risk_manager.process_fill.await_args_list[1].args[1] == 6.0
    assert remove_pending_fn.await_count == 1
    assert processor._partial_fill_tracker == {}
    assert is_processed_mock.await_args_list[0].args[0] == "broker-1:4.0"
    assert is_processed_mock.await_args_list[1].args[0] == "broker-1:10.0"
    assert mark_processed_mock.await_args_list[0].args[0] == "broker-1:4.0"
    assert mark_processed_mock.await_args_list[1].args[0] == "broker-1:10.0"
