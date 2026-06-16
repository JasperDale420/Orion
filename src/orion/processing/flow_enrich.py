"""Shared UW-flow payload enrichment.

Both the Heber-poll path (``IngestionService._heber_row_to_event``) and the
Gateway-push path (``GatewayStreamClient._process_flow_message``) must produce a
BronzeEvent with identical enriched payload keys, or the two delivery paths
would feed downstream features inconsistent data depending on which path won the
race. These keys do NOT affect ``event_id`` (the dedup key), so a drift here
would not cause double candidates — but it WOULD cause feature inconsistency.
Centralizing the enrichment here is what keeps the two paths from diverging.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from typing import Any


def compute_dte(expiry_str: str, now: datetime) -> int | None:
    """Compute days-to-expiry from an expiry string (``YYYY-MM-DD...``)."""
    try:
        exp = _date.fromisoformat(expiry_str[:10])
        return max(0, (exp - now.date()).days)
    except (ValueError, TypeError):
        return None


def infer_aggressor(payload: dict[str, Any]) -> str:
    """Infer aggressor from the raw field or ask/bid side premium."""
    raw = payload.get("aggressor")
    if raw:
        return str(raw).upper()
    ask = float(payload.get("total_ask_side_prem") or 0)
    bid = float(payload.get("total_bid_side_prem") or 0)
    if ask > bid and ask > 0:
        return "ASK"
    if bid > ask and bid > 0:
        return "BID"
    return "MID"


def enrich_flow_payload(payload: dict[str, Any], now: datetime) -> str:
    """Enrich a flow payload in place; return the resolved ticker (may be "").

    Applies the same derived keys the poll path has always produced:
    ``ticker``, ``put_call`` (first letter), ``premium_usd``, ``expiry`` (str)
    + ``dte``, and ``aggressor_ind``. Mutates ``payload``.
    """
    ticker = str(payload.get("underlying") or payload.get("symbol") or "")
    if ticker:
        payload["ticker"] = ticker
    if "put_call" in payload:
        pc = str(payload["put_call"]).upper()
        payload["put_call"] = pc[0] if pc else ""
    if "premium" in payload:
        payload["premium_usd"] = float(payload["premium"])
    if "expiry" in payload:
        payload["expiry"] = str(payload["expiry"])
        dte = compute_dte(payload["expiry"], now)
        if dte is not None:
            payload["dte"] = dte
    payload["aggressor_ind"] = infer_aggressor(payload)
    return ticker
