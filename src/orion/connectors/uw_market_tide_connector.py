"""
UW Market Tide Connector.

Fetches market-wide options flow sentiment (net call/put premium) via Data Gateway.
"""

import asyncio
from datetime import date, datetime
from typing import Any

from orion.connectors.base_gateway import BaseGatewayConnector
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.connectors.uw_market_tide")


class UWMarketTideConnector(BaseGatewayConnector):
    """Fetches market tide (net premium intraday) via Data Gateway."""

    def __init__(self, gateway_url: str | None = None, gateway_key: str | None = None):
        super().__init__(gateway_url=gateway_url, gateway_key=gateway_key)
        self._latest_ticks: list[dict[str, Any]] = []

    def _fetch_market_tide(self, market_date: date | None = None) -> dict[str, Any] | None:
        """Fetch market tide for a date via Data Gateway."""
        params: dict[str, str] = {}
        if market_date:
            params["date"] = market_date.isoformat()
        return self._gateway_get(
            "/api/v1/uw/market/tide",
            params=params,
            label="market_tide",
        )

    async def fetch_and_store(self, market_date: date | None = None) -> int:
        """Fetch market tide and store all ticks."""
        try:
            data = await asyncio.to_thread(self._fetch_market_tide, market_date)
        except Exception as e:
            logger.warning("market_tide_retry_exhausted", error=str(e))
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
                logger.warning("market_tide_timestamp_parse_failed", timestamp=ts_str, exc_info=True)
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

    async def _persist_tick(self, record: dict[str, Any]) -> None:
        """Persist market tide tick in memory while centralized sinks are externalized."""
        self._latest_ticks.append(dict(record))
        self._latest_ticks = self._trim_buffer(self._latest_ticks)
