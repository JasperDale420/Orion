from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from orion.api.main import app
from orion.storage.db import async_session_factory
from orion.storage.models_silver import SilverOptionFlow


@pytest.mark.asyncio
async def test_flows_endpoint_supports_min_premium_filter(monkeypatch):
    monkeypatch.setenv("ORION_API_KEY", "testkey")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    async with async_session_factory() as session:
        session.add(
            SilverOptionFlow(
                event_id="flow_1",
                source_event_id="src_1",
                ticker="SPY",
                flow_ts_utc=now - timedelta(seconds=10),
                put_call="C",
                expiry="2025-01-17",
                strike=500.0,
                option_price=1.0,
                size_contracts=1,
                premium_usd=500.0,
                bid=0.9,
                ask=1.1,
                underlying_price=500.0,
                aggressor="ASK",
                is_sweep="true",
                flags_json={"is_sweep": True},
                volume_contract=1,
                open_interest=10,
                ingest={"connector": "test"},
            )
        )
        session.add(
            SilverOptionFlow(
                event_id="flow_2",
                source_event_id="src_2",
                ticker="SPY",
                flow_ts_utc=now - timedelta(seconds=5),
                put_call="C",
                expiry="2025-01-17",
                strike=500.0,
                option_price=1.0,
                size_contracts=1,
                premium_usd=1500.0,
                bid=0.9,
                ask=1.1,
                underlying_price=500.0,
                aggressor="ASK",
                is_sweep="true",
                flags_json={"is_sweep": True},
                volume_contract=1,
                open_interest=10,
                ingest={"connector": "test"},
            )
        )
        await session.commit()

    headers = {"x-api-key": "testkey"}
    # Use ASGITransport to avoid DeprecationWarning/TypeError with newer httpx
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rows = (await client.get("/flows", headers=headers, params={"ticker": "SPY", "min_premium_usd": 1000})).json()
        assert isinstance(rows, list)
        ids = {r["event_id"] for r in rows}
        assert "flow_2" in ids
        assert "flow_1" not in ids
