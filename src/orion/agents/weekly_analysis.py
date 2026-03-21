"""Weekly analysis and recommendation helpers for MetaSearchAgent.

Pure functions that analyze weekly trade execution quality, ML model drift,
and generate evolution recommendations. No class state required.
"""

from __future__ import annotations

from typing import Any

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger(__name__)


def analyze_execution_quality(week_data: dict[str, Any]) -> dict[str, Any]:
    """Analyze trade execution quality vs expectations."""
    trade_data = week_data.get("trade_execution", {}).get("trades", {})

    analysis = {
        "total_orders": trade_data.get("total_orders", 0),
        "fill_rate": trade_data.get("fill_rate", 0.0),
        "rejection_rate": 0.0,
        "unique_tickers": len(trade_data.get("tickers", [])),
        "execution_health": "unknown",
    }

    total = trade_data.get("total_orders", 0)
    if total > 0:
        rejected = trade_data.get("rejected", 0)
        analysis["rejection_rate"] = rejected / total

        if analysis["fill_rate"] >= 0.9 and analysis["rejection_rate"] < 0.05:
            analysis["execution_health"] = "excellent"
        elif analysis["fill_rate"] >= 0.7:
            analysis["execution_health"] = "good"
        elif analysis["fill_rate"] >= 0.5:
            analysis["execution_health"] = "degraded"
        else:
            analysis["execution_health"] = "poor"

    return analysis


def analyze_ml_drift(week_data: dict[str, Any]) -> dict[str, Any]:
    """Analyze ML model drift from pattern miner insights.

    Uses drift_analysis computed by WeeklyDataAggregator from per-bucket AUC
    scores collected in EOD reports.
    """
    eod_data = week_data.get("eod_reports", {})
    ml_insights = week_data.get("ml_insights", {})

    drift_analysis: dict[str, Any] = {
        "buckets_analyzed": [],
        "degrading_buckets": [],
        "improving_buckets": [],
        "stable_buckets": [],
        "insufficient_buckets": [],
        "top_features": eod_data.get("top_features", {}),
        "overall_health": "unknown",
    }

    drift_info = ml_insights.get("drift_analysis", {})
    for bucket, info in drift_info.items():
        drift_analysis["buckets_analyzed"].append(bucket)
        trend = info.get("trend", "stable")

        if trend == "degrading":
            drift_analysis["degrading_buckets"].append(
                {
                    "bucket": bucket,
                    "auc_drop": info.get("drift", 0),
                    "current_auc": info.get("current_auc"),
                }
            )
        elif trend == "improving":
            drift_analysis["improving_buckets"].append(bucket)
        elif trend == "insufficient":
            drift_analysis["insufficient_buckets"].append(bucket)
        else:
            drift_analysis["stable_buckets"].append(bucket)

    n_total = len(drift_analysis["buckets_analyzed"])
    n_degrading = len(drift_analysis["degrading_buckets"])
    n_insufficient = len(drift_analysis["insufficient_buckets"])

    if n_total == 0:
        drift_analysis["overall_health"] = "no_data"
        drift_analysis["message"] = "No ML AUC scores found in this week's EOD reports."
    elif n_insufficient == n_total:
        drift_analysis["overall_health"] = "insufficient_data"
        drift_analysis["message"] = (
            f"{n_total} bucket(s) found but each has fewer than 2 data points. "
            f"Need at least 2 trading days with ML scores to compute drift."
        )
    elif n_degrading == 0:
        drift_analysis["overall_health"] = "healthy"
    elif n_degrading / max(n_total - n_insufficient, 1) < 0.3:
        drift_analysis["overall_health"] = "minor_drift"
    else:
        drift_analysis["overall_health"] = "significant_drift"

    return drift_analysis


async def generate_weekly_recommendations(
    week_data: dict[str, Any],
    execution_analysis: dict[str, Any],
    drift_analysis: dict[str, Any],
    fetch_active_solvers: Any,
) -> dict[str, Any]:
    """Generate evolution recommendations based on weekly analysis.

    fetch_active_solvers is a coroutine that returns a list of active Solver objects.
    """
    recommendations: dict[str, Any] = {
        "proposed_edits": [],
        "alerts": [],
        "insights": [],
    }

    if execution_analysis.get("execution_health") in ["degraded", "poor"]:
        recommendations["alerts"].append(
            {
                "type": "execution_degradation",
                "severity": "high",
                "message": f"Execution fill rate at {execution_analysis['fill_rate']:.1%}",
                "action": "Review order parameters and market conditions",
            }
        )

    if drift_analysis.get("overall_health") == "significant_drift":
        for bucket_info in drift_analysis.get("degrading_buckets", []):
            recommendations["alerts"].append(
                {
                    "type": "ml_drift",
                    "severity": "medium",
                    "message": f"Model {bucket_info['bucket']} AUC dropped by {abs(bucket_info.get('auc_drop', 0)):.3f}",
                    "action": "Consider retraining or feature engineering",
                }
            )

    top_features = drift_analysis.get("top_features", {})
    if top_features:
        active_solvers = await fetch_active_solvers()

        for solver in active_solvers:
            feature_list = list(top_features.keys())[:3]
            if feature_list:
                recommendations["proposed_edits"].append(
                    {
                        "base_solver_id": solver.solver_id,
                        "reason": f"Incorporate top-performing features: {', '.join(feature_list)}",
                        "context": (
                            f"Weekly analysis shows top features: {feature_list}. "
                            f"Execution health: {execution_analysis.get('execution_health')}. "
                            f"ML drift: {drift_analysis.get('overall_health')}. "
                            f"Propose parameter adjustments to align with these signals."
                        ),
                    }
                )

    eod_summary = week_data.get("eod_reports", {})
    if eod_summary.get("trading_days", 0) > 0:
        win_rate = eod_summary.get("executed_count", 0) / max(eod_summary.get("total_decisions", 1), 1)
        recommendations["insights"].append(
            {
                "metric": "decision_execution_rate",
                "value": win_rate,
                "interpretation": f"{win_rate:.1%} of decisions resulted in execution",
            }
        )

    return recommendations
