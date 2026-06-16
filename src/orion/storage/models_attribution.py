"""Daily PnL-reconciliation and per-solver / per-rule attribution tables (RB.3).

These tables persist the output of the daily ``jobs.reconcile_pnl`` job:

1. ``pnl_reconciliation`` — one row per trading day comparing the trade
   journal's realized-PnL total (for ``orion_``-attributed closed trades)
   against broker truth reconstructed from ``orion_``-attributed filled orders.
   This is the *measurement-before-evolution* gate (redesign R5): the journal's
   realized-PnL write-back has existed since 2026-06-08 but has never been
   verified against the broker, so nothing may act on it until reconciliation
   confirms it.

2. ``solver_pnl_attribution`` / ``rule_pnl_attribution`` — daily realized-PnL
   aggregates rolled up from the trade journal, grouped by ``solver_id`` and by
   ``rule_id`` respectively. Sample sizes (``n_trades``) are persisted alongside
   so downstream reporting can ALWAYS show them.

ATTRIBUTION JOIN PATH (verified against the live models, 2026-06-11):
  - solver_id is carried directly on ``TradeJournalEntry.solver_id``
    (set from ``decision.strategy_version_id`` in
    ``execution/decision_persistence.py`` and ``execution/persistence.py``).
  - rule_id is NOT on the journal; it lives on ``CandidateTrade.rule_id``
    (``storage/models_gold.py``) and is reached via the journal's
    ``candidate_id`` → ``candidate_trades.candidate_id`` → ``rule_id``.

These tables hold RECOMMENDATIONS ONLY. No code reads them to mutate a solver
stage — demotion is a separately-gated task (RD.1).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class PnlReconciliation(Base):
    """One row per trading day: journal realized-PnL total vs broker truth.

    ``status`` is one of:
      - ``MATCH``    — abs drift within tolerance (per-trade rounding allowance)
      - ``MISMATCH`` — abs drift exceeds tolerance
      - ``NO_DATA``  — no orion-attributed closed trades on either side

    ``details`` carries the per-side breakdown (counts, per-symbol broker PnL,
    tolerance used, any reconstruction caveats) for operator inspection.
    """

    __tablename__ = "pnl_reconciliation"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)

    journal_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    broker_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    drift_abs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    drift_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    journal_trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    broker_trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    tolerance_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.01)
    status: Mapped[str] = mapped_column(String, nullable=False)

    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class SolverPnlAttribution(Base):
    """Daily per-solver realized-PnL aggregate from the trade journal.

    PK is (trade_date, solver_id). ``n_trades`` is the closed-trade sample size
    and MUST be surfaced wherever expectancy is reported — a positive expectancy
    on n=2 is noise, and the demotion gate (RD.1) requires n>=20.
    """

    __tablename__ = "solver_pnl_attribution"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    solver_id: Mapped[str] = mapped_column(String, primary_key=True)

    n_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    realized_pnl_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expectancy: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class RulePnlAttribution(Base):
    """Daily per-rule realized-PnL aggregate from the trade journal.

    ``rule_id`` is reached via journal.candidate_id → candidate_trades.rule_id
    (see module docstring). PK is (trade_date, rule_id).
    """

    __tablename__ = "rule_pnl_attribution"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String, primary_key=True)

    n_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    realized_pnl_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expectancy: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
