import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

_DEFAULT_DB_URL = "postgresql+asyncpg://orion:orion_password@localhost:5432/orion_db"
DB_URL = os.getenv("DB_URL", _DEFAULT_DB_URL)


def _make_engine(url: str, *, echo: bool) -> AsyncEngine:
    if url.startswith("sqlite+aiosqlite:///:memory:"):
        return create_async_engine(
            url,
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_async_engine(url, echo=echo)


engine: AsyncEngine = _make_engine(DB_URL, echo=True)
_sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


def configure_db(db_url: str, *, echo: bool | None = None) -> None:
    """
    Reconfigure the global engine/session factory.
    Primarily intended for tests to ensure deterministic DB_URL usage.
    """
    global engine, _sessionmaker
    engine = _make_engine(db_url, echo=bool(echo) if echo is not None else True)
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


def async_session_factory() -> AsyncSession:
    return _sessionmaker()


async def init_db():
    # Ensure all models are imported so Base.metadata is complete for create_all().
    from orion.storage import (  # noqa: F401
        models,
        models_audit,
        models_dlq,
        models_execution,
        models_gold,
        models_rag,
        models_risk,
        models_signals,
        models_silver,
        models_solvers,
        models_trade_journal,
    )

    async with engine.begin() as conn:
        from sqlalchemy import text

        if conn.dialect.name == "postgresql":
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
