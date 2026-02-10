"""
UW Max Pain Connector.

Fetches max pain strike levels by expiry via Data Gateway.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.clients.heber_reader import get_heber_reader
from orion.config import system_settings
from orion.shared.db_utils import db_write

logger = logging.getLogger(__name__)
RETRYABLE_GATEWAY_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable_gateway_status(status_code: Optional[int]) -> bool:
    return status_code in RETRYABLE_GATEWAY_STATUS_CODES


class UWMaxPainConnector:
    """Fetches max pain strikes via Data Gateway."""

    def __init__(self, gateway_url: Optional[str] = None, gateway_key: Optional[str] = None):
        self.gateway_url = gateway_url or system_settings.data_gateway_url
        self.gateway_key = gateway_key or system_settings.data_gateway_api_key
        self.headers = {"X-Gateway-Key": self.gateway_key} if self.gateway_key else {}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _fetch_max_pain(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch max pain for a ticker via Data Gateway."""
        url = f"{self.gateway_url}/api/v1/uw/{ticker}/max-pain"
        try:
            resp = httpx.get(url, headers=self.headers, timeout=30)
            if resp.status_code >= 400:
                if _is_retryable_gateway_status(resp.status_code):
                    resp.raise_for_status()
                logger.warning("Non-retryable max pain status=%s ticker=%s", resp.status_code, ticker)
                return None
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if _is_retryable_gateway_status(status):
                logger.warning("Retryable max pain HTTP error status=%s ticker=%s", status, ticker)
                raise
            logger.warning("Non-retryable max pain HTTP error status=%s ticker=%s", status, ticker)
            return None
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning(f"Transient network error fetching max pain for {ticker}: {e}")
            raise
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch max pain for {ticker}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch max pain for {ticker}: {e}")
            return None

    async def fetch_and_store(self, tickers: List[str]) -> int:
        """Fetch max pain for multiple tickers and store."""
        stored = 0
        today = date.today()

        for ticker in tickers:
            try:
                data = await asyncio.to_thread(self._fetch_max_pain, ticker)
            except Exception as e:
                logger.warning(f"Max pain fetch exhausted retry budget for {ticker}: {e}")
                continue
            if not data or "data" not in data:
                continue

            expiries = data["data"]
            if not expiries:
                continue

            # Get current price from database (more reliable than API)
            current_price = await self._get_current_price(ticker)

            for exp_data in expiries:
                expiry_str = exp_data.get("expiry")
                max_pain = exp_data.get("max_pain")
                price = exp_data.get("price") or current_price

                if not expiry_str or max_pain is None:
                    continue

                try:
                    expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                except Exception:
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
                stored += 1

            await asyncio.sleep(0.5)  # Rate limit

        return stored

    async def _get_current_price(self, ticker: str) -> Optional[float]:
        """Get latest price from Heber bars."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=7)

        try:
            bars_df = await asyncio.to_thread(
                get_heber_reader().read_bars,
                symbols=[ticker],
                start_time=start,
                asof_time=now,
            )
        except Exception as exc:
            logger.warning("max_pain_heber_price_lookup_failed ticker=%s error=%s", ticker, exc)
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

    async def _persist_max_pain(self, record: Dict[str, Any]) -> None:
        """Persist max pain to database."""

        async def write(session: Any) -> None:
            stmt = text(
                """
                INSERT INTO silver_max_pain (
                    ticker, expiry, date, max_pain_strike, current_price, distance_to_max_pain_pct
                ) VALUES (
                    :ticker, :expiry, :date, :max_pain_strike, :current_price, :distance_to_max_pain_pct
                )
                ON CONFLICT (ticker, expiry, date) DO UPDATE SET
                    max_pain_strike = EXCLUDED.max_pain_strike,
                    current_price = EXCLUDED.current_price,
                    distance_to_max_pain_pct = EXCLUDED.distance_to_max_pain_pct
            """
            )
            await session.execute(stmt, record)

        await db_write(write)


def _first_existing_column(df: pd.DataFrame, names: List[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _coerce_ticker_column(df: pd.DataFrame) -> pd.DataFrame:
    if "ticker" in df.columns:
        return df
    if "symbol" in df.columns:
        return df.assign(ticker=df["symbol"].astype(str).str.upper())
    if "instrument_key" in df.columns:
        return df.assign(ticker=df["instrument_key"].astype(str).str.split(":").str[-1].str.upper())
    return df
