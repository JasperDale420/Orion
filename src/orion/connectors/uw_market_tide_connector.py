"""
UW Market Tide Connector.

Fetches market-wide options flow sentiment (net call/put premium) via Data Gateway.
"""

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.config import system_settings

logger = logging.getLogger(__name__)
RETRYABLE_GATEWAY_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable_gateway_status(status_code: Optional[int]) -> bool:
    return status_code in RETRYABLE_GATEWAY_STATUS_CODES


class UWMarketTideConnector:
    """Fetches market tide (net premium intraday) via Data Gateway."""

    def __init__(self, gateway_url: Optional[str] = None, gateway_key: Optional[str] = None):
        self.gateway_url = (gateway_url or system_settings.data_gateway_url or "").strip().rstrip("/")
        if not self.gateway_url:
            raise ValueError("DATA_GATEWAY_URL/GATEWAY_URL setting not configured")
        self.gateway_key = (gateway_key or system_settings.data_gateway_api_key or "").strip()
        if not self.gateway_key:
            raise ValueError("DATA_GATEWAY_API_KEY/GATEWAY_API_KEY setting not configured")
        self.headers = {"X-Gateway-Key": self.gateway_key}
        self._latest_ticks: list[Dict[str, Any]] = []

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _fetch_market_tide(self, market_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """Fetch market tide for a date via Data Gateway."""
        url = f"{self.gateway_url}/api/v1/uw/market/tide"
        params = {}
        if market_date:
            params["date"] = market_date.isoformat()

        try:
            resp = httpx.get(url, headers=self.headers, params=params, timeout=30)
            if resp.status_code >= 400:
                if _is_retryable_gateway_status(resp.status_code):
                    resp.raise_for_status()
                logger.warning("Non-retryable market tide status=%s", resp.status_code)
                return None
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if _is_retryable_gateway_status(status):
                logger.warning("Retryable market tide HTTP error status=%s", status)
                raise
            logger.warning("Non-retryable market tide HTTP error status=%s", status)
            return None
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning(f"Transient network error fetching market tide: {e}")
            raise
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch market tide: {e}")
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch market tide: {e}")
            return None

    async def fetch_and_store(self, market_date: Optional[date] = None) -> int:
        """Fetch market tide and store all ticks."""
        try:
            data = await asyncio.to_thread(self._fetch_market_tide, market_date)
        except Exception as e:
            logger.warning(f"Market tide fetch exhausted retry budget: {e}")
            return 0
        if not data or "data" not in data:
            return 0

        ticks = data["data"]
        if not ticks:
            return 0

        stored = 0
        for tick in ticks:
            ts_str = tick.get("timestamp")
            if not ts_str:
                continue

            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                continue

            record = {
                "ts_utc": ts,
                "date": ts.date(),
                "net_call_premium": float(tick.get("net_call_premium") or 0),
                "net_put_premium": float(tick.get("net_put_premium") or 0),
                "net_volume": int(tick.get("net_volume") or 0),
            }

            await self._persist_tick(record)
            stored += 1

        return stored

    async def _persist_tick(self, record: Dict[str, Any]) -> None:
        """Persist market tide tick in memory while centralized sinks are externalized."""
        self._latest_ticks.append(dict(record))
        if len(self._latest_ticks) > 2000:
            self._latest_ticks = self._latest_ticks[-1000:]
