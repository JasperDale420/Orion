"""
UW Max Pain Connector.

Fetches max pain strike levels by expiry via Data Gateway.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
from empire_core.http_client import create_http_client

from orion.clients.heber_reader import get_heber_reader
from orion.connectors.base_gateway import BaseGatewayConnector
from orion.shared.dataframe_utils import first_existing_column as _first_existing_column
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.connectors.uw_max_pain")


class UWMaxPainConnector(BaseGatewayConnector):
    """Fetches max pain strikes via Data Gateway."""

    def __init__(self, gateway_url: str | None = None, gateway_key: str | None = None):
        super().__init__(gateway_url=gateway_url, gateway_key=gateway_key)
        self._latest_max_pain_rows: list[dict[str, Any]] = []
        self._client = create_http_client(
            base_url=self.gateway_url,
            timeout=30.0,
            headers=self.headers,
        )

    def _fetch_max_pain(self, ticker: str) -> dict[str, Any] | None:
        """Fetch max pain for a ticker via Data Gateway."""
        return self._gateway_get(
            f"/api/v1/uw/options/{ticker}/max-pain",
            label=f"max_pain:{ticker}",
        )

    async def fetch_and_store(self, tickers: list[str]) -> int:
        """Fetch max pain for multiple tickers and store (bounded concurrency)."""
        today = date.today()
        semaphore = asyncio.Semaphore(3)

        async def _fetch_one(ticker: str) -> int:
            async with semaphore:
                try:
                    data = await asyncio.to_thread(self._fetch_max_pain, ticker)
                except Exception as e:
                    logger.warning("max_pain_retry_exhausted", ticker=ticker, error=str(e))
                    return 0
                finally:
                    await asyncio.sleep(0.5)  # Rate limit between requests

                if not data or "data" not in data:
                    return 0

                expiries = data["data"]
                if not expiries:
                    return 0

                # Get current price from database (more reliable than API)
                current_price = await self._get_current_price(ticker)
                count = 0

                for exp_data in expiries:
                    expiry_str = exp_data.get("expiry")
                    max_pain = exp_data.get("max_pain")
                    price = exp_data.get("price") or current_price

                    if not expiry_str or max_pain is None:
                        continue

                    try:
                        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                    except Exception:
                        logger.warning("max_pain_expiry_parse_failed", expiry=expiry_str, exc_info=True)
                        continue

                    distance_pct = None
                    if price and float(price) > 0:
                        distance_pct = ((float(max_pain) - float(price)) / float(price)) * 100

                    record = {
                        "ticker": ticker,
                        "expiry": expiry,
                        "date": today,
                        "max_pain_strike": float(max_pain),
                        "current_price": float(price) if price else None,
                        "distance_to_max_pain_pct": distance_pct,
                    }

                    await self._persist_max_pain(record)
                    count += 1

                return count

        results = await asyncio.gather(*[_fetch_one(t) for t in tickers], return_exceptions=True)
        stored = 0
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error("max_pain_ticker_failed", ticker=tickers[i], error=str(r))
            else:
                stored += r
        return stored

    async def _get_current_price(self, ticker: str) -> float | None:
        """Get latest price from Heber bars."""
        now = datetime.now(UTC)
        start = now - timedelta(days=7)

        try:
            bars_df = await asyncio.to_thread(
                get_heber_reader().read_bars,
                symbols=[ticker],
                start_time=start,
                asof_time=now,
            )
        except Exception as exc:
            logger.error("max_pain_heber_price_lookup_failed", ticker=ticker, error=str(exc), exc_info=True)
            return None

        if bars_df is None or bars_df.empty:
            return None

        normalized = _coerce_ticker_column(bars_df.copy())
        if "ticker" in normalized.columns:
            normalized = normalized[normalized["ticker"].astype(str).str.upper() == ticker.upper()]
        if normalized.empty:
            return None

        time_col = _first_existing_column(normalized, ["bar_start_ts_utc", "bar_start_ts", "ts_event", "ts_utc"])
        close_col = _first_existing_column(normalized, ["close", "c"])
        if time_col is None or close_col is None:
            return None

        ts_series = pd.to_datetime(normalized[time_col], utc=True, errors="coerce")
        close_series = pd.to_numeric(normalized[close_col], errors="coerce")
        temp = pd.DataFrame({"ts": ts_series, "close": close_series}).dropna(subset=["ts", "close"])
        if temp.empty:
            return None

        latest = temp.sort_values("ts").iloc[-1]
        return float(latest["close"])

    async def _persist_max_pain(self, record: dict[str, Any]) -> None:
        """Persist latest max pain rows in memory."""
        self._latest_max_pain_rows.append(dict(record))
        self._latest_max_pain_rows = self._trim_buffer(self._latest_max_pain_rows)


def _coerce_ticker_column(df: pd.DataFrame) -> pd.DataFrame:
    if "ticker" in df.columns:
        return df
    if "symbol" in df.columns:
        return df.assign(ticker=df["symbol"].astype(str).str.upper())
    if "instrument_key" in df.columns:
        return df.assign(ticker=df["instrument_key"].astype(str).str.split(":").str[-1].str.upper())
    return df
