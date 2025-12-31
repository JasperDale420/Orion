import argparse
import asyncio
import hashlib
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

# Load env and force DB URL for local host access (matches other scripts)
load_dotenv()
db_url = os.getenv("DB_URL", "")
if ":5432" in db_url:
    os.environ["DB_URL"] = db_url.replace(":5432", ":5440").replace("@timescaledb", "@localhost")

from orion.processing.ingest_pipeline import ingest_bronze_events
from orion.processing.persistence import persist_bronze_events, persist_silver_from_bronze
from orion.shared.utils import parse_timestamptz
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import BronzeEvent

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except Exception:
        return None


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False)


def _row_to_bronze(row: dict[str, Any]) -> BronzeEvent | None:
    ticker = row.get("ticker")
    executed_at = row.get("executed_at")
    price = _as_float(row.get("price"))
    size = _as_float(row.get("size"))

    if not isinstance(ticker, str) or not ticker:
        return None
    if not executed_at or price is None or size is None:
        return None

    ts = parse_timestamptz(str(executed_at), strict=True)
    event_id = _sha(f"WH_UW_DARKPOOL|{ticker}|{ts.isoformat()}|{price:.6f}|{size:.2f}")

    # Map into fields expected by Orion normalizer for UW_DARKPOOL.
    payload = {
        "ticker": ticker,
        "executed_at": ts.isoformat(),
        "price": float(price),
        "size": float(size),
        "volume": _as_float(row.get("volume")),
        "premium": _as_float(row.get("premium")),
        "nbbo_bid": _as_float(row.get("nbbo_bid")),
        "nbbo_ask": _as_float(row.get("nbbo_ask")),
        "venue": None,
        "conditions": [],
        "_wh": row,
    }

    return BronzeEvent(
        event_id=event_id,
        source="UW",
        source_event_id=None,
        event_type="UW_DARKPOOL",
        ticker=ticker,
        event_ts_utc=ts,
        received_ts_utc=datetime.now(timezone.utc),
        payload=payload,
        session="REG",
    )


async def import_day(root: Path, day: date) -> tuple[int, int]:
    dp_file = root / "dark_pool" / f"dp-eod-report-{day.isoformat()}.csv"
    if not dp_file.exists():
        return (0, 0)

    df = _read_csv(dp_file)
    candidates: list[BronzeEvent] = []
    for row in df.to_dict(orient="records"):
        e = _row_to_bronze(row)
        if e:
            candidates.append(e)

    if not candidates:
        return (0, 0)

    await init_db()
    async with async_session_factory() as session:
        unique = await ingest_bronze_events(
            session, candidates, run_id=f"wh_dp_{day.isoformat()}", trace_id=f"wh_dp_{day.isoformat()}"
        )
        await persist_bronze_events(session, unique)
        await persist_silver_from_bronze(session, unique)
        await session.commit()

    return (len(candidates), len(unique))


async def main_async(root: Path, start: date, days: int) -> None:
    for i in range(days):
        d = start + timedelta(days=i)
        cand, uniq = await import_day(root, d)
        if cand == 0 and uniq == 0:
            print(f"{d.isoformat()}: no file or no rows")
        else:
            print(f"{d.isoformat()}: imported {uniq} unique (from {cand} candidates)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="Path to whalehunter/data/uw_raw")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=1, help="Number of days to import")
    args = parser.parse_args()

    root = Path(args.root)
    start = date.fromisoformat(args.start_date)
    asyncio.run(main_async(root, start, args.days))


if __name__ == "__main__":
    main()
