"""
Model Registry for ML Model Versioning and A/B Testing.

Provides versioning, registration, and rollback capabilities for ML models.
"""

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default model directory
DEFAULT_MODEL_DIR = Path("artifacts/models")


class ModelMetadata:
    """Metadata for a registered model version."""

    def __init__(
        self,
        model_type: str,
        version: int,
        train_auc: float,
        holdout_auc: float,
        created_at: datetime,
        feature_names: list[str],
        model_path: Path,
        is_active: bool = False,
        ab_weight: float = 1.0,
        extra_metrics: dict[str, float] | None = None,
    ):
        self.model_type = model_type
        self.version = version
        self.train_auc = train_auc
        self.holdout_auc = holdout_auc
        self.created_at = created_at
        self.feature_names = feature_names
        self.model_path = model_path
        self.is_active = is_active
        self.ab_weight = ab_weight
        self.extra_metrics = extra_metrics or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "model_type": self.model_type,
            "version": self.version,
            "train_auc": self.train_auc,
            "holdout_auc": self.holdout_auc,
            "created_at": self.created_at.isoformat(),
            "feature_names": self.feature_names,
            "model_path": str(self.model_path),
            "is_active": self.is_active,
            "ab_weight": self.ab_weight,
            "extra_metrics": self.extra_metrics,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelMetadata":
        """Create from dictionary."""
        return cls(
            model_type=data["model_type"],
            version=data["version"],
            train_auc=data["train_auc"],
            holdout_auc=data["holdout_auc"],
            created_at=datetime.fromisoformat(data["created_at"]),
            feature_names=data["feature_names"],
            model_path=Path(data["model_path"]),
            is_active=data.get("is_active", False),
            ab_weight=data.get("ab_weight", 1.0),
            extra_metrics=data.get("extra_metrics", {}),
        )


class ModelRegistry:
    """
    Registry for ML model versioning and A/B testing.

    Features:
    - Version tracking for all model types
    - A/B testing with configurable weights
    - Rollback to previous versions
    - Metadata persistence
    """

    def __init__(self, model_dir: Path | None = None):
        self.model_dir = model_dir or DEFAULT_MODEL_DIR
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._registry_path = self.model_dir / "registry.json"
        self._models: dict[str, list[ModelMetadata]] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        """Load registry from disk."""
        if self._registry_path.exists():
            try:
                with open(self._registry_path) as f:
                    data = json.load(f)
                for model_type, versions in data.get("models", {}).items():
                    self._models[model_type] = [ModelMetadata.from_dict(v) for v in versions]
                logger.info(f"Loaded {sum(len(v) for v in self._models.values())} model versions from registry")
            except Exception as e:
                logger.error(f"Failed to load registry: {e}")
                self._models = {}
        else:
            self._models = {}

    def _save_registry(self) -> None:
        """Persist registry to disk."""
        data = {
            "models": {model_type: [m.to_dict() for m in versions] for model_type, versions in self._models.items()},
            "updated_at": datetime.now(UTC).isoformat(),
        }
        with open(self._registry_path, "w") as f:
            json.dump(data, f, indent=2)

    def register_model(
        self,
        model_type: str,
        model: Any,
        train_auc: float,
        holdout_auc: float,
        feature_names: list[str],
        extra_metrics: dict[str, float] | None = None,
        auto_activate: bool = True,
    ) -> ModelMetadata:
        """
        Register a new model version.

        Args:
            model_type: Model identifier (e.g., '0DTE_hit_target_50')
            model: Trained model object (LightGBM)
            train_auc: Training AUC
            holdout_auc: Holdout/CV AUC
            feature_names: List of feature names
            extra_metrics: Additional metrics to track
            auto_activate: If True, automatically activate as the active version

        Returns:
            ModelMetadata for the registered version
        """
        import joblib

        # Get next version number
        versions = self._models.get(model_type, [])
        next_version = max([v.version for v in versions], default=0) + 1

        # Create versioned directory
        version_dir = self.model_dir / model_type / f"v{next_version}"
        version_dir.mkdir(parents=True, exist_ok=True)

        # Save model
        model_path = version_dir / "model.joblib"
        joblib.dump(model, model_path)

        # Save feature names
        features_path = version_dir / "features.json"
        with open(features_path, "w") as f:
            json.dump(feature_names, f)

        # Create metadata
        metadata = ModelMetadata(
            model_type=model_type,
            version=next_version,
            train_auc=train_auc,
            holdout_auc=holdout_auc,
            created_at=datetime.now(UTC),
            feature_names=feature_names,
            model_path=model_path,
            is_active=auto_activate,
            extra_metrics=extra_metrics or {},
        )

        # Deactivate previous versions if auto-activating
        if auto_activate:
            for v in versions:
                v.is_active = False

        # Add to registry
        if model_type not in self._models:
            self._models[model_type] = []
        self._models[model_type].append(metadata)

        # Persist
        self._save_registry()

        logger.info(
            f"Registered model {model_type} v{next_version}: AUC={holdout_auc:.3f}",
            extra={
                "event": "model_registered",
                "model_type": model_type,
                "version": next_version,
                "holdout_auc": holdout_auc,
            },
        )

        return metadata

    def get_active_model(self, model_type: str) -> Any | None:
        """
        Get the currently active model for a type.

        Returns:
            Loaded model or None if no active version
        """
        import joblib

        versions = self._models.get(model_type, [])
        active = [v for v in versions if v.is_active]

        if not active:
            return None

        # Return the most recent active version
        latest = max(active, key=lambda v: v.version)

        if latest.model_path.exists():
            return joblib.load(latest.model_path)

        logger.warning(f"Model file not found: {latest.model_path}")
        return None

    def get_active_metadata(self, model_type: str) -> ModelMetadata | None:
        """Get metadata for the active model version."""
        versions = self._models.get(model_type, [])
        active = [v for v in versions if v.is_active]

        if not active:
            return None

        return max(active, key=lambda v: v.version)

    def rollback_model(self, model_type: str, target_version: int) -> bool:
        """
        Rollback to a specific model version.

        Args:
            model_type: Model identifier
            target_version: Version number to rollback to

        Returns:
            True if rollback successful
        """
        versions = self._models.get(model_type, [])
        target = [v for v in versions if v.version == target_version]

        if not target:
            logger.warning(f"Version {target_version} not found for {model_type}")
            return False

        # Deactivate all, activate target
        for v in versions:
            v.is_active = v.version == target_version

        self._save_registry()

        logger.info(
            f"Rolled back {model_type} to v{target_version}",
            extra={"event": "model_rollback", "model_type": model_type, "version": target_version},
        )

        return True

    def list_versions(self, model_type: str) -> list[ModelMetadata]:
        """List all versions for a model type."""
        return sorted(
            self._models.get(model_type, []),
            key=lambda v: v.version,
            reverse=True,
        )

    def configure_ab_test(self, model_type: str, versions: dict[int, float]) -> None:
        """
        Configure A/B testing weights for model versions.

        Args:
            model_type: Model identifier
            versions: Dict mapping version numbers to weights (should sum to 1.0)
        """
        model_versions = self._models.get(model_type, [])

        for v in model_versions:
            if v.version in versions:
                v.is_active = True
                v.ab_weight = versions[v.version]
            else:
                v.is_active = False
                v.ab_weight = 0.0

        self._save_registry()

        logger.info(
            f"Configured A/B test for {model_type}: {versions}",
            extra={"event": "ab_test_configured", "model_type": model_type, "weights": versions},
        )

    def get_ab_model(self, model_type: str) -> Any | None:
        """
        Get a model based on A/B testing weights.

        Uses weighted random selection among active versions.
        """
        import random

        import joblib

        versions = self._models.get(model_type, [])
        active = [v for v in versions if v.is_active and v.ab_weight > 0]

        if not active:
            return None

        total_weight = sum(v.ab_weight for v in active)
        if total_weight <= 0:
            return None

        # Weighted random selection
        r = random.uniform(0, total_weight)
        cumulative = 0.0
        selected = active[0]

        for v in active:
            cumulative += v.ab_weight
            if r <= cumulative:
                selected = v
                break

        if selected.model_path.exists():
            return joblib.load(selected.model_path)

        return None

    def cleanup_old_versions(self, model_type: str, keep_n: int = 5) -> int:
        """
        Remove old model versions, keeping only the most recent N.

        Args:
            model_type: Model identifier
            keep_n: Number of versions to keep

        Returns:
            Number of versions removed
        """
        versions = self._models.get(model_type, [])
        if len(versions) <= keep_n:
            return 0

        # Sort by version, keep newest
        sorted_versions = sorted(versions, key=lambda v: v.version, reverse=True)
        to_keep = sorted_versions[:keep_n]
        to_remove = sorted_versions[keep_n:]

        # Remove model files
        removed = 0
        for v in to_remove:
            if v.model_path.parent.exists():
                shutil.rmtree(v.model_path.parent)
                removed += 1

        # Update registry
        self._models[model_type] = to_keep
        self._save_registry()

        logger.info(f"Cleaned up {removed} old versions for {model_type}")
        return removed


# Global registry instance
_registry: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    """Get or create the global model registry."""
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry
