from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List

from orion.shared.utils import parse_timestamptz
from orion.storage.models import BronzeEvent
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_silver import SilverAlpacaBar, SilverDarkPool, SilverOptionFlow, SilverSignal, SilverUWAlert
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


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


def _safe_float(val: Any) -> float | None:
    """Safely convert a value to float, returning None if conversion fails."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int | None:
    """Safely convert a value to int, returning None if conversion fails."""
    if val is None:
        return None
    try:
        return int(float(val))  # Handle "10.0" -> 10
    except (ValueError, TypeError):
        return None


def _infer_aggressor(payload: dict) -> str | None:
    """Infer aggressor from UW API total_bid_side_prem vs total_ask_side_prem.

    - ASK: More premium on ask side (buyer aggressor, bullish)
    - BID: More premium on bid side (seller aggressor, bearish)
    - MID: Equal or no data
    """
    ask_prem = _safe_float(payload.get("total_ask_side_prem"))
    bid_prem = _safe_float(payload.get("total_bid_side_prem"))

    if ask_prem is None and bid_prem is None:
        return payload.get("aggressor")  # Fallback to explicit field if present

    ask_prem = ask_prem or 0.0
    bid_prem = bid_prem or 0.0

    if ask_prem > bid_prem:
        return "ASK"
    elif bid_prem > ask_prem:
        return "BID"
    else:
        return "MID"


async def _enrich_flows_with_greeks(flow_rows: List[dict]) -> None:
    """Enrich flow records with Alpaca Greeks.

    Fetches Greeks in batch for efficiency and updates flow_rows in place.
    """
    if not flow_rows:
        return

    try:
        from orion.connectors.alpaca_option_greeks_connector import get_connector

        connector = get_connector()

        # Collect option symbols for batch fetch
        symbols = [r.get("option_chain") for r in flow_rows if r.get("option_chain")]
        if not symbols:
            return

        # Batch fetch Greeks (max 100 per request)
        greeks_map = await connector.get_greeks_batch(symbols[:100])

        # Enrich flow rows with Greeks
        for row in flow_rows:
            option_chain = row.get("option_chain")
            if option_chain and option_chain in greeks_map:
                greeks = greeks_map[option_chain]
                row["delta_alpaca"] = greeks.get("delta")
                row["gamma_alpaca"] = greeks.get("gamma")
                row["theta_alpaca"] = greeks.get("theta")
                row["vega_alpaca"] = greeks.get("vega")
                row["rho_alpaca"] = greeks.get("rho")
                row["iv_alpaca"] = greeks.get("implied_volatility")

        logger.debug(f"Enriched {len(greeks_map)} flows with Alpaca Greeks")
    except Exception as e:
        logger.warning(f"Failed to enrich flows with Greeks: {e}")


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
            # Data quality: skip flow records with missing required fields
            # UW API uses 'price' for option price
            option_price = _safe_float(p.get("price") or p.get("option_price"))
            if option_price is None:
                continue
            flow_rows.append(
                {
                    "event_id": e.event_id,
                    "source_event_id": getattr(e, "source_event_id", None),
                    "ticker": getattr(e, "ticker", None) or p.get("ticker"),
                    "flow_ts_utc": _required_event_ts_utc(e, p, "flow_ts_utc"),
                    "put_call": p.get("put_call"),
                    "expiry": p.get("expiry"),
                    "strike": _safe_float(p.get("strike")),
                    "option_price": option_price,
                    # UW API uses total_size or size for contracts, premium or total_premium
                    "size_contracts": _safe_int(p.get("size_contracts") or p.get("total_size")),
                    "premium_usd": _safe_float(p.get("premium_usd") or p.get("premium") or p.get("total_premium")),
                    "bid": _safe_float(p.get("bid")),
                    "ask": _safe_float(p.get("ask")),
                    "underlying_price": _safe_float(p.get("underlying_price")),
                    # Infer aggressor from UW API total_bid_side_prem vs total_ask_side_prem
                    "aggressor": _infer_aggressor(p),
                    # UW uses has_sweep boolean - database column is boolean
                    "is_sweep": bool(p.get("is_sweep") or p.get("has_sweep")),
                    "flags_json": p.get("flags_json") if p.get("flags_json") != "null" else None,
                    "volume_contract": _safe_int(p.get("volume_contract") or p.get("volume")),
                    "open_interest": _safe_int(p.get("open_interest")),
                    # New UW fields - UW flow_alerts uses iv_start/iv_end instead of iv
                    "iv": _safe_float(p.get("iv") or p.get("implied_volatility") or p.get("iv_start")),
                    "volume_oi_ratio": _safe_float(p.get("volume_oi_ratio")),
                    "trade_count": _safe_int(p.get("trade_count")),
                    "alert_rule": p.get("alert_rule"),
                    "option_chain": p.get("option_chain"),
                    # ML Feature Fields
                    "ask_volume": _safe_int(p.get("ask_volume")),
                    "bid_volume": _safe_int(p.get("bid_volume")),
                    "delta_diff": _safe_float(p.get("delta_diff")),
                    "iv_change": _safe_float(p.get("iv_change")),
                    "multi_leg_vol_ratio": _safe_float(p.get("multi_leg_vol_ratio")),
                    "alert_name": p.get("alert_name"),
                    "noti_type": p.get("noti_type"),
                    "ingest": getattr(e, "ingest", None) or {},
                    "created_at_utc": datetime.now(timezone.utc),
                }
            )
        elif e.event_type == "ALPACA_BAR_1M":
            # Data quality: skip bars with invalid close values
            bar_close = p.get("close")
            if not bar_close or bar_close <= 0:
                continue
            bar_rows.append(
                {
                    "ticker": getattr(e, "ticker", None) or p.get("ticker"),
                    "bar_start_ts_utc": _required_event_ts_utc(e, p, "bar_start_ts_utc"),
                    "open": p.get("open"),
                    "high": p.get("high"),
                    "low": p.get("low"),
                    "close": bar_close,
                    "volume": p.get("volume"),
                    "vwap": p.get("vwap"),
                    "ingest": getattr(e, "ingest", None) or {},
                    "created_at_utc": datetime.now(timezone.utc),
                }
            )
        elif e.event_type == "UW_DARKPOOL":
            # Data quality: skip darkpool records with invalid trade_price
            trade_price = _safe_float(p.get("trade_price") or p.get("price"))
            if trade_price is None:
                continue
            dark_rows.append(
                {
                    "event_id": e.event_id,
                    "source_event_id": getattr(e, "source_event_id", None),
                    "ticker": getattr(e, "ticker", None) or p.get("ticker"),
                    "dark_ts_utc": _required_event_ts_utc(e, p, "dark_ts_utc"),
                    "trade_price": trade_price,
                    "size_shares": _safe_int(p.get("size_shares") or p.get("size")),
                    "venue": p.get("venue") or p.get("market_center"),
                    "conditions": p.get("conditions"),
                    "ingest": getattr(e, "ingest", None) or {},
                    "created_at_utc": datetime.now(timezone.utc),
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
                    "created_at_utc": datetime.now(timezone.utc),
                }
            )

    BATCH_SIZE = 1000

    if flow_rows:
        # Enrich flow records with Alpaca Greeks before persisting
        await _enrich_flows_with_greeks(flow_rows)
        for i in range(0, len(flow_rows), BATCH_SIZE):
            batch = flow_rows[i : i + BATCH_SIZE]
            stmt = insert(SilverOptionFlow).values(batch)
            stmt = stmt.on_conflict_do_nothing(index_elements=["event_id", "flow_ts_utc"])
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
            stmt = stmt.on_conflict_do_nothing(index_elements=["event_id", "dark_ts_utc"])
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
                "created_at_utc": datetime.now(timezone.utc),
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
                "created_at_utc": c.created_at_utc or datetime.now(timezone.utc),
            }
        )

    BATCH_SIZE = 1000
    for i in range(0, len(values), BATCH_SIZE):
        batch = values[i : i + BATCH_SIZE]
        stmt = insert(CandidateTrade).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=["candidate_id"])
        await session.execute(stmt)
