"""Heber Gold data → BronzeEvent mapping for backtesting.

Converts raw bar and flow DataFrames from Heber Gold into BronzeEvent objects
and price DataFrames, used by MetaSearchAgent.evaluate_variant.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd

from orion.clients.heber_reader import get_heber_reader
from orion.core.solver_schema import EvaluationTask
from orion.shared.dataframe_utils import first_existing_column
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import make_json_safe
from orion.storage.models import BronzeEvent

logger = setup_struct_logger(__name__)


def normalize_ticker(value: Any) -> str | None:
    if value is None:
        return None
    ticker = str(value).strip().upper()
    if not ticker:
        return None
    if ":" in ticker:
        ticker = ticker.split(":")[-1]
    return ticker or None


async def read_heber_frames(task: EvaluationTask) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    reader = get_heber_reader()
    symbols = task.ticker_filter or []
    asof_time = task.end_time_utc
    try:
        bars_frame = await asyncio.to_thread(
            reader.read_bars,
            symbols=symbols,
            asof_time=asof_time,
            start_time=task.start_time_utc,
            end_time=task.end_time_utc,
        )
        flow_frame = await asyncio.to_thread(
            reader.read_flow,
            symbols=symbols or None,
            asof_time=asof_time,
            start_time=task.start_time_utc,
            min_premium=1000,
        )
        return bars_frame, flow_frame
    except Exception as exc:
        logger.warning(f"Heber read failed for meta-search events: {exc}", exc_info=True)
        return None


async def fetch_events_from_heber(
    task: EvaluationTask,
) -> tuple[list[Any], list[Any], dict[str, Any]] | None:
    frames = await read_heber_frames(task)
    if frames is None:
        return None
    bars_frame, flow_frame = frames
    alpaca_events, price_data = map_heber_bar_events(task, bars_frame)
    flow_events = map_heber_flow_events(task, flow_frame)
    return alpaca_events, flow_events, price_data


def map_heber_bar_events(task: EvaluationTask, bars_frame: pd.DataFrame) -> tuple[list[Any], dict[str, Any]]:
    if bars_frame.empty:
        return [], {}

    alpaca_events: list[Any] = []
    data_by_ticker: dict[str, list[dict[str, Any]]] = {}
    ticker_col = first_existing_column(bars_frame, ("ticker", "symbol", "instrument_key"))
    ts_col = first_existing_column(bars_frame, ("bar_start_ts", "bar_start_ts_utc", "ts_event"))
    open_col = first_existing_column(bars_frame, ("open", "o"))
    high_col = first_existing_column(bars_frame, ("high", "h"))
    low_col = first_existing_column(bars_frame, ("low", "l"))
    close_col = first_existing_column(bars_frame, ("close", "c"))
    volume_col = first_existing_column(bars_frame, ("volume", "v"))
    vwap_col = first_existing_column(bars_frame, ("vwap", "vw"))
    trades_col = first_existing_column(bars_frame, ("trade_count", "n"))
    required_cols = (ticker_col, ts_col, open_col, high_col, low_col, close_col, volume_col)
    if any(col is None for col in required_cols):
        return [], {}

    tickers_filter = {t.upper() for t in (task.ticker_filter or [])}
    for _, row in bars_frame.iterrows():
        mapped = _map_single_bar_row(
            row=row,
            tickers_filter=tickers_filter,
            ticker_col=ticker_col,
            ts_col=ts_col,
            open_col=open_col,
            high_col=high_col,
            low_col=low_col,
            close_col=close_col,
            volume_col=volume_col,
            vwap_col=vwap_col,
            trades_col=trades_col,
        )
        if mapped is None:
            continue
        event, series_row = mapped
        ticker = event.ticker
        alpaca_events.append(event)
        data_by_ticker.setdefault(ticker, []).append(series_row)
    return alpaca_events, _price_data_from_rows(data_by_ticker)


def _map_single_bar_row(
    row: Any,
    tickers_filter: set[str],
    ticker_col: str,
    ts_col: str,
    open_col: str,
    high_col: str,
    low_col: str,
    close_col: str,
    volume_col: str,
    vwap_col: str | None,
    trades_col: str | None,
) -> tuple[Any, dict[str, Any]] | None:
    ticker = normalize_ticker(row.get(ticker_col))
    if ticker is None:
        return None
    if tickers_filter and ticker not in tickers_filter:
        return None
    bar_ts = pd.to_datetime(row.get(ts_col), utc=True, errors="coerce")
    if pd.isna(bar_ts):
        return None
    ts_value = bar_ts.to_pydatetime()
    payload = make_json_safe(
        {
            "symbol": ticker,
            "ticker": ticker,
            "o": row.get(open_col),
            "h": row.get(high_col),
            "l": row.get(low_col),
            "c": row.get(close_col),
            "v": row.get(volume_col),
            "vw": row.get(vwap_col) if vwap_col else None,
            "t": ts_value,
            "n": row.get(trades_col) if trades_col else None,
        }
    )
    event = BronzeEvent(
        event_id=f"heber_bar_{ticker}_{int(ts_value.timestamp())}",
        event_type="ALPACA_BAR_1M",
        source="BACKTEST",
        event_ts_utc=ts_value,
        payload=payload,
        ticker=ticker,
    )
    series_row = {
        "timestamp": ts_value,
        "open": row.get(open_col),
        "high": row.get(high_col),
        "low": row.get(low_col),
        "close": row.get(close_col),
        "volume": row.get(volume_col),
    }
    return event, series_row


def _price_data_from_rows(data_by_ticker: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    price_data: dict[str, Any] = {}
    for ticker, bar_list in data_by_ticker.items():
        if not bar_list:
            continue
        df = pd.DataFrame(bar_list)
        price_data[ticker] = df.set_index("timestamp").sort_index()
    return price_data


def map_heber_flow_events(task: EvaluationTask, flow_frame: pd.DataFrame) -> list[Any]:
    if flow_frame.empty:
        return []

    ticker_col = first_existing_column(flow_frame, ("ticker", "symbol", "instrument_key"))
    ts_col = first_existing_column(flow_frame, ("flow_ts_utc", "ts_event", "timestamp"))
    premium_col = first_existing_column(flow_frame, ("premium_usd", "premium"))
    put_call_col = first_existing_column(flow_frame, ("put_call", "call_put"))
    sweep_col = first_existing_column(flow_frame, ("is_sweep", "sweep"))
    aggressor_col = first_existing_column(flow_frame, ("aggressor", "aggressor_ind", "side"))
    underlying_col = first_existing_column(flow_frame, ("underlying_price", "underlying"))
    expiry_col = first_existing_column(flow_frame, ("expiry",))
    event_id_col = first_existing_column(flow_frame, ("event_id", "id"))
    if not (ticker_col and ts_col and premium_col):
        return []

    flow_events: list[Any] = []
    tickers_filter = {t.upper() for t in (task.ticker_filter or [])}
    for idx, row in flow_frame.iterrows():
        event = _map_single_flow_row(
            row=row,
            idx=idx,
            tickers_filter=tickers_filter,
            ticker_col=ticker_col,
            ts_col=ts_col,
            premium_col=premium_col,
            put_call_col=put_call_col,
            sweep_col=sweep_col,
            aggressor_col=aggressor_col,
            underlying_col=underlying_col,
            expiry_col=expiry_col,
            event_id_col=event_id_col,
        )
        if event is not None:
            flow_events.append(event)
    return flow_events


def _map_single_flow_row(
    row: Any,
    idx: Any,
    tickers_filter: set[str],
    ticker_col: str,
    ts_col: str,
    premium_col: str,
    put_call_col: str | None,
    sweep_col: str | None,
    aggressor_col: str | None,
    underlying_col: str | None,
    expiry_col: str | None,
    event_id_col: str | None,
) -> Any | None:
    ticker = normalize_ticker(row.get(ticker_col))
    if ticker is None:
        return None
    if tickers_filter and ticker not in tickers_filter:
        return None
    flow_ts = pd.to_datetime(row.get(ts_col), utc=True, errors="coerce")
    if pd.isna(flow_ts):
        return None
    premium = pd.to_numeric(row.get(premium_col), errors="coerce")
    if pd.isna(premium):
        return None

    payload: dict[str, Any] = {
        "ticker": ticker,
        "premium": float(premium),
        "put_call": str(row.get(put_call_col)).strip().upper() if put_call_col and row.get(put_call_col) else "",
        "is_sweep": bool(row.get(sweep_col)) if sweep_col else False,
        "aggressor_ind": str(row.get(aggressor_col)).strip().upper()
        if aggressor_col and row.get(aggressor_col)
        else "",
        "underlying_price": row.get(underlying_col) if underlying_col else None,
    }
    _maybe_add_expiry_dte(payload, row.get(expiry_col) if expiry_col else None, flow_ts)
    payload = make_json_safe(payload)
    event_id = str(row.get(event_id_col)).strip() if event_id_col and row.get(event_id_col) else f"heber_flow_{idx}"
    return BronzeEvent(
        event_id=event_id,
        event_type="UW_FLOW",
        source="BACKTEST",
        event_ts_utc=flow_ts.to_pydatetime(),
        payload=payload,
        ticker=ticker,
    )


def _maybe_add_expiry_dte(payload: dict[str, Any], expiry_value: Any, flow_ts: Any) -> None:
    if not expiry_value:
        return
    payload["expiry"] = expiry_value
    try:
        exp_date = pd.to_datetime(expiry_value, errors="coerce")
        if not pd.isna(exp_date):
            payload["dte"] = (exp_date.date() - flow_ts.date()).days
    except Exception:
        return
