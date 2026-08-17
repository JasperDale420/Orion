"""Nightly per-bucket / per-rule realized-performance metrics.

The mechanical replacement for the deleted LLM review machinery: every
night, compute what actually happened from CLOSED trades (realized fills,
spread cost embedded) and surface it — win rate, expectancy, profit factor,
hold time, and the exit-reason mix (a bucket exiting mostly on time-stops
has no directional edge regardless of P&L).

Verdicts follow the sample-size discipline from the 2026-07 recovery plan:
under 30 closed trades touch nothing; at 100 the first expectancy verdict
is meaningful (SE of a ~40% win rate is ±5pp); sizing up requires n>=100,
positive expectancy, and PF>=1.15; a trailing-50 PF under 0.6 flags the
bucket for halting.

The halting verdict acts: it opens a time-boxed per-bucket entry halt (see
``orion.jobs.bucket_halt``) that ``preflight_live_signal`` enforces on new
entries only. Every other verdict stays advisory — sizing up is still a human
decision — and routine results remain in structured logs.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select

from orion.execution.exit_costs import realized_exit_slippage
from orion.execution.exit_fallback_rules import bucket_for_dte
from orion.jobs.bucket_halt import record_halt, release_expired_halts
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.storage.models_execution import FillRecord
from orion.storage.models_gold import CandidateTrade, ExitDecision
from orion.storage.models_trade_journal import TradeJournalEntry

logger = setup_struct_logger("orion.jobs.bucket_metrics")

# Sample-size / promotion thresholds (plan §6). Advisory only.
MIN_TRADES_FOR_VERDICT = 30
SIZE_UP_MIN_TRADES = 100
SIZE_UP_MIN_PROFIT_FACTOR = 1.15
HALT_TRAILING_WINDOW = 50
HALT_TRAILING_PROFIT_FACTOR = 0.60


@dataclass
class GroupStats:
    n: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    gross_wins: float = 0.0
    gross_losses: float = 0.0
    hold_hours_total: float = 0.0
    hold_hours_n: int = 0
    exit_reasons: dict[str, int] = field(default_factory=dict)
    # Realized P&L per trade, newest first (for trailing-window checks).
    pnls_newest_first: list[float] = field(default_factory=list)

    def add(self, pnl: float, hold_hours: float | None, exit_reason: str | None) -> None:
        self.n += 1
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
            self.gross_wins += pnl
        else:
            self.gross_losses += -pnl
        if hold_hours is not None:
            self.hold_hours_total += hold_hours
            self.hold_hours_n += 1
        if exit_reason:
            self.exit_reasons[exit_reason] = self.exit_reasons.get(exit_reason, 0) + 1
        self.pnls_newest_first.append(pnl)

    def summary(self) -> dict[str, Any]:
        pf = (self.gross_wins / self.gross_losses) if self.gross_losses > 0 else None
        trailing = self.pnls_newest_first[:HALT_TRAILING_WINDOW]
        t_wins = sum(p for p in trailing if p > 0)
        t_losses = sum(-p for p in trailing if p <= 0)
        trailing_pf = (t_wins / t_losses) if t_losses > 0 else None
        expectancy = self.total_pnl / self.n if self.n else 0.0

        verdict = "collecting"
        if self.n >= MIN_TRADES_FOR_VERDICT:
            verdict = "measurable"
        if (
            len(trailing) >= HALT_TRAILING_WINDOW
            and trailing_pf is not None
            and trailing_pf < HALT_TRAILING_PROFIT_FACTOR
        ):
            verdict = "consider_halting"
        elif self.n >= SIZE_UP_MIN_TRADES and expectancy > 0 and pf is not None and pf >= SIZE_UP_MIN_PROFIT_FACTOR:
            verdict = "consider_sizing_up"

        return {
            "n": self.n,
            "win_rate": round(self.wins / self.n, 3) if self.n else None,
            "total_pnl": round(self.total_pnl, 2),
            "expectancy": round(expectancy, 2),
            "profit_factor": round(pf, 3) if pf is not None else None,
            "trailing_pf": round(trailing_pf, 3) if trailing_pf is not None else None,
            "avg_hold_hours": round(self.hold_hours_total / self.hold_hours_n, 1) if self.hold_hours_n else None,
            "exit_reason_mix": dict(sorted(self.exit_reasons.items(), key=lambda kv: -kv[1])),
            "verdict": verdict,
        }


# Realized exit-cost fields aggregated per bucket. Slippage vs the quote mid is
# the market's price of the exit; slippage vs the mark the rule evaluated is our
# own — a wide gap there means rules are firing on stale marks.
_SLIPPAGE_FIELDS = ("slippage_vs_mid_usd", "slippage_vs_mid_pct", "slippage_vs_mark_usd")

# Stands in for a lot whose entry bucket cannot be derived. Never an aggregation
# key — its only job is to make a close that touched such a lot fail the
# single-bucket test instead of being charged to a bucket we guessed.
UNKNOWN_BUCKET = "__unknown__"


def _summarize_slippage(samples: list[dict[str, float | None]]) -> dict[str, Any]:
    """Median/mean of each realized exit-cost field. ``n`` counts closes that had
    BOTH a captured exit quote and a matching fill; a field stays None when no
    sample could supply it (a mark-only quote measures no mid)."""
    out: dict[str, Any] = {"n": len(samples)}
    for name in _SLIPPAGE_FIELDS:
        values = [v for s in samples if (v := s.get(name)) is not None]
        suffix = name.removeprefix("slippage_")
        out[f"median_{suffix}"] = round(statistics.median(values), 4) if values else None
        out[f"mean_{suffix}"] = round(statistics.fmean(values), 4) if values else None
    return out


async def compute_bucket_metrics(days: int = 30) -> dict[str, Any]:
    """Aggregate closed-trade performance by bucket and by rule.

    Closed = realized_pnl set. Bucket = entry-DTE classification (same
    convention as the entry caps). Exit reason = the latest exit_decisions
    row for the same candidate (expiry sweeps carry notes='expired_worthless').

    Each bucket also carries `exit_slippage`: what the closes in that bucket
    actually cost, from the quote captured at close submission against the
    realized fill. Log-only — it feeds no verdict.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)

    async def read(session: Any) -> tuple[list[Any], list[Any], list[Any], list[Any], list[Any]]:
        closed = (
            await session.execute(
                select(
                    TradeJournalEntry.candidate_id,
                    TradeJournalEntry.realized_pnl,
                    TradeJournalEntry.filled_at_utc,
                    TradeJournalEntry.exit_filled_at_utc,
                    TradeJournalEntry.notes,
                    CandidateTrade.rule_id,
                    CandidateTrade.expiration_date,
                    TradeJournalEntry.raw_json,
                )
                .join(CandidateTrade, CandidateTrade.candidate_id == TradeJournalEntry.candidate_id)
                .where(
                    TradeJournalEntry.realized_pnl.is_not(None),
                    TradeJournalEntry.filled_at_utc.is_not(None),
                    # Window on REALIZATION time (close fill / expiry sweep),
                    # not entry time — a multi-day hold entered before the
                    # window but closed inside it belongs to this window.
                    # Fallback to the entry fill for legacy rows without an
                    # exit stamp (same convention as reconcile_pnl).
                    func.coalesce(TradeJournalEntry.exit_filled_at_utc, TradeJournalEntry.filled_at_utc) >= cutoff,
                )
                .order_by(func.coalesce(TradeJournalEntry.exit_filled_at_utc, TradeJournalEntry.filled_at_utc).desc())
            )
        ).all()
        exits = (
            await session.execute(
                select(ExitDecision.candidate_id, ExitDecision.rule_id, ExitDecision.exit_ts_utc)
                .where(ExitDecision.candidate_id.is_not(None), ExitDecision.exit_ts_utc >= cutoff)
                .order_by(ExitDecision.exit_ts_utc.desc())
            )
        ).all()
        # Exit rows carrying a close-time quote, and the fills that realized
        # them. `fills` holds ONE row per broker order with the broker's
        # CUMULATIVE qty/avg price, so a close that filled in several partials
        # still yields a single realized price.
        quoted_exits = (
            await session.execute(
                select(
                    ExitDecision.exit_id,
                    ExitDecision.broker_order_id,
                    ExitDecision.details,
                    ExitDecision.ticker,
                ).where(ExitDecision.exit_ts_utc >= cutoff, ExitDecision.details.is_not(None))
            )
        ).all()
        exit_fills = (
            await session.execute(
                select(
                    FillRecord.broker_order_id,
                    FillRecord.client_order_id,
                    FillRecord.filled_avg_price,
                    FillRecord.side,
                    FillRecord.ticker,
                ).where(FillRecord.filled_at_utc >= cutoff, FillRecord.filled_avg_price.is_not(None))
            )
        ).all()
        # EVERY lot a close order touched, whether or not that lot is fully
        # realized. The allocator books a leg before a lot closes, so one order
        # can fully close one lot and partially close another; reading only the
        # realized lots would hide the second and make a two-bucket order look
        # single-bucket.
        #
        # Scoped by CONTRACT, not by time. Both timestamps on a journal row are
        # the LAST-applied leg's, and the EOD reconcile can book an older missed
        # fill after a newer one — so a row-level time filter could drop a lot
        # that shares an in-window order with another lot, silently collapsing a
        # two-bucket order to one. Every lot a close order can touch matches that
        # order's contract on the SAME predicate `allocate_exit_in_session` uses
        # to find lots — an OR across the candidate's OCC symbol and the journal
        # ticker, NOT a coalesce: an option lot carries the underlying in
        # `ticker` and the contract in `option_symbol`, and the allocator can
        # match on either. So the contracts closed in the window bound the scan
        # without dropping an out-of-order leg.
        #
        # OUTER join because a lot can legitimately have no candidate row (the
        # allocator resolves underlying-ticker lots too) — those must stay in
        # the map as a bucket we cannot name, not vanish and leave a mixed close
        # looking single-bucket.
        # Only the contracts of exits that CLAIM a capture — a close is
        # measurable only if it has one, and its exit_decisions row carries the
        # same contract the fill does. Widening this to every in-window fill
        # would drag in unrelated symbols, and an equity ticker there would pull
        # every historical option lot on that underlying (an option lot's
        # journal `ticker` IS the underlying). Quotes are captured on the
        # options close path only, so these are contract symbols.
        contracts = {t for (*_, t) in quoted_exits if t}
        allocated = []
        if contracts:
            allocated = (
                await session.execute(
                    select(
                        TradeJournalEntry.filled_at_utc,
                        CandidateTrade.candidate_id,
                        CandidateTrade.expiration_date,
                        TradeJournalEntry.raw_json,
                    )
                    .outerjoin(CandidateTrade, CandidateTrade.candidate_id == TradeJournalEntry.candidate_id)
                    .where(
                        TradeJournalEntry.exit_broker_order_id.is_not(None),
                        or_(
                            CandidateTrade.option_symbol.in_(sorted(contracts)),
                            TradeJournalEntry.ticker.in_(sorted(contracts)),
                        ),
                    )
                )
            ).all()
        return list(closed), list(exits), list(quoted_exits), list(exit_fills), list(allocated)

    closed, exits, quoted_exits, exit_fills, allocated = await db_query(read)

    # Latest exit rule per candidate (rows arrive newest-first).
    exit_rule_by_candidate: dict[str, str] = {}
    for candidate_id, rule_id, _ts in exits:
        exit_rule_by_candidate.setdefault(candidate_id, rule_id)

    def _bucket_of(filled_at: Any, expiration: Any) -> str:
        dte = (expiration.date() - filled_at.date()).days if expiration is not None and filled_at is not None else None
        return bucket_for_dte(dte)

    def _leg_order_ids(raw_json: Any) -> list[str]:
        legs = raw_json.get("exit_allocations") if isinstance(raw_json, dict) else None
        return [
            str(leg["order_id"])
            for leg in (legs if isinstance(legs, list) else [])
            if isinstance(leg, dict) and leg.get("order_id")
        ]

    # Broker order id of each close leg → the entry bucket(s) of every lot it
    # touched, realized or not. An exit_decisions row carries no candidate link
    # (it means "a close was submitted", not "the position is closed"), so the
    # journal's per-order exit ledger is the bridge. A lot with no candidate row
    # has no derivable DTE, so it enters the map as UNKNOWN_BUCKET rather than
    # as `bucket_for_dte(None)`, whose SWING default would fabricate a bucket.
    buckets_by_exit_order: dict[str, set[str]] = {}
    for filled_at, candidate_id, expiration, raw_json in allocated:
        bucket = _bucket_of(filled_at, expiration) if candidate_id is not None else UNKNOWN_BUCKET
        for order_id in _leg_order_ids(raw_json):
            buckets_by_exit_order.setdefault(order_id, set()).add(bucket)

    by_bucket: dict[str, GroupStats] = {}
    by_rule: dict[str, GroupStats] = {}
    # Orders that closed out at least one lot: cost is measured on CLOSED trades
    # only, matching the rest of this report.
    realized_exit_orders: set[str] = set()
    for candidate_id, pnl, filled_at, exit_filled_at, notes, rule_id, expiration, raw_json in closed:
        bucket = _bucket_of(filled_at, expiration)

        hold_hours = None
        if filled_at is not None and exit_filled_at is not None:
            hold_hours = max((exit_filled_at - filled_at).total_seconds() / 3600.0, 0.0)

        if notes == "expired_worthless":
            exit_reason = "expired_worthless"
        else:
            exit_reason = exit_rule_by_candidate.get(candidate_id, "unattributed")

        by_bucket.setdefault(bucket, GroupStats()).add(float(pnl), hold_hours, exit_reason)
        by_rule.setdefault(rule_id or "unknown", GroupStats()).add(float(pnl), hold_hours, exit_reason)

        realized_exit_orders.update(_leg_order_ids(raw_json))

    fills_by_broker: dict[str, tuple[str, float, str | None]] = {}
    fills_by_client: dict[str, tuple[str, float, str | None]] = {}
    for broker_id, client_id, price, side, _fill_ticker in exit_fills:
        if not broker_id or price is None:
            continue
        fill = (str(broker_id), float(price), side)
        fills_by_broker.setdefault(str(broker_id), fill)
        if client_id:
            fills_by_client.setdefault(str(client_id), fill)

    slippage_by_bucket: dict[str, list[dict[str, float | None]]] = {}
    seen_orders: set[str] = set()
    # Mutually exclusive and exhaustive: quoted_exits == measured + the four
    # exclusion counters + duplicate_order.
    coverage = dict.fromkeys(
        (
            "quoted_exits",
            "no_matching_fill",
            "filled_without_journal_bridge",
            "filled_lot_still_open",
            "excluded_multi_bucket",
            "duplicate_order",
            "unknown_bucket",
            "unusable_quote",
            "malformed_quote",
            "capture_error",
        ),
        0,
    )
    for exit_id, exit_broker_order_id, details, _ticker in quoted_exits:
        if not isinstance(details, dict) or "exit_quote" not in details:
            continue
        # Counted the moment a row CLAIMS a capture, before its shape is
        # trusted: a null/string/list value is a capture regression, and
        # skipping it before the denominator would hide that regression behind
        # an invariant that still balances.
        coverage["quoted_exits"] += 1
        quote = details["exit_quote"]
        if not isinstance(quote, dict):
            coverage["malformed_quote"] += 1
            continue
        if "error" in quote:
            # The close path stores {"error": ...} when capture itself failed.
            # Kept separate from `unusable_quote` (a well-formed quote with no
            # usable benchmark) so a systemic capture bug is visible on its own.
            coverage["capture_error"] += 1
            continue
        # `exit_id` IS the client_order_id, so a close whose submit response
        # carried no broker id is still matched through the fill.
        fill = fills_by_broker.get(str(exit_broker_order_id)) if exit_broker_order_id else None
        if fill is None:
            fill = fills_by_client.get(str(exit_id))
        if fill is None:
            # No orion-attributed fill at all: an unfilled/cancelled close, or a
            # native escalation (the Gateway's DELETE carries no
            # client_order_id, so its fill is dropped by the orion_ prefix
            # filter). Counted, not silently dropped — escalations are the
            # expensive exits, and a mean that quietly omits them reads better
            # than reality.
            coverage["no_matching_fill"] += 1
            continue
        fill_broker_id, fill_price, fill_side = fill
        # One sample per closing order, never per fill event: the fills row
        # holds the broker's CUMULATIVE qty/avg, so partials are already one
        # price. A second decision row resolving to the same order would be a
        # double count.
        if fill_broker_id in seen_orders:
            coverage["duplicate_order"] += 1
            continue
        seen_orders.add(fill_broker_id)
        buckets = buckets_by_exit_order.get(fill_broker_id)
        if not buckets:
            # The fill exists but no journal lot records it: allocation failed
            # or has not been reconciled yet. Kept distinct from "never filled"
            # — this one is a repair signal, not an unmeasurable exit.
            coverage["filled_without_journal_bridge"] += 1
            continue
        if len(buckets) > 1:
            # One order touched lots from different entry-DTE buckets. It has a
            # single realized price, so charging it to one of them would misstate
            # that bucket's cost — exclude and count it instead of guessing.
            coverage["excluded_multi_bucket"] += 1
            continue
        bucket = next(iter(buckets))
        if bucket == UNKNOWN_BUCKET:
            coverage["unknown_bucket"] += 1
            continue
        if fill_broker_id not in realized_exit_orders:
            # Bridged, but every lot it touched is still partially open; cost is
            # measured on closed trades, so it counts once the lot closes.
            coverage["filled_lot_still_open"] += 1
            continue
        slippage = realized_exit_slippage(quote, fill_price, side=fill_side or "sell")
        if all(v is None for v in slippage.values()):
            # A well-formed quote with no usable benchmark (no mid, no mark).
            # Counting it would claim coverage for a close whose cost is
            # entirely unknown.
            coverage["unusable_quote"] += 1
            continue
        slippage_by_bucket.setdefault(bucket, []).append(slippage)

    bucket_summaries = {k: v.summary() for k, v in sorted(by_bucket.items())}
    for bucket_name, summary in bucket_summaries.items():
        summary["exit_slippage"] = _summarize_slippage(slippage_by_bucket.get(bucket_name, []))

    return {
        "window_days": days,
        "closed_trades": len(closed),
        "by_bucket": bucket_summaries,
        "by_rule": {k: v.summary() for k, v in sorted(by_rule.items())},
        # The denominator for every per-bucket `exit_slippage`: how much of the
        # exit flow the cost figures actually cover, and why the rest is out.
        # A large `no_matching_fill` means the measured sample is a biased
        # subset (escalated closes are unmeasurable); a non-zero
        # `filled_without_journal_bridge` means an allocation needs repair.
        "exit_cost_coverage": {**coverage, "measured": sum(len(v) for v in slippage_by_bucket.values())},
    }


def _format_summary(metrics: dict[str, Any]) -> str:
    lines = [f"**Bucket metrics** ({metrics['window_days']}d, {metrics['closed_trades']} closed trades)"]
    for bucket, s in metrics["by_bucket"].items():
        mix = ", ".join(f"{k}×{v}" for k, v in list(s["exit_reason_mix"].items())[:3])
        lines.append(
            f"{bucket}: n={s['n']} win={s['win_rate']} exp=${s['expectancy']} "
            f"PF={s['profit_factor']} hold={s['avg_hold_hours']}h [{s['verdict']}] exits: {mix}"
        )
    if not metrics["by_bucket"]:
        lines.append("no closed trades yet")
    return "\n".join(lines)


async def apply_halt_verdicts(metrics: dict[str, Any], *, now: datetime | None = None) -> list[str]:
    """Turn ``consider_halting`` verdicts into durable per-bucket entry halts.

    Releases lapsed halts first, so a bucket that has served its window starts
    its sampling window before this pass re-measures it. A bucket that is still
    failing after that window is simply halted again. Returns one
    human-readable line per action taken, for the nightly alert.
    """
    actions = [
        f"resumed {halt.bucket} — sampling until {halt.expires_after_session.isoformat()}"
        for halt in await release_expired_halts(now=now)
    ]

    for bucket, stats in metrics["by_bucket"].items():
        # The same criterion GroupStats.summary applies, restated so a caller
        # cannot hand us a halting verdict without the sample behind it.
        if stats["verdict"] != "consider_halting" or (stats.get("n") or 0) < HALT_TRAILING_WINDOW:
            continue
        write = await record_halt(
            bucket,
            profit_factor=stats.get("trailing_pf"),
            n_closed=stats.get("n"),
            now=now,
            reason=f"trailing-{HALT_TRAILING_WINDOW} PF below {HALT_TRAILING_PROFIT_FACTOR}",
        )
        # Never silent about a halt the criterion asked for but did not get:
        # each of these is something the operator has to be able to see.
        if write.outcome == "written" and write.halt is not None:
            actions.append(f"HALTED {write.halt.describe()}")
        elif write.outcome == "operator_halt_present":
            actions.append(f"{bucket} halt suppressed by a live operator halt — clear it to re-arm the automatic one")
        elif write.outcome == "resuming" and write.halt is not None:
            actions.append(
                f"{bucket} still failing but sampling until "
                f"{write.halt.expires_after_session.isoformat()} — no new halt yet"
            )
    return actions


async def run_bucket_metrics(days: int = 30, post_discord: bool = True) -> dict[str, Any]:
    """Compute and log nightly metrics; act on halts and page actionable verdicts."""
    metrics = await compute_bucket_metrics(days=days)
    logger.info(
        "bucket_metrics",
        window_days=metrics["window_days"],
        closed_trades=metrics["closed_trades"],
        by_bucket=metrics["by_bucket"],
        by_rule=metrics["by_rule"],
        exit_cost_coverage=metrics.get("exit_cost_coverage"),
    )

    # The halt is an addition to the advisory path, not a replacement for it:
    # a failure to write one must still leave the verdict alert to be sent.
    halt_actions: list[str] = []
    try:
        halt_actions = await apply_halt_verdicts(metrics)
    except Exception as e:
        logger.error("bucket_halt_apply_failed", error=str(e), exc_info=True)
        halt_actions = [f"halt update FAILED: {e}"]

    flagged = [
        f"{name} → {s['verdict']}"
        for name, s in metrics["by_bucket"].items()
        if s["verdict"] in ("consider_sizing_up", "consider_halting")
    ]
    if post_discord and (flagged or halt_actions):
        from orion.shared.alerts import send_discord_alert

        text = _format_summary(metrics)
        if flagged:
            text += "\n⚠ verdicts: " + "; ".join(flagged)
        if halt_actions:
            text += "\n⛔ entry halts: " + "; ".join(halt_actions)
        await send_discord_alert(text, dedupe_key="bucket_metrics_nightly")
    return metrics


if __name__ == "__main__":
    import asyncio

    print(_format_summary(asyncio.run(run_bucket_metrics(post_discord=False))))  # noqa: T201
