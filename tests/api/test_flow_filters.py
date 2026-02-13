from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from orion.api.main import app


@pytest.mark.asyncio
async def test_flows_endpoint_supports_min_premium_filter_from_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_API_KEY", "testkey")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    flow_df = pd.DataFrame(
        [
            {
                "event_id": "flow_1",
                "instrument_key": "equity:SPY",
                "ts_event": now - timedelta(seconds=10),
                "call_put": "call",
                "premium": 500.0,
            },
            {
                "event_id": "flow_2",
                "instrument_key": "equity:SPY",
                "ts_event": now - timedelta(seconds=5),
                "call_put": "call",
                "premium": 1500.0,
            },
            {
                "event_id": "flow_3",
                "instrument_key": "equity:QQQ",
                "ts_event": now - timedelta(seconds=1),
                "call_put": "put",
                "premium": 5000.0,
            },
        ]
    )

    captured: dict[str, object] = {}

    class _FakeReader:
        def read_flow(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return flow_df

    monkeypatch.setattr("orion.api.main.get_heber_reader", lambda: _FakeReader())

    headers = {"x-api-key": "testkey"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rows = (
            await client.get(
                "/flows",
                headers=headers,
                params={"ticker": "SPY", "min_premium_usd": 1000},
            )
        ).json()

    assert isinstance(rows, list)
    ids = {r["event_id"] for r in rows}
    assert "flow_2" in ids
    assert "flow_1" not in ids
    assert "flow_3" not in ids
    assert captured["symbols"] == ["SPY"]
    assert captured["min_premium"] == 1000.0


@pytest.mark.asyncio
async def test_flows_endpoint_returns_empty_when_heber_read_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_API_KEY", "testkey")

    class _FakeReader:
        def read_flow(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("heber unavailable")

    monkeypatch.setattr("orion.api.main.get_heber_reader", lambda: _FakeReader())

    headers = {"x-api-key": "testkey"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rows = (await client.get("/flows", headers=headers, params={"ticker": "SPY"})).json()

    assert rows == []
