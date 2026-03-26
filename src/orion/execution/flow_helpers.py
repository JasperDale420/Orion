"""Flow data helpers for the execution service.

Provides option chain parsing, recent flow fetching from Heber,
and position-to-flow scoping for exit rule evaluation.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pandas as pd

from orion.clients.heber_reader import get_heber_reader
from orion.config import system_settings
from orion.shared.dataframe_utils import first_existing_column as _first_existing_column
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.execution.flow_helpers")


# ---------------------------------------------------------------------------
# Option chain parsing
# ---------------------------------------------------------------------------


def _should_apply_options_exit_rules(position: Any) -> bool:
    """Guard options-only exit policy from being applied to equity positions."""
    option_chain = getattr(position, "option_chain", None)
    if isinstance(option_chain, str):
        return bool(option_chain.strip())
    return bool(option_chain)


def _parse_option_chain_contract(option_chain: str) -> tuple[str, str, float] | None:
    """Parse OCC option chain to comparable contract components.

    Returns tuple: (expiry_yyyy_mm_dd, put_call, strike_float)
    """
    match = re.search(r"(\d{6})([PC])(\d{8})$", option_chain.strip().upper())
    if not match:
        return None
    yymmdd, put_call, strike_raw = match.groups()
    expiry = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    strike = int(strike_raw) / 1000.0
    return (expiry, put_call, strike)


def _flow_matches_contract_components(flow: Any, contract: tuple[str, str, float]) -> bool:
    expiry, put_call, strike = contract
    flow_expiry = str(getattr(flow, "expiry", "") or "").strip()
    flow_put_call = str(getattr(flow, "put_call", "") or "").strip().upper()
    flow_strike_raw = getattr(flow, "strike", None)

    if not flow_expiry or not flow_put_call or flow_strike_raw is None:
        return False
    try:
        flow_strike = float(flow_strike_raw)
    except (TypeError, ValueError):
        return False

    return flow_expiry == expiry and flow_put_call == put_call and abs(flow_strike - strike) < 1e-6


def _scope_recent_flow_for_position(position: Any, recent_flow: list[Any]) -> list[Any]:
    """Scope ticker-level recent flow to the tracked option contract when possible.

    This reduces cross-contract contamination for same-underlying positions.
    """
    if not _should_apply_options_exit_rules(position):
        return recent_flow

    position_chain = (getattr(position, "option_chain", None) or "").strip()
    if not position_chain:
        return recent_flow

    position_contract = _parse_option_chain_contract(position_chain)
    scoped = []
    for flow in recent_flow:
        flow_chain = (getattr(flow, "option_chain", None) or "").strip()
        if flow_chain == position_chain:
            scoped.append(flow)
            continue
        if (
            not flow_chain
            and position_contract is not None
            and _flow_matches_contract_components(flow, position_contract)
        ):
            scoped.append(flow)
    return scoped


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _normalize_flow_ticker(value: Any) -> str | None:
    if value is None:
        return None
    ticker = str(value).strip().upper()
    if not ticker:
        return None
    if ":" in ticker:
        ticker = ticker.split(":")[-1]
    return ticker or None


def _normalize_put_call(value: Any) -> str:
    if value is None:
        return ""
    token = str(value).strip().upper()
    if token in {"C", "CALL"}:
        return "C"
    if token in {"P", "PUT"}:
        return "P"
    return token


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


# ---------------------------------------------------------------------------
# Heber flow fetching
# ---------------------------------------------------------------------------


def _prefer_heber_recent_flow_source() -> bool:
    return system_settings.execution_prefer_heber_recent_flow


async def _fetch_recent_flow_from_heber(ticker: str, minutes: int) -> list[Any] | None:
    reader = get_heber_reader()
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=minutes)
    try:
        frame = await asyncio.to_thread(
            reader.read_flow,
            symbols=[ticker],
            asof_time=now,
            start_time=cutoff,
        )
    except Exception as exc:
        logger.warning(f"Heber recent flow read failed for {ticker}: {exc}")
        return None

    if frame.empty:
        return []

    ticker_col = _first_existing_column(frame, ("ticker", "symbol", "instrument_key"))
    ts_col = _first_existing_column(frame, ("flow_ts_utc", "ts_event", "timestamp"))
    premium_col = _first_existing_column(frame, ("premium_usd", "premium"))
    if ticker_col is None or ts_col is None or premium_col is None:
        return []

    put_call_col = _first_existing_column(frame, ("put_call", "call_put"))
    aggressor_col = _first_existing_column(frame, ("aggressor", "aggressor_ind", "side"))
    sweep_col = _first_existing_column(frame, ("is_sweep", "sweep"))
    option_chain_col = _first_existing_column(frame, ("option_chain", "option_symbol"))
    expiry_col = _first_existing_column(frame, ("expiry",))
    strike_col = _first_existing_column(frame, ("strike",))
    underlying_col = _first_existing_column(frame, ("underlying_price", "underlying"))
    event_id_col = _first_existing_column(frame, ("event_id", "id"))

    work = frame.copy()
    work["_event_ts"] = pd.to_datetime(work[ts_col], utc=True, errors="coerce")
    work = work.dropna(subset=["_event_ts"]).sort_values("_event_ts", ascending=False).head(100)

    ticker_upper = ticker.upper()
    rows: list[Any] = []
    for idx, row in work.iterrows():
        flow_ticker = _normalize_flow_ticker(row.get(ticker_col))
        if flow_ticker != ticker_upper:
            continue

        premium = pd.to_numeric(row.get(premium_col), errors="coerce")
        if pd.isna(premium):
            continue

        underlying_price = None
        if underlying_col is not None:
            underlying_price_value = pd.to_numeric(row.get(underlying_col), errors="coerce")
            if not pd.isna(underlying_price_value):
                underlying_price = float(underlying_price_value)

        strike = None
        if strike_col is not None:
            strike_value = pd.to_numeric(row.get(strike_col), errors="coerce")
            if not pd.isna(strike_value):
                strike = float(strike_value)

        option_chain = str(row.get(option_chain_col)).strip() if option_chain_col and row.get(option_chain_col) else ""
        event_id = str(row.get(event_id_col)).strip() if event_id_col and row.get(event_id_col) else f"heber_flow_{idx}"

        rows.append(
            SimpleNamespace(
                event_id=event_id,
                ticker=flow_ticker,
                flow_ts_utc=row["_event_ts"].to_pydatetime(),
                premium_usd=float(premium),
                put_call=_normalize_put_call(row.get(put_call_col)) if put_call_col else "",
                aggressor=str(row.get(aggressor_col)).strip().upper()
                if aggressor_col and row.get(aggressor_col)
                else "",
                is_sweep=_coerce_bool(row.get(sweep_col)) if sweep_col else False,
                underlying_price=underlying_price,
                option_chain=option_chain,
                expiry=row.get(expiry_col) if expiry_col else None,
                strike=strike,
            )
        )
    return rows


async def fetch_recent_flow_for_ticker(ticker: str, minutes: int = 30) -> list[Any]:
    """Fetch recent flow data for a ticker for exit rule evaluation."""
    if _prefer_heber_recent_flow_source():
        heber_rows = await _fetch_recent_flow_from_heber(ticker=ticker, minutes=minutes)
        if heber_rows is None:
            return []
        return heber_rows

    logger.info(
        "Recent flow read skipped because Heber source is disabled",
        extra={"event_type": "RECENT_FLOW_HEBER_DISABLED", "ticker": ticker, "minutes": minutes},
    )
    return []
