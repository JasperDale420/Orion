import pytest
from sqlalchemy import select

from orion.execution.persistence import persist_fill_record
from orion.storage.db import async_session_factory
from orion.storage.models_execution import FillRecord


@pytest.mark.asyncio
async def test_persist_fill_record_updates_existing_order_row() -> None:
    fill_one = {
        "id": "broker-2",
        "symbol": "AAPL",
        "client_order_id": "orion_456",
        "filled_qty": 4.0,
        "qty": 10.0,
        "filled_avg_price": 2.5,
        "side": "buy",
    }
    fill_two = {
        "id": "broker-2",
        "symbol": "AAPL",
        "client_order_id": "orion_456",
        "filled_qty": 10.0,
        "qty": 10.0,
        "filled_avg_price": 2.6,
        "side": "buy",
    }

    await persist_fill_record(fill_one)
    await persist_fill_record(fill_two)

    async with async_session_factory() as session:
        row = (
            (await session.execute(select(FillRecord).where(FillRecord.broker_order_id == "broker-2")))
            .scalars()
            .first()
        )

    assert row is not None
    assert row.filled_qty == 10.0
    assert row.filled_avg_price == 2.6
