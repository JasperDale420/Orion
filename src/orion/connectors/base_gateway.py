"""Base class for Data Gateway connectors.

Consolidates shared boilerplate: gateway URL/key validation, header setup,
retryable status code handling, HTTP GET with retry, and in-memory buffer trimming.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.config import system_settings
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.connectors.base_gateway")

RETRYABLE_GATEWAY_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class BaseGatewayConnector:
    """Common foundation for connectors that talk to Data Gateway over HTTP."""

    def __init__(self, gateway_url: str | None = None, gateway_key: str | None = None) -> None:
        raw_url = gateway_url if gateway_url is not None else (system_settings.data_gateway_url or "")
        self.gateway_url = raw_url.strip().rstrip("/")
        if not self.gateway_url:
            raise ValueError("DATA_GATEWAY_URL/GATEWAY_URL setting not configured")
        raw_key = gateway_key if gateway_key is not None else (system_settings.data_gateway_api_key or "")
        self.gateway_key = raw_key.strip()
        if not self.gateway_key:
            raise ValueError("DATA_GATEWAY_API_KEY/GATEWAY_API_KEY setting not configured")
        self.headers: dict[str, str] = {"X-Gateway-Key": self.gateway_key}

    @staticmethod
    def _is_retryable_gateway_status(status_code: int | None) -> bool:
        """Return True if the HTTP status code is transient and worth retrying."""
        return status_code in RETRYABLE_GATEWAY_STATUS_CODES

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _gateway_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
        *,
        label: str = "gateway_request",
    ) -> dict[str, Any] | None:
        """Issue a GET against the gateway with standard retry/error handling.

        Returns parsed JSON on success, ``None`` on non-retryable client errors.
        Raises on retryable or transient errors so tenacity can retry.
        """
        url = f"{self.gateway_url}{path}"
        try:
            resp = httpx.get(url, headers=self.headers, params=params or {}, timeout=timeout)
            if resp.status_code >= 400:
                if self._is_retryable_gateway_status(resp.status_code):
                    resp.raise_for_status()
                logger.warning("non_retryable_status", label=label, status=resp.status_code, url=path)
                return None
            return resp.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if self._is_retryable_gateway_status(status):
                logger.warning("retryable_http_error", label=label, status=status, url=path)
                raise
            logger.warning("non_retryable_http_error", label=label, status=status, url=path)
            return None
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.warning("transient_network_error", label=label, url=path, error=str(exc))
            raise
        except httpx.HTTPError as exc:
            logger.error("http_error", label=label, url=path, error=str(exc), exc_info=True)
            raise
        except Exception as exc:
            logger.error("unexpected_error", label=label, url=path, error=str(exc), exc_info=True)
            raise

    @staticmethod
    def _trim_buffer(buffer: list, max_size: int = 2000, trim_to: int = 1000) -> list:
        """Trim an in-memory buffer when it exceeds *max_size*, keeping the last *trim_to* items."""
        if len(buffer) > max_size:
            return buffer[-trim_to:]
        return buffer

    async def _fetch_many_bounded(
        self,
        tickers: list[str],
        fetch_one: Callable[[str], dict[str, Any] | None],
        process_one: Callable[[str, Any], Awaitable[int]],
        *,
        label: str,
        log: Any = None,
        concurrency: int = 3,
        rate_limit_delay: float = 0.5,
    ) -> int:
        """Fetch+process a batch of tickers with bounded concurrency.

        Shared per-ticker orchestration for connectors that fan out over a
        ticker list: bounds concurrency with a semaphore, rate-limits between
        requests, isolates one ticker's failure from the rest, and unwraps
        the standard gateway ``{"data": ...}`` envelope before handing the
        payload to *process_one* (which does the connector-specific parsing
        and persistence and returns the number of records stored).

        *log* is the calling connector's logger, so retry/failure events keep
        being emitted under that connector's logger name; it defaults to this
        module's logger.

        *fetch_one* is a synchronous call (run in a thread) that returns the
        raw gateway payload for one ticker, or ``None``.
        """
        log = log or logger
        semaphore = asyncio.Semaphore(concurrency)

        async def _fetch_one(ticker: str) -> int:
            async with semaphore:
                try:
                    data = await asyncio.to_thread(fetch_one, ticker)
                except Exception as e:
                    log.warning(f"{label}_retry_exhausted", ticker=ticker, error=str(e))
                    return 0
                finally:
                    await asyncio.sleep(rate_limit_delay)  # Rate limit between requests

                if not data or "data" not in data:
                    return 0

                payload = data["data"]
                if not payload:
                    return 0

                return await process_one(ticker, payload)

        results = await asyncio.gather(*[_fetch_one(t) for t in tickers], return_exceptions=True)
        stored = 0
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                log.error(f"{label}_ticker_failed", ticker=tickers[i], error=str(r))
            else:
                stored += r
        return stored
