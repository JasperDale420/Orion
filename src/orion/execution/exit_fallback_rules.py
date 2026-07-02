"""Deterministic exit rules — the primary exit policy, per bucket.

These rules run independent of the ML exit classifier. They exist because
the classifier was observed returning a constant 0.17 confidence on
2026-05-19 regardless of position return — leaving profitable positions
to decay through expiry. See FOLLOWUPS.md item #0.

As of 2026-07 these are the PRIMARY exit policy, not a safety net: every
position exits by disaster valve, stop-loss, profit target, 0DTE flatten,
expiry proximity, no-progress, max-hold, or drawdown-from-peak — in that
priority order. The ML classifier only runs when none of these fire.

Thresholds are per-bucket (0DTE / SHORT_SWING / SWING / POSITION), defined
in DEFAULT_BUCKET_PARAMS and overridable via ORION_EXIT_BUCKET_OVERRIDES
(JSON, deep-merged per bucket — see SystemSettings.exit_bucket_overrides).
The barriers double as the triple-barrier label definition for closed
trades, so executed trades label themselves.

Each rule returns an ExitSignal or None. `evaluate_fallback_rules` is the
composition entry point used by position_monitor.evaluate_exits — it
returns the first rule that fires, or None.

Position attributes consumed: `unrealized_pnl_pct` and `max_return_pct`
are read in PERCENT units (the convention `TrackedPosition` uses — see
`position_monitor.py:141`); rule thresholds remain FRACTIONS (e.g.
0.50 = 50%, matching the env-var defaults). Conversion happens inside
each rule.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


@dataclass
class ExitSignal:
    """Compatible shape with ExitPrediction used by execute_exits."""

    rule_id: str
    reason: str
    urgency: str  # IMMEDIATE, SOON, CONSIDER
    confidence: float = 1.0
    should_exit: bool = True


@dataclass(frozen=True)
class BucketExitParams:
    """Exit thresholds for one bucket. Fractions of option premium."""

    profit_target_pct: float  # exit at +X (0 disables)
    stop_loss_pct: float  # exit at -X (0 disables)
    min_dte: int  # exit when DTE <= this (0 disables)
    max_hold_hours: float  # exit when held longer (0 disables)
    max_drawdown_pct: float  # retracement from peak (0 disables)
    drawdown_min_peak_pct: float = 0.0  # peak must reach this before drawdown arms
    disaster_pct: float = 0.60  # unconditional exit at -X (0 disables)
    no_progress_minutes: float = 0.0  # dead-trade exit after N minutes (0 disables)
    no_progress_band_pct: float = 0.15  # ...when |return| is inside this band
    flatten_after_et: str | None = None  # "HH:MM" ET hard flatten (0DTE)


# Starter barriers from the 2026-07 recovery plan. Wide enough that
# bid/ask-bounce on marked-at-mid positions doesn't stop us out on noise;
# tight enough that theta never rides a loser to zero again.
DEFAULT_BUCKET_PARAMS: dict[str, BucketExitParams] = {
    "0DTE": BucketExitParams(
        profit_target_pct=0.40,
        stop_loss_pct=0.30,
        min_dte=0,  # a 0DTE is always at min DTE; flatten_after_et is the deadline
        max_hold_hours=0,
        max_drawdown_pct=0,
        no_progress_minutes=90,  # afternoon 0DTE theta ~1%/10min; dead trades bleed
        no_progress_band_pct=0.15,
        flatten_after_et="15:45",
    ),
    "SHORT_SWING": BucketExitParams(
        profit_target_pct=0.50,
        stop_loss_pct=0.35,
        min_dte=1,
        # ponytail: wall-clock hours, weekend-blind — a Thu entry fires Sat and
        # the close executes Monday open. Swap for trading-day math if that drift matters.
        max_hold_hours=54,
        max_drawdown_pct=0.40,
        drawdown_min_peak_pct=0.25,
    ),
    "SWING": BucketExitParams(
        profit_target_pct=0.60,
        stop_loss_pct=0.40,
        min_dte=2,  # final-week theta kills a thesis that hasn't resolved
        max_hold_hours=168,  # ~5 trading days incl. one weekend
        max_drawdown_pct=0.40,
        drawdown_min_peak_pct=0.30,
    ),
    "POSITION": BucketExitParams(
        profit_target_pct=0.75,
        stop_loss_pct=0.45,
        min_dte=2,
        max_hold_hours=336,
        max_drawdown_pct=0.40,
        drawdown_min_peak_pct=0.30,
    ),
}


def resolve_exit_params(
    bucket: str,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> BucketExitParams:
    """Params for a bucket: defaults merged with per-bucket env overrides.

    Unknown buckets get SWING defaults (the conservative middle).
    """
    base = DEFAULT_BUCKET_PARAMS.get(bucket, DEFAULT_BUCKET_PARAMS["SWING"])
    bucket_overrides = (overrides or {}).get(bucket, {})
    if not bucket_overrides:
        return base
    return replace(base, **bucket_overrides)


def _return_fraction(position: Any) -> float:
    """Position return as a fraction (TrackedPosition stores percent)."""
    return float(getattr(position, "unrealized_pnl_pct", 0.0) or 0.0) / 100.0


def _hours_held(position: Any) -> float | None:
    entry_time = getattr(position, "entry_time", None)
    if entry_time is None:
        return None
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=UTC)
    return (datetime.now(UTC) - entry_time).total_seconds() / 3600.0


class DisasterValveRule:
    """Unconditional exit on catastrophic loss (gaps, quote-outage marks)."""

    rule_id = "disaster_valve_v1"

    def __init__(self, disaster_pct: float) -> None:
        self.disaster_pct = disaster_pct

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.disaster_pct <= 0:
            return None
        ret = _return_fraction(position)
        if ret > -self.disaster_pct:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"disaster valve: return={ret:.1%} <= -{self.disaster_pct:.1%}",
            urgency="IMMEDIATE",
        )


class StopLossRule:
    """Exit when position loss crosses the stop threshold."""

    rule_id = "stop_loss_v1"

    def __init__(self, stop_loss_pct: float) -> None:
        self.stop_loss_pct = stop_loss_pct

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.stop_loss_pct <= 0:
            return None
        ret = _return_fraction(position)
        if ret > -self.stop_loss_pct:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"stop loss hit: return={ret:.1%} <= -{self.stop_loss_pct:.1%}",
            urgency="IMMEDIATE",
        )


class ProfitTargetRule:
    """Exit when position return crosses the target threshold."""

    rule_id = "profit_target_v1"

    def __init__(self, target_pct: float) -> None:
        self.target_pct = target_pct

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.target_pct <= 0:
            return None
        ret = _return_fraction(position)
        if ret < self.target_pct:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"profit target hit: return={ret:.1%} >= target={self.target_pct:.1%}",
            urgency="SOON",
        )


class ZeroDTEFlattenRule:
    """Hard-flatten 0DTE positions after a cutoff time (ET).

    A 0DTE held into the close is a coin-flip on pin risk plus guaranteed
    theta wipeout; nothing about the entry thesis survives 15:45.
    """

    rule_id = "zero_dte_flatten_v1"

    def __init__(self, flatten_after_et: str | None) -> None:
        self.flatten_after_et = flatten_after_et

    def should_exit(self, position: Any) -> ExitSignal | None:
        if not self.flatten_after_et:
            return None
        if getattr(position, "bucket", None) != "0DTE":
            return None
        hour, minute = (int(part) for part in self.flatten_after_et.split(":"))
        now_et = datetime.now(_ET)
        if (now_et.hour, now_et.minute) < (hour, minute):
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"0DTE flatten: {now_et:%H:%M} ET >= {self.flatten_after_et} ET cutoff",
            urgency="IMMEDIATE",
        )


class TimeToExpiryRule:
    """Exit when remaining time-to-expiry is below min_dte days.

    Requires `position.expiry_date` to be a tz-aware (or naive UTC) datetime.
    When `expiry_date is None` the rule no-ops — consumer code is responsible
    for populating the field.
    """

    rule_id = "time_to_expiry_v1"

    def __init__(self, min_dte: int) -> None:
        self.min_dte = min_dte

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.min_dte <= 0:
            return None
        expiry = getattr(position, "expiry_date", None)
        if expiry is None:
            # Can't evaluate without expiry; rule doesn't fire (no false trigger).
            return None
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        remaining = (expiry - datetime.now(UTC)).total_seconds() / 86400.0
        if remaining > self.min_dte:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"time to expiry: {remaining:.2f} days <= min_dte={self.min_dte}",
            urgency="IMMEDIATE",
        )


class NoProgressRule:
    """Exit a dead trade: held past the window with return stuck near zero.

    Intended for 0DTE, where a flat position is pure theta bleed — a trade
    that hasn't moved in 90 minutes has no momentum thesis left.
    """

    rule_id = "no_progress_v1"

    def __init__(self, no_progress_minutes: float, band_pct: float) -> None:
        self.no_progress_minutes = no_progress_minutes
        self.band_pct = band_pct

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.no_progress_minutes <= 0:
            return None
        hours = _hours_held(position)
        if hours is None or hours * 60.0 < self.no_progress_minutes:
            return None
        ret = _return_fraction(position)
        if abs(ret) > self.band_pct:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=(
                f"no progress: held {hours * 60.0:.0f}min >= {self.no_progress_minutes:.0f}min "
                f"with return={ret:.1%} inside ±{self.band_pct:.0%}"
            ),
            urgency="SOON",
        )


class MaxHoldRule:
    """Exit when the position has been held past the bucket's max hold time."""

    rule_id = "max_hold_v1"

    def __init__(self, max_hold_hours: float) -> None:
        self.max_hold_hours = max_hold_hours

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.max_hold_hours <= 0:
            return None
        hours = _hours_held(position)
        if hours is None or hours < self.max_hold_hours:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"max hold: held {hours:.1f}h >= {self.max_hold_hours:.0f}h",
            urgency="SOON",
        )


class DrawdownFromPeakRule:
    """Exit when position has retraced max_drawdown_pct from its peak return.

    Only fires when peak_return > 0 (don't protect a loss-trajectory peak)
    and, when min_peak_pct is set, only after the peak has reached it —
    a trailing stop that arms once the trade has actually worked.
    """

    rule_id = "drawdown_from_peak_v1"

    def __init__(self, max_drawdown_pct: float, min_peak_pct: float = 0.0) -> None:
        self.max_drawdown_pct = max_drawdown_pct
        self.min_peak_pct = min_peak_pct

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.max_drawdown_pct <= 0:
            return None
        # TrackedPosition stores these in PERCENT (200.0 = +200%);
        # convert to fractions so the retracement ratio is well-defined and
        # comparable to max_drawdown_pct (a fraction).
        peak_pct = float(getattr(position, "max_return_pct", 0.0) or 0.0)
        peak = peak_pct / 100.0
        if peak <= 0:
            return None  # never been profitable; no peak to defend
        if peak < self.min_peak_pct:
            return None  # trailing stop not armed yet
        current = _return_fraction(position)
        # Retracement as a fraction of peak: (peak - current) / peak.
        # peak=2.00 current=0.50 → (2.0 - 0.5)/2.0 = 0.75 retracement.
        retracement = (peak - current) / peak
        if retracement < self.max_drawdown_pct:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=(
                f"drawdown from peak: retracement={retracement:.1%} >= "
                f"max={self.max_drawdown_pct:.1%} (peak={peak:.1%}, current={current:.1%})"
            ),
            urgency="SOON",
        )


def evaluate_fallback_rules(
    position: Any,
    *,
    params: BucketExitParams,
) -> ExitSignal | None:
    """Run all exit rules in priority order. Return first signal that fires.

    Priority: disaster valve first (catastrophic loss trumps everything),
    then stop-loss (hard risk limit), profit target (most informative
    signal), 0DTE flatten and expiry (hard deadlines), no-progress and
    max-hold (time stops), drawdown-from-peak (trailing stop) last.
    """
    for rule in (
        DisasterValveRule(params.disaster_pct),
        StopLossRule(params.stop_loss_pct),
        ProfitTargetRule(params.profit_target_pct),
        ZeroDTEFlattenRule(params.flatten_after_et),
        TimeToExpiryRule(params.min_dte),
        NoProgressRule(params.no_progress_minutes, params.no_progress_band_pct),
        MaxHoldRule(params.max_hold_hours),
        DrawdownFromPeakRule(params.max_drawdown_pct, params.drawdown_min_peak_pct),
    ):
        signal = rule.should_exit(position)
        if signal is not None:
            return signal
    return None
