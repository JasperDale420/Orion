"""Shared HTTP client with structured logging, retry, and Docker TCP fix.

Usage:
    from empire_core.http_client import create_http_client, create_async_http_client, http_retry, raise_for_status

    client = create_http_client(base_url="http://localhost:8080")

    @http_retry
    async def fetch_data(client):
        resp = await client.get("/api/data")
        raise_for_status(resp)
        return resp.json()
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import structlog
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger("empire.http")

DEFAULT_TIMEOUT = 30.0

# Disable keepalive to prevent stale TCP sockets through Docker Desktop's
# userspace TCP proxy (causes FIN_WAIT_2/CLOSE_WAIT pile-up and sporadic
# "Server disconnected without sending a response" errors).
_NO_KEEPALIVE = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=0,
    keepalive_expiry=0.0,
)


def _log_request(request: httpx.Request) -> None:
    logger.debug("http_request", method=str(request.method), url=str(request.url))


def _log_response(response: httpx.Response) -> None:
    try:
        elapsed = response.elapsed.total_seconds() if response.elapsed else 0.0
    except RuntimeError:
        elapsed = 0.0
    logger.debug(
        "http_response",
        method=str(response.request.method),
        url=str(response.request.url),
        status_code=response.status_code,
        elapsed_s=round(elapsed, 3),
    )


async def _alog_request(request: httpx.Request) -> None:
    _log_request(request)


async def _alog_response(response: httpx.Response) -> None:
    _log_response(response)


def create_http_client(
    base_url: str = "",
    timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> httpx.Client:
    """Create a configured synchronous httpx.Client."""
    limits = kwargs.pop("limits", _NO_KEEPALIVE)
    transport = kwargs.pop("transport", httpx.HTTPTransport(retries=2))
    headers = headers or {}
    if limits is _NO_KEEPALIVE or limits is None or getattr(limits, "max_keepalive_connections", None) == 0:
        headers.setdefault("Connection", "close")

    return httpx.Client(
        base_url=base_url,
        timeout=timeout,
        headers=headers,
        follow_redirects=True,
        limits=limits,
        transport=transport,
        event_hooks={"request": [_log_request], "response": [_log_response]},
        **kwargs,
    )


def create_async_http_client(
    base_url: str = "",
    timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create a configured asynchronous httpx.AsyncClient."""
    limits = kwargs.pop("limits", _NO_KEEPALIVE)
    transport = kwargs.pop("transport", httpx.AsyncHTTPTransport(retries=2))
    headers = headers or {}
    if limits is _NO_KEEPALIVE or limits is None or getattr(limits, "max_keepalive_connections", None) == 0:
        headers.setdefault("Connection", "close")

    return httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
        headers=headers,
        follow_redirects=True,
        limits=limits,
        transport=transport,
        event_hooks={"request": [_alog_request], "response": [_alog_response]},
        **kwargs,
    )


def raise_for_status(response: httpx.Response) -> None:
    """Validate HTTP response, raising with structured context on failure."""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        logger.error(
            "http_error",
            method=str(response.request.method),
            url=str(response.request.url),
            status_code=response.status_code,
            response_text=response.text[:500],
        )
        raise


http_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
    before_sleep=before_sleep_log(logger, logging.WARNING),  # type: ignore[arg-type]
    reraise=True,
)
