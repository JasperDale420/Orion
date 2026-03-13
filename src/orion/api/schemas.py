from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SolverResponse(BaseModel):
    solver_id: str
    family_name: str
    stage: str
    is_active: bool
    config: dict[str, Any]
    created_at_utc: datetime
    # Metrics snapshot
    total_pnl: float
    sharpe_ratio: float
    win_rate: float
    trades_count: int

    model_config = ConfigDict(from_attributes=True)


class SolverMetricsResponse(BaseModel):
    id: str
    solver_id: str
    sector: str
    dataset_tag: str
    num_runs: int
    num_trades: int
    sharpe_ratio: float
    profit_factor: float
    max_dd_pct: float
    stability_score: float
    metrics_json: dict[str, Any]
    evaluated_at_utc: datetime

    model_config = ConfigDict(from_attributes=True)


class ExperimentResponse(BaseModel):
    experiment_id: str
    description: str
    status: str
    best_solver_id: str | None
    start_time_utc: datetime
    end_time_utc: datetime | None

    model_config = ConfigDict(from_attributes=True)


class PromotionRecommendationResponse(BaseModel):
    id: str
    solver_id: str
    current_stage: str
    recommended_stage: str
    reason: str
    metrics_snapshot: dict[str, Any] | None
    status: str
    created_at_utc: datetime
    reviewed_at_utc: datetime | None
    reviewed_by: str | None

    model_config = ConfigDict(from_attributes=True)
