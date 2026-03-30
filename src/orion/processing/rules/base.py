import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime
from typing import Any

from orion.storage.models_gold import CandidateTrade
from orion.storage.models_silver import SilverSignal


class TradingRule(ABC):
    """
    Abstract Base Class for all trading rules.
    """

    def __init__(self, rule_id: str):
        self.rule_id = rule_id

    @abstractmethod
    def evaluate(self, signal: SilverSignal) -> CandidateTrade | None:
        """
        Evaluate a signal and return a CandidateTrade if criteria met, else None.
        """
        pass

    def _create_candidate(
        self,
        signal: SilverSignal,
        direction: str,
        confidence: float = 1.0,
        evidence_extras: dict[Any, Any] | None = None,
    ) -> CandidateTrade:
        """
        Helper to create a deterministic candidate object.
        """
        ts = signal.signal_ts_utc
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        else:
            ts = ts.astimezone(UTC)

        def _rollup_id(*, ticker: str, period: str, ts_utc: datetime) -> str:
            return f"{ticker}|{period}|{ts_utc.isoformat()}"

        def _floor_to_minute(dt: datetime) -> datetime:
            return dt.replace(second=0, microsecond=0)

        def _floor_to_5min(dt: datetime) -> datetime:
            minute = (dt.minute // 5) * 5
            return dt.replace(minute=minute, second=0, microsecond=0)

        evidence = {
            "trigger_signal_id": signal.signal_id,
            "signal_ids": [signal.signal_id],
            "rule_id": self.rule_id,
            "event_ids": [],
            "rollup_ids": [
                _rollup_id(ticker=signal.ticker, period="1m", ts_utc=_floor_to_minute(ts)),
                _rollup_id(ticker=signal.ticker, period="5m", ts_utc=_floor_to_5min(ts)),
            ],
            "segments": [signal.signal_type],
        }

        if evidence_extras:
            # Merge list-like evidence fields without losing defaults.
            for key in ("event_ids", "rollup_ids", "segments", "signal_ids"):
                if key in evidence_extras:
                    extra_val = evidence_extras.get(key)
                    if isinstance(extra_val, list):
                        existing = evidence.get(key) or []
                        merged = list(existing)
                        for item in extra_val:
                            if item not in merged:
                                merged.append(item)
                        evidence[key] = merged
                    else:
                        evidence[key] = extra_val

            for k, v in evidence_extras.items():
                if k not in {"event_ids", "rollup_ids", "segments", "signal_ids"}:
                    evidence[k] = v

        # Deterministic ID
        raw = f"{signal.ticker}_{ts.isoformat()}_{self.rule_id}"
        c_id = hashlib.sha256(raw.encode()).hexdigest()

        candidate = CandidateTrade(
            candidate_id=c_id,
            ticker=signal.ticker,
            timestamp_utc=ts,
            rule_id=self.rule_id,
            direction=direction,
            confidence=confidence,
            evidence=evidence,
        )

        # Populate option fields from signal features for execution
        feat = signal.features or {}
        strike = feat.get("strike_price") or feat.get("strike")
        put_call_raw = feat.get("put_call", "")
        underlying = feat.get("underlying_price")
        premium_val = feat.get("premium") or feat.get("premium_usd")

        if strike is not None:
            candidate.strike_price = float(strike)
        if underlying is not None:
            candidate.underlying_price = float(underlying)
        if premium_val is not None:
            candidate.premium = float(premium_val)

        # Normalize option_type
        pc = str(put_call_raw).upper()
        if pc in ("C", "CALL"):
            candidate.option_type = "CALL"
        elif pc in ("P", "PUT"):
            candidate.option_type = "PUT"

        # Parse expiration and generate OCC symbol
        expiry_raw = feat.get("expiry") or feat.get("expiration_date")
        if expiry_raw:
            try:
                if isinstance(expiry_raw, str):
                    exp_date = date.fromisoformat(expiry_raw[:10])
                elif isinstance(expiry_raw, datetime):
                    exp_date = expiry_raw.date()
                elif isinstance(expiry_raw, date):
                    exp_date = expiry_raw
                else:
                    exp_date = None

                if exp_date:
                    candidate.expiration_date = datetime(exp_date.year, exp_date.month, exp_date.day, tzinfo=UTC)

                    # Generate OCC symbol: SYMBOL + YYMMDD + C/P + STRIKE*1000
                    if candidate.strike_price and candidate.option_type:
                        date_str = exp_date.strftime("%y%m%d")
                        opt_char = "C" if candidate.option_type == "CALL" else "P"
                        strike_int = int(candidate.strike_price * 1000)
                        candidate.option_symbol = f"{signal.ticker.upper()}{date_str}{opt_char}{strike_int:08d}"
            except (ValueError, TypeError):
                pass  # Best-effort; execution will skip if option_symbol is None

        return candidate
