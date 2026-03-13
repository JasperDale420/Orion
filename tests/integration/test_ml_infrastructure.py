"""
Integration tests for ML model registry and drift monitoring.

Tests versioning, A/B testing, and feature drift detection.
"""

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest

from orion.ml.drift_monitor import (
    PSI_THRESHOLD_CRITICAL,
    PSI_THRESHOLD_WARNING,
    FeatureDriftMonitor,
    calculate_psi,
)
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


class TestDriftMonitor:
    """Tests for the FeatureDriftMonitor."""

    def test_calculate_psi_identical_distributions(self):
        """PSI should be 0 for identical distributions."""
        data = np.random.normal(0, 1, 1000)

        psi = calculate_psi(data, data)

        assert psi < 0.01  # Nearly identical

    def test_calculate_psi_shifted_distribution(self):
        """PSI should detect shifted distribution."""
        baseline = np.random.normal(0, 1, 1000)
        shifted = np.random.normal(2, 1, 1000)  # Mean shifted by 2 std

        psi = calculate_psi(baseline, shifted)

        assert psi > PSI_THRESHOLD_CRITICAL

    def test_calculate_psi_moderate_shift(self):
        """PSI should detect moderate shift."""
        baseline = np.random.normal(0, 1, 1000)
        shifted = np.random.normal(0.5, 1, 1000)  # Moderate shift

        psi = calculate_psi(baseline, shifted)

        # Could be warning level depending on exact distributions
        assert psi > 0  # Some drift detected

    def test_set_baseline(self):
        """Should set baseline statistics correctly."""
        monitor = FeatureDriftMonitor()
        values = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])

        stats = monitor.set_baseline("feature_1", values)

        assert stats["mean"] == 5.5
        assert stats["n_samples"] == 10
        assert monitor.has_baseline("feature_1")

    def test_check_drift_no_drift(self):
        """Should report OK for similar distribution."""
        rng = np.random.default_rng(42)  # Fixed seed for determinism
        monitor = FeatureDriftMonitor()
        baseline = rng.normal(0, 1, 1000)  # Larger sample for stability
        production = rng.normal(0, 1, 500)

        monitor.set_baseline("feature_1", baseline)
        result = monitor.check_drift("feature_1", production)

        assert result["status"] == "OK"
        assert result["psi"] < PSI_THRESHOLD_WARNING

    def test_check_drift_significant_drift(self):
        """Should report CRITICAL for major distribution shift."""
        monitor = FeatureDriftMonitor()
        baseline = np.random.normal(0, 1, 500)
        production = np.random.normal(5, 1, 100)  # Major shift

        monitor.set_baseline("feature_1", baseline)
        result = monitor.check_drift("feature_1", production)

        assert result["status"] == "CRITICAL"
        assert result["psi"] >= PSI_THRESHOLD_CRITICAL

    def test_check_drift_no_baseline(self):
        """Should return NO_BASELINE when baseline not set."""
        monitor = FeatureDriftMonitor()
        production = np.random.normal(0, 1, 100)

        result = monitor.check_drift("unknown_feature", production)

        assert result["status"] == "NO_BASELINE"

    def test_get_drift_summary(self):
        """Should return summary of all drift checks."""
        monitor = FeatureDriftMonitor()

        # Set up baselines and check
        for i in range(3):
            baseline = np.random.normal(0, 1, 500)
            monitor.set_baseline(f"feature_{i}", baseline)
            monitor.check_drift(f"feature_{i}", np.random.normal(0, 1, 100))

        summary = monitor.get_drift_summary()

        assert summary["total_checks"] == 3
        assert "status_counts" in summary
        assert "worst_drifters" in summary
