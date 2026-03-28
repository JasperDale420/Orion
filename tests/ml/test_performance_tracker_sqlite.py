"""
Integration test to validate ML performance tracker SQL queries work with SQLite.
"""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_get_daily_accuracy_sqlite_query_syntax() -> None:
    """Validate get_daily_accuracy SQL is valid SQLite syntax."""
    from orion.storage.db import async_session_factory, configure_db, init_db

    configure_db("sqlite+aiosqlite:///:memory:")
    await init_db()

    # Insert test data
    async with async_session_factory() as session:
        await session.execute(
            text("""
                INSERT INTO ml_predictions
                (id, symbol, option_chain, bucket, model_type, prediction_score,
                 prediction_class, prediction_correct, actual_return_pct, prediction_ts)
                VALUES
                ('test-1', 'AAPL', 'OPT1', '0DTE', 'entry_score', 0.75, 1, 1, 5.2, datetime('now')),
                ('test-2', 'AAPL', 'OPT2', '0DTE', 'entry_score', 0.25, 0, 1, -1.3, datetime('now'))
            """)
        )
        await session.commit()

    # Test get_daily_accuracy with SQLite
    from orion.ml.performance_tracker import get_daily_accuracy

    result = await get_daily_accuracy()

    assert result["total"] == 2
    assert result["correct"] == 2
    assert result["accuracy_pct"] == 100.0
    assert result["avg_return_high_score"] == 5.2
    assert result["avg_return_low_score"] == -1.3


@pytest.mark.asyncio
async def test_get_weekly_performance_sqlite_query_syntax() -> None:
    """Validate get_weekly_performance SQL is valid SQLite syntax."""
    from orion.storage.db import async_session_factory, configure_db, init_db

    configure_db("sqlite+aiosqlite:///:memory:")
    await init_db()

    # Insert test data with outcome_ts set (required for weekly query)
    async with async_session_factory() as session:
        await session.execute(
            text("""
                INSERT INTO ml_predictions
                (id, symbol, option_chain, bucket, model_type, prediction_score,
                 prediction_class, prediction_correct, actual_return_pct, prediction_ts, outcome_ts)
                VALUES
                ('test-3', 'MSFT', 'OPT3', 'SWING', 'entry_score', 0.8, 1, 1, 4.5, datetime('now'), datetime('now')),
                ('test-4', 'MSFT', 'OPT4', 'SWING', 'exit_score', 0.3, 0, 0, -2.1, datetime('now'), datetime('now'))
            """)
        )
        await session.commit()

    # Test get_weekly_performance with SQLite
    from orion.ml.performance_tracker import get_weekly_performance

    result = await get_weekly_performance()

    assert "SWING_entry_score" in result
    assert result["SWING_entry_score"]["predictions"] == 1
    assert result["SWING_entry_score"]["correct"] == 1
    assert result["SWING_entry_score"]["accuracy_pct"] == 100.0
