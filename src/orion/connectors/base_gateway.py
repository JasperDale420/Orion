"""Base class for Data Gateway connectors.

Consolidates shared boilerplate: gateway URL/key validation, header setup,
retryable status code handling, HTTP GET with retry, and in-memory buffer trimming.
"""

from __future__ import annotations

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
