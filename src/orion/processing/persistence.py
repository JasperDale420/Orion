from __future__ import annotations

from datetime import datetime
from typing import Any, List

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from orion.shared.utils import parse_timestamptz
from orion.storage.models import BronzeEvent
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_silver import SilverAlpacaBar, SilverDarkPool, SilverOptionFlow, SilverSignal, SilverUWAlert


async def persist_bronze_events(session: AsyncSession, events: List[BronzeEvent]) -> None:
    if not events:
        return

    values = []
    for e in events:
        values.append(
            {
                "event_id": e.event_id,
                "source": e.source,
                "source_event_id": getattr(e, "source_event_id", None),
                "event_type": e.event_type,
                "ticker": e.ticker,
                "trading_date": getattr(e, "trading_date", None),
                "session": e.session,
                "schema_version": getattr(e, "schema_version", None) or "v1",
                "event_ts_utc": e.event_ts_utc,
                "received_ts_utc": e.received_ts_utc,
                "payload": e.payload,
                "ingest": getattr(e, "ingest", None) or {},
            }
        )

    BATCH_SIZE = 1000
    for i in range(0, len(values), BATCH_SIZE):
        batch = values[i : i + BATCH_SIZE]
        stmt = insert(BronzeEvent).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=["event_id"])
        await session.execute(stmt)


def _required_event_ts_utc(e: BronzeEvent, payload: dict[str, Any], payload_key: str) -> datetime:
    if getattr(e, "event_ts_utc", None) is not None:
        return e.event_ts_utc

    raw = payload.get(payload_key)
    if raw is None:
        raise ValueError(f"Missing required timestamp: event_ts_utc and payload[{payload_key!r}] are both None")
    if isinstance(raw, datetime):
        return raw
    return parse_timestamptz(raw, strict=True)


async def persist_silver_from_bronze(session: AsyncSession, events: List[BronzeEvent]) -> None:
    if not events:
        return

    flow_rows = []
    bar_rows = []
    dark_rows = []
    alert_rows = []

    for e in events:
        p = e.payload or {}
        if e.event_type == "UW_FLOW":
            flow_rows.append(
                {
                    "event_id": e.event_id,
                    "source_event_id": getattr(e, "source_event_id", None),
                    "ticker": getattr(e, "ticker", None) or p.get("ticker"),
                    "flow_ts_utc": _required_event_ts_utc(e, p, "flow_ts_utc"),
                    "put_call": p.get("put_call"),
                    "expiry": p.get("expiry"),
                    "strike": p.get("strike"),
                    "option_price": p.get("option_price"),
                    "size_contracts": p.get("size_contracts"),
                    "premium_usd": p.get("premium_usd"),
                    "bid": p.get("bid"),
                    "ask": p.get("ask"),
                    "underlying_price": p.get("underlying_price"),
                    "aggressor": p.get("aggressor"),
                    "is_sweep": p.get("is_sweep"),
                    "flags_json": p.get("flags_json"),
                    "volume_contract": p.get("volume_contract"),
                    "open_interest": p.get("open_interest"),
                    "ingest": getattr(e, "ingest", None) or {},
                }
            )
        elif e.event_type == "ALPACA_BAR_1M":
            bar_rows.append(
                {
                    "ticker": getattr(e, "ticker", None) or p.get("ticker"),
                    "bar_start_ts_utc": _required_event_ts_utc(e, p, "bar_start_ts_utc"),
                    "open": p.get("open"),
                    "high": p.get("high"),
                    "low": p.get("low"),
                    "close": p.get("close"),
                    "volume": p.get("volume"),
                    "vwap": p.get("vwap"),
                    "ingest": getattr(e, "ingest", None) or {},
                }
            )
        elif e.event_type == "UW_DARKPOOL":
            dark_rows.append(
                {
                    "event_id": e.event_id,
                    "source_event_id": getattr(e, "source_event_id", None),
                    "ticker": getattr(e, "ticker", None) or p.get("ticker"),
                    "dark_ts_utc": _required_event_ts_utc(e, p, "dark_ts_utc"),
                    "trade_price": p.get("trade_price"),
                    "size_shares": p.get("size_shares"),
                    "venue": p.get("venue"),
                    "conditions": p.get("conditions"),
                    "ingest": getattr(e, "ingest", None) or {},
                }
            )
        elif e.event_type == "UW_ALERT":
            alert_rows.append(
                {
                    "event_id": e.event_id,
                    "source_event_id": getattr(e, "source_event_id", None),
                    "ticker": getattr(e, "ticker", None) or p.get("ticker"),
                    "alert_ts_utc": _required_event_ts_utc(e, p, "alert_ts_utc"),
                    "put_call": p.get("put_call"),
                    "expiry": p.get("expiry"),
                    "strike": p.get("strike"),
                    "option_price": p.get("option_price"),
                    "size_contracts": p.get("size_contracts"),
                    "premium_usd": p.get("premium_usd"),
                    "volume_contract": p.get("volume_contract"),
                    "open_interest": p.get("open_interest"),
                    "flags_json": p.get("flags_json"),
                    "alert_tags": p.get("alert_tags"),
                    "ingest": getattr(e, "ingest", None) or {},
                }
            )

    BATCH_SIZE = 1000

    if flow_rows:
        for i in range(0, len(flow_rows), BATCH_SIZE):
            batch = flow_rows[i : i + BATCH_SIZE]
            stmt = insert(SilverOptionFlow).values(batch)
            stmt = stmt.on_conflict_do_nothing(index_elements=["event_id"])
            await session.execute(stmt)
    if bar_rows:
        for i in range(0, len(bar_rows), BATCH_SIZE):
            batch = bar_rows[i : i + BATCH_SIZE]
            stmt = insert(SilverAlpacaBar).values(batch)
            stmt = stmt.on_conflict_do_nothing(index_elements=["ticker", "bar_start_ts_utc"])
            await session.execute(stmt)
    if dark_rows:
        for i in range(0, len(dark_rows), BATCH_SIZE):
            batch = dark_rows[i : i + BATCH_SIZE]
            stmt = insert(SilverDarkPool).values(batch)
            stmt = stmt.on_conflict_do_nothing(index_elements=["event_id"])
            await session.execute(stmt)
    if alert_rows:
        for i in range(0, len(alert_rows), BATCH_SIZE):
            batch = alert_rows[i : i + BATCH_SIZE]
            stmt = insert(SilverUWAlert).values(batch)
            stmt = stmt.on_conflict_do_nothing(index_elements=["event_id"])
            await session.execute(stmt)


async def persist_silver_signals(session: AsyncSession, signals: List[SilverSignal]) -> None:
    if not signals:
        return

    values = []
    for s in signals:
        values.append(
            {
                "signal_id": s.signal_id,
                "ticker": s.ticker,
                "signal_ts_utc": s.signal_ts_utc,
                "signal_type": s.signal_type,
                "features": s.features,
            }
        )

    BATCH_SIZE = 1000
    for i in range(0, len(values), BATCH_SIZE):
        batch = values[i : i + BATCH_SIZE]
        stmt = insert(SilverSignal).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=["signal_id"])
        await session.execute(stmt)


async def persist_candidates(session: AsyncSession, candidates: List[CandidateTrade]) -> None:
    if not candidates:
        return

    values = []
    for c in candidates:
        values.append(
            {
                "candidate_id": c.candidate_id,
                "ticker": c.ticker,
                "timestamp_utc": c.timestamp_utc,
                "rule_id": c.rule_id,
                "direction": c.direction,
                "confidence": c.confidence,
                "source": c.source,
                "execution_params": c.execution_params,
                "evidence": c.evidence,
                "created_at_utc": c.created_at_utc,
            }
        )

    BATCH_SIZE = 1000
    for i in range(0, len(values), BATCH_SIZE):
        batch = values[i : i + BATCH_SIZE]
        stmt = insert(CandidateTrade).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=["candidate_id"])
        await session.execute(stmt)
