"""
Database transaction utilities for Orion.

Provides helpers to reduce boilerplate for database operations.
"""

import logging
from typing import Awaitable, Callable, TypeVar

from orion.storage.db import async_session_factory
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

T = TypeVar("T")


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
    async with async_session_factory() as session:
        try:
            result = await operation(session)
            if commit:
                await session.commit()
            return result
        except Exception as e:
            await session.rollback()
            logger.error(f"Database transaction failed: {e}", exc_info=True)
            raise


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
