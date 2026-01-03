import argparse
import asyncio
import hashlib
import os
from datetime import datetime, timezone
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


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _boolish(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in {"1", "t", "true", "y", "yes"}


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False)


def _flow_row_to_bronze(row: dict[str, Any]) -> BronzeEvent | None:
    # Whalehunter columns: executed_at, underlying_symbol, option_chain_id, side, strike, option_type, expiry,
    # underlying_price, nbbo_bid, nbbo_ask, price, size, premium, volume, open_interest, ...
    executed_at = row.get("executed_at")
    if not executed_at:
        return None
    ts = parse_timestamptz(str(executed_at), strict=True)

    ticker = row.get("underlying_symbol")
    if not isinstance(ticker, str) or not ticker:
        return None

    option_chain_id = row.get("option_chain_id")
    event_id = _sha(f"WH_UW_FLOW|{ticker}|{option_chain_id}|{executed_at}|{row.get('size')}|{row.get('price')}")

    option_type = str(row.get("option_type") or "").strip().lower()
    put_call = "C" if option_type == "call" else ("P" if option_type == "put" else None)

    payload = {
        # Fields the normalizer expects for UW_FLOW:
        "timestamp": ts.isoformat(),
        "ticker": ticker,
        "put_call": put_call,
        "expiry": row.get("expiry"),
        "strike_price": row.get("strike"),
        "price": row.get("price"),
        "size": row.get("size"),
        "premium": row.get("premium"),
        "bid": row.get("nbbo_bid"),
        "ask": row.get("nbbo_ask"),
        "underlying_price": row.get("underlying_price"),
        "open_interest": row.get("open_interest"),
        "volume": row.get("volume"),
        # Keep raw extras:
        "_wh": row,
    }

    return BronzeEvent(
        event_id=event_id,
        source="UW",
        source_event_id=str(option_chain_id) if option_chain_id else None,
        event_type="UW_FLOW",
        ticker=ticker,
        event_ts_utc=ts,
        received_ts_utc=datetime.now(timezone.utc),
        payload=payload,
        session="REG",
    )


def _darkpool_row_to_bronze(row: dict[str, Any]) -> BronzeEvent | None:
    # Whalehunter columns: ticker, executed_at, nbbo_ask, nbbo_bid, size, volume, premium, price, date, ... canceled
    executed_at = row.get("executed_at")
    if not executed_at:
        return None
    ts = parse_timestamptz(str(executed_at), strict=True)

    ticker = row.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        return None

    event_id = _sha(f"WH_UW_DARKPOOL|{ticker}|{executed_at}|{row.get('price')}|{row.get('size')}")

    payload = {
        # Fields the normalizer expects for UW_DARKPOOL:
        "executed_at": ts.isoformat(),
        "ticker": ticker,
        "price": row.get("price"),
        "size": row.get("size"),
        "volume": row.get("volume"),
        "premium": row.get("premium"),
        "nbbo_bid": row.get("nbbo_bid"),
        "nbbo_ask": row.get("nbbo_ask"),
        "trade_settlement": row.get("trade_settlement"),
        "sale_cond_codes": row.get("sale_cond_codes"),
        "trade_code": row.get("trade_code"),
        "canceled": _boolish(row.get("canceled")),
        "date": row.get("date"),
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


async def import_day(root: Path, day: str) -> None:
    flow_file = root / "options_flow" / f"bot-eod-report-{day}.csv"
    dark_file = root / "dark_pool" / f"dp-eod-report-{day}.csv"

    events: list[BronzeEvent] = []

    if flow_file.exists():
        df = _read_csv(flow_file)
        for row in df.to_dict(orient="records"):
            e = _flow_row_to_bronze(row)
            if e:
                events.append(e)

    if dark_file.exists():
        df = _read_csv(dark_file)
        for row in df.to_dict(orient="records"):
            e = _darkpool_row_to_bronze(row)
            if e:
                events.append(e)

    if not events:
        print(f"No files/events found for {day}")
        return

    await init_db()
    async with async_session_factory() as session:
        unique = await ingest_bronze_events(session, events, run_id=f"whalehunter_{day}", trace_id=f"wh_{day}")
        await persist_bronze_events(session, unique)
        await persist_silver_from_bronze(session, unique)
        await session.commit()

    print(f"Imported {len(unique)} events for {day} (from {len(events)} candidates)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="Path to whalehunter data/uw_raw")
    parser.add_argument("--day", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    asyncio.run(import_day(Path(args.root), args.day))


if __name__ == "__main__":
    main()
