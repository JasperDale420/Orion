"""
UW IV Rank Connector.

Fetches IV rank and percentile via Data Gateway.
"""

from datetime import UTC, datetime
from typing import Any

from orion.connectors.base_gateway import BaseGatewayConnector
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.connectors.uw_iv_rank")


class UWIVRankConnector(BaseGatewayConnector):
    """Fetches IV rank/percentile via Data Gateway."""

    def __init__(self, gateway_url: str | None = None, gateway_key: str | None = None):
        super().__init__(gateway_url=gateway_url, gateway_key=gateway_key)
        self._latest_iv_rank_rows: list[dict[str, Any]] = []

    def _fetch_iv_rank(self, ticker: str) -> dict[str, Any] | None:
        """Fetch IV rank for a ticker via Data Gateway."""
        return self._gateway_get(
            f"/api/v1/uw/{ticker}/iv-rank",
            label=f"iv_rank:{ticker}",
        )

    async def fetch_and_store(self, tickers: list[str]) -> int:
        """Fetch IV rank for multiple tickers and store (bounded concurrency)."""
        now = datetime.now(UTC)

        async def _process(ticker: str, iv_data: Any) -> int:
            # Handle both list and dict responses from UW API
            if isinstance(iv_data, list):
                iv_data = iv_data[0] if iv_data else {}

            if not isinstance(iv_data, dict):
                logger.warning("unexpected_iv_data_type", ticker=ticker, data_type=str(type(iv_data)))
                return 0

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
            return 1

        return await self._fetch_many_bounded(tickers, self._fetch_iv_rank, _process, label="iv_rank", log=logger)

    async def _persist_iv_rank(self, record: dict[str, Any]) -> None:
        """Persist latest IV rank rows in memory."""
        self._latest_iv_rank_rows.append(dict(record))
        self._latest_iv_rank_rows = self._trim_buffer(self._latest_iv_rank_rows)
