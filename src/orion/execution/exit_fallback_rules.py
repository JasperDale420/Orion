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
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger(__name__)

_ET = ZoneInfo("America/New_York")

# The 0DTE flatten must leave runway for a marketable limit to fill before
# the session ends: the monitor re-checks every 30s while a 0DTE is open and
# the close escalates through repricing. 15 minutes mirrors the gap the fixed
# 15:45 cutoff assumed against a 16:00 close — widening it would move the
# normal-day cutoff earlier, so the retry policy adapts instead (see
# PositionMonitor._close_attempts_exhausted).
FLATTEN_CLOSE_BUFFER_MINUTES = 15


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
    # Calendar-day DTE floor, compared STRICTLY: exit once fewer than this many
    # whole calendar days remain to expiry (min_dte=1 -> exits on expiry day,
    # min_dte=2 -> exits with 1 day left). 0 disables. Same DTE convention as
    # the entry rules and bucket_for_dte, so an entry is never exited on the
    # DTE that admitted it.
    min_dte: int
    max_hold_hours: float  # exit when held longer (0 disables)
    max_drawdown_pct: float  # retracement from peak (0 disables)
    drawdown_min_peak_pct: float = 0.0  # peak must reach this before drawdown arms
    disaster_pct: float = 0.60  # unconditional exit at -X (0 disables)
    no_progress_minutes: float = 0.0  # dead-trade exit after N minutes (0 disables)
    no_progress_band_pct: float = 0.15  # ...when |return| is inside this band
    # "HH:MM" ET hard flatten for positions expiring TODAY (bucket 0DTE, or
    # any bucket whose expiry_date says today — defends against bucket
    # mislabels). Set on every bucket; None disables.
    flatten_after_et: str | None = "15:45"


# Starter barriers from the 2026-07 recovery plan. Wide enough that
# bid/ask-bounce on marked-at-mid positions doesn't stop us out on noise;
# tight enough that theta never rides a loser to zero again.
DEFAULT_BUCKET_PARAMS: dict[str, BucketExitParams] = {
    "0DTE": BucketExitParams(
        profit_target_pct=0.40,
        stop_loss_pct=0.30,
        min_dte=0,  # disabled: a 0DTE's deadline is flatten_after_et, not a DTE floor
        max_hold_hours=0,
        max_drawdown_pct=0,
        no_progress_minutes=90,  # afternoon 0DTE theta ~1%/10min; dead trades bleed
        no_progress_band_pct=0.15,
        flatten_after_et="15:45",
    ),
    "SHORT_SWING": BucketExitParams(
        profit_target_pct=0.50,
        stop_loss_pct=0.35,
        min_dte=1,  # exits on expiry day; the 15:45 ET flatten backs it up
        # ponytail: wall-clock hours, weekend-blind — a Thu entry fires Sat and
        # the close executes Monday open. Swap for trading-day math if that drift matters.
        max_hold_hours=54,
        max_drawdown_pct=0.40,
        drawdown_min_peak_pct=0.25,
    ),
    "SWING": BucketExitParams(
        profit_target_pct=0.60,
        stop_loss_pct=0.40,
        min_dte=2,  # out with 1 day left: final-week theta kills an unresolved thesis
        max_hold_hours=168,  # ~5 trading days incl. one weekend
        max_drawdown_pct=0.40,
        drawdown_min_peak_pct=0.30,
    ),
    "POSITION": BucketExitParams(
        profit_target_pct=0.75,
        stop_loss_pct=0.45,
        min_dte=2,  # out with 1 day left
        max_hold_hours=336,
        max_drawdown_pct=0.40,
        drawdown_min_peak_pct=0.30,
    ),
}


def bucket_for_dte(dte: int | None) -> str:
    """Classify a position's bucket from its DTE at entry.

    Unknown DTE gets SWING (the conservative middle) — same convention as
    resolve_exit_params.
    """
    if dte is None:
        return "SWING"
    if dte < 1:
        return "0DTE"
    if dte <= 3:
        return "SHORT_SWING"
    if dte <= 14:
        return "SWING"
    return "POSITION"


def resolve_exit_params(
    bucket: str,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> BucketExitParams:
    """Params for a bucket: defaults merged with per-bucket env overrides.

    Unknown buckets get SWING defaults (the conservative middle). Overrides
    carry the field's own units — notably `min_dte` is a CALENDAR-day floor
    compared strictly (exit once fewer than that many days remain), not a
    fraction-of-a-day threshold.
    """
    base = DEFAULT_BUCKET_PARAMS.get(bucket, DEFAULT_BUCKET_PARAMS["SWING"])
    bucket_overrides = (overrides or {}).get(bucket, {})
    if not bucket_overrides:
        return base
    return replace(base, **bucket_overrides)


def _xnys_session_on_or_before(day: date) -> date:
    """Latest XNYS session at or before ``day``. Raises if the calendar can't answer."""
    import exchange_calendars as xcals
    import pandas as pd

    cal = xcals.get_calendar("XNYS")
    session: date = cal.date_to_session(pd.Timestamp(day), direction="previous").date()
    return session


@lru_cache(maxsize=512)
def _cached_session_on_or_before(day: date) -> date:
    """`_xnys_session_on_or_before`, memoised. ONLY successful answers are cached.

    A raised lookup stores nothing, so a calendar outage is retried on the next
    evaluation instead of freezing a degraded answer in for the life of the
    process. The one-warning-per-expiry log lives here for the same reason:
    the cache makes it fire once per contract rather than every monitor cycle.
    """
    session = _xnys_session_on_or_before(day)
    if session != day:
        logger.warning(
            "Contract expiry is not a trading session; time stop deadline pulled back",
            extra={
                "event": "expiry_not_a_session",
                "expiry": day.isoformat(),
                "effective_deadline": session.isoformat(),
            },
        )
    return session


# Whether the last calendar lookup failed. Drives edge-triggered alerting: the
# monitor re-evaluates every tracked position every cycle, so logging each
# degraded lookup would bury the incident under its own repetitions.
_calendar_degraded = False


def _last_tradeable_date(expiry: date) -> date:
    """The last date the contract can actually be closed on.

    Normally ``expiry`` itself — every listed contract expires on a session, and
    all 43 distinct expiries Orion has traded are XNYS sessions. It matters only
    when a malformed or non-standard expiry lands on a weekend or exchange
    holiday: counting days to a closed date would leave a min_dte=1 time stop
    firing for the first time on a day ``close_position`` cannot submit, with
    the next evaluation already past expiry — silently dropping the last
    session the position could have been exited on.

    When the calendar cannot answer, the deadline degrades to the nearest
    weekday at or before ``expiry`` rather than raising: a dead exchange
    calendar has already blocked every option exit once, and this rule must
    never be the thing that raises. The degraded answer is deliberately NOT
    cached, so the holiday case is resolved correctly as soon as the calendar
    recovers. A degraded deadline cannot see holidays, so entering and leaving
    that state each raise one alert — the operator, not this rule, closes a
    holiday-dated contract during a sustained outage. Backing the deadline off
    a whole weekday instead would exit every healthy position a session early,
    which for a 1-DTE SHORT_SWING is the same-day dump this rule exists to
    prevent.
    """
    global _calendar_degraded
    try:
        effective = _cached_session_on_or_before(expiry)
    except Exception as e:
        fallback = expiry
        while fallback.weekday() >= 5:  # Saturday/Sunday are never tradeable
            fallback -= timedelta(days=1)
        if not _calendar_degraded:
            _calendar_degraded = True
            logger.error(
                "Exchange calendar unavailable; expiry time stops are running on weekday-only "
                "deadlines and cannot see holidays — close any holiday-dated contract by hand",
                extra={
                    "event": "expiry_calendar_unavailable",
                    "expiry": expiry.isoformat(),
                    "degraded_deadline": fallback.isoformat(),
                    "error": str(e),
                },
            )
        return fallback
    if _calendar_degraded:
        _calendar_degraded = False
        logger.info(
            "Exchange calendar resolved again; expiry time stops are session-accurate",
            extra={"event": "expiry_calendar_recovered", "expiry": expiry.isoformat()},
        )
    return effective


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


def expires_today(position: Any, now_et: datetime | None = None) -> bool:
    """True when the position's contract expires in today's ET session.

    Reads the bucket label first, then `expiry_date` as defense in depth:
    the label can be wrong (missing entry context, DB hiccup at reload)
    and a contract that really expires today must still be treated as
    one. Consumers use this both to flatten and to decide how long a
    failing close may be left alone.
    """
    now_et = now_et or datetime.now(_ET)
    if getattr(position, "bucket", None) == "0DTE":
        return True
    expiry = getattr(position, "expiry_date", None)
    if expiry is None:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    # Expiry is stored midnight UTC and encodes the contract's CALENDAR
    # date — converting it to ET would shift it to the prior evening and
    # flatten a tomorrow-expiring position a day early. Compare the UTC
    # calendar date to today-in-ET.
    return bool(expiry.astimezone(UTC).date() <= now_et.date())


def racing_expiry(position: Any, now_et: datetime | None = None) -> bool:
    """`expires_today`, minus contracts whose expiry has already passed.

    A row still tracked after its expiry date is stale, not a position
    with a deadline ahead of it — closing it can only fail. Callers that
    trade off retry pressure against the deadline use this; the flatten
    rule itself keeps the looser test so a mislabeled position is still
    caught. A missing expiry falls back to the bucket label, because the
    positions most likely to reach expiry unexited are exactly the ones
    whose entry context went missing.
    """
    now_et = now_et or datetime.now(_ET)
    if not expires_today(position, now_et):
        return False
    expiry = getattr(position, "expiry_date", None)
    if expiry is None:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return bool(expiry.astimezone(UTC).date() == now_et.date())


class ZeroDTEFlattenRule:
    """Hard-flatten 0DTE positions after a cutoff time (ET).

    A 0DTE held into the close is a coin-flip on pin risk plus guaranteed
    theta wipeout; nothing about the entry thesis survives 15:45. Worse,
    an ITM contract left at expiry is auto-exercised into equity Orion
    can neither see nor sell.

    The effective cutoff is the EARLIER of `flatten_after_et` and
    `session_close` minus the fill buffer, so a 13:00 ET half-day
    flattens at 12:45 instead of waiting for a 15:45 that never comes.
    `session_close` is supplied by the caller (see resolve_session_close)
    to keep this rule pure and injectable; None leaves the fixed cutoff
    in force.
    """

    rule_id = "zero_dte_flatten_v1"

    def __init__(self, flatten_after_et: str | None, session_close: datetime | None = None) -> None:
        self.flatten_after_et = flatten_after_et
        self.session_close = session_close

    def _cutoff(self, now_et: datetime) -> datetime:
        """Effective flatten deadline for the session `now_et` falls in."""
        hour, minute = (int(part) for part in str(self.flatten_after_et).split(":"))
        cutoff = now_et.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if self.session_close is None:
            return cutoff
        close = self.session_close
        if close.tzinfo is None:
            close = close.replace(tzinfo=UTC)
        return min(cutoff, close.astimezone(_ET) - timedelta(minutes=FLATTEN_CLOSE_BUFFER_MINUTES))

    def should_exit(self, position: Any) -> ExitSignal | None:
        if not self.flatten_after_et:
            return None
        now_et = datetime.now(_ET)
        if not expires_today(position, now_et):
            return None
        cutoff = self._cutoff(now_et)
        if now_et < cutoff:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"0DTE flatten: {now_et:%H:%M} ET >= {cutoff:%H:%M} ET cutoff",
            urgency="IMMEDIATE",
        )


class TimeToExpiryRule:
    """Exit when CALENDAR days-to-expiry drops below the bucket's min_dte floor.

    DTE is counted the way the entry rules, `bucket_for_dte` and the entry caps
    count it: whole calendar days between today's New York date and the
    contract's expiry date. Wall-clock hours are the wrong unit here — an entry
    rule that admits 1-3 DTE hands the monitor a position whose remaining
    fraction of a day is 0.4, and comparing that fraction against the same
    integer floor exits it minutes after entry for the spread. (5 such exits on
    2026-08-18, all within 12 minutes of entry, all pure friction.)

    The comparison is STRICT: a position is exited once FEWER than min_dte days
    remain, so min_dte=1 leaves the whole expiry-day session tradeable and
    min_dte=2 exits with one day left. min_dte=0 disables the rule (0DTE, whose
    deadline is `flatten_after_et`).

    "Today" is the New York calendar date, matching `expires_today`: the UTC
    date rolls over at 20:00 ET and would arm the time stop overnight, a
    session early. Expiry itself is stored at midnight UTC and encodes the
    contract's calendar date, so it is read as a UTC date and never converted
    to ET (which would shift it to the prior evening, a day early). Days are
    counted to `_last_tradeable_date`, so the deadline is always a day the
    position can actually be closed on.

    Requires `position.expiry_date` to be a tz-aware (or naive UTC) datetime.
    When `expiry_date is None` the rule no-ops — consumer code is responsible
    for populating the field (`position_monitor` backfills it from the OCC
    symbol so no option position is left without a time stop).
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
        deadline = _last_tradeable_date(expiry.astimezone(UTC).date())
        dte = (deadline - datetime.now(_ET).date()).days
        if dte >= self.min_dte:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"time to expiry: dte={dte} < min_dte={self.min_dte}",
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
    session_close: datetime | None = None,
) -> ExitSignal | None:
    """Run all exit rules in priority order. Return first signal that fires.

    Priority: disaster valve first (catastrophic loss trumps everything),
    then stop-loss (hard risk limit), profit target (most informative
    signal), 0DTE flatten and expiry (hard deadlines), no-progress and
    max-hold (time stops), drawdown-from-peak (trailing stop) last.

    `session_close` shortens the 0DTE flatten deadline on early-close
    sessions; see resolve_session_close.
    """
    for rule in (
        DisasterValveRule(params.disaster_pct),
        StopLossRule(params.stop_loss_pct),
        ProfitTargetRule(params.profit_target_pct),
        ZeroDTEFlattenRule(params.flatten_after_et, session_close),
        TimeToExpiryRule(params.min_dte),
        NoProgressRule(params.no_progress_minutes, params.no_progress_band_pct),
        MaxHoldRule(params.max_hold_hours),
        DrawdownFromPeakRule(params.max_drawdown_pct, params.drawdown_min_peak_pct),
    ):
        signal = rule.should_exit(position)
        if signal is not None:
            return signal
    return None
