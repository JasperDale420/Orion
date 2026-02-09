from __future__ import annotations

import numpy as np
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
    assert feature_names == []


@pytest.mark.asyncio
async def test_build_bucket_training_data_uses_single_lateral_window_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Result:
        def mappings(self) -> "_Result":
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class _Session:
        async def execute(self, stmt, params=None) -> _Result:
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _Result()

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    assert X.size == 0
    assert y.size == 0
    assert feature_names == []

    sql = captured["sql"]
    assert "LEFT JOIN LATERAL" in sql
    assert sql.count("LEFT JOIN LATERAL") == 1
    assert "jsonb_object_agg(period, features)" in sql
    assert "period IN ('1h', '1d', '1w')" in sql
    assert "w1h" not in sql
    assert "w1d" not in sql
    assert "w1w" not in sql


@pytest.mark.asyncio
async def test_build_bucket_training_data_binds_trade_type_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Result:
        def mappings(self) -> "_Result":
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class _Session:
        async def execute(self, stmt, params=None) -> _Result:
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _Result()

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    assert feature_names == []
    assert "p.trade_type = :trade_type" in str(captured["sql"])
    assert captured["params"] == {"trade_type": "0DTE"}


@pytest.mark.asyncio
async def test_build_bucket_training_data_normalizes_is_sweep_string_false_and_shapes_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "premium_usd": 125000.0,
        "dte": 0,
        "is_sweep": "false",
        "max_return_pct": 100.0,
        "max_drawdown_pct": -15.0,
        "return_at_5m": 85.0,
    }

    class _Result:
        def mappings(self) -> "_Result":
            return self

        def all(self) -> list[dict[str, object]]:
            return [row]

    class _Session:
        async def execute(self, _stmt, _params=None) -> _Result:
            return _Result()

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert X.shape[0] == 1
    assert X.shape[1] == len(feature_names)
    assert y.shape[0] == 1
    assert y[0] == 1
    assert X[0][11] == 0.0


@pytest.mark.asyncio
async def test_build_bucket_training_data_skips_malformed_numeric_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "premium_usd": 125000.0,
            "dte": 0,
            "is_sweep": "false",
            "max_return_pct": "bad-value",
            "max_drawdown_pct": -15.0,
            "return_at_5m": 85.0,
        },
        {
            "premium_usd": 125000.0,
            "dte": 0,
            "is_sweep": "true",
            "max_return_pct": 100.0,
            "max_drawdown_pct": -15.0,
            "return_at_5m": "bad-checkpoint",
        },
        {
            "premium_usd": 125000.0,
            "dte": 0,
            "is_sweep": True,
            "max_return_pct": 100.0,
            "max_drawdown_pct": -15.0,
            "return_at_5m": 85.0,
        },
    ]

    class _Result:
        def mappings(self) -> "_Result":
            return self

        def all(self) -> list[dict[str, object]]:
            return rows

    class _Session:
        async def execute(self, _stmt, _params=None) -> _Result:
            return _Result()

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert X.shape == (1, len(feature_names))
    assert y.shape == (1,)
    assert y[0] == 1


@pytest.mark.asyncio
async def test_build_bucket_training_data_skips_non_numeric_checkpoint_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "premium_usd": "125000.0",
        "dte": "0",
        "is_sweep": "true",
        "max_return_pct": "100.0",
        "return_at_5m": "not-a-number",
        "return_at_10m": "85.0",
        "delta_at_10m": "bad-delta",
    }

    class _Result:
        def mappings(self) -> "_Result":
            return self

        def all(self) -> list[dict[str, object]]:
            return [row]

    class _Session:
        async def execute(self, _stmt, _params=None) -> _Result:
            return _Result()

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert X.shape[0] == 1
    assert X.shape[1] == len(feature_names)
    assert y.shape[0] == 1
    assert y[0] == 1
    assert X[0][2] == 0.0  # bad delta_at_10m safely normalized


@pytest.mark.asyncio
async def test_build_bucket_training_data_handles_missing_max_return_pct_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "premium_usd": 100000.0,
        "dte": 0,
        "is_sweep": True,
        "return_at_5m": 20.0,
    }

    class _Result:
        def mappings(self) -> "_Result":
            return self

        def all(self) -> list[dict[str, object]]:
            return [row]

    class _Session:
        async def execute(self, _stmt, _params=None) -> _Result:
            return _Result()

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    assert X.size == 0
    assert y.size == 0
    assert len(feature_names) > 0
    assert X.shape == (0, len(feature_names))
    assert y.shape == (0,)


@pytest.mark.asyncio
async def test_build_bucket_training_data_returns_stable_empty_matrix_shape_when_rows_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "premium_usd": 100000.0,
            "dte": 0,
            "is_sweep": True,
            "max_return_pct": -10.0,
            "return_at_5m": 20.0,
        }
    ]

    class _Result:
        def mappings(self) -> "_Result":
            return self

        def all(self) -> list[dict[str, object]]:
            return rows

    class _Session:
        async def execute(self, _stmt, _params=None) -> _Result:
            return _Result()

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert X.shape == (0, len(feature_names))
    assert y.shape == (0,)
    assert X.dtype == float
    assert y.dtype == int


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bucket,expected_cols",
    [
        ("0DTE", ["return_at_5m", "delta_at_5m", "time_value_pct_at_5m"]),
        ("SHORT_SWING", ["return_at_8h", "theta_at_8h", "dte_at_8h"]),
        ("SWING", ["return_at_2w", "gamma_at_2w", "theta_decay_pct_at_2w"]),
        ("POSITION", ["return_at_4w", "iv_at_4w", "time_value_pct_at_4w"]),
    ],
)
async def test_build_bucket_training_data_query_contract_per_bucket(
    monkeypatch: pytest.MonkeyPatch,
    bucket: str,
    expected_cols: list[str],
) -> None:
    captured: dict[str, object] = {}

    class _Result:
        def mappings(self) -> "_Result":
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class _Session:
        async def execute(self, stmt, params=None) -> _Result:
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _Result()

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data(bucket)

    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    assert feature_names == []
    assert captured["params"] == {"trade_type": bucket}
    sql = str(captured["sql"])
    for col in expected_cols:
        assert col in sql


@pytest.mark.asyncio
async def test_build_bucket_training_data_query_coalesces_entry_and_window_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Result:
        def mappings(self) -> "_Result":
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class _Session:
        async def execute(self, stmt, params=None) -> _Result:
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _Result()

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)

    await exit_classifier.build_bucket_training_data("0DTE")

    sql = str(captured["sql"])
    assert "COALESCE(p.premium_usd, 0) as premium_usd" in sql
    assert "COALESCE(p.dte, 0) as dte" in sql
    assert "COALESCE(p.iv_rank_at_entry, 50) as iv_rank_at_entry" in sql
    assert "COALESCE(p.vix_at_entry, 20) as vix_at_entry" in sql
    assert "COALESCE(w.features_by_period->'1h'->>'sweep_ratio', '0') as window_sweep_ratio_1h" in sql
    assert "COALESCE(w.features_by_period->'1d'->>'dp_volume', '0') as window_dp_volume_1d" in sql
