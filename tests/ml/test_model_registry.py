"""
Tests for ModelRegistry and ModelMetadata.

Covers version registration, activation, rollback, A/B testing,
serialization round-trips, and cleanup.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orion.ml.model_registry import ModelMetadata, ModelRegistry


# ---------------------------------------------------------------------------
# ModelMetadata
# ---------------------------------------------------------------------------


class TestModelMetadata:
    """Tests for ModelMetadata data class."""

    @pytest.mark.unit
    def test_to_dict_contains_all_fields(self) -> None:
        """to_dict should serialise every field."""
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        md = ModelMetadata(
            model_type="entry",
            version=3,
            train_auc=0.85,
            holdout_auc=0.82,
            created_at=now,
            feature_names=["f1", "f2"],
            model_path=Path("/tmp/model.joblib"),
            is_active=True,
            ab_weight=0.6,
            extra_metrics={"precision": 0.9},
        )
        d = md.to_dict()

        assert d["model_type"] == "entry"
        assert d["version"] == 3
        assert d["train_auc"] == 0.85
        assert d["holdout_auc"] == 0.82
        assert d["created_at"] == now.isoformat()
        assert d["feature_names"] == ["f1", "f2"]
        assert d["model_path"] == "/tmp/model.joblib"
        assert d["is_active"] is True
        assert d["ab_weight"] == 0.6
        assert d["extra_metrics"] == {"precision": 0.9}

    @pytest.mark.unit
    def test_from_dict_round_trip(self) -> None:
        """from_dict(to_dict()) should preserve every field."""
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        original = ModelMetadata(
            model_type="exit",
            version=7,
            train_auc=0.91,
            holdout_auc=0.88,
            created_at=now,
            feature_names=["x1", "x2", "x3"],
            model_path=Path("/models/exit/v7/model.joblib"),
            is_active=False,
            ab_weight=0.3,
            extra_metrics={"recall": 0.77},
        )

        restored = ModelMetadata.from_dict(original.to_dict())

        assert restored.model_type == original.model_type
        assert restored.version == original.version
        assert restored.train_auc == original.train_auc
        assert restored.holdout_auc == original.holdout_auc
        assert restored.created_at == original.created_at
        assert restored.feature_names == original.feature_names
        assert restored.model_path == original.model_path
        assert restored.is_active == original.is_active
        assert restored.ab_weight == original.ab_weight
        assert restored.extra_metrics == original.extra_metrics

    @pytest.mark.unit
    def test_from_dict_defaults(self) -> None:
        """from_dict should use defaults for optional keys."""
        data = {
            "model_type": "entry",
            "version": 1,
            "train_auc": 0.8,
            "holdout_auc": 0.75,
            "created_at": "2025-01-01T00:00:00+00:00",
            "feature_names": [],
            "model_path": "/tmp/m.joblib",
        }
        md = ModelMetadata.from_dict(data)

        assert md.is_active is False
        assert md.ab_weight == 1.0
        assert md.extra_metrics == {}

    @pytest.mark.unit
    def test_extra_metrics_defaults_to_empty_dict(self) -> None:
        """Passing None for extra_metrics should produce an empty dict."""
        md = ModelMetadata(
            model_type="entry",
            version=1,
            train_auc=0.8,
            holdout_auc=0.75,
            created_at=datetime.now(UTC),
            feature_names=[],
            model_path=Path("/tmp/m.joblib"),
            extra_metrics=None,
        )
        assert md.extra_metrics == {}


# ---------------------------------------------------------------------------
# ModelRegistry
# ---------------------------------------------------------------------------


class TestModelRegistry:
    """Tests for ModelRegistry class."""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> ModelRegistry:
        """Create a registry backed by a temp directory."""
        return ModelRegistry(model_dir=tmp_path / "models")

    # -- initialisation / persistence --

    @pytest.mark.unit
    def test_init_creates_model_dir(self, tmp_path: Path) -> None:
        """Constructor should create the model directory."""
        model_dir = tmp_path / "new_models"
        assert not model_dir.exists()
        ModelRegistry(model_dir=model_dir)
        assert model_dir.exists()

    @pytest.mark.unit
    def test_init_empty_registry(self, registry: ModelRegistry) -> None:
        """Fresh registry has no models."""
        assert registry._models == {}

    @pytest.mark.unit
    def test_load_persisted_registry(self, tmp_path: Path) -> None:
        """A registry should load previously persisted data."""
        model_dir = tmp_path / "models"
        model_dir.mkdir(parents=True)
        registry_path = model_dir / "registry.json"

        now = datetime.now(UTC)
        data = {
            "models": {
                "entry": [
                    {
                        "model_type": "entry",
                        "version": 1,
                        "train_auc": 0.8,
                        "holdout_auc": 0.75,
                        "created_at": now.isoformat(),
                        "feature_names": ["f1"],
                        "model_path": str(model_dir / "entry" / "v1" / "model.joblib"),
                        "is_active": True,
                        "ab_weight": 1.0,
                        "extra_metrics": {},
                    }
                ]
            },
            "updated_at": now.isoformat(),
        }
        with open(registry_path, "w") as f:
            json.dump(data, f)

        reg = ModelRegistry(model_dir=model_dir)
        assert "entry" in reg._models
        assert len(reg._models["entry"]) == 1
        assert reg._models["entry"][0].version == 1

    @pytest.mark.unit
    def test_load_empty_registry_file(self, tmp_path: Path) -> None:
        """Empty registry file should result in an empty registry, not a crash."""
        model_dir = tmp_path / "models"
        model_dir.mkdir(parents=True)
        registry_path = model_dir / "registry.json"
        registry_path.write_text("")

        reg = ModelRegistry(model_dir=model_dir)
        assert reg._models == {}

    @pytest.mark.unit
    def test_load_corrupt_registry_falls_back_empty(self, tmp_path: Path) -> None:
        """Corrupt JSON should result in an empty registry, not a crash."""
        model_dir = tmp_path / "models"
        model_dir.mkdir(parents=True)
        registry_path = model_dir / "registry.json"
        registry_path.write_text("NOT VALID JSON{{{")

        reg = ModelRegistry(model_dir=model_dir)
        assert reg._models == {}

    # -- register_model --

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_register_model_first_version(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """First registration should create version 1."""
        fake_model = MagicMock()

        md = registry.register_model(
            model_type="entry",
            model=fake_model,
            train_auc=0.85,
            holdout_auc=0.82,
            feature_names=["f1", "f2"],
        )

        assert md.version == 1
        assert md.is_active is True
        assert md.model_type == "entry"
        assert md.train_auc == 0.85
        assert md.holdout_auc == 0.82
        assert md.feature_names == ["f1", "f2"]
        mock_dump.assert_called_once()

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_register_model_increments_version(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """Successive registrations should increment the version number."""
        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])
        md2 = registry.register_model("entry", MagicMock(), 0.85, 0.82, ["f1", "f2"])

        assert md2.version == 2

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_register_model_auto_activate_deactivates_previous(
        self, mock_dump: MagicMock, registry: ModelRegistry
    ) -> None:
        """auto_activate should deactivate previous versions."""
        md1 = registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])
        assert md1.is_active is True

        registry.register_model("entry", MagicMock(), 0.85, 0.82, ["f1"])

        # The first version should now be inactive
        assert registry._models["entry"][0].is_active is False
        assert registry._models["entry"][1].is_active is True

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_register_model_no_auto_activate(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """auto_activate=False should leave new model inactive."""
        md = registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"], auto_activate=False)
        assert md.is_active is False

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_register_model_persists_features_json(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """Registration should write features.json alongside the model."""
        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["alpha", "beta"])

        features_path = registry.model_dir / "entry" / "v1" / "features.json"
        assert features_path.exists()
        with open(features_path) as f:
            assert json.load(f) == ["alpha", "beta"]

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_register_model_saves_registry_json(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """Registration should persist registry.json."""
        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])

        assert registry._registry_path.exists()
        with open(registry._registry_path) as f:
            data = json.load(f)
        assert "entry" in data["models"]
        assert len(data["models"]["entry"]) == 1

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_register_model_extra_metrics(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """Extra metrics should be stored in metadata."""
        md = registry.register_model(
            "entry", MagicMock(), 0.8, 0.75, ["f1"], extra_metrics={"precision": 0.9, "recall": 0.85}
        )
        assert md.extra_metrics == {"precision": 0.9, "recall": 0.85}

    # -- get_active_model / get_active_metadata --

    @pytest.mark.unit
    def test_get_active_metadata_no_models(self, registry: ModelRegistry) -> None:
        """Should return None when no models registered."""
        assert registry.get_active_metadata("nonexistent") is None

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_get_active_metadata_returns_latest_active(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """Should return the most recent active version."""
        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])
        registry.register_model("entry", MagicMock(), 0.85, 0.82, ["f1", "f2"])

        md = registry.get_active_metadata("entry")
        assert md is not None
        assert md.version == 2

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_get_active_model_no_active_returns_none(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """Should return None when no version is active."""
        md = registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"], auto_activate=False)
        assert md.is_active is False

        result = registry.get_active_model("entry")
        assert result is None

    @pytest.mark.unit
    @patch("joblib.load")
    @patch("joblib.dump")
    def test_get_active_model_loads_via_joblib(
        self, mock_dump: MagicMock, mock_load: MagicMock, registry: ModelRegistry
    ) -> None:
        """Should load the model file via joblib when it exists on disk."""
        fake_model = MagicMock()
        mock_load.return_value = fake_model

        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])

        active_md = registry.get_active_metadata("entry")
        with patch.object(Path, "exists", return_value=True):
            loaded = registry.get_active_model("entry")

        assert loaded is fake_model
        mock_load.assert_called_once_with(active_md.model_path)

    @pytest.mark.unit
    @patch("joblib.load")
    @patch("joblib.dump")
    def test_get_active_model_missing_file_returns_none(
        self, mock_dump: MagicMock, mock_load: MagicMock, registry: ModelRegistry
    ) -> None:
        """Should return None when model file doesn't exist on disk."""
        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])

        with patch.object(Path, "exists", return_value=False):
            result = registry.get_active_model("entry")
        assert result is None

    # -- rollback_model --

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_rollback_to_previous_version(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """Rollback should activate the target version and deactivate others."""
        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])
        registry.register_model("entry", MagicMock(), 0.85, 0.82, ["f1", "f2"])

        assert registry.get_active_metadata("entry").version == 2

        success = registry.rollback_model("entry", target_version=1)
        assert success is True
        assert registry.get_active_metadata("entry").version == 1
        # Version 2 should be inactive
        assert registry._models["entry"][1].is_active is False

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_rollback_nonexistent_version_returns_false(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """Rollback to a version that doesn't exist should fail."""
        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])

        success = registry.rollback_model("entry", target_version=99)
        assert success is False

    @pytest.mark.unit
    def test_rollback_no_models_returns_false(self, registry: ModelRegistry) -> None:
        """Rollback when no models exist should fail."""
        success = registry.rollback_model("entry", target_version=1)
        assert success is False

    # -- list_versions --

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_list_versions_sorted_descending(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """Versions should be returned newest first."""
        registry.register_model("entry", MagicMock(), 0.80, 0.75, ["f1"])
        registry.register_model("entry", MagicMock(), 0.85, 0.82, ["f1"])
        registry.register_model("entry", MagicMock(), 0.90, 0.88, ["f1"])

        versions = registry.list_versions("entry")
        assert len(versions) == 3
        assert [v.version for v in versions] == [3, 2, 1]

    @pytest.mark.unit
    def test_list_versions_unknown_type_returns_empty(self, registry: ModelRegistry) -> None:
        """Listing versions for unknown model type returns empty list."""
        assert registry.list_versions("nonexistent") == []

    # -- configure_ab_test / get_ab_model --

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_configure_ab_test_sets_weights(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """configure_ab_test should set weights and activate specified versions."""
        registry.register_model("entry", MagicMock(), 0.80, 0.75, ["f1"])
        registry.register_model("entry", MagicMock(), 0.85, 0.82, ["f1"])

        registry.configure_ab_test("entry", {1: 0.3, 2: 0.7})

        v1, v2 = registry._models["entry"]
        assert v1.is_active is True
        assert v1.ab_weight == 0.3
        assert v2.is_active is True
        assert v2.ab_weight == 0.7

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_configure_ab_test_deactivates_omitted_versions(
        self, mock_dump: MagicMock, registry: ModelRegistry
    ) -> None:
        """Versions not included in the weight dict should be deactivated."""
        registry.register_model("entry", MagicMock(), 0.80, 0.75, ["f1"])
        registry.register_model("entry", MagicMock(), 0.85, 0.82, ["f1"])
        registry.register_model("entry", MagicMock(), 0.90, 0.88, ["f1"])

        # Only include v2 and v3
        registry.configure_ab_test("entry", {2: 0.5, 3: 0.5})

        v1, v2, v3 = registry._models["entry"]
        assert v1.is_active is False
        assert v1.ab_weight == 0.0
        assert v2.is_active is True
        assert v3.is_active is True

    @pytest.mark.unit
    def test_get_ab_model_no_active_returns_none(self, registry: ModelRegistry) -> None:
        """get_ab_model with no active versions should return None."""
        assert registry.get_ab_model("nonexistent") is None

    @pytest.mark.unit
    @patch("joblib.load")
    @patch("joblib.dump")
    def test_get_ab_model_single_version(
        self, mock_dump: MagicMock, mock_load: MagicMock, registry: ModelRegistry
    ) -> None:
        """get_ab_model with a single active version always picks it."""
        fake_model = MagicMock()
        mock_load.return_value = fake_model

        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])
        registry.configure_ab_test("entry", {1: 1.0})

        with patch.object(Path, "exists", return_value=True):
            result = registry.get_ab_model("entry")
        assert result is fake_model

    @pytest.mark.unit
    @patch("joblib.load")
    @patch("joblib.dump")
    @patch("random.uniform")
    def test_get_ab_model_weighted_selection(
        self, mock_uniform: MagicMock, mock_dump: MagicMock, mock_load: MagicMock, registry: ModelRegistry
    ) -> None:
        """get_ab_model should use weighted random selection."""
        fake_model_v1 = MagicMock(name="v1_model")
        fake_model_v2 = MagicMock(name="v2_model")

        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])
        registry.register_model("entry", MagicMock(), 0.85, 0.82, ["f1"])
        registry.configure_ab_test("entry", {1: 0.3, 2: 0.7})

        # random.uniform returns 0.2 -> falls in v1 bucket (cumulative weight 0.3)
        mock_uniform.return_value = 0.2
        mock_load.return_value = fake_model_v1
        with patch.object(Path, "exists", return_value=True):
            result = registry.get_ab_model("entry")
        assert result is fake_model_v1

        # random.uniform returns 0.5 -> falls in v2 bucket (cumulative weight 1.0)
        mock_uniform.return_value = 0.5
        mock_load.return_value = fake_model_v2
        with patch.object(Path, "exists", return_value=True):
            result = registry.get_ab_model("entry")
        assert result is fake_model_v2

    @pytest.mark.unit
    @patch("joblib.load")
    @patch("joblib.dump")
    def test_get_ab_model_missing_file_returns_none(
        self, mock_dump: MagicMock, mock_load: MagicMock, registry: ModelRegistry
    ) -> None:
        """get_ab_model should return None when model file is missing."""
        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])
        registry.configure_ab_test("entry", {1: 1.0})

        with patch.object(Path, "exists", return_value=False):
            result = registry.get_ab_model("entry")
        assert result is None

    # -- cleanup_old_versions --

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_cleanup_removes_old_versions(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """cleanup_old_versions should remove oldest versions and keep newest N."""
        for i in range(7):
            registry.register_model("entry", MagicMock(), 0.8 + i * 0.01, 0.75 + i * 0.01, ["f1"])

        # There are now 7 versions; the version dirs were created by register_model
        assert len(registry._models["entry"]) == 7

        removed = registry.cleanup_old_versions("entry", keep_n=3)

        assert removed == 4
        assert len(registry._models["entry"]) == 3
        # Kept versions should be the 3 newest
        remaining_versions = [v.version for v in registry._models["entry"]]
        assert sorted(remaining_versions, reverse=True) == [7, 6, 5]

    @pytest.mark.unit
    @patch("joblib.dump")
    def test_cleanup_fewer_than_keep_n_removes_nothing(self, mock_dump: MagicMock, registry: ModelRegistry) -> None:
        """If total versions <= keep_n, nothing should be removed."""
        registry.register_model("entry", MagicMock(), 0.8, 0.75, ["f1"])
        registry.register_model("entry", MagicMock(), 0.85, 0.82, ["f1"])

        removed = registry.cleanup_old_versions("entry", keep_n=5)
        assert removed == 0
        assert len(registry._models["entry"]) == 2

    @pytest.mark.unit
    def test_cleanup_unknown_type_removes_nothing(self, registry: ModelRegistry) -> None:
        """Cleanup for unknown model type should remove 0."""
        removed = registry.cleanup_old_versions("nonexistent", keep_n=3)
        assert removed == 0
