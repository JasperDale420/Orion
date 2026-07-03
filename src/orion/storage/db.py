import asyncio
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


async def _check_db_connection() -> None:
    """Cheap connectivity probe: open a connection and run SELECT 1.

    Separate module-level function so ``wait_for_db`` has a clean seam to patch
    in tests without replacing the engine.
    """
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def wait_for_db(
    max_attempts: int = 30,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    cancel_event: "asyncio.Event | None" = None,
) -> None:
    """Block until the database accepts a connection, with bounded backoff.

    Startup used to call ``init_db()`` directly; a transient DB outage
    (TimescaleDB still coming up, or briefly down) then raised, the process
    exited, and launchd restarted it on a 30s throttle — a noisy crash-loop
    (observed live 2026-06-07 18:03-18:23). Worse, for ingestion a degraded
    start left the WS subscription pinned to the static watchlist for the whole
    session (the 2026-06-01 near-outage). Polling a cheap ``SELECT 1`` lets a
    transient outage clear before the service proceeds. Raises after
    ``max_attempts`` so a genuinely-down DB still surfaces loudly
    (fail-fast-after-bounded-retry, per the Empire error philosophy).

    ``cancel_event``: when provided (e.g. the service's shutdown event), a
    SIGTERM/SIGINT during the wait aborts promptly instead of blocking startup
    for the full backoff (~265s with defaults) — important so launchd/Docker can
    stop the service during the exact DB outage this guards against.
    """
    from orion.shared.logger import setup_struct_logger

    log = setup_struct_logger("orion.storage.db")
    delay = base_delay
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("wait_for_db aborted: shutdown requested")
        try:
            await _check_db_connection()
            if attempt > 1:
                log.info("db_ready", extra={"event_type": "DB_READY", "attempt": attempt})
            return
        except Exception as exc:
            last_exc = exc
            log.warning(
                "db_not_ready",
                extra={
                    "event_type": "DB_NOT_READY",
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "error": str(exc),
                },
            )
            if attempt < max_attempts:
                if cancel_event is not None:
                    # Race the backoff against shutdown: if the event fires
                    # within `delay`, abort promptly; otherwise sleep the full
                    # delay and retry.
                    try:
                        await asyncio.wait_for(cancel_event.wait(), timeout=delay)
                        raise RuntimeError("wait_for_db aborted: shutdown requested")
                    except (TimeoutError, asyncio.TimeoutError):
                        pass
                else:
                    await asyncio.sleep(delay)
                delay = min(delay * 2.0, max_delay)
    raise RuntimeError(f"Database not reachable after {max_attempts} attempts: {last_exc}")


async def init_db() -> None:
    # Ensure all models are imported so Base.metadata is complete for create_all().
    from orion.storage import (  # noqa: F401
        models,
        models_attribution,
        models_audit,
        models_dlq,
        models_execution,
        models_flow_parity,
        models_gold,
        models_liveness,
        models_ml,
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
