"""
Integration tests for ML model registry and drift monitoring.

Tests versioning, A/B testing, and feature drift detection.
"""

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest

from orion.ml.model_registry import ModelRegistry


# Module-level MockModel for picklability (required by joblib)
class PicklableMockModel:
    """A simple mock model that can be pickled."""

    def __init__(self, version: int = 1):
        self.version = version

    def predict_proba(self, X):  # noqa: N803
        return np.array([[0.5, 0.5]])


class TestModelRegistry:
    """Tests for the ModelRegistry."""

    @pytest.fixture
    def temp_registry(self):
        """Create a temporary registry for testing."""
        temp_dir = Path(tempfile.mkdtemp())
        registry = ModelRegistry(model_dir=temp_dir)
        yield registry
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_register_model(self, temp_registry):
        """Should register a model and assign version."""
        model = PicklableMockModel(version=1)
        metadata = temp_registry.register_model(
            model_type="test_model",
            model=model,
            train_auc=0.85,
            holdout_auc=0.80,
            feature_names=["feature_1", "feature_2"],
        )

        assert metadata.version == 1
        assert metadata.model_type == "test_model"
        assert metadata.holdout_auc == 0.80
        assert metadata.is_active is True

    def test_version_incrementing(self, temp_registry):
        """Should increment version for each registration."""
        temp_registry.register_model("test_model", PicklableMockModel(1), 0.8, 0.75, ["f1"])
        temp_registry.register_model("test_model", PicklableMockModel(2), 0.85, 0.78, ["f1"])
        metadata = temp_registry.register_model("test_model", PicklableMockModel(3), 0.9, 0.82, ["f1"])

        assert metadata.version == 3

    def test_list_versions(self, temp_registry):
        """Should list all versions for a model type."""
        temp_registry.register_model("test_model", PicklableMockModel(1), 0.8, 0.75, ["f1"])
        temp_registry.register_model("test_model", PicklableMockModel(2), 0.85, 0.78, ["f1"])

        versions = temp_registry.list_versions("test_model")

        assert len(versions) == 2
        assert versions[0].version == 2  # Most recent first
        assert versions[1].version == 1

    def test_get_active_metadata(self, temp_registry):
        """Should return metadata for active version."""
        temp_registry.register_model("test_model", PicklableMockModel(1), 0.8, 0.75, ["f1"])

        metadata = temp_registry.get_active_metadata("test_model")

        assert metadata is not None
        assert metadata.is_active is True

    def test_rollback_model(self, temp_registry):
        """Should rollback to a previous version."""
        temp_registry.register_model("test_model", PicklableMockModel(1), 0.8, 0.75, ["f1"])
        temp_registry.register_model("test_model", PicklableMockModel(2), 0.85, 0.78, ["f1"])

        result = temp_registry.rollback_model("test_model", target_version=1)

        assert result is True
        active = temp_registry.get_active_metadata("test_model")
        assert active.version == 1
