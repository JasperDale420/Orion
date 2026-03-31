from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from orion.api.main import app
from orion.config import system_settings
from orion.storage.db import async_session_factory
from orion.storage.models_rag import VectorDocument


@pytest.mark.asyncio
async def test_search_supports_tickers_premium_filters_and_pointers(monkeypatch):
    monkeypatch.setattr(system_settings, "api_key", "testkey")

    # Avoid network calls: stub embeddings to a deterministic vector.
    from orion.rag import vector_store as vs

    async def fake_get_embedding(self, text: str):
        return [0.0] * 1536

    monkeypatch.setattr(vs.EmbeddingClient, "get_embedding", fake_get_embedding, raising=True)

    now = datetime.now(UTC)

    docs = [
        VectorDocument(
            doc_id="doc_spy_big",
            source_type="TEST",
            source_id="row_1",
            content="major call sweep cluster",
            embedding=[0.0] * 1536,
            embedding_vec=None,
            metadata_json={
                "doc_type": "major_event_card",
                "ticker": "SPY",
                "session": "REG",
                "timestamp": now.isoformat(),
                "premium_usd": 100000.0,
                "pointers": {"event_ids": ["evt_1"], "rollup_ids": ["SPY|5m|2025-01-01T00:00:00+00:00"]},
            },
        ),
        VectorDocument(
            doc_id="doc_tsla_small",
            source_type="TEST",
            source_id="row_2",
            content="major call sweep cluster",
            embedding=[0.0] * 1536,
            embedding_vec=None,
            metadata_json={
                "doc_type": "major_event_card",
                "ticker": "TSLA",
                "session": "REG",
                "timestamp": now.isoformat(),
                "premium_usd": 1000.0,
                "pointers": {"event_ids": ["evt_2"]},
            },
        ),
        VectorDocument(
            doc_id="doc_aapl_big_pre",
            source_type="TEST",
            source_id="row_3",
            content="major call sweep cluster",
            embedding=[0.0] * 1536,
            embedding_vec=None,
            metadata_json={
                "doc_type": "major_event_card",
                "ticker": "AAPL",
                "session": "PRE",
                "timestamp": now.isoformat(),
                "premium_usd": 250000.0,
                "pointers": {"event_ids": ["evt_3"]},
            },
        ),
    ]

    async with async_session_factory() as session:
        for d in docs:
            session.add(d)
        await session.commit()

    headers = {"x-api-key": "testkey"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/search",
            headers=headers,
            params={
                "q": "sweep",
                "k": 10,
                "tickers": "SPY,TSLA",
                "session": "REG",
                "min_premium_usd": 50000,
            },
        )
        assert res.status_code == 200, res.text
        rows = res.json()
        assert len(rows) == 1

        row = rows[0]
        assert row["doc_id"] == "doc_spy_big"
        assert row["metadata"]["ticker"] == "SPY"
        assert row["metadata"]["premium_usd"] >= 50000
        assert row["pointers"]["source_type"] == "TEST"
        assert row["pointers"]["source_id"] == "row_1"
        assert row["pointers"]["event_ids"] == ["evt_1"]


@pytest.mark.asyncio
async def test_search_returns_503_when_vector_store_fails(monkeypatch):
    monkeypatch.setattr(system_settings, "api_key", "testkey")

    async def failing_search(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("vector store down")

    monkeypatch.setattr("orion.api.main.VectorStore.search", failing_search)

    headers = {"x-api-key": "testkey"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/search", headers=headers, params={"q": "sweep"})

    assert res.status_code == 503
    assert res.json()["detail"] == "Search unavailable"
