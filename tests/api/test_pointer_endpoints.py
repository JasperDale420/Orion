from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from orion.api.main import app
from orion.config import system_settings
from orion.storage.db import async_session_factory
from orion.storage.models import BronzeEvent
from orion.storage.models_gold import CandidateTrade, GoldTickerRollup


@pytest.mark.asyncio
async def test_pointer_endpoints_return_raw_entities(monkeypatch):
    monkeypatch.setattr(system_settings, "api_key", "testkey")

    now = datetime.now(UTC)

    async with async_session_factory() as session:
        session.add(
            BronzeEvent(
                event_id="evt_1",
                source="UW",
                source_event_id="src_1",
                event_type="UW_FLOW",
                ticker="SPY",
                trading_date=now.date(),
                session="REG",
                schema_version="v1",
                event_ts_utc=now,
                received_ts_utc=now,
                payload={"ticker": "SPY", "premium": 123},
                ingest={"connector": "test"},
            )
        )
        session.add(
            CandidateTrade(
                candidate_id="cand_1",
                ticker="SPY",
                timestamp_utc=now,
                rule_id="rule_bullish_sweep_v1",
                direction="LONG",
                confidence=0.7,
                source="UW",
                execution_params={"limit_price": 500.0},
                evidence={"event_ids": ["evt_1"]},
            )
        )
        session.add(
            GoldTickerRollup(
                ticker="SPY",
                period="5m",
                timestamp_utc=now.replace(second=0, microsecond=0),
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=100.0,
                vwap=1.4,
            )
        )
        await session.commit()

    headers = {"x-api-key": "testkey"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ev = (await client.get("/events/evt_1", headers=headers)).json()
        assert ev["event_id"] == "evt_1"
        assert ev["payload"]["ticker"] == "SPY"

        cand = (await client.get("/candidates/cand_1", headers=headers)).json()
        assert cand["candidate_id"] == "cand_1"
        assert cand["evidence"]["event_ids"] == ["evt_1"]

        rollups = (await client.get("/rollups", headers=headers, params={"ticker": "SPY", "period": "5m"})).json()
        assert isinstance(rollups, list)
        assert rollups and rollups[0]["ticker"] == "SPY"

        r0 = rollups[0]
        one = (
            await client.get(
                f"/rollups/{r0['ticker']}/{r0['period']}/{r0['timestamp_utc']}",
                headers=headers,
            )
        ).json()
        assert one["ticker"] == "SPY"
