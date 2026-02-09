from __future__ import annotations

import numpy as np
import orion.ml.exit_classifier as exit_classifier
import pytest


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


def test_required_price_target_columns_for_bucket_includes_checkpoint_families() -> None:
    required = exit_classifier._required_price_target_columns_for_bucket(exit_classifier.BUCKET_CHECKPOINTS["0DTE"])

    assert "trade_type" in required
    assert "return_at_5m" in required
    assert "delta_at_5m" in required
    assert "time_value_pct_at_5m" in required
    assert "theta_decay_pct_at_1h" in required


def test_group_missing_columns_by_family_assigns_expected_buckets() -> None:
    checkpoints = exit_classifier.BUCKET_CHECKPOINTS["0DTE"]
    grouped = exit_classifier._group_missing_columns_by_family(
        {
            "ticker",
            "max_return_pct",
            "return_at_5m",
            "delta_at_5m",
            "time_value_pct_at_5m",
            "unknown_column",
        },
        checkpoints,
    )

    assert grouped["entry_context"] == ["ticker"]
    assert grouped["outcome"] == ["max_return_pct"]
    assert grouped["checkpoint_returns"] == ["return_at_5m"]
    assert grouped["checkpoint_greeks"] == ["delta_at_5m"]
    assert grouped["checkpoint_time_decay"] == ["time_value_pct_at_5m"]
    assert grouped["other"] == ["unknown_column"]


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
    assert len(feature_names) > 0
    assert X.shape == (0, len(feature_names))
    assert y.shape == (0,)

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
    assert len(feature_names) > 0
    assert X.shape == (0, len(feature_names))
    assert y.shape == (0,)
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
    assert len(feature_names) > 0
    assert X.shape == (0, len(feature_names))
    assert y.shape == (0,)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("bucket", ["0DTE", "SHORT_SWING", "SWING", "POSITION"])
async def test_build_bucket_training_data_returns_empty_with_feature_schema_on_query_error(
    monkeypatch: pytest.MonkeyPatch,
    bucket: str,
) -> None:
    async def _fail_db_query(_operation):
        raise RuntimeError('column "return_at_missing" does not exist')

    monkeypatch.setattr(exit_classifier, "db_query", _fail_db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data(bucket)

    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    assert len(feature_names) > 0
    assert X.shape == (0, len(feature_names))
    assert y.shape == (0,)
    assert X.dtype == float
    assert y.dtype == int


@pytest.mark.asyncio
async def test_build_bucket_training_data_short_circuits_when_required_columns_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = {"return_at_5m", "delta_at_5m", "theta_decay_pct_at_5m"}

    async def _fake_columns() -> set[str]:
        base = exit_classifier._required_price_target_columns_for_bucket(exit_classifier.BUCKET_CHECKPOINTS["0DTE"])
        return base - missing

    async def _fail_db_query(_operation):
        raise AssertionError("main training query should not execute when schema preflight fails")

    monkeypatch.setattr(exit_classifier, "_load_price_target_label_columns", _fake_columns, raising=False)
    monkeypatch.setattr(exit_classifier, "db_query", _fail_db_query, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert X.shape == (0, len(feature_names))
    assert y.shape == (0,)
    assert X.dtype == float
    assert y.dtype == int


@pytest.mark.asyncio
async def test_load_price_target_label_columns_uses_ttl_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    exit_classifier._clear_price_target_label_schema_cache()
    call_count = 0
    clock = {"now": 100.0}

    class _Result:
        def fetchall(self) -> list[tuple[str]]:
            return [("ticker",), ("entry_ts",)]

    class _Session:
        async def execute(self, _stmt) -> _Result:
            nonlocal call_count
            call_count += 1
            return _Result()

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)
    monkeypatch.setattr(exit_classifier.time, "monotonic", lambda: clock["now"], raising=False)

    first = await exit_classifier._load_price_target_label_columns()
    second = await exit_classifier._load_price_target_label_columns()

    assert first == {"ticker", "entry_ts"}
    assert second == {"ticker", "entry_ts"}
    assert call_count == 1

    clock["now"] = 100.0 + exit_classifier.SCHEMA_CACHE_TTL_SECONDS + 1.0
    third = await exit_classifier._load_price_target_label_columns()

    assert third == {"ticker", "entry_ts"}
    assert call_count == 2


@pytest.mark.asyncio
async def test_load_price_target_label_columns_force_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    exit_classifier._clear_price_target_label_schema_cache()
    call_count = 0

    class _Result:
        def __init__(self, rows: list[tuple[str]]) -> None:
            self._rows = rows

        def fetchall(self) -> list[tuple[str]]:
            return self._rows

    class _Session:
        async def execute(self, _stmt) -> _Result:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _Result([("ticker",)])
            return _Result([("ticker",), ("entry_ts",)])

    async def _db_query(operation):
        return await operation(_Session())

    monkeypatch.setattr(exit_classifier, "db_query", _db_query, raising=False)
    monkeypatch.setattr(exit_classifier.time, "monotonic", lambda: 200.0, raising=False)

    first = await exit_classifier._load_price_target_label_columns()
    second = await exit_classifier._load_price_target_label_columns()
    refreshed = await exit_classifier._load_price_target_label_columns(force_refresh=True)

    assert first == {"ticker"}
    assert second == {"ticker"}
    assert refreshed == {"ticker", "entry_ts"}
    assert call_count == 2


@pytest.mark.asyncio
async def test_build_bucket_training_data_logs_missing_family_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = {"return_at_5m", "delta_at_5m", "theta_decay_pct_at_5m"}
    captured_warning: dict[str, object] = {}

    async def _fake_columns() -> set[str]:
        base = exit_classifier._required_price_target_columns_for_bucket(exit_classifier.BUCKET_CHECKPOINTS["0DTE"])
        return base - missing

    def _fake_warning(_message: str, *, extra: dict[str, object]) -> None:
        captured_warning["extra"] = extra

    monkeypatch.setattr(exit_classifier, "_load_price_target_label_columns", _fake_columns, raising=False)
    monkeypatch.setattr(exit_classifier.logger, "warning", _fake_warning, raising=False)

    X, y, feature_names = await exit_classifier.build_bucket_training_data("0DTE")

    assert X.shape == (0, len(feature_names))
    assert y.shape == (0,)
    extra = captured_warning["extra"]
    assert extra["event"] == "exit_training_schema_missing_columns"
    assert extra["missing_by_family_counts"]["checkpoint_returns"] == 1
    assert extra["missing_by_family_counts"]["checkpoint_greeks"] == 1
    assert extra["missing_by_family_counts"]["checkpoint_time_decay"] == 1


@pytest.mark.asyncio
async def test_train_bucket_exit_classifier_passes_force_schema_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_build_bucket_training_data(bucket: str, force_schema_refresh: bool = False):
        captured["bucket"] = bucket
        captured["force_schema_refresh"] = force_schema_refresh
        return np.empty((0, len(exit_classifier.EXIT_FEATURE_NAMES))), np.empty((0,), dtype=int), list(
            exit_classifier.EXIT_FEATURE_NAMES
        )

    monkeypatch.setattr(
        exit_classifier,
        "build_bucket_training_data",
        _fake_build_bucket_training_data,
        raising=False,
    )

    result = await exit_classifier.train_bucket_exit_classifier("0DTE", force_schema_refresh=True)

    assert result is None
    assert captured["bucket"] == "0DTE"
    assert captured["force_schema_refresh"] is True


@pytest.mark.asyncio
async def test_train_all_exit_classifiers_force_refreshes_schema_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_calls: dict[str, object] = {"refresh_flags": [], "buckets": []}

    async def _fake_load_price_target_label_columns(force_refresh: bool = False) -> set[str]:
        captured_calls["refresh_flags"].append(force_refresh)
        return {"ticker", "entry_ts"}

    async def _fake_train_bucket_exit_classifier(bucket: str, force_schema_refresh: bool = False):
        captured_calls["buckets"].append((bucket, force_schema_refresh))
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

    results = await exit_classifier.train_all_exit_classifiers(force_schema_refresh=True)

    assert results == {}
    assert captured_calls["refresh_flags"] == [True]
    assert len(captured_calls["buckets"]) == len(exit_classifier.BUCKET_CHECKPOINTS)
    assert all(force is False for _bucket, force in captured_calls["buckets"])
