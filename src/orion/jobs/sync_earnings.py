"""
Sync and backfill earnings calendar from UW API.

This job:
1. Syncs today's upcoming earnings (premarket/afterhours) - run daily
2. Backfills historical earnings for all tickers - run once
"""

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pandas as pd

from orion.clients.heber_reader import get_heber_reader
from orion.config import system_settings
from orion.core.logging_config import setup_logging

logger = logging.getLogger("orion.jobs.sync_earnings")


def _gateway_base_url() -> str:
    base_url = (system_settings.data_gateway_url or "").strip()
    if not base_url:
        raise ValueError("DATA_GATEWAY_URL/GATEWAY_URL setting not configured")
    return base_url.rstrip("/")


def _gateway_headers() -> dict[str, str]:
    api_key = (system_settings.data_gateway_api_key or "").strip()
    if not api_key:
        raise ValueError("DATA_GATEWAY_API_KEY/GATEWAY_API_KEY setting not configured")
    return {"X-Gateway-Key": api_key}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_gateway_date(raw: Any) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        from datetime import datetime as dt

        value = raw.strip()
        if not value:
            return None
        try:
            return dt.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


async def _fetch_gateway_earnings(endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    url = f"{_gateway_base_url()}{endpoint}"
    headers = _gateway_headers()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict):
        return []

    data = payload.get("data", [])
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


async def sync_todays_earnings() -> dict[str, int]:
    """Sync today's earnings via Data Gateway (premarket + afterhours)."""
    results = {"synced": 0, "errors": 0}
    today = date.today()

    # Fetch both premarket and afterhours earnings
    await _fetch_and_sync_earnings("/api/v1/uw/earnings/premarket", today, "premarket", results)
    await _fetch_and_sync_earnings("/api/v1/uw/earnings/afterhours", today, "afterhours", results)

    logger.info(f"Earnings sync complete: {results}")
    return results


async def _fetch_and_sync_earnings(
    endpoint: str, target_date: date, fallback_announce_time: str, results: dict[str, int]
) -> None:
    """Fetch earnings from Data Gateway endpoint and sync to database."""
    try:
        rows = await _fetch_gateway_earnings(
            endpoint=endpoint,
            params={"date": target_date.isoformat(), "limit": 100},
        )
        for row in rows:
            try:
                await _upsert_earnings_row(row=row, fallback_announce_time=fallback_announce_time)
                results["synced"] += 1
            except Exception as ex:
                logger.debug(f"Failed to upsert earnings row: {ex}")
                results["errors"] += 1
    except Exception as e:
        logger.error(f"Failed to fetch earnings from {endpoint}: {e}")
        results["errors"] += 1


async def backfill_ticker_earnings(ticker: str) -> int:
    """Backfill historical earnings for a single ticker."""
    count = 0
    try:
        rows = await _fetch_gateway_earnings(
            endpoint=f"/api/v1/uw/earnings/{ticker.upper()}",
            params={"limit": 100},
        )
        for row in rows:
            count += await _process_single_earnings_record(ticker=ticker, row=row)
    except Exception as e:
        logger.debug(f"Failed to fetch earnings for {ticker}: {e}")

    return count


async def _process_single_earnings_record(ticker: str, row: dict[str, Any]) -> int:
    """Process a single earnings record and upsert to database."""
    report_date = _parse_gateway_date(row.get("date") or row.get("report_date") or row.get("earnings_date"))
    if report_date is None:
        return 0

    try:
        announce = row.get("time") or row.get("announce_time") or row.get("report_time") or row.get("earnings_time")

        await _upsert_earnings_direct(
            ticker=ticker.upper(),
            report_date=report_date,
            announce_time=announce,
            eps_estimate=_to_float(row.get("eps_estimate") or row.get("street_mean_est") or row.get("eps_mean_est")),
            eps_actual=_to_float(row.get("eps_actual")),
            revenue_estimate=_to_float(row.get("revenue_estimate")),
            revenue_actual=_to_float(row.get("revenue_actual")),
        )
        return 1
    except Exception as ex:
        logger.debug(f"Failed to upsert earnings for {ticker}: {ex}")
        return 0


def _parse_report_date(report_date_raw: Any) -> date:
    """Parse report date from string or date object."""
    from datetime import datetime as dt

    if isinstance(report_date_raw, str):
        return dt.strptime(report_date_raw, "%Y-%m-%d").date()
    return report_date_raw


def _extract_announce_time(e: Any, UNSET: Any) -> str | None:  # noqa: N803
    """Extract announce time from earnings record."""
    if hasattr(e, "additional_properties") and e.additional_properties:
        return e.additional_properties.get("report_time")

    if hasattr(e, "report_time") and e.report_time and not isinstance(e.report_time, type(UNSET)):
        return str(e.report_time.value) if hasattr(e.report_time, "value") else str(e.report_time)

    return None


def _extract_eps_estimate(e: Any, UNSET: Any) -> float | None:  # noqa: N803
    """Extract EPS estimate from earnings record."""
    if not hasattr(e, "street_mean_est") or not e.street_mean_est:
        return None

    try:
        return float(e.street_mean_est) if not isinstance(e.street_mean_est, type(UNSET)) else None
    except (ValueError, TypeError):
        return None


async def backfill_all_earnings() -> dict[str, int]:
    """Backfill earnings for all unique tickers via Data Gateway."""
    results = {"tickers": 0, "earnings": 0, "errors": 0}
    tickers = await _get_backfill_tickers_from_heber_gold()
    logger.info(f"Backfilling earnings for {len(tickers)} tickers")

    for i, ticker in enumerate(tickers):
        try:
            count = await backfill_ticker_earnings(ticker)
            results["earnings"] += count
            results["tickers"] += 1
            if (i + 1) % 50 == 0:
                logger.info(f"Progress: {i + 1}/{len(tickers)} tickers, {results['earnings']} earnings")
            # Rate limit: 1 request per 100ms
            await asyncio.sleep(0.5)  # Rate limit: 2 requests per second
        except Exception as e:
            logger.debug(f"Failed to backfill {ticker}: {e}")
            results["errors"] += 1

    logger.info(f"Earnings backfill complete: {results}")
    return results


def _extract_tickers_from_frame(df: pd.DataFrame) -> set[str]:
    if df.empty:
        return set()

    tickers: set[str] = set()
    candidates = ["ticker", "symbol", "underlying", "instrument_key"]
    for column in candidates:
        if column not in df.columns:
            continue
        series = df[column].dropna().astype(str)
        if column == "instrument_key":
            series = series.str.split(":").str[-1]
        tickers.update(value.strip().upper() for value in series if value and value.strip())
    return tickers


def _coerce_to_frame(payload: Any) -> pd.DataFrame:
    if isinstance(payload, pd.DataFrame):
        return payload
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return pd.DataFrame()


async def _read_heber_gold_dataset(dataset: str) -> pd.DataFrame:
    now = datetime.now(UTC)
    reader = get_heber_reader()
    try:
        payload = await asyncio.to_thread(
            reader.read_gold_features,
            dataset=dataset,
            asof_time=now,
        )
    except Exception as exc:
        logger.warning(f"Failed to read Heber gold dataset {dataset}: {exc}")
        return pd.DataFrame()
    return _coerce_to_frame(payload)


async def _get_backfill_tickers_from_heber_gold() -> list[str]:
    outcomes_df = await _read_heber_gold_dataset("labels_alert_barriers")
    features_df = await _read_heber_gold_dataset("meta_label_features")
    tickers = _extract_tickers_from_frame(outcomes_df) | _extract_tickers_from_frame(features_df)
    return sorted(tickers)


async def _upsert_earnings(earnings_obj: Any, report_date: date, announce_time: str) -> None:
    """Upsert a single earnings record from API response object."""
    from orion.unusualwhales.types import UNSET

    ticker = getattr(earnings_obj, "ticker", None) or getattr(earnings_obj, "symbol", None)
    if not ticker or isinstance(ticker, type(UNSET)):
        return

    await _upsert_earnings_direct(
        ticker=str(ticker),
        report_date=report_date,
        announce_time=announce_time,
        eps_estimate=getattr(earnings_obj, "eps_estimate", None),
        eps_actual=getattr(earnings_obj, "eps_actual", None),
        revenue_estimate=getattr(earnings_obj, "revenue_estimate", None),
        revenue_actual=getattr(earnings_obj, "revenue_actual", None),
    )


async def _upsert_earnings_row(row: dict[str, Any], fallback_announce_time: str) -> None:
    ticker = (row.get("symbol") or row.get("ticker") or "").strip().upper()
    if not ticker:
        return

    report_date = _parse_gateway_date(row.get("date") or row.get("report_date") or row.get("earnings_date"))
    if report_date is None:
        return

    announce_time = (
        row.get("time")
        or row.get("announce_time")
        or row.get("report_time")
        or row.get("earnings_time")
        or fallback_announce_time
    )

    await _upsert_earnings_direct(
        ticker=ticker,
        report_date=report_date,
        announce_time=str(announce_time) if announce_time is not None else None,
        eps_estimate=_to_float(row.get("eps_estimate") or row.get("street_mean_est") or row.get("eps_mean_est")),
        eps_actual=_to_float(row.get("eps_actual")),
        revenue_estimate=_to_float(row.get("revenue_estimate")),
        revenue_actual=_to_float(row.get("revenue_actual")),
    )


async def _upsert_earnings_direct(
    ticker: str,
    report_date: date,
    announce_time: str | None = None,
    eps_estimate: float | None = None,
    eps_actual: float | None = None,
    revenue_estimate: int | None = None,
    revenue_actual: int | None = None,
) -> None:
    """Compatibility shim while earnings storage is centralized in Gateway/Heber."""
    _ = (ticker, report_date, announce_time, eps_estimate, eps_actual, revenue_estimate, revenue_actual)
    return None


async def get_earnings_for_ticker(ticker: str, as_of_date: date) -> dict[str, Any]:
    """Get earnings info for a ticker as of a specific date.

    Returns:
        {
            "days_to_earnings": int or None (days until next earnings),
            "is_post_earnings": bool (within 5 days after earnings),
            "next_earnings_date": date or None,
            "last_earnings_date": date or None,
        }
    """

    result = {
        "days_to_earnings": None,
        "is_post_earnings": False,
        "next_earnings_date": None,
        "last_earnings_date": None,
    }

    try:
        rows = await _fetch_gateway_earnings(
            endpoint=f"/api/v1/uw/earnings/{ticker.upper()}",
            params={"limit": 100},
        )
    except Exception as exc:
        logger.debug(f"Failed to fetch earnings timeline for {ticker}: {exc}")
        return result

    report_dates: list[date] = []
    for row in rows:
        report_date = _parse_gateway_date(row.get("date") or row.get("report_date") or row.get("earnings_date"))
        if report_date is not None:
            report_dates.append(report_date)

    if not report_dates:
        return result

    next_dates = [d for d in report_dates if d >= as_of_date]
    last_dates = [d for d in report_dates if d < as_of_date]

    if next_dates:
        next_date = min(next_dates)
        result["next_earnings_date"] = next_date
        result["days_to_earnings"] = (next_date - as_of_date).days

    if last_dates:
        last_date = max(last_dates)
        result["last_earnings_date"] = last_date
        days_since = (as_of_date - last_date).days
        result["is_post_earnings"] = 0 <= days_since <= 5

    return result


if __name__ == "__main__":
    import sys

    setup_logging()

    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        print("Running earnings backfill...")
        result = asyncio.run(backfill_all_earnings())
        print(f"Result: {result}")
    else:
        print("Running daily earnings sync...")
        result = asyncio.run(sync_todays_earnings())
        print(f"Result: {result}")
