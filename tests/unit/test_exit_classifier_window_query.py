from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import orion.ml.exit_classifier as exit_classifier


def test_can_train_with_labels_rejects_single_class_and_sparse_classes() -> None:
    can_train, reason = exit_classifier._can_train_with_labels(np.zeros(100, dtype=int), min_samples=100)
    assert can_train is False
    assert "single class" in reason

    can_train, reason = exit_classifier._can_train_with_labels(np.array([0, 1]), min_samples=2)
    assert can_train is False
    assert "fewer than 2 samples" in reason

    can_train, reason = exit_classifier._can_train_with_labels(
        np.array([0, 0, 1, 1, 0, 1]),
        min_samples=6,
    )
    assert can_train is True
    assert reason == ""


def test_exit_classifier_training_control_prefers_specific_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING", "false")

    enabled, key, raw = exit_classifier._legacy_exit_training_control()

    assert enabled is False
    assert key == "ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING"
    assert raw == "false"


def test_exit_classifier_training_control_specific_true_overrides_global_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "false")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING", "true")

    enabled, key, raw = exit_classifier._legacy_exit_training_control()

    assert enabled is True
    assert key == "ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING"
    assert raw == "true"


def test_exit_classifier_training_source_prefers_heber_gold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_TRAINING_SOURCE", "HeBeR")

    assert exit_classifier._exit_classifier_training_source() == "heber_gold"


def test_exit_classifier_training_source_invalid_falls_back_to_heber_gold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_TRAINING_SOURCE", "not-a-real-source")

    assert exit_classifier._exit_classifier_training_source() == "heber_gold"


def test_exit_classifier_training_source_legacy_sql_falls_back_to_heber_gold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_TRAINING_SOURCE", "legacy_sql")

    assert exit_classifier._exit_classifier_training_source() == "heber_gold"


@pytest.mark.asyncio
async def test_build_bucket_training_data_unknown_bucket_short_circuits_without_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_db_query(_operation):
        raise AssertionError("db_query should not execute for unknown bucket")

    monkeypatch.setattr(exit_classifier, "db_query", _fail_db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("NOT_A_BUCKET")

    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    assert X.size == 0
    assert y.size == 0
    assert len(feature_names) > 0
    assert X.shape == (0, len(feature_names))
    assert y.shape == (0,)


@pytest.mark.asyncio
async def test_build_bucket_training_data_returns_empty_when_legacy_training_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING", "false")
    db_calls = {"count": 0}

    async def _db_query(_operation):
        db_calls["count"] += 1
        return []

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    assert X.shape == (0, len(feature_names))
    assert y.shape == (0,)
    assert db_calls["count"] == 0


@pytest.mark.asyncio
async def test_build_bucket_training_data_heber_source_uses_gold_datasets_without_local_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING", "true")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_TRAINING_SOURCE", "heber_gold")
    db_calls = {"count": 0}

    async def _db_query(_operation):
        db_calls["count"] += 1
        return []

    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time) -> pd.DataFrame:  # type: ignore[no-untyped-def]
            if dataset == "labels_alert_barriers":
                return pd.DataFrame(
                    [
                        {
                            "alert_id": "evt-1",
                            "outcome_return": 0.65,
                            "hit_tp_first": 1,
                            "trading_minutes_to_hit": 45,
                        }
                    ]
                )
            if dataset == "meta_label_features":
                return pd.DataFrame(
                    [
                        {
                            "alert_id": "evt-1",
                            "premium": 125000.0,
                            "days_to_expiry": 0,
                            "is_sweep": 1,
                            "iv_rank": 60.0,
                            "delta": 0.31,
                            "theta": -0.02,
                            "iv": 0.41,
                        }
                    ]
                )
            return pd.DataFrame()

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)
    monkeypatch.setattr(exit_classifier, "get_heber_reader", lambda: _FakeReader(), raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    assert X.shape == (1, len(feature_names))
    assert y.shape == (1,)
    assert y[0] == 1
    assert X[0][0] == pytest.approx(0.65)
    assert db_calls["count"] == 0


@pytest.mark.asyncio
async def test_build_bucket_training_data_legacy_source_still_uses_heber_without_local_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING", "true")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_TRAINING_SOURCE", "legacy_sql")
    db_calls = {"count": 0}

    async def _db_query(_operation):
        db_calls["count"] += 1
        return []

    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time) -> pd.DataFrame:  # type: ignore[no-untyped-def]
            if dataset == "labels_alert_barriers":
                return pd.DataFrame(
                    [
                        {
                            "alert_id": "evt-1",
                            "outcome_return": 0.65,
                            "hit_tp_first": 1,
                            "trading_minutes_to_hit": 45,
                        }
                    ]
                )
            if dataset == "meta_label_features":
                return pd.DataFrame(
                    [
                        {
                            "alert_id": "evt-1",
                            "premium": 125000.0,
                            "days_to_expiry": 0,
                            "is_sweep": 1,
                            "iv_rank": 60.0,
                            "delta": 0.31,
                            "theta": -0.02,
                            "iv": 0.41,
                        }
                    ]
                )
            return pd.DataFrame()

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)
    monkeypatch.setattr(exit_classifier, "get_heber_reader", lambda: _FakeReader(), raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    assert X.shape == (1, len(feature_names))
    assert y.shape == (1,)
    assert y[0] == 1
    assert db_calls["count"] == 0


@pytest.mark.asyncio
async def test_build_bucket_training_data_ignores_no_snapshot_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING", "true")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_TRAINING_SOURCE", "heber_gold")

    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time) -> pd.DataFrame:  # type: ignore[no-untyped-def]
            if dataset == "labels_alert_barriers":
                return pd.DataFrame(
                    [
                        {
                            "alert_id": "evt-no-snap",
                            "outcome": "no_snapshot",
                            "outcome_return": 0.10,
                            "hit_tp_first": 0,
                            "trading_minutes_to_hit": 45,
                            "bars_to_hit": 0,
                            "snapshot_count": 0,
                        },
                        {
                            "alert_id": "evt-expired",
                            "outcome": "expired",
                            "outcome_return": -0.20,
                            "hit_tp_first": 0,
                            "trading_minutes_to_hit": 50,
                            "bars_to_hit": 0,
                            "snapshot_count": 12,
                        },
                        {
                            "alert_id": "evt-valid",
                            "outcome": "hit_tp",
                            "outcome_return": 0.30,
                            "hit_tp_first": 1,
                            "trading_minutes_to_hit": 30,
                            "bars_to_hit": 3,
                            "snapshot_count": 3,
                        },
                    ]
                )
            if dataset == "meta_label_features":
                return pd.DataFrame(
                    [
                        {
                            "alert_id": "evt-no-snap",
                            "premium": 125000.0,
                            "days_to_expiry": 0,
                            "is_sweep": 1,
                            "iv_rank": 60.0,
                            "delta": 0.31,
                            "theta": -0.02,
                            "iv": 0.41,
                        },
                        {
                            "alert_id": "evt-expired",
                            "premium": 110000.0,
                            "days_to_expiry": 0,
                            "is_sweep": 0,
                            "iv_rank": 55.0,
                            "delta": 0.22,
                            "theta": -0.01,
                            "iv": 0.33,
                        },
                        {
                            "alert_id": "evt-valid",
                            "premium": 100000.0,
                            "days_to_expiry": 0,
                            "is_sweep": 0,
                            "iv_rank": 55.0,
                            "delta": 0.22,
                            "theta": -0.01,
                            "iv": 0.33,
                        },
                    ]
                )
            return pd.DataFrame()

    monkeypatch.setattr(exit_classifier, "get_heber_reader", lambda: _FakeReader(), raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert X.shape == (2, len(feature_names))
    assert y.shape == (2,)
    assert y.tolist() == [0, 1]
    assert X[0][0] == pytest.approx(-0.20)
    assert X[1][0] == pytest.approx(0.30)


@pytest.mark.asyncio
async def test_train_bucket_exit_classifier_passes_force_schema_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_build_bucket_training_data(bucket: str, force_schema_refresh: bool = False):
        captured["bucket"] = bucket
        captured["force_schema_refresh"] = force_schema_refresh
        return (
            np.empty((0, len(exit_classifier.EXIT_FEATURE_NAMES)), dtype=float),
            np.empty((0,), dtype=int),
            list(exit_classifier.EXIT_FEATURE_NAMES),
        )

    monkeypatch.setattr(exit_classifier, "build_bucket_training_data", _fake_build_bucket_training_data, raising=False)

    result = await exit_classifier.train_bucket_exit_classifier("0DTE", force_schema_refresh=True)

    assert captured["bucket"] == "0DTE"
    assert captured["force_schema_refresh"] is True
    assert result is None


@pytest.mark.asyncio
async def test_train_all_exit_classifiers_prefetches_schema_when_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls = {"count": 0}

    async def _fake_load_price_target_label_columns(force_refresh: bool = False) -> set[str]:
        refresh_calls["count"] += 1
        assert force_refresh is True
        return set()

    async def _fake_train_bucket_exit_classifier(bucket: str, force_schema_refresh: bool = False):
        _ = bucket
        assert force_schema_refresh is False
        return None

    monkeypatch.setattr(
        exit_classifier,
        "_load_price_target_label_columns",
        _fake_load_price_target_label_columns,
        raising=False,
    )
    monkeypatch.setattr(
        exit_classifier,
        "train_bucket_exit_classifier",
        _fake_train_bucket_exit_classifier,
        raising=False,
    )

    results = await exit_classifier.train_all_exit_classifiers(
        force_schema_refresh=True,
        refresh_each_bucket=False,
    )

    assert results == {}
    assert refresh_calls["count"] == 1
