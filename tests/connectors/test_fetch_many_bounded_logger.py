"""_fetch_many_bounded must emit failure events on the caller-supplied logger."""

from __future__ import annotations

import asyncio
from typing import Any

from orion.connectors.base_gateway import BaseGatewayConnector


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def warning(self, event: str, **_: Any) -> None:
        self.events.append(("warning", event))

    def error(self, event: str, **_: Any) -> None:
        self.events.append(("error", event))


def test_failures_use_caller_logger() -> None:
    connector = object.__new__(BaseGatewayConnector)
    log = _RecordingLogger()

    def fetch_one(ticker: str) -> dict[str, Any] | None:
        raise RuntimeError("boom")

    async def process_one(ticker: str, payload: Any) -> int:
        return 1

    stored = asyncio.run(
        connector._fetch_many_bounded(["AAPL"], fetch_one, process_one, label="x", log=log, rate_limit_delay=0)
    )

    assert stored == 0
    assert log.events == [("warning", "x_retry_exhausted")]


def test_process_failure_uses_caller_logger() -> None:
    connector = object.__new__(BaseGatewayConnector)
    log = _RecordingLogger()

    async def process_one(ticker: str, payload: Any) -> int:
        raise ValueError("bad payload")

    stored = asyncio.run(
        connector._fetch_many_bounded(
            ["AAPL"], lambda t: {"data": {"a": 1}}, process_one, label="x", log=log, rate_limit_delay=0
        )
    )

    assert stored == 0
    assert log.events == [("error", "x_ticker_failed")]
