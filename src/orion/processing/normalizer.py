import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional

from orion.shared.utils import parse_timestamptz


class NormalizationEngine:
    """
    Normalizes raw provider payloads into canonical Silver schemas (PRD 6.2).
    """

    @staticmethod
    def normalize_event(source: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Routes to specific normalization logic based on source and type.
        """
        if source == "UW":
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
    def _normalize_uw_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        PRD 6.2 Silver Schema: UW Options Flow
        """
        # Parse timestamp
        ts_str = payload.get("timestamp")
        flow_ts = parse_timestamptz(ts_str, strict=True)

        # Normalize flags
        is_sweep = str(payload.get("sweep", False)).lower() == "true"
        is_block = payload.get("trade_type") == "BLOCK"

        normalized = {
            "ticker": payload.get("ticker"),
            # Keep payload JSON-serializable (Bronze payload stored as JSON).
            "flow_ts_utc": flow_ts.isoformat(),
            "put_call": payload.get("put_call"),  # 'C' or 'P'
            "expiry": payload.get("expiry"),
            "strike": float(payload.get("strike_price", 0)),
            "option_price": float(payload.get("price", 0)),
            "size_contracts": int(float(payload.get("size", 0) or 0)),
            "bid": float(payload.get("bid", 0)),
            "ask": float(payload.get("ask", 0)),
            "underlying_price": float(payload.get("underlying_price", 0)),
            "aggressor": payload.get("aggressor", "UNK"),  # ASK/BID/MID
            "is_sweep": str(is_sweep).lower(),  # Stored as string 'true'/'false' to match model
            "flags_json": {"is_sweep": is_sweep, "is_block": is_block, "is_multi_leg": payload.get("multi_leg", False)},
            "open_interest": float(payload.get("open_interest", 0)),
            "volume_contract": float(payload.get("volume", 0)),
        }

        # Derived: premium_usd
        if "premium" in payload:
            normalized["premium_usd"] = float(payload["premium"])
        else:
            normalized["premium_usd"] = normalized["option_price"] * normalized["size_contracts"] * 100

        return normalized

    @staticmethod
    def _normalize_uw_darkpool(payload: Dict[str, Any]) -> Dict[str, Any]:
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
    def _normalize_uw_alert(payload: Dict[str, Any]) -> Dict[str, Any]:
        ts_str = payload.get("timestamp") or payload.get("created_at")
        alert_ts = parse_timestamptz(ts_str, strict=True)

        tags = payload.get("alert_tags") or payload.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]

        normalized: Dict[str, Any] = {
            "ticker": payload.get("ticker"),
            "alert_ts_utc": alert_ts.isoformat(),
            "put_call": payload.get("put_call") or payload.get("call_put"),
            "expiry": payload.get("expiry"),
            "strike": float(payload.get("strike") or payload.get("strike_price") or 0),
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
    def _normalize_alpaca_bar(payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        PRD 6.2 Silver Schema: Alpaca Bars 1m
        """
        ts_val = payload.get("t")
        bar_ts = None
        if isinstance(ts_val, str):
            try:
                bar_ts = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
            except Exception:
                pass

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
    def generate_event_id(source: str, event_type: str, ticker: Optional[str], ts: str, payload_subset: Dict) -> str:
        """
        PRD 6.1: backup ID generation if provider doesn't give one.
        sha256(source + event_type + ticker + event_ts_utc + stable_payload_subset)
        """
        raw_str = f"{source}|{event_type}|{ticker}|{ts}|{json.dumps(payload_subset, sort_keys=True)}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()
