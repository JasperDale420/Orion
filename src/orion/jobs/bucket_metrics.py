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
bucket for halting. Verdicts are ADVISORY — they alert, they never act.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from orion.execution.exit_fallback_rules import bucket_for_dte
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
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


async def compute_bucket_metrics(days: int = 30) -> dict[str, Any]:
    """Aggregate closed-trade performance by bucket and by rule.

    Closed = realized_pnl set. Bucket = entry-DTE classification (same
    convention as the entry caps). Exit reason = the latest exit_decisions
    row for the same candidate (expiry sweeps carry notes='expired_worthless').
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)

    async def read(session: Any) -> tuple[list[Any], list[Any]]:
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
        return list(closed), list(exits)

    closed, exits = await db_query(read)

    # Latest exit rule per candidate (rows arrive newest-first).
    exit_rule_by_candidate: dict[str, str] = {}
    for candidate_id, rule_id, _ts in exits:
        exit_rule_by_candidate.setdefault(candidate_id, rule_id)

    by_bucket: dict[str, GroupStats] = {}
    by_rule: dict[str, GroupStats] = {}
    for candidate_id, pnl, filled_at, exit_filled_at, notes, rule_id, expiration in closed:
        dte = None
        if expiration is not None and filled_at is not None:
            dte = (expiration.date() - filled_at.date()).days
        bucket = bucket_for_dte(dte)

        hold_hours = None
        if filled_at is not None and exit_filled_at is not None:
            hold_hours = max((exit_filled_at - filled_at).total_seconds() / 3600.0, 0.0)

        if notes == "expired_worthless":
            exit_reason = "expired_worthless"
        else:
            exit_reason = exit_rule_by_candidate.get(candidate_id, "unattributed")

        by_bucket.setdefault(bucket, GroupStats()).add(float(pnl), hold_hours, exit_reason)
        by_rule.setdefault(rule_id or "unknown", GroupStats()).add(float(pnl), hold_hours, exit_reason)

    return {
        "window_days": days,
        "closed_trades": len(closed),
        "by_bucket": {k: v.summary() for k, v in sorted(by_bucket.items())},
        "by_rule": {k: v.summary() for k, v in sorted(by_rule.items())},
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


async def run_bucket_metrics(days: int = 30, post_discord: bool = True) -> dict[str, Any]:
    """Compute, log, and (optionally) post the nightly metrics summary."""
    metrics = await compute_bucket_metrics(days=days)
    logger.info(
        "bucket_metrics",
        window_days=metrics["window_days"],
        closed_trades=metrics["closed_trades"],
        by_bucket=metrics["by_bucket"],
        by_rule=metrics["by_rule"],
    )
    flagged = [
        f"{name} → {s['verdict']}"
        for name, s in metrics["by_bucket"].items()
        if s["verdict"] in ("consider_sizing_up", "consider_halting")
    ]
    if post_discord:
        from orion.shared.alerts import send_discord_alert

        text = _format_summary(metrics)
        if flagged:
            text += "\n⚠ verdicts: " + "; ".join(flagged)
        await send_discord_alert(text, dedupe_key="bucket_metrics_nightly")
    return metrics


if __name__ == "__main__":
    import asyncio

    print(_format_summary(asyncio.run(run_bucket_metrics(post_discord=False))))  # noqa: T201
