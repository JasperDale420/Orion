from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from orion.config import risk_settings
from orion.core.feature_registry import FeatureRegistry
from orion.core.rule_registry import RuleRegistry


class SolverDSLFeatures(BaseModel):
    feature_set_id: str = Field(default="v1_legacy")
    event_features: List[str] = Field(default_factory=list)
    window_features: List[str] = Field(default_factory=list)
    feature_engine_version: str = Field(default="v1")

    @field_validator("feature_set_id")
    @classmethod
    def _validate_feature_set_id(cls, v: str) -> str:
        if not FeatureRegistry.validate_id(v):
            raise ValueError(f"Invalid feature_set_id={v}; available={FeatureRegistry.list_all()}")
        return v


class SolverDSLModel(BaseModel):
    type: Literal["meta_classifier", "none"] = "meta_classifier"
    model_version: Optional[str] = None
    model_uri: Optional[str] = None
    thresholds: Dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_model_ref(self):
        # If a model is requested, require at least one reference.
        if self.type != "none" and not (self.model_version or self.model_uri):
            raise ValueError("model.type=meta_classifier requires model_version or model_uri")
        return self


class SolverDSLRisk(BaseModel):
    risk_per_trade_pct: Optional[float] = None
    risk_per_trade_bps: Optional[int] = None
    max_positions: int = Field(default=5, ge=1)
    max_ticker_exposure_pct: float = Field(default=5.0, gt=0)
    time_of_day_bans: List[str] = Field(default_factory=list)
    session_filter: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_risk_bounds(self):
        # Normalize pct->bps if provided.
        if self.risk_per_trade_bps is None and self.risk_per_trade_pct is not None:
            self.risk_per_trade_bps = int(round(float(self.risk_per_trade_pct) * 10000.0))
        if self.risk_per_trade_pct is None and self.risk_per_trade_bps is not None:
            self.risk_per_trade_pct = float(self.risk_per_trade_bps) / 10000.0

        if self.risk_per_trade_bps is None:
            # Default to system risk setting if not provided
            self.risk_per_trade_bps = int(round(float(risk_settings.risk_per_trade_pct) * 10000.0))
            self.risk_per_trade_pct = float(self.risk_per_trade_bps) / 10000.0

        if self.risk_per_trade_bps > int(risk_settings.max_system_bps):
            raise ValueError(
                f"risk_per_trade_bps={self.risk_per_trade_bps} exceeds max_system_bps={risk_settings.max_system_bps}"
            )
        if self.max_positions > int(risk_settings.max_positions):
            raise ValueError(
                f"max_positions={self.max_positions} exceeds system max_positions={risk_settings.max_positions}"
            )

        return self


class SolverDSLExecution(BaseModel):
    order_type: Literal["MARKET", "LIMIT"] = "LIMIT"
    max_spread_frac: float = Field(default=0.2, ge=0.0, le=1.0)
    slippage_bps_assumed: int = Field(default=20, ge=0, le=1000)


class SolverDSLPromotionPolicy(BaseModel):
    target_stage: Literal["research", "shadow", "paper", "limited_live", "scaled_live"] = "paper"
    min_trades_for_eval: int = Field(default=100, ge=0)
    gates_profile: str = Field(default="default")


class SolverDSL(BaseModel):
    """
    PRD Addendum §5.1: strict solver DSL schema stored in solvers.definition_json.
    """

    rules: List[str] = Field(default_factory=list)
    features: SolverDSLFeatures = Field(default_factory=SolverDSLFeatures)
    model: SolverDSLModel = Field(default_factory=SolverDSLModel)
    risk: SolverDSLRisk = Field(default_factory=SolverDSLRisk)
    execution: SolverDSLExecution = Field(default_factory=SolverDSLExecution)
    promotion_policy: SolverDSLPromotionPolicy = Field(default_factory=SolverDSLPromotionPolicy)

    @field_validator("rules")
    @classmethod
    def _validate_rules(cls, v: List[str]) -> List[str]:
        for rule_id in v:
            if not RuleRegistry.exists(rule_id):
                raise ValueError(f"Unknown rule_id={rule_id}; available={RuleRegistry.list_all()}")
        return v

    @classmethod
    def from_legacy_config(cls, cfg: Dict[str, Any]) -> "SolverDSL":
        """
        Best-effort conversion from the legacy SolverConfig JSON blob to PRD DSL.
        """
        risk_blob = cfg.get("risk") or {}
        features_blob = cfg.get("features") or {}
        model_blob = cfg.get("model") or {}

        rules_list: List[str] = []
        for r in cfg.get("rules", []) or []:
            if isinstance(r, str):
                rules_list.append(r)
            elif isinstance(r, dict) and "id" in r:
                rules_list.append(str(r["id"]))

        return cls(
            rules=rules_list,
            features={
                "feature_set_id": features_blob.get("feature_set_id", "v1_legacy"),
                "event_features": features_blob.get("event_features", []) or [],
                "window_features": features_blob.get("window_features", []) or [],
                "feature_engine_version": features_blob.get("feature_engine_version", "v1"),
            },
            model={
                "type": (model_blob.get("type") or "none") if model_blob is not None else "none",
                "model_version": model_blob.get("model_version") if model_blob else None,
                "model_uri": model_blob.get("model_uri") if model_blob else None,
                "thresholds": model_blob.get("thresholds", {}) if model_blob else {},
            },
            risk={
                "risk_per_trade_bps": (risk_blob.get("risk_per_trade_bps") if risk_blob else None),
                "max_positions": (
                    risk_blob.get("max_open_positions") if risk_blob else risk_blob.get("max_positions", 5)
                ),
                "max_ticker_exposure_pct": risk_blob.get("max_ticker_exposure_pct", 5.0) if risk_blob else 5.0,
                "time_of_day_bans": risk_blob.get("time_of_day_bans") or [],
                "session_filter": risk_blob.get("session_filter") or [],
            },
        )
