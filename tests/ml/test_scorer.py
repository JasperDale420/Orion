"""
Tests for MLScorer.

Tests the flow event scoring logic including heuristic baseline.
"""

import pickle
import warnings
from unittest.mock import MagicMock

import numpy as np
import pytest
from sklearn.exceptions import InconsistentVersionWarning

from orion.ml.scorer import MLScorer, get_scorer


class TestMLScorer:
    """Tests for MLScorer class."""

    @pytest.fixture(autouse=True)
    def _isolate_models(self, tmp_path):
        """Force heuristic scoring so tests don't depend on trained model files."""
        import orion.ml.scorer as scorer_mod

        orig = scorer_mod.MODEL_DIR
        scorer_mod.MODEL_DIR = tmp_path / "empty_models"
        scorer_mod._scorer = None
        yield
        scorer_mod.MODEL_DIR = orig
        scorer_mod._scorer = None

    @pytest.fixture
    def scorer(self) -> MLScorer:
        """Create a fresh scorer instance."""
        return MLScorer()

    def test_scorer_initialization(self, scorer: MLScorer) -> None:
        """Test scorer initializes with heuristic mode when no model exists."""
        assert scorer.use_heuristic is True
        assert scorer.model is None

    def test_extract_features_basic(self, scorer: MLScorer) -> None:
        """Test feature extraction from flow dict."""
        flow = {
            "premium_usd": 100000,
            "dte": 5,
            "iv": 0.45,
            "volume_contract": 500,
            "open_interest": 1000,
            "underlying_price": 150.0,
            "strike": 155.0,
            "size_contracts": 100,
            "option_price": 2.50,
        }

        features = scorer.extract_features(flow)

        assert features["premium_usd"] == 100000
        assert features["dte"] == 5
        assert features["iv"] == 0.45
        assert features["moneyness"] == pytest.approx(155.0 / 150.0, rel=0.01)
        assert features["volume_oi_ratio"] == pytest.approx(0.5, rel=0.01)
        assert features["premium_per_contract"] == pytest.approx(1000, rel=0.01)

    def test_extract_features_handles_missing_values(self, scorer: MLScorer) -> None:
        """Test feature extraction handles missing/None values."""
        flow = {"ticker": "AAPL"}  # Minimal flow

        features = scorer.extract_features(flow)

        assert features["premium_usd"] == 0
        assert features["dte"] == 0
        assert features["moneyness"] == 1.0  # Default when underlying is 0

    def test_heuristic_score_high_premium_sweep(self, scorer: MLScorer) -> None:
        """Test heuristic scoring for high premium sweep trade."""
        flow = {
            "premium_usd": 500000,
            "is_sweep": "true",
            "aggressor": "ASK",
            "put_call": "C",
            "volume_contract": 1000,
            "open_interest": 200,  # High vol/OI ratio
            "underlying_price": 100,
            "strike": 105,
            "size_contracts": 500,
        }

        score = scorer.score(flow)

        # High premium + sweep + ASK aggressor + high vol/OI should score high
        assert score >= 0.7

    def test_heuristic_score_low_premium(self, scorer: MLScorer) -> None:
        """Test heuristic scoring penalizes low premium."""
        flow = {
            "premium_usd": 5000,  # Below 10k threshold
            "is_sweep": "false",
            "aggressor": "MID",
            "put_call": "C",
            "volume_contract": 100,
            "open_interest": 500,
            "underlying_price": 50,
            "strike": 55,
            "size_contracts": 10,
        }

        score = scorer.score(flow)

        # Low premium should score low
        assert score < 0.3

    def test_score_batch(self, scorer: MLScorer) -> None:
        """Test batch scoring returns correct number of scores."""
        flows = [
            {"premium_usd": 100000, "is_sweep": "true", "aggressor": "ASK", "put_call": "C"},
            {"premium_usd": 50000, "is_sweep": "false", "aggressor": "BID", "put_call": "P"},
            {"premium_usd": 25000, "is_sweep": "true", "aggressor": "ASK", "put_call": "C"},
        ]

        scores = scorer.score_batch(flows)

        assert len(scores) == 3
        assert all(0 <= s <= 1 for s in scores)

    def test_should_trade_threshold(self, scorer: MLScorer) -> None:
        """Test should_trade respects threshold."""
        high_premium_flow = {"premium_usd": 500000, "is_sweep": "true", "aggressor": "ASK", "put_call": "C"}
        low_premium_flow = {"premium_usd": 5000, "is_sweep": "false", "aggressor": "MID", "put_call": "C"}

        assert scorer.should_trade(high_premium_flow, threshold=0.5) is True
        assert scorer.should_trade(low_premium_flow, threshold=0.5) is False

    def test_get_scorer_singleton(self) -> None:
        """Test get_scorer returns singleton."""
        scorer1 = get_scorer()
        scorer2 = get_scorer()

        assert scorer1 is scorer2


class TestLegacyModelCategoricalFallback:
    """Tests that scoring works for legacy models without categorical_mappings.

    Reproduces the bug where 0DTE models trained before Apr 2026 have
    categorical features (put_call, aggressor, is_sweep) in their feature_names
    but no categorical_mappings key in the pickle, causing
    "could not convert string to float: 'P'" at np.array(..., dtype=float).
    """

    @pytest.fixture
    def legacy_model_scorer(self, tmp_path):
        """Create a scorer with a fake legacy 0DTE model (no categorical_mappings).

        Injects model data directly into the scorer rather than pickling to disk,
        because MagicMock objects are not picklable.
        """
        import orion.ml.scorer as scorer_mod

        orig = scorer_mod.MODEL_DIR
        # Point at empty dir so _load_models finds nothing on disk
        scorer_mod.MODEL_DIR = tmp_path / "empty_models"
        scorer_mod._scorer = None
        scorer = MLScorer()

        # Simulate a legacy 0DTE model: has categorical features in feature_names
        # but NO categorical_mappings key (matching the Jan 2026 format).
        feature_names = ["premium", "dte", "iv", "put_call", "aggressor", "is_sweep"]

        mock_model = MagicMock()
        mock_model.predict_proba.return_value = np.array([[0.3, 0.7]])

        model_data = {
            "model": mock_model,
            "feature_names": feature_names,
            "model_type": "0DTE_hit_target_50",
            "created_at": "2026-01-10T01:00:00+00:00",
            # NOTE: deliberately no "categorical_mappings" key
        }

        # Inject the model directly into the scorer's internal state
        scorer.models["0DTE"] = model_data
        scorer.feature_names["0DTE"] = feature_names
        scorer.use_heuristic = False
        yield scorer

        scorer_mod.MODEL_DIR = orig
        scorer_mod._scorer = None

    def test_score_does_not_crash_on_string_categorical(self, legacy_model_scorer):
        """Scoring a 0DTE flow with put_call='P' must not raise ValueError."""
        flow = {
            "premium_usd": 100000,
            "dte": 0,
            "iv": 0.55,
            "put_call": "P",
            "aggressor": "ASK",
            "is_sweep": "True",
            "underlying_price": 150.0,
            "strike": 148.0,
            "size_contracts": 50,
            "volume_contract": 200,
            "open_interest": 500,
        }
        score = legacy_model_scorer.score(flow)
        # Should return a valid probability, not crash
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_fallback_encoding_is_numeric(self, legacy_model_scorer):
        """Verify the fallback encodes string categoricals to numeric values."""
        flow = {
            "premium_usd": 50000,
            "dte": 0,
            "iv": 0.40,
            "put_call": "C",
            "aggressor": "BID",
            "is_sweep": "False",
            "underlying_price": 200.0,
            "strike": 205.0,
            "size_contracts": 100,
            "volume_contract": 300,
            "open_interest": 800,
        }
        features = legacy_model_scorer.extract_features(flow, bucket="0DTE")
        # Categorical values should still be strings at extract_features level
        # (encoding happens inside score()), but let's verify they exist
        assert "put_call" in features
        assert "aggressor" in features
        assert "is_sweep" in features

    def test_fallback_encoding_deterministic(self, legacy_model_scorer):
        """Same string value must always produce the same numeric code."""
        flow1 = {
            "premium_usd": 100000,
            "dte": 0,
            "iv": 0.5,
            "put_call": "P",
            "aggressor": "ASK",
            "is_sweep": "True",
            "underlying_price": 150.0,
            "strike": 148.0,
            "size_contracts": 50,
        }
        flow2 = dict(flow1)  # identical flow

        score1 = legacy_model_scorer.score(flow1)
        score2 = legacy_model_scorer.score(flow2)
        assert score1 == score2


class _VersionMismatch:
    """A picklable object whose __setstate__ raises InconsistentVersionWarning
    on unpickle, simulating a scikit-learn artifact trained under a different
    sklearn release (same fixture shape as tests/ml/test_model_loading.py)."""

    def __getstate__(self) -> dict[str, bool]:
        return {"restore": True}

    def __setstate__(self, state: dict[str, bool]) -> None:
        warnings.warn(
            InconsistentVersionWarning(
                estimator_name="test estimator",
                current_sklearn_version="1.9.0",
                original_sklearn_version="1.8.0",
            ),
            stacklevel=2,
        )
        self.__dict__.update(state)


class TestModelUnloadableLogging:
    """PR #187 promoted sklearn's InconsistentVersionWarning to a load
    failure (orion/ml/model_loading.py), so any stale-format model artifact
    now fails to load instead of loading with a compatibility warning. When
    every present model file fails this way, the scorer must say so loudly
    (ERROR, event=ml_models_unloadable) instead of emitting the same WARNING
    used for a directory that legitimately has no model files at all — those
    are operationally very different states."""

    def test_files_present_but_unloadable_logs_error_event(self, tmp_path, monkeypatch):
        import orion.ml.scorer as scorer_mod

        model_dir = tmp_path / "models"
        model_dir.mkdir()
        (model_dir / "SWING_hit_target_50.pkl").write_bytes(pickle.dumps(_VersionMismatch()))

        monkeypatch.setattr(scorer_mod, "MODEL_DIR", model_dir)
        mock_logger = MagicMock()
        monkeypatch.setattr(scorer_mod, "logger", mock_logger)

        scorer = scorer_mod.MLScorer()

        assert scorer.use_heuristic is True
        mock_logger.error.assert_called_once()
        _args, kwargs = mock_logger.error.call_args
        extra = kwargs.get("extra", {})
        assert extra.get("event") == "ml_models_unloadable"
        assert extra.get("files_present") == 1
        assert extra.get("loaded") == 0
        assert extra.get("reason")

    def test_empty_model_dir_logs_warning_not_error(self, tmp_path, monkeypatch):
        """No .pkl files present at all is a distinct, benign state — it
        must stay a WARNING, not escalate to the new ERROR event."""
        import orion.ml.scorer as scorer_mod

        model_dir = tmp_path / "empty_models"
        model_dir.mkdir()

        monkeypatch.setattr(scorer_mod, "MODEL_DIR", model_dir)
        mock_logger = MagicMock()
        monkeypatch.setattr(scorer_mod, "logger", mock_logger)

        scorer = scorer_mod.MLScorer()

        assert scorer.use_heuristic is True
        mock_logger.error.assert_not_called()
        mock_logger.warning.assert_called()

    def test_successfully_loaded_model_logs_no_error(self, tmp_path, monkeypatch):
        """At least one model loading successfully must not trip the new
        ERROR event, even though other bucket files are absent."""
        import orion.ml.scorer as scorer_mod

        model_dir = tmp_path / "models"
        model_dir.mkdir()
        (model_dir / "SWING_hit_target_50.pkl").write_bytes(pickle.dumps({"model": "ok", "feature_names": []}))

        monkeypatch.setattr(scorer_mod, "MODEL_DIR", model_dir)
        mock_logger = MagicMock()
        monkeypatch.setattr(scorer_mod, "logger", mock_logger)

        scorer = scorer_mod.MLScorer()

        assert scorer.use_heuristic is False
        mock_logger.error.assert_not_called()

    def test_persistent_unloadable_file_does_not_reflood_error_on_reload(self, tmp_path, monkeypatch):
        """An unchanged unloadable file must not re-trigger ml_models_unloadable
        on every periodic reload check — only a genuine mtime change (a fresh
        load attempt worth reporting) should. Without recording the failing
        file's mtime, check_and_reload() sees the bucket stuck at its 0
        default forever and re-attempts + re-logs every reload interval."""
        import orion.ml.scorer as scorer_mod

        model_dir = tmp_path / "models"
        model_dir.mkdir()
        (model_dir / "SWING_hit_target_50.pkl").write_bytes(pickle.dumps(_VersionMismatch()))

        monkeypatch.setattr(scorer_mod, "MODEL_DIR", model_dir)
        mock_logger = MagicMock()
        monkeypatch.setattr(scorer_mod, "logger", mock_logger)

        scorer = scorer_mod.MLScorer()
        assert mock_logger.error.call_count == 1

        mock_logger.reset_mock()
        reloaded = scorer.check_and_reload()

        assert reloaded is False
        mock_logger.error.assert_not_called()

    def test_stale_skipped_file_does_not_reflood_error_on_reload(self, tmp_path, monkeypatch):
        """Same reflood risk as an unloadable file, but for a file rejected
        by stale_model_policy='skip': its mtime must also be recorded so an
        unchanged stale file isn't treated as newer-than-loaded (and
        re-attempted + re-logged) on every periodic reload check."""
        import os
        import time

        import orion.ml.scorer as scorer_mod
        from orion.config import system_settings

        model_dir = tmp_path / "models"
        model_dir.mkdir()
        model_path = model_dir / "SWING_hit_target_50.pkl"
        model_path.write_bytes(pickle.dumps({"model": "ok", "feature_names": []}))
        old_time = time.time() - 30 * 86400  # 30 days old > default 14-day max
        os.utime(model_path, (old_time, old_time))

        monkeypatch.setattr(scorer_mod, "MODEL_DIR", model_dir)
        monkeypatch.setattr(system_settings, "ml_stale_model_policy", "skip")
        mock_logger = MagicMock()
        monkeypatch.setattr(scorer_mod, "logger", mock_logger)

        scorer = scorer_mod.MLScorer()
        assert scorer.use_heuristic is True  # skipped, nothing loaded
        assert mock_logger.error.call_count == 1  # file present but 0 loaded -> ml_models_unloadable

        mock_logger.reset_mock()
        reloaded = scorer.check_and_reload()

        assert reloaded is False
        mock_logger.error.assert_not_called()

    def test_replacing_a_loaded_model_with_an_unloadable_file_keeps_the_old_model(self, tmp_path, monkeypatch):
        """A model already loaded must not be silently dropped just because
        its refreshed file on disk fails to load — the scorer keeps scoring
        with the old (still valid) model, so ml_models_unloadable (which
        means "nothing loaded, heuristic for everyone") must not fire."""
        import os

        import orion.ml.scorer as scorer_mod

        model_dir = tmp_path / "models"
        model_dir.mkdir()
        model_path = model_dir / "SWING_hit_target_50.pkl"
        model_path.write_bytes(pickle.dumps({"model": "ok", "feature_names": []}))

        monkeypatch.setattr(scorer_mod, "MODEL_DIR", model_dir)
        scorer = scorer_mod.MLScorer()
        assert scorer.use_heuristic is False
        assert "SWING" in scorer.models

        # Replace the file with a broken one, with a deliberately later mtime
        # (no sleeping — set it explicitly) so check_and_reload() sees it.
        original_mtime = model_path.stat().st_mtime
        model_path.write_bytes(pickle.dumps(_VersionMismatch()))
        os.utime(model_path, (original_mtime + 10, original_mtime + 10))

        mock_logger = MagicMock()
        monkeypatch.setattr(scorer_mod, "logger", mock_logger)

        reloaded = scorer.check_and_reload()

        assert reloaded is True
        assert "SWING" in scorer.models  # old model retained, not dropped
        assert scorer.use_heuristic is False
        mock_logger.error.assert_not_called()


class TestLastScoringMode:
    """`last_scoring_mode` must reflect the ACTUAL path the most recent
    score()/score_enriched() call took — not the scorer's global
    `use_heuristic` flag. Models are bucket-specific, so a candidate can be
    heuristically scored even while other buckets have a trained model
    loaded, and a loaded model's inference can itself raise and fall back
    to the heuristic scorer for that one call. use_heuristic only reflects
    whether ANY bucket has a model loaded at all, so it captures neither
    case correctly."""

    @pytest.fixture
    def partial_model_scorer(self, tmp_path):
        """A scorer with ONLY a SWING model loaded — SHORT_SWING and other
        buckets have no model despite `use_heuristic` reading False overall."""
        import orion.ml.scorer as scorer_mod

        orig = scorer_mod.MODEL_DIR
        scorer_mod.MODEL_DIR = tmp_path / "empty_models"
        scorer_mod._scorer = None
        scorer = MLScorer()
        mock_model = MagicMock()
        mock_model.predict_proba.return_value = np.array([[0.3, 0.7]])
        feature_names: list[str] = []
        scorer.models["SWING"] = {"model": mock_model, "feature_names": feature_names}
        scorer.feature_names["SWING"] = feature_names
        scorer.use_heuristic = False
        yield scorer

        scorer_mod.MODEL_DIR = orig
        scorer_mod._scorer = None

    def test_bucket_with_loaded_model_reports_model(self, partial_model_scorer):
        flow = {"dte": 10}  # SWING bucket (4-14 dte) — has a model
        partial_model_scorer.score(flow)
        assert partial_model_scorer.last_scoring_mode == "model"

    def test_bucket_without_a_loaded_model_reports_heuristic(self, partial_model_scorer):
        flow = {"dte": 1}  # SHORT_SWING bucket (1-3 dte) — no model loaded
        partial_model_scorer.score(flow)
        assert partial_model_scorer.last_scoring_mode == "heuristic"

    def test_inference_exception_reports_heuristic_even_though_bucket_has_a_model(self, partial_model_scorer):
        """A loaded model whose predict_proba raises mid-call must record
        `last_scoring_mode == "heuristic"` for THAT call — the model entry
        stays in self.models (untouched), so a bucket-presence check alone
        would wrongly say "model"."""
        partial_model_scorer.models["SWING"]["model"].predict_proba.side_effect = RuntimeError("boom")
        flow = {"dte": 10, "premium_usd": 50000, "put_call": "C"}

        score = partial_model_scorer.score(flow)

        assert partial_model_scorer.last_scoring_mode == "heuristic"
        assert isinstance(score, float)  # heuristic fallback still returns a usable score
        assert "SWING" in partial_model_scorer.models  # model entry itself is untouched
