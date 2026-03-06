from datetime import UTC, datetime, timedelta

import pytest
from orion.connectors.uw_alerts_connector import UWAlertsConnector
from orion.connectors.uw_darkpool_connector import UWDarkPoolConnector
from orion.storage.db import async_session_factory
from orion.storage.models import IngestWatermark
from orion.storage.watermarks import get_watermark


@pytest.mark.asyncio
async def test_darkpool_fetch():
    connector = UWDarkPoolConnector(api_key="test_key", base_url="http://test.url")
    base_now = datetime.now(UTC)
    prior_wm = base_now - timedelta(seconds=10)

    async with async_session_factory() as session:
        session.add(IngestWatermark(key="uw_darkpool", last_seen_ts_utc=prior_wm))
        await session.commit()

    async def fake_fetch_raw_for_date(_date_str: str):
        return [
            {
                "id": "dp_old",
                "ticker": "SPY",
                "price": 400.0,
                "size": 1000,
                "executed_at": (base_now - timedelta(seconds=200)).isoformat(),
            },
            {
                "id": "dp_1",
                "ticker": "SPY",
                "price": 401.0,
                "size": 1000,
                "executed_at": (base_now - timedelta(seconds=3)).isoformat(),
            },
        ]

    connector._fetch_raw_for_date = fake_fetch_raw_for_date  # type: ignore[assignment]

    events = await connector.fetch_events(lookback_seconds=0, overlap_seconds=120)

    assert len(events) == 1
    assert events[0].event_type == "UW_DARKPOOL"
    assert events[0].payload["ticker"] == "SPY"

    async with async_session_factory() as session:
        persisted = await get_watermark(session, key="uw_darkpool")
        assert persisted is not None
        assert persisted >= base_now - timedelta(seconds=3)


@pytest.mark.asyncio
async def test_alerts_fetch():
    connector = UWAlertsConnector(api_key="test_key", base_url="http://test.url")
    base_now = datetime.now(UTC)
    prior_wm = base_now - timedelta(seconds=10)

    async with async_session_factory() as session:
        session.add(IngestWatermark(key="uw_alerts", last_seen_ts_utc=prior_wm))
        await session.commit()

    async def fake_fetch_raw_events(*, newer_than: datetime):
        assert newer_than <= prior_wm
        return {
            "data": [
                {
                    "id": "alert_old",
                    "ticker": "TSLA",
                    "msg": "Old",
                    "timestamp": (base_now - timedelta(seconds=200)).isoformat(),
                },
                {
                    "id": "alert_1",
                    "ticker": "TSLA",
                    "msg": "Bullish",
                    "timestamp": (base_now - timedelta(seconds=2)).isoformat(),
                },
            ]
        }

    connector._fetch_raw_events = fake_fetch_raw_events  # type: ignore[assignment]

    events = await connector.fetch_events(lookback_seconds=0, overlap_seconds=120)

    assert len(events) == 1
    assert events[0].event_type == "UW_ALERT"
    assert events[0].payload["ticker"] == "TSLA"

    async with async_session_factory() as session:
        persisted = await get_watermark(session, key="uw_alerts")
        assert persisted is not None
        assert persisted >= base_now - timedelta(seconds=2)
