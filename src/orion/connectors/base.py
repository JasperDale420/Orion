from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from orion.storage.models import BronzeEvent


@runtime_checkable
class PollingConnector(Protocol):
    """
    PRDv2 7.2: Polling transport interface; callers should be able to swap polling↔websocket
    without changing downstream normalization/dedupe/storage code.
    """

    async def fetch_since(self, ts: datetime, *, overlap_seconds: int = 120) -> list[BronzeEvent]: ...


@runtime_checkable
class StreamingConnector(Protocol):
    async def stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[BronzeEvent, None]: ...
