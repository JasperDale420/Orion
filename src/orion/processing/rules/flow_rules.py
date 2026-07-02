"""Bucket entry rules — one rule per horizon bucket, 2026-07 overhaul.

The previous five rules were narrow pattern filters with exact premium bands
(e.g. 0DTE required $100–150k), one-direction-only restrictions, and
hour-of-day bonuses citing win rates that were never persisted anywhere. They
produced candidates with fictional confidences and starved two of the three
buckets of samples.

v2 collapses them into three bucket rules with a shared core:

- buyer-initiated conviction only: sweep + ASK/ABOVE_ASK aggressor
- trade WITH the flow, both sides: call sweep → buy calls, put sweep → buy puts
- premium FLOORS, no ceilings (bands were point-estimates from unrecorded
  backtests; premium is logged as a feature for the measurement loop instead)
- liquid-underlying allowlist (cheapest possible option-liquidity proxy)
- contract-volume floor and a delta band (0.25–0.60) when the fields are
  present — missing fields pass through and are flagged in evidence
- per-bucket signal-age budgets (a 2-minute-old 0DTE signal is dead; a
  15-minute-old multi-day swing thesis is fine)
- ET entry windows (no first-5-minutes chaos; 0DTE stops entering at 15:00)

Confidence is a flat 1.0: the rules ARE the gate; ranking comes later from
per-bucket models trained on realized outcomes, not hardcoded numbers.
"""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from orion.processing.rules.base import TradingRule
from orion.storage.models_gold import CandidateTrade, TradeDirection
from orion.storage.models_silver import SilverSignal

_ET = ZoneInfo("America/New_York")

# 0DTE trades only deep, liquid index-ETF chains; single-name 0DTE is a
# spread lottery.
ZERO_DTE_UNDERLYINGS = frozenset({"SPY", "QQQ", "IWM"})

_AGGRESSOR_BUY = ("ASK", "ABOVE_ASK")
_DELTA_BAND = (0.25, 0.60)  # excludes deep-OTM lottery contracts when delta is known


def _normalize_put_call_token(value: object) -> str | None:
    token = str(value or "").strip().upper()
    if token in {"C", "CALL", "CALLS"}:
        return "CALL"
    if token in {"P", "PUT", "PUTS"}:
        return "PUT"
    return token or None


def _resolve_limit_price(feat: dict) -> float:
    """Resolve the best available price for limit_price.

    UW flow data often has underlying_price=0.  Fall back through
    strike → option_price so the preflight sizing check has a
    usable number.  The execution engine fetches live option chain
    prices anyway; this is a best-effort signal-time estimate.
    """
    for key in ("underlying_price", "strike", "strike_price", "option_price"):
        val = feat.get(key)
        if val is not None:
            try:
                fval = float(val)
                if fval > 0:
                    return fval
            except (TypeError, ValueError):
                continue
    return 0.0


def _coerce_boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _signal_age_seconds(signal: SilverSignal, now: datetime) -> float | None:
    """Age of the underlying flow print. Prefers the flow's own timestamp
    (features.flow_ts_utc) over the silver row's — batch ingestion can stamp
    the row minutes after the print."""
    feat = signal.features or {}
    ts_raw = feat.get("flow_ts_utc")
    ts: datetime | None = None
    if isinstance(ts_raw, str):
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            ts = None
    elif isinstance(ts_raw, datetime):
        ts = ts_raw
    if ts is None:
        ts = signal.signal_ts_utc
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now - ts).total_seconds()


class BucketFlowRule(TradingRule):
    """Shared bucket-entry core; subclasses only set the bucket envelope."""

    def __init__(
        self,
        rule_id: str,
        *,
        dte_min: int,
        dte_max: int,
        min_premium: float,
        max_signal_age_seconds: float,
        entry_window_et: tuple[tuple[int, int], tuple[int, int]],
        min_contract_volume: float,
        allowlist: frozenset[str] | None,
        enforce_universe_and_window: bool = True,
    ):
        super().__init__(rule_id=rule_id)
        self.dte_min = dte_min
        self.dte_max = dte_max
        self.min_premium = min_premium
        self.max_signal_age_seconds = max_signal_age_seconds
        self.entry_window_et = entry_window_et
        self.min_contract_volume = min_contract_volume
        # None → resolve the configured liquid universe lazily (env-overridable).
        self._allowlist = allowlist
        # The smoke e2e injects synthetic tickers at arbitrary wall-clock
        # times; RuleEngine disables these two live-market checks in the
        # test stage only. Signal quality checks are never bypassed.
        self.enforce_universe_and_window = enforce_universe_and_window

    @property
    def allowlist(self) -> frozenset[str]:
        if self._allowlist is not None:
            return self._allowlist
        from orion.config import system_settings

        return frozenset(t.upper() for t in system_settings.liquid_universe)

    def evaluate(self, signal: SilverSignal) -> CandidateTrade | None:
        if signal.signal_type != "UW_FLOW":
            return None
        feat = signal.features
        if not feat:
            return None

        if self.enforce_universe_and_window and (signal.ticker or "").upper() not in self.allowlist:
            return None

        if not _coerce_boolish(feat.get("is_sweep", False)):
            return None

        aggressor = feat.get("aggressor_ind") or feat.get("aggressor") or ""
        if aggressor not in _AGGRESSOR_BUY:
            return None

        # Both directions, with the flow: we always BUY the option the sweep
        # bought. LONG = long premium; the contract type carries the view.
        put_call = _normalize_put_call_token(feat.get("put_call"))
        if put_call not in ("CALL", "PUT"):
            return None

        dte = feat.get("dte")
        if dte is None or not (self.dte_min <= int(dte) <= self.dte_max):
            return None

        premium = _coerce_float(feat.get("premium")) or 0.0
        if premium < self.min_premium:
            return None

        # Delta band when known; missing delta passes (flagged in evidence).
        delta = _coerce_float(feat.get("delta"))
        if delta is not None and not (_DELTA_BAND[0] <= abs(delta) <= _DELTA_BAND[1]):
            return None

        # Contract-volume floor when known; missing passes (flagged).
        volume = _coerce_float(feat.get("volume_contract") or feat.get("volume"))
        if volume is not None and volume < self.min_contract_volume:
            return None

        now = datetime.now(UTC)
        age_seconds = _signal_age_seconds(signal, now)
        if age_seconds is not None and age_seconds > self.max_signal_age_seconds:
            return None

        # ET entry window on the flow print's wall-clock time.
        ts = signal.signal_ts_utc
        if self.enforce_universe_and_window and ts is not None:
            ts_et = (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(_ET)
            start, end = self.entry_window_et
            if not (start <= (ts_et.hour, ts_et.minute) < end):
                return None

        candidate = self._create_candidate(
            signal=signal,
            direction=TradeDirection.LONG.value,
            confidence=1.0,
            evidence_extras={
                "event_ids": [feat.get("event_id")] if feat.get("event_id") else [],
                "source_event_id": feat.get("source_event_id"),
                "premium": premium,
                "premium_usd": premium,
                "dte": int(dte),
                "is_sweep": True,
                "aggressor": aggressor,
                "put_call": "C" if put_call == "CALL" else "P",
                "delta": delta,
                "delta_missing": delta is None,
                "volume_contract": volume,
                "volume_missing": volume is None,
                "signal_age_seconds": age_seconds,
                "reason": f"{self.rule_id}: {put_call} sweep ${premium / 1000:.0f}K DTE={int(dte)}",
            },
        )
        candidate.source = "UW"
        candidate.execution_params = {"limit_price": _resolve_limit_price(feat)}
        return candidate


class ZeroDTEBucketRule(BucketFlowRule):
    """0DTE index sweeps: SPY/QQQ/IWM only, fresh signals, no late entries."""

    def __init__(self, min_premium: float = 50_000.0, enforce_universe_and_window: bool = True):
        super().__init__(
            "rule_0dte_sweep_v2",
            dte_min=0,
            dte_max=0,
            min_premium=min_premium,
            max_signal_age_seconds=120,
            entry_window_et=((9, 35), (15, 0)),  # existing wind-down also blocks the last hour
            min_contract_volume=500,
            allowlist=ZERO_DTE_UNDERLYINGS,
            enforce_universe_and_window=enforce_universe_and_window,
        )


class ShortSwingBucketRule(BucketFlowRule):
    """1–3 DTE sweeps on the liquid universe."""

    def __init__(self, min_premium: float = 50_000.0, enforce_universe_and_window: bool = True):
        super().__init__(
            "rule_short_swing_v2",
            dte_min=1,
            dte_max=3,
            min_premium=min_premium,
            max_signal_age_seconds=300,
            entry_window_et=((9, 35), (15, 30)),
            min_contract_volume=200,
            allowlist=None,  # configured liquid universe
            enforce_universe_and_window=enforce_universe_and_window,
        )


class SwingBucketRule(BucketFlowRule):
    """4–14 DTE sweeps: higher conviction bar (longer-dated flow has more
    hedging noise), generous age budget (a multi-day thesis survives a
    15-minute-old signal)."""

    def __init__(self, min_premium: float = 100_000.0, enforce_universe_and_window: bool = True):
        super().__init__(
            "rule_swing_v2",
            dte_min=4,
            dte_max=14,
            min_premium=min_premium,
            max_signal_age_seconds=900,
            entry_window_et=((9, 30), (16, 0)),
            min_contract_volume=200,
            allowlist=None,  # configured liquid universe
            enforce_universe_and_window=enforce_universe_and_window,
        )
