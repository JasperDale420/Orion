"""
UW Greek Exposure Connector.

Fetches GEX (Gamma), VEX (Vanna), CEX (Charm) exposure data via Data Gateway.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from orion.connectors.base_gateway import BaseGatewayConnector
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.connectors.uw_greek_exposure")


class UWGreekExposureConnector(BaseGatewayConnector):
    """Fetches Greek exposure (GEX, Vanna, Charm) via Data Gateway."""

    def __init__(self, gateway_url: str | None = None, gateway_key: str | None = None):
        super().__init__(gateway_url=gateway_url, gateway_key=gateway_key)
        self._latest_exposures: list[dict[str, Any]] = []

    def _fetch_greek_exposure(self, ticker: str) -> dict[str, Any] | None:
        """Fetch greek exposure for a ticker via Data Gateway.

        Uses the aggregate ``/gex/{ticker}`` route. The per-strike
        ``/{ticker}/spot-exposures`` route returns empty data because the
        vendored UW SDK's ``SpotGreekExposuresByStrike`` model is a single-row
        model wrongly applied to a ``{"data":[...]}`` wrapper (2026-06-09 RCA).
        """
        return self._gateway_get(
            f"/api/v1/uw/gex/{ticker}",
            label=f"greek_exposure:{ticker}",
        )

    async def fetch_and_store(self, tickers: list[str]) -> int:
        """Fetch greek exposure for multiple tickers and store (bounded concurrency)."""
        now = datetime.now(UTC)
        semaphore = asyncio.Semaphore(3)
        results: list[int] = []

        async def _fetch_one(ticker: str) -> int:
            async with semaphore:
                try:
                    data = await asyncio.to_thread(self._fetch_greek_exposure, ticker)
                except Exception as e:
                    logger.warning("greek_exposure_retry_exhausted", ticker=ticker, error=str(e))
                    return 0
                finally:
                    await asyncio.sleep(0.5)  # Rate limit between requests

                if not data or "data" not in data:
                    return 0

                exposure_data = data["data"]
                if not exposure_data:
                    return 0

                # API returns: call_gamma, put_gamma, call_vanna, put_vanna, call_charm, put_charm
                # GEX = call_gamma + put_gamma (net gamma exposure)
                # VEX = call_vanna + put_vanna (net vanna exposure)
                # CEX = call_charm + put_charm (net charm exposure)
                if isinstance(exposure_data, list):
                    # /gex returns one aggregate row per day; upstream order is
                    # NOT guaranteed, so pick the most recent by timestamp (do
                    # not use [-1]). Summing across the series would be wrong.
                    latest = max(exposure_data, key=lambda e: str(e.get("timestamp") or ""))
                    total_gex_oi = float(latest.get("call_gamma") or 0) + float(latest.get("put_gamma") or 0)
                    total_gex_vol = 0
                    total_vex_oi = float(latest.get("call_vanna") or 0) + float(latest.get("put_vanna") or 0)
                    total_vex_vol = 0
                    total_cex_oi = float(latest.get("call_charm") or 0) + float(latest.get("put_charm") or 0)
                    total_cex_vol = 0
                    call_delta = float(latest.get("call_delta") or 0)
                    put_delta = float(latest.get("put_delta") or 0)
                    spot = 0
                else:
                    total_gex_oi = float(exposure_data.get("call_gamma") or 0) + float(
                        exposure_data.get("put_gamma") or 0
                    )
                    total_gex_vol = 0
                    total_vex_oi = float(exposure_data.get("call_vanna") or 0) + float(
                        exposure_data.get("put_vanna") or 0
                    )
                    total_vex_vol = 0
                    total_cex_oi = float(exposure_data.get("call_charm") or 0) + float(
                        exposure_data.get("put_charm") or 0
                    )
                    total_cex_vol = 0
                    call_delta = float(exposure_data.get("call_delta") or 0)
                    put_delta = float(exposure_data.get("put_delta") or 0)
                    spot = 0

                record = {
                    "ticker": ticker,
                    "ts_utc": now,
                    "gex_oi": total_gex_oi,
                    "gex_volume": total_gex_vol,
                    "vex_oi": total_vex_oi,
                    "vex_volume": total_vex_vol,
                    "cex_oi": total_cex_oi,
                    "cex_volume": total_cex_vol,
                    "call_delta": call_delta,
                    "put_delta": put_delta,
                    "call_fill_delta": 0,
                    "put_fill_delta": 0,
                    "spot_price": spot,
                }

                await self._persist_exposure(record)
                return 1

        results = await asyncio.gather(*[_fetch_one(t) for t in tickers], return_exceptions=True)
        stored = 0
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error("greek_exposure_ticker_failed", ticker=tickers[i], error=str(r))
            else:
                stored += r
        return stored

    async def _persist_exposure(self, record: dict[str, Any]) -> None:
        """Persist latest greek exposure samples in memory."""
        self._latest_exposures.append(dict(record))
        self._latest_exposures = self._trim_buffer(self._latest_exposures)
