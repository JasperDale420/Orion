"""
Database transaction utilities for Orion.

Provides helpers to reduce boilerplate for database operations.
"""

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from orion.config import system_settings
from orion.storage.db import async_session_factory

logger = logging.getLogger(__name__)

T = TypeVar("T")

SQLITE_LOCK_RETRY_ATTEMPTS = system_settings.sqlite_lock_retry_attempts
SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS = system_settings.sqlite_lock_retry_base_delay_seconds
SQLITE_LOCK_RETRY_MAX_DELAY_SECONDS = system_settings.sqlite_lock_retry_max_delay_seconds


def _is_sqlite_session(session: AsyncSession) -> bool:
    try:
        bind = session.get_bind()
    except Exception:
        return False
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    return str(dialect_name).lower() == "sqlite"


def _is_retryable_sqlite_lock_error(error: Exception) -> bool:
    if not isinstance(error, (OperationalError, DBAPIError)):
        return False

    raw_message = str(error).lower()
    patterns = (
        "database is locked",
        "database table is locked",
        "database schema is locked",
        "sqlite_busy",
    )
    return any(pattern in raw_message for pattern in patterns)


def _retry_delay_seconds(attempt_index: int) -> float:
    delay = SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS * (2**attempt_index)
    return min(delay, SQLITE_LOCK_RETRY_MAX_DELAY_SECONDS)


async def db_transaction(operation: Callable[[AsyncSession], Awaitable[T]], *, commit: bool = True) -> T:
    """
    Execute a database operation within a transaction.

    Automatically handles session lifecycle, commit, and rollback.

    Args:
        operation: Async function that takes a session and returns a result
        commit: Whether to commit after successful execution (default: True)

    Returns:
        The result from the operation

    Raises:
        Any exception raised by the operation (after rollback)

    Example:
        async def create_user(session: AsyncSession) -> User:
            user = User(name="Alice")
            session.add(user)
            return user

        user = await db_transaction(create_user)
    """
    max_attempts = SQLITE_LOCK_RETRY_ATTEMPTS + 1
    for attempt in range(max_attempts):
        async with async_session_factory() as session:
            try:
                result = await operation(session)
                if commit:
                    await session.commit()
                return result
            except Exception as e:
                await session.rollback()
                is_retryable = _is_sqlite_session(session) and _is_retryable_sqlite_lock_error(e)
                has_retries_remaining = attempt < (max_attempts - 1)
                if is_retryable and has_retries_remaining:
                    delay = _retry_delay_seconds(attempt)
                    logger.warning(
                        "Database transaction lock contention detected; retrying",
                        extra={
                            "attempt": attempt + 1,
                            "max_attempts": max_attempts,
                            "delay_seconds": delay,
                        },
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(f"Database transaction failed: {e}", exc_info=True)
                raise

    raise RuntimeError("db_transaction retry loop ended without returning or raising")


async def db_query(query_fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """
    Execute a read-only database query.

    Convenience wrapper for db_transaction with commit=False.

    Args:
        query_fn: Async function that takes a session and returns a result

    Returns:
        The result from the query

    Example:
        async def get_user_count(session: AsyncSession) -> int:
            result = await session.execute(select(func.count(User.id)))
            return result.scalar_one()

        count = await db_query(get_user_count)
    """
    return await db_transaction(query_fn, commit=False)


async def db_write(write_fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """
    Execute a write operation with automatic commit.

    Convenience wrapper for db_transaction with commit=True (default).

    Args:
        write_fn: Async function that takes a session and returns a result

    Returns:
        The result from the write operation

    Example:
        async def update_user(session: AsyncSession) -> User:
            user = await session.get(User, user_id)
            user.name = "Bob"
            return user

        user = await db_write(update_user)
    """
    return await db_transaction(write_fn, commit=True)
