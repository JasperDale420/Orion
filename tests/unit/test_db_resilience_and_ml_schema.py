from __future__ import annotations

import pytest
from sqlalchemy import text


def test_make_engine_enables_pre_ping_for_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    from orion.storage import db as db_mod

    captured: dict[str, object] = {}

    def _fake_create_async_engine(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(db_mod, "create_async_engine", _fake_create_async_engine)

    db_mod._make_engine("postgresql+asyncpg://u:p@localhost:5432/orion_db", echo=False)

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("pool_pre_ping") is True
    assert kwargs.get("pool_recycle") == 1800


@pytest.mark.asyncio
async def test_init_db_creates_ml_tables() -> None:
    from orion.storage.db import async_session_factory, configure_db, init_db

    configure_db("sqlite+aiosqlite:///:memory:")
    await init_db()

    async with async_session_factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                AND name IN (
                    'ml_pattern_insights',
                    'ml_feature_importance_history',
                    'ml_predictions'
                )
                ORDER BY name
                """
            )
        )
        names = [row[0] for row in rows.fetchall()]

    assert names == [
        "ml_feature_importance_history",
        "ml_pattern_insights",
        "ml_predictions",
    ]
