"""
ML Pattern Mining Schemas.

Pydantic models for pattern insights and feature importance tracking.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TreeRule(BaseModel):
    """A single decision tree rule extracted from the model."""

    condition: str  # "iv_rank < 0.5 AND session = PRE"
    hit_rate: float  # 0.72
    sample_size: int  # 45
    confidence: float  # Statistical confidence


class FeatureImportance(BaseModel):
    """Feature importance with week-over-week delta."""

    feature: str
    importance: float
    rank: int
    delta_vs_last_week: Optional[float] = None


class PatternInsight(BaseModel):
    """
    Weekly ML pattern insight for a specific prediction target.

    This is what gets passed to the EOD agent for reasoning.
    """

    insight_id: str = Field(..., description="Unique ID for this insight run")
    created_at_utc: datetime
    model_type: str  # "hit_target_50", "avoid_stop", "optimal_exit"

    # Training metadata
    training_window_days: int = 30
    sample_size: int
    positive_rate: float  # Base rate of target

    # Model performance
    train_auc: float
    holdout_auc: float
    precision_at_50: Optional[float] = None  # Precision at 50% recall

    # Top patterns (human-readable rules)
    top_rules: List[TreeRule] = Field(default_factory=list)

    # Feature importance
    top_features: List[FeatureImportance] = Field(default_factory=list)

    # Drift detection
    degraded_features: List[str] = Field(default_factory=list, description="Features with significant importance drop")
    emerging_patterns: List[str] = Field(default_factory=list, description="New patterns relative to last week")

    # Raw for debugging
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MLInsightsSummary(BaseModel):
    """
    Summary of all ML insights for EOD agent consumption.

    This is the top-level object passed to the LLM.
    """

    generated_at_utc: datetime
    insights: Dict[str, PatternInsight] = Field(default_factory=dict)

    # Week-over-week performance delta
    overall_auc_delta: Optional[float] = None

    # Key alerts for LLM attention
    alerts: List[str] = Field(default_factory=list)

    def to_llm_prompt(self) -> str:
        """Format insights for LLM consumption."""
        lines = ["## ML Pattern Insights", ""]

        for model_type, insight in self.insights.items():
            lines.append(f"### {model_type}")
            lines.append(f"- Model AUC: {insight.holdout_auc:.2f} (n={insight.sample_size})")
            lines.append(f"- Base rate: {insight.positive_rate:.1%}")
            lines.append("")

            if insight.top_rules:
                lines.append("**Top Rules:**")
                for rule in insight.top_rules[:5]:
                    lines.append(f"- {rule.condition} → {rule.hit_rate:.0%} hit rate (n={rule.sample_size})")
                lines.append("")

            if insight.degraded_features:
                lines.append(f"**⚠️ Degraded:** {', '.join(insight.degraded_features)}")

            if insight.emerging_patterns:
                lines.append(f"**📈 Emerging:** {', '.join(insight.emerging_patterns)}")

            lines.append("")

        if self.alerts:
            lines.append("### Alerts")
            for alert in self.alerts:
                lines.append(f"- {alert}")

        return "\n".join(lines)
