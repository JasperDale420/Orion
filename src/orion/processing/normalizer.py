import hashlib
import json
from datetime import datetime
from typing import Any

from orion.shared.logger import setup_struct_logger
from orion.shared.utils import parse_timestamptz

logger = setup_struct_logger(__name__)


def _coerce_boolish(value: Any) -> bool:
    """Return a stable boolean for common provider encodings."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_put_call_short(value: Any) -> str | None:
    """Normalize a put/call token to the one-character Silver contract."""
    token = str(value or "").strip().upper()
    if token in {"C", "CALL", "CALLS"}:
        return "C"
    if token in {"P", "PUT", "PUTS"}:
        return "P"
    return None


class NormalizationEngine:
    """
    Normalizes raw provider payloads into canonical Silver schemas (PRD 6.2).
    """

    @staticmethod
    def normalize_event(source: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Routes to specific normalization logic based on source and type.
        """
        if source in ("UW", "HEBER"):
            if event_type == "UW_FLOW":
                return NormalizationEngine._normalize_uw_flow(payload)
            elif event_type == "UW_DARKPOOL":
                return NormalizationEngine._normalize_uw_darkpool(payload)
            elif event_type == "UW_ALERT":
                return NormalizationEngine._normalize_uw_alert(payload)
        elif source == "ALPACA":
            if event_type == "ALPACA_BAR_1M":
                return NormalizationEngine._normalize_alpaca_bar(payload)

        # Fallback: return raw payload if no specific normalizer
        return payload

    @staticmethod
    def _normalize_uw_flow(payload: dict[str, Any]) -> dict[str, Any]:
        """
        PRD 6.2 Silver Schema: UW Options Flow
        """
        # Parse timestamp — three known sources of UW flow data, three
        # different field names:
        #   - Data-Gateway UW stream → "timestamp"
        #   - Legacy UW push → "created_at"
        #   - Heber Silver `feed=flow_alerts` rows (current default since
        #     the gateway-to-Heber migration) → "flow_ts_utc"
        # Without the `flow_ts_utc` branch, every Heber-sourced flow event
        # fell through `payload.get(...) or payload.get(...)` to None, and
        # `parse_timestamptz(None, strict=True)` silently returned now()
        # (strict only catches parse exceptions, not None input). Net
        # effect: silver UW_FLOW signals were timestamped at ingestion
        # time instead of event time, breaking the bar/flow time-
        # correlation that downstream feature engineering and rule
        # firing depend on. Found 2026-05-21 during the end-to-end
        # pipeline health check.
        ts_str = payload.get("timestamp") or payload.get("created_at") or payload.get("flow_ts_utc")
        flow_ts = parse_timestamptz(ts_str, strict=True)

        # Normalize sweep flag - support is_sweep (Heber Silver), has_sweep (Data-Gateway), sweep (legacy).
        is_sweep = _coerce_boolish(payload.get("is_sweep", payload.get("has_sweep", payload.get("sweep", False))))
        is_block = (
            _coerce_boolish(payload.get("has_floor", False)) or str(payload.get("trade_type") or "").upper() == "BLOCK"
        )
        is_multi_leg = _coerce_boolish(payload.get("has_multileg", False)) or _coerce_boolish(
            payload.get("multi_leg", False)
        )

        # Derive aggressor from total_ask_side_prem vs total_bid_side_prem
        # If ask_prem > bid_prem, buyers are initiating (ASK aggressor = bullish)
        # If bid_prem > ask_prem, sellers are initiating (BID aggressor = bearish)
        aggressor = payload.get("aggressor", "UNK")
        if aggressor == "UNK" or not aggressor:
            ask_prem = float(payload.get("total_ask_side_prem", 0) or 0)
            bid_prem = float(payload.get("total_bid_side_prem", 0) or 0)
            if ask_prem > bid_prem:
                aggressor = "ASK"
            elif bid_prem > ask_prem:
                aggressor = "BID"
            else:
                aggressor = "MID"

        # Normalize put/call - UW uses 'type' (C/P/call/put) or 'put_call'
        raw_put_call = payload.get("put_call") or payload.get("type") or ""
        put_call = _normalize_put_call_short(raw_put_call)
        if put_call is None:
            raw_put_call_upper = str(raw_put_call).upper()
            first_char = raw_put_call_upper[:1] if raw_put_call_upper else ""
            if first_char in ("P", "C"):
                put_call = first_char
                logger.error("put_call field had unexpected value %r, inferred %r", raw_put_call, put_call)
            else:
                put_call = "UNKNOWN"
                logger.error("put_call field had unrecognizable value %r, setting UNKNOWN", raw_put_call)

        normalized = {
            "ticker": payload.get("ticker"),
            # Keep payload JSON-serializable (Bronze payload stored as JSON).
            "flow_ts_utc": flow_ts.isoformat(),
            "put_call": put_call,
            # Legacy compatibility alias used by older tests/callers.
            "call_put": put_call,
            "expiry": payload.get("expiry"),
            "strike": float(payload.get("strike_price") or payload.get("strike") or 0),
            "option_price": float(payload.get("price", 0) or 0),
            "size_contracts": int(float(payload.get("size") or payload.get("total_size") or 0)),
            "bid": float(payload.get("bid", 0) or 0),
            "ask": float(payload.get("ask", 0) or 0),
            "underlying_price": float(payload.get("underlying_price", 0) or 0),
            "aggressor": aggressor,
            "is_sweep": str(is_sweep).lower(),  # Stored as string 'true'/'false' to match model
            "flags_json": {"is_sweep": is_sweep, "is_block": is_block, "is_multi_leg": is_multi_leg},
            # Legacy compatibility alias.
            "flags": {"is_sweep": is_sweep, "is_block": is_block, "is_multi_leg": is_multi_leg},
            "open_interest": float(payload.get("open_interest", 0) or 0),
            "volume_contract": float(payload.get("volume", 0) or 0),
            # New UW fields
            "iv": float(payload.get("iv_start") or payload.get("iv") or 0) or None,
            "volume_oi_ratio": float(payload.get("volume_oi_ratio") or payload.get("vol_oi_ratio") or 0) or None,
            "trade_count": int(payload.get("trade_count", 0) or 0) or None,
            "alert_rule": payload.get("alert_rule") or payload.get("rule_name"),
            "option_chain": payload.get("option_chain") or payload.get("symbol"),
            # ML Feature Fields
            "ask_volume": int(payload.get("ask_volume", 0) or 0) or None,
            "bid_volume": int(payload.get("bid_volume", 0) or 0) or None,
            "delta_diff": float(payload.get("diff", 0) or 0) or None,
            "iv_change": float(payload.get("iv_change", 0) or 0) or None,
            "multi_leg_vol_ratio": float(payload.get("multi_leg_vol_ratio", 0) or 0) or None,
            "alert_name": payload.get("name"),  # Alert classification
            "noti_type": payload.get("noti_type"),  # Notification type
        }

        # Derived: premium_usd - UW uses 'total_premium' or 'premium'
        if "total_premium" in payload:
            normalized["premium_usd"] = float(payload["total_premium"])
        elif "premium" in payload:
            normalized["premium_usd"] = float(payload["premium"])
        else:
            normalized["premium_usd"] = normalized["option_price"] * normalized["size_contracts"] * 100

        return normalized

    @staticmethod
    def _normalize_uw_darkpool(payload: dict[str, Any]) -> dict[str, Any]:
        """
        PRD 6.2 Silver Schema: UW Dark Pool
        """
        ts_str = payload.get("executed_at") or payload.get("timestamp") or payload.get("date")
        dark_ts = parse_timestamptz(ts_str, strict=True)

        conditions = payload.get("conditions", [])
        if isinstance(conditions, list):
            conditions_str = ",".join(str(c) for c in conditions)
        else:
            conditions_str = str(conditions) if conditions is not None else ""

        return {
            "ticker": payload.get("ticker"),
            "dark_ts_utc": dark_ts.isoformat(),
            "trade_price": float(payload.get("price", 0)),
            "size_shares": int(payload.get("size", 0)),
            "venue": payload.get("venue", "UNK"),
            "conditions": conditions_str,
        }

    @staticmethod
    def _normalize_uw_alert(payload: dict[str, Any]) -> dict[str, Any]:
        from orion.shared.utils import parse_occ_symbol

        ts_str = payload.get("timestamp") or payload.get("created_at")
        alert_ts = parse_timestamptz(ts_str, strict=True)

        tags = payload.get("alert_tags") or payload.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]

        # Try to get ticker - might be an OCC option symbol
        raw_ticker = payload.get("ticker") or payload.get("symbol")

        # Parse OCC symbol if it looks like one (e.g., SLV251231P00064000)
        occ_data = parse_occ_symbol(raw_ticker)

        # Use parsed underlying if available, else use raw ticker
        underlying = occ_data.get("underlying") if occ_data else raw_ticker

        normalized_put_call = _normalize_put_call_short(
            occ_data.get("put_call") or payload.get("put_call") or payload.get("call_put")
        )

        normalized: dict[str, Any] = {
            "ticker": underlying,  # Use underlying stock ticker
            "option_symbol": raw_ticker if occ_data else None,  # Store full OCC symbol
            "alert_ts_utc": alert_ts.isoformat(),
            "put_call": normalized_put_call,
            "expiry": occ_data.get("expiry") or payload.get("expiry"),
            "strike": occ_data.get("strike") or float(payload.get("strike") or payload.get("strike_price") or 0),
            "option_price": float(payload.get("price") or payload.get("option_price") or 0),
            "size_contracts": int(float(payload.get("size") or payload.get("size_contracts") or 0)),
            "premium_usd": float(payload.get("premium") or payload.get("premium_usd") or 0),
            "volume_contract": float(payload.get("volume") or payload.get("volume_contract") or 0),
            "open_interest": float(payload.get("open_interest") or 0),
            "flags_json": payload.get("flags") or {},
            "alert_tags": tags,
        }

        if normalized["premium_usd"] == 0 and normalized["option_price"] and normalized["size_contracts"]:
            normalized["premium_usd"] = float(normalized["option_price"]) * float(normalized["size_contracts"]) * 100

        return normalized

    @staticmethod
    def _normalize_alpaca_bar(payload: dict[str, Any]) -> dict[str, Any]:
        """
        PRD 6.2 Silver Schema: Alpaca Bars 1m
        """
        import pandas as pd

        ts_val = payload.get("t")
        bar_ts = None
        if isinstance(ts_val, pd.Timestamp):
            bar_ts = ts_val.to_pydatetime()
        elif isinstance(ts_val, datetime):
            bar_ts = ts_val
        elif isinstance(ts_val, str):
            try:
                bar_ts = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
            except Exception:
                logger.warning("Failed to parse bar timestamp: %r", ts_val)

        return {
            "ticker": payload.get("symbol") or payload.get("ticker"),
            "bar_start_ts_utc": bar_ts.isoformat() if bar_ts else None,
            "open": float(payload.get("o", 0)),
            "high": float(payload.get("h", 0)),
            "low": float(payload.get("l", 0)),
            "close": float(payload.get("c", 0)),
            "volume": float(payload.get("v", 0)),
            "vwap": float(payload.get("vw", 0)) if "vw" in payload else None,
        }

    @staticmethod
    def generate_event_id(
        source: str, event_type: str, ticker: str | None, ts: str, payload_subset: dict[str, Any]
    ) -> str:
        """
        PRD 6.1: backup ID generation if provider doesn't give one.
        sha256(source + event_type + ticker + event_ts_utc + stable_payload_subset)
        """
        raw_str = f"{source}|{event_type}|{ticker}|{ts}|{json.dumps(payload_subset, sort_keys=True)}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()
