from __future__ import annotations

import numpy as np
import pytest

import orion.ml.exit_classifier as exit_classifier


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
