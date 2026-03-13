from datetime import UTC, datetime, timedelta

import pytest
from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.core.errors import ProviderError
from orion.storage.db import async_session_factory
from orion.storage.models import IngestWatermark
from orion.storage.watermarks import get_watermark


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("UW_API_KEY", "test_key")


def test_init_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("UW_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="UW_API_KEY is required"):
        UWFlowConnector()


@pytest.mark.asyncio
async def test_poll_uses_db_watermark_and_persists_updates(mock_env):
    base_now = datetime.now(UTC)
    prior_wm = base_now - timedelta(seconds=10)

    async with async_session_factory() as session:
        session.add(IngestWatermark(key="uw_flow", last_seen_ts_utc=prior_wm))
        await session.commit()

    connector = UWFlowConnector()
    captured: dict = {}

    async def fake_fetch_raw_events(start_ts: datetime, end_ts: datetime):
        captured["start_ts"] = start_ts
        captured["end_ts"] = end_ts
        return [
            {"id": "old", "ticker": "AAPL", "premium": 1, "timestamp": (base_now - timedelta(seconds=200)).isoformat()},
            {"id": "dup", "ticker": "AAPL", "premium": 2, "timestamp": (base_now - timedelta(seconds=5)).isoformat()},
            {"id": "dup", "ticker": "AAPL", "premium": 2, "timestamp": (base_now - timedelta(seconds=5)).isoformat()},
            {"id": "new", "ticker": "TSLA", "premium": 3, "timestamp": (base_now - timedelta(seconds=3)).isoformat()},
        ]

    connector.fetch_raw_events = fake_fetch_raw_events  # type: ignore[assignment]

    events = await connector.poll(lookback_seconds=0, overlap_seconds=120)

    assert captured["start_ts"] <= prior_wm - timedelta(seconds=120) + timedelta(seconds=1)
    assert captured["start_ts"] >= prior_wm - timedelta(seconds=120) - timedelta(seconds=1)

    # "old" is older than the fetch window and should be filtered out; "dup" should be deduped.
    assert len(events) == 2
    ids = {e.source_event_id for e in events}
    assert ids == {"dup", "new"}

    async with async_session_factory() as session:
        persisted = await get_watermark(session, key="uw_flow")
        assert persisted is not None
        assert persisted >= base_now - timedelta(seconds=3)


@pytest.mark.asyncio
async def test_watermark_persists_across_instances(mock_env):
    base_now = datetime.now(UTC)
    prior_wm = base_now - timedelta(seconds=30)

    async with async_session_factory() as session:
        session.add(IngestWatermark(key="uw_flow", last_seen_ts_utc=prior_wm))
        await session.commit()

    connector1 = UWFlowConnector()

    async def fetch_for_first(_start: datetime, _end: datetime):
        return [{"id": "x", "ticker": "SPY", "premium": 1, "timestamp": (base_now - timedelta(seconds=1)).isoformat()}]

    connector1.fetch_raw_events = fetch_for_first  # type: ignore[assignment]
    await connector1.poll(lookback_seconds=0, overlap_seconds=120)

    connector2 = UWFlowConnector()
    captured: dict = {}

    async def fetch_for_second(start: datetime, end: datetime):
        captured["start"] = start
        captured["end"] = end
        return []

    connector2.fetch_raw_events = fetch_for_second  # type: ignore[assignment]
    await connector2.poll(lookback_seconds=0, overlap_seconds=120)

    async with async_session_factory() as session:
        persisted = await get_watermark(session, key="uw_flow")
        assert persisted is not None

    assert captured["start"] >= persisted - timedelta(seconds=120) - timedelta(seconds=1)
    assert captured["start"] <= persisted - timedelta(seconds=120) + timedelta(seconds=1)
