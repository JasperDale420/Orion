"""
Tests for ML Performance Tracker.

Covers prediction logging (entry/exit), outcome recording,
daily accuracy queries, weekly performance, and error handling.

Note on the helper below: pre-2026-05-20 the inner `write` callbacks in
`log_entry_prediction` / `log_exit_prediction` were SYNC functions that
returned `None`. The real `db_write` → `db_transaction` does
`await operation(session)`, which raised `TypeError: object NoneType can't
be used in 'await' expression` on every call — caught by the broad
except and silently logged. The existing mock-based tests passed despite
this because `_make_db_write_side_effect` called `write_fn` synchronously
without awaiting. After the fix the write callbacks are async; the helper
now awaits them. See `test_real_db_write_*` below for a real-DB
integration test that exercises the actual await chain and would have
caught the regression.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.ml.performance_tracker import (
    get_daily_accuracy,
    get_weekly_performance,
    log_entry_prediction,
    log_exit_prediction,
    log_outcome,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_write_side_effect():
    """Create a side effect that calls the (async) write_fn with a mock session.

    db_write takes an async callable ``write_fn(session)`` and awaits it
    inside a transaction. The mock replicates that: call the function with
    a fake session and await the result so the internal
    ``await session.execute(...)`` is exercised.
    """

    async def _side_effect(write_fn):
        mock_session = MagicMock()
        # AsyncMock so write_fn's `await session.execute(...)` resolves.
        mock_session.execute = AsyncMock()
        await write_fn(mock_session)
        return None

    return _side_effect


async def _await_write_fn(write_fn, mock_session=None):
    """Test helper to invoke an async write callback and return the session
    used so callers can inspect `mock_session.execute.call_args`."""
    if mock_session is None:
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
    await write_fn(mock_session)
    return mock_session


# ---------------------------------------------------------------------------
# log_entry_prediction
# ---------------------------------------------------------------------------


class TestLogEntryPrediction:
    """Tests for log_entry_prediction."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_returns_prediction_id(self, mock_db_write: AsyncMock) -> None:
        """Should return a UUID prediction ID on success."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        result = await log_entry_prediction(
            symbol="AAPL",
            option_chain="AAPL240119C00190000",
            bucket="0DTE",
            prediction_score=0.75,
        )

        assert result is not None
        assert len(result) == 36  # UUID format

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_prediction_class_high_score(self, mock_db_write: AsyncMock) -> None:
        """Score >= 0.5 should produce prediction_class = 1."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        await log_entry_prediction(
            symbol="AAPL",
            option_chain="AAPL240119C00190000",
            bucket="0DTE",
            prediction_score=0.5,
        )

        # Extract the parameters passed to session.execute
        call_args = mock_db_write.call_args
        write_fn = call_args[0][0]
        mock_session = await _await_write_fn(write_fn)
        params = mock_session.execute.call_args[0][1]
        assert params["pred_class"] == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_prediction_class_low_score(self, mock_db_write: AsyncMock) -> None:
        """Score < 0.5 should produce prediction_class = 0."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        await log_entry_prediction(
            symbol="AAPL",
            option_chain="AAPL240119C00190000",
            bucket="0DTE",
            prediction_score=0.49,
        )

        call_args = mock_db_write.call_args
        write_fn = call_args[0][0]
        mock_session = await _await_write_fn(write_fn)
        params = mock_session.execute.call_args[0][1]
        assert params["pred_class"] == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_prediction_class_boundary_zero(self, mock_db_write: AsyncMock) -> None:
        """Score of exactly 0.0 should produce prediction_class = 0."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        await log_entry_prediction(
            symbol="AAPL",
            option_chain="AAPL240119C00190000",
            bucket="0DTE",
            prediction_score=0.0,
        )

        call_args = mock_db_write.call_args
        write_fn = call_args[0][0]
        mock_session = await _await_write_fn(write_fn)
        params = mock_session.execute.call_args[0][1]
        assert params["pred_class"] == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_prediction_class_boundary_one(self, mock_db_write: AsyncMock) -> None:
        """Score of exactly 1.0 should produce prediction_class = 1."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        await log_entry_prediction(
            symbol="AAPL",
            option_chain="AAPL240119C00190000",
            bucket="0DTE",
            prediction_score=1.0,
        )

        call_args = mock_db_write.call_args
        write_fn = call_args[0][0]
        mock_session = await _await_write_fn(write_fn)
        params = mock_session.execute.call_args[0][1]
        assert params["pred_class"] == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_optional_params_passed_through(self, mock_db_write: AsyncMock) -> None:
        """confidence and position_id should be forwarded to the SQL params."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        await log_entry_prediction(
            symbol="TSLA",
            option_chain="TSLA240119P00200000",
            bucket="SHORT_SWING",
            prediction_score=0.65,
            confidence=0.8,
            position_id="pos-123",
        )

        call_args = mock_db_write.call_args
        write_fn = call_args[0][0]
        mock_session = await _await_write_fn(write_fn)
        params = mock_session.execute.call_args[0][1]
        assert params["confidence"] == 0.8
        assert params["position_id"] == "pos-123"

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_db_write_failure_returns_none(self, mock_db_write: AsyncMock) -> None:
        """Database failure should return None, not raise."""
        mock_db_write.side_effect = RuntimeError("DB connection lost")

        result = await log_entry_prediction(
            symbol="AAPL",
            option_chain="AAPL240119C00190000",
            bucket="0DTE",
            prediction_score=0.75,
        )

        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_unique_ids_per_call(self, mock_db_write: AsyncMock) -> None:
        """Each call should generate a unique prediction ID."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        id1 = await log_entry_prediction("AAPL", "OPT1", "0DTE", 0.5)
        id2 = await log_entry_prediction("AAPL", "OPT1", "0DTE", 0.5)

        assert id1 != id2


# ---------------------------------------------------------------------------
# log_exit_prediction
# ---------------------------------------------------------------------------


class TestLogExitPrediction:
    """Tests for log_exit_prediction."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_returns_prediction_id(self, mock_db_write: AsyncMock) -> None:
        """Should return a UUID prediction ID on success."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        result = await log_exit_prediction(
            symbol="MSFT",
            option_chain="MSFT240119C00350000",
            bucket="SWING",
            prediction_score=0.6,
        )

        assert result is not None
        assert len(result) == 36

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_exit_model_type_is_exit_score(self, mock_db_write: AsyncMock) -> None:
        """The SQL should insert with model_type='exit_score'."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        await log_exit_prediction(
            symbol="MSFT",
            option_chain="MSFT240119C00350000",
            bucket="SWING",
            prediction_score=0.6,
        )

        call_args = mock_db_write.call_args
        write_fn = call_args[0][0]
        mock_session = await _await_write_fn(write_fn)
        # The model_type 'exit_score' is embedded in the SQL text, not a param.
        # Verify the SQL text contains 'exit_score'.
        sql_text = str(mock_session.execute.call_args[0][0].text)
        assert "exit_score" in sql_text

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_prediction_class_derivation(self, mock_db_write: AsyncMock) -> None:
        """Exit prediction should also classify score >= 0.5 as 1."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        await log_exit_prediction("MSFT", "OPT", "SWING", 0.8)

        call_args = mock_db_write.call_args
        write_fn = call_args[0][0]
        mock_session = await _await_write_fn(write_fn)
        params = mock_session.execute.call_args[0][1]
        assert params["pred_class"] == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_db_failure_returns_none(self, mock_db_write: AsyncMock) -> None:
        """Database failure should return None."""
        mock_db_write.side_effect = RuntimeError("DB error")

        result = await log_exit_prediction("MSFT", "OPT", "SWING", 0.6)
        assert result is None


# ---------------------------------------------------------------------------
# log_outcome
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Real-DB integration — catches the sync-vs-async regression that all the
# mock-based tests above missed.
# ---------------------------------------------------------------------------


class TestPerformanceTrackerRealDB:
    """Integration tests that exercise the actual await chain through
    db_write → db_transaction → session.execute on in-memory SQLite.

    Pre-fix (2026-05-20), the inner `write` callbacks were sync and
    returned None. `db_transaction` does `await operation(session)` which
    raised `TypeError: object NoneType can't be used in 'await' expression`,
    caught by the broad except in log_*_prediction, swallowed, and the
    callers got None back. The mock-based tests above missed this because
    their `_make_db_write_side_effect` (pre-fix) called write_fn
    SYNCHRONOUSLY. These tests use the real db_write to prove the await
    chain actually completes.
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_log_exit_prediction_writes_to_real_db(self) -> None:
        from sqlalchemy import text

        from orion.storage.db import async_session_factory, init_db

        await init_db()

        # SQLAlchemy's metadata.create_all (run by init_db) covers the
        # full Orion schema including ml_predictions.
        prediction_id = await log_exit_prediction(
            symbol="AAPL",
            option_chain="AAPL260522C00200000",
            bucket="SWING",
            prediction_score=0.73,
            position_id="pos-real-1",
        )

        # Pre-fix: returned None because TypeError fell through the except.
        assert prediction_id is not None, "log_exit_prediction must return a UUID, not None"
        assert len(prediction_id) == 36

        async with async_session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT id, model_type, prediction_score FROM ml_predictions WHERE id = :id"),
                    {"id": prediction_id},
                )
            ).fetchone()

        assert row is not None, "ml_predictions row was not written"
        assert row[1] == "exit_score"
        assert abs(row[2] - 0.73) < 1e-9

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_log_entry_prediction_writes_to_real_db(self) -> None:
        """Same bug shape in log_entry_prediction — exercise it too."""
        from sqlalchemy import text

        from orion.storage.db import async_session_factory, init_db

        await init_db()

        prediction_id = await log_entry_prediction(
            symbol="AAPL",
            option_chain="AAPL260522C00200000",
            bucket="SWING",
            prediction_score=0.65,
            confidence=0.8,
            position_id="pos-real-2",
        )

        assert prediction_id is not None

        async with async_session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT id, model_type, confidence FROM ml_predictions WHERE id = :id"),
                    {"id": prediction_id},
                )
            ).fetchone()

        assert row is not None
        assert row[1] == "entry_score"
        assert abs(row[2] - 0.8) < 1e-9


class TestLogOutcome:
    """Tests for log_outcome."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_success_returns_true(self, mock_db_write: AsyncMock) -> None:
        """Successful outcome recording should return True."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        result = await log_outcome(
            position_id="pos-abc",
            actual_return_pct=5.2,
            hit_target=True,
            hit_stop=False,
        )

        assert result is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_params_passed_correctly(self, mock_db_write: AsyncMock) -> None:
        """All outcome params should be forwarded to the SQL."""
        mock_db_write.side_effect = _make_db_write_side_effect()

        await log_outcome(
            position_id="pos-xyz",
            actual_return_pct=-2.5,
            hit_target=False,
            hit_stop=True,
        )

        call_args = mock_db_write.call_args
        write_fn = call_args[0][0]
        mock_session = await _await_write_fn(write_fn)
        params = mock_session.execute.call_args[0][1]
        assert params["position_id"] == "pos-xyz"
        assert params["return_pct"] == -2.5
        assert params["hit_target"] is False
        assert params["hit_stop"] is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_db_failure_returns_false(self, mock_db_write: AsyncMock) -> None:
        """Database failure should return False, not raise."""
        mock_db_write.side_effect = RuntimeError("Connection refused")

        result = await log_outcome(
            position_id="pos-abc",
            actual_return_pct=5.2,
            hit_target=True,
            hit_stop=False,
        )

        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.ml.performance_tracker.db_write", new_callable=AsyncMock)
    async def test_log_outcome_binds_return_pct_with_explicit_float_type(self, mock_db_write: AsyncMock) -> None:
        """`:return_pct` is used twice in the UPDATE — as the Float column value
        and inside `:return_pct >= 0`. Untyped, asyncpg deduces double precision
        from the first use and integer from the second and rejects the statement
        with AmbiguousParameterError on every exit ("Failed to log outcome").
        The bind must carry an explicit Float type so both renderings agree.
        """
        from sqlalchemy import Float
        from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg

        mock_db_write.side_effect = _make_db_write_side_effect()

        await log_outcome(position_id="pos-typed", actual_return_pct=0, hit_target=False, hit_stop=False)

        write_fn = mock_db_write.call_args[0][0]
        mock_session = await _await_write_fn(write_fn)
        stmt = mock_session.execute.call_args[0][0]

        assert isinstance(stmt._bindparams["return_pct"].type, Float)

        compiled = stmt.compile(dialect=pg_asyncpg.dialect())
        placeholder = f"${compiled.positiontup.index('return_pct') + 1}"
        rendered = str(compiled)
        assert rendered.count(f"{placeholder}::FLOAT") == 2, rendered
        assert rendered.count(placeholder) == 2, f"untyped use of {placeholder} remains: {rendered}"


class TestLogOutcomeRealDB:
    """`log_outcome` must actually update the row through the real await chain
    on the in-memory test DB, for both an int-valued and a float-valued return.
    """

    @staticmethod
    async def _seed_exit_prediction(position_id: str) -> str:
        prediction_id = await log_exit_prediction(
            symbol="AAPL",
            option_chain="AAPL260522C00200000",
            bucket="SWING",
            prediction_score=0.9,
            position_id=position_id,
        )
        assert prediction_id is not None
        return prediction_id

    @pytest.mark.integration
    @pytest.mark.asyncio
    @pytest.mark.parametrize("return_pct", [0, 12.5])
    async def test_log_outcome_updates_row_for_int_and_float_returns(self, return_pct: float) -> None:
        from sqlalchemy import text

        from orion.storage.db import async_session_factory, init_db

        await init_db()
        position_id = f"pos-outcome-{return_pct}"
        prediction_id = await self._seed_exit_prediction(position_id)

        result = await log_outcome(
            position_id=position_id,
            actual_return_pct=return_pct,
            hit_target=False,
            hit_stop=False,
        )

        assert result is True, "log_outcome must not swallow a failed UPDATE"

        async with async_session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT outcome_ts, actual_return_pct, prediction_correct FROM ml_predictions WHERE id = :id"),
                    {"id": prediction_id},
                )
            ).fetchone()

        assert row is not None
        assert row[0] is not None, "outcome_ts must be stamped"
        assert abs(float(row[1]) - float(return_pct)) < 1e-9
        # exit_score with prediction_class=1 and a non-negative return is correct.
        assert bool(row[2]) is True


# ---------------------------------------------------------------------------
# get_daily_accuracy
# ---------------------------------------------------------------------------


class TestGetDailyAccuracy:
    """Tests for get_daily_accuracy."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.shared.db_utils.db_query", new_callable=AsyncMock)
    async def test_returns_metrics_dict(self, mock_db_query: AsyncMock) -> None:
        """Should return a dict with accuracy metrics when data exists."""
        # Mock db_query to call the query_fn and return a row
        mock_row = (50, 40, 10, 80.0, 5.2, -1.3)

        async def _side_effect(query_fn):
            return mock_row

        mock_db_query.side_effect = _side_effect

        result = await get_daily_accuracy()

        assert result["total"] == 50
        assert result["correct"] == 40
        assert result["incorrect"] == 10
        assert result["accuracy_pct"] == 80.0
        assert result["avg_return_high_score"] == 5.2
        assert result["avg_return_low_score"] == -1.3

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.shared.db_utils.db_query", new_callable=AsyncMock)
    async def test_with_bucket_filter(self, mock_db_query: AsyncMock) -> None:
        """Should pass bucket parameter when provided."""
        mock_row = (10, 8, 2, 80.0, None, None)

        async def _side_effect(query_fn):
            mock_session = MagicMock()
            mock_result = MagicMock()
            mock_result.fetchone.return_value = mock_row
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await query_fn(mock_session)

            sql_text = str(mock_session.execute.call_args.args[0])
            params = mock_session.execute.call_args.args[1]
            assert "AND bucket = :bucket" in sql_text
            assert params == {"bucket": "0DTE"}
            return result

        mock_db_query.side_effect = _side_effect

        result = await get_daily_accuracy(bucket="0DTE")
        assert result["total"] == 10

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.shared.db_utils.db_query", new_callable=AsyncMock)
    async def test_handles_null_values(self, mock_db_query: AsyncMock) -> None:
        """Should handle None values in result row."""
        mock_row = (0, 0, 0, None, None, None)

        async def _side_effect(query_fn):
            return mock_row

        mock_db_query.side_effect = _side_effect

        result = await get_daily_accuracy()
        assert result["total"] == 0
        assert result["accuracy_pct"] is None
        assert result["avg_return_high_score"] is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.shared.db_utils.db_query", new_callable=AsyncMock)
    async def test_db_failure_returns_defaults(self, mock_db_query: AsyncMock) -> None:
        """Database failure should return a default dict with zeros."""
        mock_db_query.side_effect = RuntimeError("Query failed")

        result = await get_daily_accuracy()
        assert result["total"] == 0
        assert result["correct"] == 0
        assert result["incorrect"] == 0
        assert result["accuracy_pct"] is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.shared.db_utils.db_query", new_callable=AsyncMock)
    async def test_no_result_returns_defaults(self, mock_db_query: AsyncMock) -> None:
        """None result from db_query should return defaults."""

        async def _side_effect(query_fn):
            return None

        mock_db_query.side_effect = _side_effect

        result = await get_daily_accuracy()
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# get_weekly_performance
# ---------------------------------------------------------------------------


class TestGetWeeklyPerformance:
    """Tests for get_weekly_performance."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.shared.db_utils.db_query", new_callable=AsyncMock)
    async def test_returns_performance_by_bucket(self, mock_db_query: AsyncMock) -> None:
        """Should return per-bucket performance dict."""
        mock_rows = [
            ("0DTE", "entry_score", 30, 25, 83.33, 4.5),
            ("SWING", "exit_score", 20, 15, 75.0, 2.1),
        ]

        async def _side_effect(query_fn):
            return mock_rows

        mock_db_query.side_effect = _side_effect

        result = await get_weekly_performance()

        assert "0DTE_entry_score" in result
        assert result["0DTE_entry_score"]["predictions"] == 30
        assert result["0DTE_entry_score"]["correct"] == 25
        assert result["0DTE_entry_score"]["accuracy_pct"] == 83.33
        assert result["0DTE_entry_score"]["avg_return"] == 4.5

        assert "SWING_exit_score" in result
        assert result["SWING_exit_score"]["predictions"] == 20

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.shared.db_utils.db_query", new_callable=AsyncMock)
    async def test_empty_results_returns_empty_dict(self, mock_db_query: AsyncMock) -> None:
        """No rows should produce an empty dict."""

        async def _side_effect(query_fn):
            return []

        mock_db_query.side_effect = _side_effect

        result = await get_weekly_performance()
        assert result == {}

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.shared.db_utils.db_query", new_callable=AsyncMock)
    async def test_none_results_returns_empty_dict(self, mock_db_query: AsyncMock) -> None:
        """None result from db_query should produce an empty dict."""

        async def _side_effect(query_fn):
            return None

        mock_db_query.side_effect = _side_effect

        result = await get_weekly_performance()
        assert result == {}

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.shared.db_utils.db_query", new_callable=AsyncMock)
    async def test_handles_null_accuracy(self, mock_db_query: AsyncMock) -> None:
        """Null accuracy values should be represented as None."""
        mock_rows = [
            ("0DTE", "entry_score", 5, 0, None, None),
        ]

        async def _side_effect(query_fn):
            return mock_rows

        mock_db_query.side_effect = _side_effect

        result = await get_weekly_performance()
        assert result["0DTE_entry_score"]["accuracy_pct"] is None
        assert result["0DTE_entry_score"]["avg_return"] is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("orion.shared.db_utils.db_query", new_callable=AsyncMock)
    async def test_db_failure_returns_empty_dict(self, mock_db_query: AsyncMock) -> None:
        """Database failure should return an empty dict, not raise."""
        mock_db_query.side_effect = RuntimeError("DB unreachable")

        result = await get_weekly_performance()
        assert result == {}
