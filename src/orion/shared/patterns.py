"""
Shared design patterns for Orion.

Provides reusable base classes and utilities for common patterns.
"""

import asyncio
from typing import Any, Dict, Type, TypeVar

T = TypeVar("T", bound="AsyncSingleton")


class AsyncSingleton:
    """
    Base class for async singleton pattern.

    Usage:
        class MyService(AsyncSingleton):
            def __init__(self) -> None:
                # initialization code
                pass

        # Get singleton instance
        instance = await MyService.get_instance()
    """

    _instances: Dict[Type["AsyncSingleton"], "AsyncSingleton"] = {}
    _lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls: Type[T], *args: Any, **kwargs: Any) -> T:
        """
        Get or create the singleton instance of this class.

        Args:
            *args: Arguments to pass to __init__ on first creation
            **kwargs: Keyword arguments to pass to __init__ on first creation

        Returns:
            The singleton instance
        """
        if cls not in cls._instances:
            async with cls._lock:
                if cls not in cls._instances:
                    instance = cls(*args, **kwargs)
                    cls._instances[cls] = instance
                    async_init = getattr(instance, "_async_init", None)
                    if async_init is not None:
                        result = async_init()
                        if asyncio.iscoroutine(result):
                            await result
        return cls._instances[cls]  # type: ignore

    @classmethod
    def _reset_instance(cls) -> None:
        """Reset the singleton instance. Used for testing only."""
        if cls in cls._instances:
            del cls._instances[cls]
