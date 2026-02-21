import enum
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from orion.config import risk_settings
from orion.core.feature_registry import FeatureRegistry

# --------------------------------------------------------
# Solver DNA Components
# --------------------------------------------------------


class SolverRiskConfig(BaseModel):
    risk_per_trade_bps: int = Field(default=100, ge=1)  # Validated dynamically
    max_open_positions: int = Field(default=5, ge=1)
    max_ticker_exposure_pct: float = 5.0
    time_of_day_bans: Optional[List[str]] = None
    session_filter: Optional[List[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_global_limits(self) -> "SolverRiskConfig":
        # Global Hard Limits
        MAX_SYSTEM_BPS = risk_settings.max_system_bps
        MAX_GLOBAL_POSITIONS = risk_settings.max_positions

        if self.risk_per_trade_bps > MAX_SYSTEM_BPS:
            raise ValueError(f"Risk per trade {self.risk_per_trade_bps}bps exceeds system limit {MAX_SYSTEM_BPS}bps")

        if self.max_open_positions > MAX_GLOBAL_POSITIONS:
            raise ValueError(f"Max positions {self.max_open_positions} exceeds system limit {MAX_GLOBAL_POSITIONS}")

        return self

    class Config:
        validate_assignment = True


class SolverFeatures(BaseModel):
    feature_set_id: str = Field(default="v1_legacy")
    event_features: List[str] = Field(default_factory=list)
    window_features: List[str] = Field(default_factory=list)
    feature_engine_version: str = "v1"

    @field_validator("feature_set_id")
    @classmethod
    def validate_feature_set(cls, v: str) -> str:
        if not FeatureRegistry.validate_id(v):
            raise ValueError(f"Invalid feature_set_id: {v}. Available: {FeatureRegistry.list_all()}")
        return v


class SolverModel(BaseModel):
    type: str = "meta_classifier"  # meta_classifier, none
    model_version: Optional[str] = None  # e.g. "lgbm_v1"
    model_uri: Optional[str] = None
    thresholds: Dict[str, float] = Field(default_factory=dict)


class SolverUniverse(BaseModel):
    ticker_allowlist: Optional[List[str]] = None
    ticker_blocklist: Optional[List[str]] = None
    required_regime: Optional[str] = None


class ExitLogic(BaseModel):
    take_profit_atr_multiple: Optional[float] = None
    stop_loss_atr_multiple: Optional[float] = None
    fixed_tp_pct: Optional[float] = None
    fixed_sl_pct: Optional[float] = None
    time_exit_bars: Optional[int] = None

    # Optional dynamic exit support
    model_uri: Optional[str] = None


class RuleConfig(BaseModel):
    id: str
    params: Dict[str, Any] = {}


class SolverConfig(BaseModel):
    """
    Complete configuration for a Trading Solver (Strategy V2).
    This JSON blob defines the 'DNA' of a strategy version.
    Corresponds to PRDv2 Solver Definition.
    """

    version_id: str = Field(..., description="Unique Version ID (hash)")

    # Logic
    rules: List[Union[str, RuleConfig]] = Field(default_factory=list)  # Rule IDs or Configs
    features: SolverFeatures = Field(default_factory=SolverFeatures)
    model: Optional[SolverModel] = None

    # Execution & Risk
    risk: Optional[SolverRiskConfig] = Field(default_factory=SolverRiskConfig)
    universe: Optional[SolverUniverse] = Field(default_factory=SolverUniverse)
    exit_logic: ExitLogic = Field(default_factory=ExitLogic)

    # Extra params
    volatility_penalty_threshold: Optional[float] = 0.02
    entry_logic: Optional[Dict[str, Any]] = None

    # Rule Parameter Overrides (Map[RuleID -> Map[Param -> Value]])
    rule_overrides: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    class Config:
        validate_assignment = True


# --------------------------------------------------------
# Meta-Layer Schemas (FR 5.4)
# --------------------------------------------------------


class EditOpType(str, enum.Enum):
    MODIFY_PARAM = "modify_param"
    TOGGLE_FEATURE = "toggle_feature"
    MODIFY_RISK = "modify_risk"
    ADD_RULE = "add_rule"
    REMOVE_RULE = "remove_rule"
    REPLACE_LOGIC = "replace_logic"


class EditOp(BaseModel):
    """
    Single atomic mutation operation (FR 5.4).
    """

    op: EditOpType = Field(..., description="Type of operation")

    # Target context
    rule_id: Optional[str] = Field(None, description="Target rule (if applicable)")
    param_name: Optional[str] = Field(None, description="Target parameter name")
    feature_name: Optional[str] = Field(None, description="Target feature name")

    # Value change
    old_value: Optional[Any] = None
    new_value: Any = Field(..., description="New value to apply")

    reasoning: str = Field(..., description="Why this edit was made (LLM provided)")


class SolverEdit(BaseModel):
    """
    Collection of edits to derive a new solver from a base one.
    """

    base_solver_id: str = Field(..., description="Parent solver ID")
    new_solver_id: str = Field(..., description="Resulting solver ID")

    generated_by: str = Field(..., description="'meta_agent' or 'llm_eod_agent'")
    ops: List[EditOp] = Field(..., description="List of operations applied")

    created_at_utc: datetime = Field(default_factory=datetime.utcnow)


class EvaluationTask(BaseModel):
    """
    Defines a specific historical window and context for evaluating a solver.
    """

    task_id: str = Field(..., description="Unique ID for this task")
    dataset_tag: str = Field("validation", description="Dataset split tag")

    start_time_utc: datetime = Field(..., description="Start of evaluation window")
    end_time_utc: datetime = Field(..., description="End of evaluation window")

    ticker_filter: Optional[List[str]] = None


class LiveContext(BaseModel):
    """
    Run-time context provided to the SolverRouter for selection.
    """

    ticker: str = Field(..., description="Current ticker symbol")
    regime: str = Field("neutral", description="Detected market regime: 'trend_up', 'trend_down', 'chop', 'neutral'")
    time_of_day_utc: datetime = Field(..., description="Current timestamp used for TOD checks")
    current_stage: str = Field("paper", description="System execution stage: 'paper', 'live'")
