"""
UW IV Rank Connector.

Fetches IV rank and percentile via Data Gateway.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.config import system_settings

logger = logging.getLogger(__name__)
RETRYABLE_GATEWAY_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable_gateway_status(status_code: Optional[int]) -> bool:
    return status_code in RETRYABLE_GATEWAY_STATUS_CODES


class UWIVRankConnector:
    """Fetches IV rank/percentile via Data Gateway."""

    def __init__(self, gateway_url: Optional[str] = None, gateway_key: Optional[str] = None):
        self.gateway_url = (gateway_url or system_settings.data_gateway_url or "").strip().rstrip("/")
        if not self.gateway_url:
            raise ValueError("DATA_GATEWAY_URL/GATEWAY_URL setting not configured")
        self.gateway_key = (gateway_key or system_settings.data_gateway_api_key or "").strip()
        if not self.gateway_key:
            raise ValueError("DATA_GATEWAY_API_KEY/GATEWAY_API_KEY setting not configured")
        self.headers = {"X-Gateway-Key": self.gateway_key}
        self._latest_iv_rank_rows: list[Dict[str, Any]] = []

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _fetch_iv_rank(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch IV rank for a ticker via Data Gateway."""
        url = f"{self.gateway_url}/api/v1/uw/{ticker}/iv-rank"
        try:
            resp = httpx.get(url, headers=self.headers, timeout=30)
            if resp.status_code >= 400:
                if _is_retryable_gateway_status(resp.status_code):
                    resp.raise_for_status()
                logger.warning("Non-retryable iv-rank status=%s ticker=%s", resp.status_code, ticker)
                return None
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if _is_retryable_gateway_status(status):
                logger.warning("Retryable iv-rank HTTP error status=%s ticker=%s", status, ticker)
                raise
            logger.warning("Non-retryable iv-rank HTTP error status=%s ticker=%s", status, ticker)
            return None
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning(f"Transient network error fetching IV rank for {ticker}: {e}")
            raise
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch IV rank for {ticker}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch IV rank for {ticker}: {e}")
            return None

    async def fetch_and_store(self, tickers: List[str]) -> int:
        """Fetch IV rank for multiple tickers and store."""
        stored = 0
        now = datetime.now(timezone.utc)

        for ticker in tickers:
            try:
                data = await asyncio.to_thread(self._fetch_iv_rank, ticker)
            except Exception as e:
                logger.warning(f"IV-rank fetch exhausted retry budget for {ticker}: {e}")
                continue
            if not data or "data" not in data:
                continue

            iv_data = data["data"]
            if not iv_data:
                continue

            # Handle both list and dict responses from UW API
            if isinstance(iv_data, list):
                iv_data = iv_data[0] if iv_data else {}

            if not isinstance(iv_data, dict):
                logger.warning(f"Unexpected iv_data type for {ticker}: {type(iv_data)}")
                continue

            record = {
                "ticker": ticker,
                "ts_utc": now,
                "iv_rank": float(iv_data.get("iv_rank") or 0),
                "iv_percentile": float(iv_data.get("iv_percentile") or 0),
                "current_iv": float(iv_data.get("current_iv") or 0),
                "iv_52w_high": float(iv_data.get("iv_high") or 0),
                "iv_52w_low": float(iv_data.get("iv_low") or 0),
                "iv_30d": float(iv_data.get("iv_30d") or 0),
            }

            await self._persist_iv_rank(record)
            stored += 1
            await asyncio.sleep(0.5)  # Rate limit

        return stored

    async def _persist_iv_rank(self, record: Dict[str, Any]) -> None:
        """Persist latest IV rank rows in memory."""
        self._latest_iv_rank_rows.append(dict(record))
        if len(self._latest_iv_rank_rows) > 2000:
            self._latest_iv_rank_rows = self._latest_iv_rank_rows[-1000:]
