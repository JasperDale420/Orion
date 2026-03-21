"""
ML Pattern Miner.

Orchestrates training LightGBM models on trading data and extracting
human-readable rules for the EOD agent.

Sub-modules:
  - feature_config: Feature column lists, targets, bucket configs
  - training_data: Heber Gold data loading, normalization, feature joining
  - model_training: LightGBM training, saving, rule/importance extraction
"""

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from orion.ml.feature_config import (
    ALERT_FLOW_CONTEXT_FEATURES,
    ALERT_SECTOR_FLOW_FEATURES,
    ALL_EQUITY_FEATURE_COLUMNS,
    CATEGORICAL_COLUMNS,
    EQUITY_DARKPOOL_FEATURES,
    EQUITY_FLOW_FEATURES,
    EQUITY_FLOW_NORM_FEATURES,
    EQUITY_FLOW_TOXICITY_FEATURES,
    EQUITY_GEX_REGIME_FEATURES,
    EQUITY_GOLD_DATASETS,
    EQUITY_IV_SURFACE_FEATURES,
    EQUITY_MARKET_TIDE_FEATURES,
    EQUITY_MOMENTUM_FEATURES,
    EQUITY_OI_MOMENTUM_FEATURES,
    EQUITY_REGIME_FEATURES,
    EQUITY_STRADDLE_FEATURES,
    EQUITY_TICKER_RATES_FEATURES,
    EQUITY_TREND_SCAN_FEATURES,
    EQUITY_VOLATILITY_FEATURES,
    FEATURE_COLUMNS,
    TARGETS,
    TRADE_BUCKET_CONFIGS,
    get_quick_winner_target,
)
from orion.ml.model_training import (
    MODEL_DIR,
    extract_feature_importance,
    extract_tree_rules,
    prepare_features,
    save_model,
    train_model,
)
from orion.ml.schemas import (
    FeatureImportance,
    MLInsightsSummary,
    PatternInsight,
    TreeRule,
)
from orion.ml.training_data import (
    fetch_training_data,
    prefetch_heber_gold_data,
)
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.pattern_miner")


# ── Schema refresh config helpers ────────────────────────────────────────


def _exit_classifier_schema_refresh_config_from_env() -> tuple[bool, bool]:
    """Read schema-refresh strategy flags for exit-classifier training orchestration."""
    force_schema_refresh, refresh_each_bucket, _source = _exit_classifier_schema_refresh_config_details_from_env()
    return force_schema_refresh, refresh_each_bucket


def _exit_classifier_schema_refresh_config_details_from_env() -> tuple[bool, bool, str]:
    """Read schema-refresh config with source metadata for observability."""
    strategy = (
        os.getenv(
            "ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY",
            "",
        )
        .strip()
        .lower()
    )
    if strategy:
        if strategy in {"off", "disabled", "none", "false"}:
            return False, False, "strategy_env"
        if strategy in {"prefetch_once", "once"}:
            return True, False, "strategy_env"
        if strategy in {"per_bucket", "each_bucket", "each"}:
            return True, True, "strategy_env"
        logger.warning(
            "Invalid ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY; falling back to legacy flags",
            extra={
                "event": "exit_training_schema_refresh_strategy_invalid",
                "strategy": strategy,
            },
        )

    force_schema_refresh = os.getenv(
        "ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH",
        "false",
    ).strip().lower() in {"1", "true", "yes", "y", "on"}
    refresh_each_bucket = os.getenv(
        "ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET",
        "false",
    ).strip().lower() in {"1", "true", "yes", "y", "on"}

    if refresh_each_bucket and not force_schema_refresh:
        logger.warning(
            "Ignoring ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET because force refresh is disabled",
            extra={
                "event": "exit_training_schema_refresh_config_invalid",
                "force_schema_refresh": force_schema_refresh,
                "refresh_each_bucket": refresh_each_bucket,
            },
        )
        refresh_each_bucket = False

    source = "strategy_env_invalid_fallback" if strategy else "legacy_flags"
    return force_schema_refresh, refresh_each_bucket, source


def _exit_classifier_schema_refresh_mode(force_schema_refresh: bool, refresh_each_bucket: bool) -> str:
    """Return a human-readable refresh strategy mode label."""
    if not force_schema_refresh:
        return "off"
    if refresh_each_bucket:
        return "per_bucket"
    return "prefetch_once"


# ── Persistence ──────────────────────────────────────────────────────────


async def get_last_week_importance(model_type: str) -> dict[str, float]:
    """Fetch last week's feature importance for drift detection."""

    cutoff = datetime.now(UTC) - timedelta(days=7)

    async def query(session: Any) -> dict[str, float]:
        stmt = text(
            """
            SELECT feature_name, importance
            FROM ml_feature_importance_history
            WHERE model_type = :model_type
            AND created_at_utc >= :cutoff
            ORDER BY created_at_utc DESC
        """
        )
        result = await session.execute(stmt, {"model_type": model_type, "cutoff": cutoff})
        rows = result.fetchall()
        return {row[0]: row[1] for row in rows}

    return await db_query(query)


async def persist_insight(insight: PatternInsight) -> None:
    """Persist pattern insight to database."""
    from orion.storage.models_ml import MLFeatureImportanceHistory, MLPatternInsight

    async def write(session: Any) -> None:
        session.add(
            MLPatternInsight(
                insight_id=insight.insight_id,
                model_type=insight.model_type,
                training_window_days=insight.training_window_days,
                sample_size=insight.sample_size,
                positive_rate=insight.positive_rate,
                train_auc=insight.train_auc,
                holdout_auc=insight.holdout_auc,
                top_rules_json=[r.model_dump() for r in insight.top_rules],
                top_features_json=[f.model_dump() for f in insight.top_features],
                degraded_features_json=insight.degraded_features,
                emerging_patterns_json=insight.emerging_patterns,
                metadata_json=insight.metadata,
            )
        )

        for feat in insight.top_features:
            session.add(
                MLFeatureImportanceHistory(
                    id=str(uuid.uuid4()),
                    model_type=insight.model_type,
                    feature_name=feat.feature,
                    importance=feat.importance,
                    rank=feat.rank,
                )
            )

    await db_write(write)


# ── Orchestration ────────────────────────────────────────────────────────


async def run_pattern_mining(
    target_name: str = "hit_target_50",
    bucket_name: str | None = None,
    bucket_config: dict[str, Any] | None = None,
    prefetched: tuple[Any, Any, dict[str, Any]] | None = None,
) -> PatternInsight | None:
    """Run full pattern mining pipeline for a single target and optional bucket."""
    model_type = f"{bucket_name}_{target_name}" if bucket_name else target_name

    window_days = bucket_config.get("window_days", 30) if bucket_config else 30
    min_samples = bucket_config.get("min_samples", 50) if bucket_config else 50
    trade_filter = bucket_config.get("filter") if bucket_config else None
    quick_winner_seconds = bucket_config.get("quick_winner_seconds", 3600) if bucket_config else 3600

    logger.info(
        f"Starting pattern mining for {model_type}",
        extra={"bucket": bucket_name, "target": target_name, "window_days": window_days},
    )

    # 1. Fetch data
    df, feature_names = await fetch_training_data(
        window_days=window_days,
        min_samples=min_samples,
        trade_type_filter=trade_filter,
        quick_winner_seconds=quick_winner_seconds,
        prefetched=prefetched,
    )
    if df is None or df.empty:
        logger.warning(f"No training data available for {model_type}")
        return None

    # 2. Prepare features
    X, feature_names, categorical_mappings = prepare_features(df, feature_names)  # noqa: N806
    y = df[f"target_{target_name}"]

    if y.nunique() < 2:
        logger.warning(f"Target has no variance for {model_type}, skipping")
        return None

    # 3. Train model
    try:
        dates = df["entry_ts"] if "entry_ts" in df.columns else None
        model, train_auc, holdout_auc = train_model(X, y, dates=dates)
    except Exception as e:
        logger.error(f"Model training failed for {model_type}: {e}")
        return None

    # 4. Save model
    if holdout_auc >= 0.55:
        save_model(model, model_type, feature_names, categorical_mappings)
    else:
        logger.warning(
            f"Skipping model save for {model_type}: AUC {holdout_auc:.3f} below threshold",
            extra={"event": "model_save_skipped", "model_type": model_type, "auc": holdout_auc},
        )

    # 5. Extract patterns
    top_features = extract_feature_importance(model, feature_names, top_k=10)
    top_rules = extract_tree_rules(model, feature_names, X, y, top_k=5)

    # 6. Detect drift
    last_week = await get_last_week_importance(model_type)
    degraded = []
    for feat in top_features[:5]:
        if feat.feature in last_week:
            old_imp = last_week[feat.feature]
            delta = (feat.importance - old_imp) / max(old_imp, 0.01)
            feat.delta_vs_last_week = delta
            if delta < -0.3:
                degraded.append(f"{feat.feature} (dropped {abs(delta):.0%})")

    # 7. Build insight
    insight = PatternInsight(
        insight_id=hashlib.sha256(f"{model_type}_{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:16],
        created_at_utc=datetime.now(UTC),
        model_type=model_type,
        training_window_days=window_days,
        sample_size=len(df),
        positive_rate=float(y.mean()),
        train_auc=train_auc,
        holdout_auc=holdout_auc,
        top_rules=top_rules,
        top_features=top_features,
        degraded_features=degraded,
        metadata={"bucket": bucket_name, "target": target_name},
    )

    # 8. Persist
    await persist_insight(insight)

    logger.info(
        f"Pattern mining complete for {model_type}",
        extra={
            "event": "ml_mining_complete",
            "model_type": model_type,
            "auc": holdout_auc,
            "sample_size": len(df),
            "rules_extracted": len(top_rules),
        },
    )

    return insight


async def run_all_pattern_mining() -> MLInsightsSummary:
    """Run pattern mining for all bucket x target combinations.

    Produces 4 buckets x 4 targets = 16 entry models + 4 exit models.
    """
    insights: dict[str, PatternInsight] = {}
    alerts: list[str] = []

    prefetched = await prefetch_heber_gold_data()
    if prefetched is None:
        logger.error(
            "Cannot train any models: Heber gold data unavailable after retries",
            extra={"event": "pattern_miner_heber_prefetch_failed"},
        )
        alerts.append("ALL: Heber gold data unavailable - no models trained")
        return MLInsightsSummary(
            generated_at_utc=datetime.now(UTC),
            insights=insights,
            alerts=alerts,
        )

    all_target_names = list(TARGETS.keys()) + ["quick_winner"]
    for bucket_name, bucket_config in TRADE_BUCKET_CONFIGS.items():
        for target_name in all_target_names:
            try:
                insight = await run_pattern_mining(
                    target_name=target_name,
                    bucket_name=bucket_name,
                    bucket_config=bucket_config,
                    prefetched=prefetched,
                )
                if insight:
                    insights[insight.model_type] = insight

                    if insight.holdout_auc < 0.55:
                        alerts.append(f"{insight.model_type}: Model AUC very low ({insight.holdout_auc:.2f})")
                    if insight.degraded_features:
                        alerts.append(
                            f"{insight.model_type}: Feature drift in {len(insight.degraded_features)} features"
                        )

            except Exception as e:
                model_type = f"{bucket_name}_{target_name}"
                logger.error(f"Pattern mining failed for {model_type}: {e}", exc_info=True)
                alerts.append(f"{model_type}: Mining failed - {str(e)[:50]}")

    # Train exit models
    try:
        from orion.ml.exit_classifier import train_all_exit_classifiers

        logger.info("Training exit classifiers for all buckets")
        force_schema_refresh, refresh_each_bucket, refresh_source = (
            _exit_classifier_schema_refresh_config_details_from_env()
        )
        refresh_mode = _exit_classifier_schema_refresh_mode(force_schema_refresh, refresh_each_bucket)
        logger.info(
            "Exit classifier schema refresh config: force=%s refresh_each_bucket=%s",
            force_schema_refresh,
            refresh_each_bucket,
            extra={
                "event": "exit_training_schema_refresh_config_resolved",
                "refresh_mode": refresh_mode,
                "refresh_source": refresh_source,
                "force_schema_refresh": force_schema_refresh,
                "refresh_each_bucket": refresh_each_bucket,
            },
        )
        exit_results = await train_all_exit_classifiers(
            force_schema_refresh=force_schema_refresh,
            refresh_each_bucket=refresh_each_bucket,
        )

        for bucket, data in exit_results.items():
            if data.get("auc", 0) < 0.55:
                alerts.append(f"{bucket}_exit: Exit AUC low ({data.get('auc', 0):.2f})")
            logger.info(
                f"Exit classifier trained for {bucket}: AUC={data.get('auc', 0):.3f}",
                extra={"bucket": bucket, "auc": data.get("auc")},
            )
    except Exception as e:
        logger.error(f"Exit classifier training failed: {e}", exc_info=True)
        alerts.append(f"exit_classifiers: Training failed - {str(e)[:50]}")

    summary = MLInsightsSummary(
        generated_at_utc=datetime.now(UTC),
        insights=insights,
        alerts=alerts,
    )

    logger.info(
        f"All pattern mining complete: {len(insights)} entry models + 4 exit models trained",
        extra={"models_count": len(insights), "alerts_count": len(alerts)},
    )

    return summary
