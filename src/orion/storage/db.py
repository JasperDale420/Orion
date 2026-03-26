from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from orion.config import system_settings

DB_URL = system_settings.db_url


def _make_engine(url: str, *, echo: bool) -> AsyncEngine:
    if url.startswith("sqlite+aiosqlite:///:memory:"):
        return create_async_engine(
            url,
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


engine: AsyncEngine = _make_engine(DB_URL, echo=system_settings.db_echo)
_sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


def _default_async_session_factory() -> AsyncSession:
    return _sessionmaker()


async_session_factory = _default_async_session_factory


def configure_db(db_url: str, *, echo: bool | None = None) -> None:
    """
    Reconfigure the global engine/session factory.
    Primarily intended for tests to ensure deterministic DB_URL usage.
    """
    global engine, _sessionmaker, async_session_factory
    engine = _make_engine(db_url, echo=bool(echo) if echo is not None else system_settings.db_echo)
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    # Reset any external symbol reassignment performed by tests.
    async_session_factory = _default_async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    # Ensure all models are imported so Base.metadata is complete for create_all().
    from orion.storage import (  # noqa: F401
        models,
        models_audit,
        models_dlq,
        models_execution,
        models_gold,
        models_ml,
        models_rag,
        models_risk,
        models_signals,
        models_silver,
        models_solvers,
        models_trade_journal,
    )

    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
