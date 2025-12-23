from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from orion.storage.db import async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for providing an async database session.
    """
    async with async_session_factory() as session:
        yield session
