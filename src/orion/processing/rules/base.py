import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, datetime
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

        return CandidateTrade(
            candidate_id=c_id,
            ticker=signal.ticker,
            timestamp_utc=ts,
            rule_id=self.rule_id,
            direction=direction,
            confidence=confidence,
            evidence=evidence,
        )
