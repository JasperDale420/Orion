from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from orion.storage.db import Base


class Solver(Base):
    """
    Represents a specific configuration (DNA) of a trading strategy.
    Corresponds to PRDv2 'Solver' concept.
    """

    __tablename__ = "solvers"

    solver_id: Mapped[str] = mapped_column(String, primary_key=True)  # Hash of config
    family_name: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "TrendRider", "MeanRevert"

    # PRDv2 §4.1 alignment (staged): store DSL in definition_json but keep legacy config for compatibility.
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)  # draft|candidate|active|deprecated
    parent_solver_id: Mapped[str | None] = mapped_column(String, ForeignKey("solvers.solver_id"), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)  # human|llm_eod_agent|meta_agent
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    definition_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # The DNA
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Metadata
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    stage: Mapped[str] = mapped_column(String, default="research")  # research, shadow, paper, live

    # Performance Metrics (Snapshot)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)

    # Extended Metrics (Snapshot for Policy)
    info_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, default=0.0)
    max_dd_pct: Mapped[float] = mapped_column(Float, default=0.0)
    stability_score: Mapped[float] = mapped_column(Float, default=0.0)
    oos_expect_bp: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("ix_solver_active_stage", "is_active", "stage"),)

    def __repr__(self) -> str:
        return f"<Solver(id={self.solver_id}, family={self.family_name}, active={self.is_active})>"


class MetaExperiment(Base):
    """
    Tracks an automated experiment run (e.g., 'Evolution Gen 1').
    """

    __tablename__ = "meta_experiments"

    experiment_id: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(String)
    # PRDv2 Addendum §4.4: PRD-aligned fields (staged; legacy fields retained)
    # status is stored as string for portability; expected values: running|completed|failed
    status: Mapped[str] = mapped_column(String, default="running")

    start_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    end_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    trial_count: Mapped[int] = mapped_column(Integer, default=0)  # Tracks total number of trials run in this experiment

    best_solver_id: Mapped[str] = mapped_column(String, ForeignKey("solvers.solver_id"), nullable=True)

    # PRD fields (best-effort; kept nullable for backwards compatibility)
    id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    objective: Mapped[str | None] = mapped_column(String, nullable=True)
    base_solver_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def prd_id(self) -> str:
        return self.id or self.experiment_id

    def __repr__(self) -> str:
        return f"<MetaExperiment(id={self.experiment_id}, status={self.status})>"


class SolverMetrics(Base):
    """
    PRD 4.3: Aggregated metrics per solver per context.
    """

    __tablename__ = "solver_metrics"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID
    solver_id: Mapped[str] = mapped_column(String, ForeignKey("solvers.solver_id"), index=True)

    # Context
    sector: Mapped[str] = mapped_column(String, default="ALL")
    ticker_bucket: Mapped[str | None] = mapped_column(String, nullable=True)
    horizon_profile: Mapped[str | None] = mapped_column(String, nullable=True)
    dataset_tag: Mapped[str] = mapped_column(String, default="test")  # train/val/test

    # Validation/Performance
    num_runs: Mapped[int] = mapped_column(Integer, default=0)
    num_trades: Mapped[int] = mapped_column(Integer, default=0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    info_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, default=0.0)
    oos_expect_bp: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_dd_pct: Mapped[float] = mapped_column(Float, default=0.0)

    stability_score: Mapped[float] = mapped_column(Float, default=0.0)

    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default={})

    evaluated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SolverRun(Base):
    """
    PRDv2 Addendum §4.2: One row per solver evaluation/backtest run (offline or live replay).
    """

    __tablename__ = "solver_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID
    solver_id: Mapped[str] = mapped_column(String, ForeignKey("solvers.solver_id"), index=True)

    dataset_tag: Mapped[str] = mapped_column(String, default="test")  # train/val/test/live_replay/shadow
    time_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    num_candidates: Mapped[int] = mapped_column(Integer, default=0)
    num_trades: Mapped[int] = mapped_column(Integer, default=0)

    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    expect_return_bp: Mapped[float | None] = mapped_column(Float, nullable=True)

    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default={})
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SolverEdits(Base):
    """
    PRD 4.5: Captures how new solvers were derived (Genetic lineage).
    """

    __tablename__ = "solver_edits"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    experiment_id: Mapped[str] = mapped_column(String, ForeignKey("meta_experiments.experiment_id"), nullable=True)

    base_solver_id: Mapped[str] = mapped_column(String, ForeignKey("solvers.solver_id"))
    new_solver_id: Mapped[str] = mapped_column(String, ForeignKey("solvers.solver_id"))

    # The JSON blob of ops
    edit_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    generated_by: Mapped[str] = mapped_column(String)  # meta_agent / llm_eod_agent

    # Reward (populated after eval)
    reward: Mapped[float] = mapped_column(Float, nullable=True)

    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    evaluated_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PromotionRecommendation(Base):
    """
    Proposed transition of a solver to a higher stage.
    Requires human or policy approval.
    """

    __tablename__ = "promotion_recommendations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    solver_id: Mapped[str] = mapped_column(String, ForeignKey("solvers.solver_id"))

    current_stage: Mapped[str] = mapped_column(String)
    recommended_stage: Mapped[str] = mapped_column(String)

    reason: Mapped[str] = mapped_column(String)
    metrics_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String, default="PENDING")  # PENDING, APPROVED, REJECTED

    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return f"<PromotionRecommendation(id={self.id}, solver={self.solver_id}, status={self.status})>"
