import os

# Force SQLite for unit tests
os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.config import RiskSettings
from orion.core.circuit_breaker import CircuitBreaker
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_risk import RiskState


@pytest.mark.asyncio
async def test_process_fill_rolls_back_state_and_dedup_when_save_state_fails(risk_manager_factory) -> None:
    """process_fill computes a fill's effect, persists it (_save_fill_effect),
    and only THEN applies it to self.* -- so a persist failure now means
    nothing was ever mutated in the first place (nothing to roll back). This
    guards that invariant: a failed attempt must leave current_equity/positions
    untouched and the fill unmarked (or a retry is blocked), and a clean retry
    must apply exactly once.
    """
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="save-fail-1")

    real_save_fill_effect = rm._save_fill_effect
    rm._save_fill_effect = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(RuntimeError, match="db down"):
        await rm.process_fill("AAPL", 10, 110.0, "sell", fill_id="save-fail-2", filled_at=datetime.now(UTC))

    # Nothing from the failed attempt may stick: equity/position unchanged,
    # and the fill must not be marked processed (or a retry is blocked).
    assert rm.current_equity == pytest.approx(10000.0)
    assert rm.positions["AAPL"]["qty"] == pytest.approx(10.0)
    assert "save-fail-2" not in rm.processed_fill_ids

    rm._save_fill_effect = real_save_fill_effect

    # Retry with the same fill_id: applies exactly once, cleanly.
    await rm.process_fill("AAPL", 10, 110.0, "sell", fill_id="save-fail-2", filled_at=datetime.now(UTC))

    assert rm.current_equity == pytest.approx(10100.0)
    assert rm.positions["AAPL"]["qty"] == pytest.approx(0.0)
    assert "save-fail-2" in rm.processed_fill_ids


@pytest.mark.asyncio
async def test_process_fill_rolls_back_greeks_when_save_state_fails(risk_manager_factory) -> None:
    """A closing fill's effect (clearing the position's Greeks among other
    things) is only applied to self.* AFTER the effect has been durably
    saved. If the save fails, nothing -- including Greeks -- is mutated:
    a still-open position must remain visible to the next order's
    portfolio-Greeks limit check.
    """
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    rm.set_intended_position_greeks("AAPL", delta=50.0, gamma=1.0, theta=-0.5, vega=2.0)
    await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="greeks-fail-1")

    assert rm.position_greeks["AAPL"]["delta"] == pytest.approx(50.0)
    assert rm.portfolio_delta == pytest.approx(50.0)

    real_save_fill_effect = rm._save_fill_effect
    rm._save_fill_effect = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(RuntimeError, match="db down"):
        await rm.process_fill("AAPL", 10, 110.0, "sell", fill_id="greeks-fail-2", filled_at=datetime.now(UTC))

    # The (still-open) position's Greeks must still be tracked.
    assert rm.position_greeks["AAPL"]["delta"] == pytest.approx(50.0)
    assert rm.portfolio_delta == pytest.approx(50.0)

    rm._save_fill_effect = real_save_fill_effect
    await rm.process_fill("AAPL", 10, 110.0, "sell", fill_id="greeks-fail-2", filled_at=datetime.now(UTC))

    assert "AAPL" not in rm.position_greeks
    assert rm.portfolio_delta == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_process_fill_persists_new_peak_equity_in_the_same_save(risk_manager_factory) -> None:
    """A profitable close that sets a new equity high must persist the new
    peak_equity in the SAME _save_state() call as the fill itself.
    _evaluate_drawdown_kill_switch (which also updates peak_equity) now runs
    AFTER _save_state, so without this, a restart right after a profitable
    close -- before any other fill triggers another save -- would reload a
    stale, too-low peak_equity, understating drawdown % against the true
    high-water mark and letting the kill switch under-fire.
    """
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.peak_equity = 10000.0
    rm.current_daily_loss = 0.0

    await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="peak-1")
    # Sell at a profit: new equity 10100 is a new high.
    await rm.process_fill("AAPL", 10, 110.0, "sell", fill_id="peak-2", filled_at=datetime.now(UTC))

    assert rm.peak_equity == pytest.approx(10100.0)

    async with async_session_factory() as session:
        state = await session.get(RiskState, "global_risk_v1")
        assert state is not None
        assert state.peak_equity == pytest.approx(10100.0)


@pytest.mark.asyncio
async def test_process_fill_rolls_back_peak_equity_when_save_state_fails(risk_manager_factory) -> None:
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.peak_equity = 10000.0
    rm.current_daily_loss = 0.0

    await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="peak-fail-1")

    real_save_fill_effect = rm._save_fill_effect
    rm._save_fill_effect = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(RuntimeError, match="db down"):
        await rm.process_fill("AAPL", 10, 110.0, "sell", fill_id="peak-fail-2", filled_at=datetime.now(UTC))

    assert rm.peak_equity == pytest.approx(10000.0)

    rm._save_fill_effect = real_save_fill_effect
    await rm.process_fill("AAPL", 10, 110.0, "sell", fill_id="peak-fail-2", filled_at=datetime.now(UTC))

    assert rm.peak_equity == pytest.approx(10100.0)


@pytest.mark.asyncio
async def test_process_fill_rolls_back_when_baseline_unverified_save_is_discarded() -> None:
    """_save_state() discards its write (returns False, no exception) when
    this process holds unverified legacy-scale figures while another
    process has already recorded a verified baseline at the current
    accounting version -- an existing, deliberate safety branch that direct
    callers of _save_state() rely on staying non-raising (see
    test_risk_baseline_gate.py). That discard, unhandled, would let
    process_fill() mark the fill processed and durably commit the
    fill_processor.py marker without ever having persisted the fill's
    equity/PnL effect -- exactly the kind of "success without a save" this
    whole rollback mechanism exists to catch. process_fill() itself checks
    _save_state()'s return value and turns a discard into the same rollback
    path as any other failure.
    """
    from orion.core.errors import StorageError
    from orion.execution.risk.manager import RISK_ACCOUNTING_VERSION, RiskManager

    await init_db()

    async with async_session_factory() as session:
        session.add(
            RiskState(
                id="global_risk_v1",
                current_daily_loss=0.0,
                current_equity=10000.0,
                starting_equity=10000.0,
                peak_equity=10000.0,
                open_positions_count=0,
                accounting_version=RISK_ACCOUNTING_VERSION,
            )
        )
        await session.commit()

    rm = RiskManager()
    rm.baseline_unverified = True
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    with pytest.raises(StorageError):
        await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="baseline-discard-1")

    # A discarded save must roll back exactly like any other _save_state
    # failure: no phantom position, no dedup entry blocking a real retry.
    assert "AAPL" not in rm.positions
    assert "baseline-discard-1" not in rm.processed_fill_ids

    async with async_session_factory() as session:
        state = await session.get(RiskState, "global_risk_v1")
        assert state is not None
        assert state.current_equity == pytest.approx(10000.0)


@pytest.mark.asyncio
async def test_save_state_metrics_failure_does_not_mask_a_successful_db_commit(risk_manager_factory) -> None:
    """_save_state's db_write() is the actual commit; the metrics update
    after it is best-effort telemetry. A metrics failure must not make
    _save_state() look like it failed -- process_fill's rollback-on-failure
    would otherwise revert in-memory state (and fill_processor.py would
    delete the just-written processed-fill marker) even though the DB
    already durably holds the new state, letting a retry double-apply on
    top of an already-committed value.
    """
    rm = risk_manager_factory()
    rm.current_equity = 5000.0

    broken_metrics = MagicMock()
    broken_metrics.risk_equity.set.side_effect = RuntimeError("metrics backend down")

    with patch("orion.execution.risk.manager._metrics", broken_metrics):
        await rm._save_state()  # must not raise

    async with async_session_factory() as session:
        state = await session.get(RiskState, "global_risk_v1")
        assert state is not None
        assert state.current_equity == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_process_fill_survives_drawdown_breaker_persistence_failure() -> None:
    """The drawdown kill-switch's own CircuitBreaker.open() DB write must not
    abort process_fill -- otherwise a breaker-table hiccup would strand the
    fill's equity/position update exactly like a _save_state failure would,
    even though the fill itself was fully processed correctly.
    """
    with patch("orion.execution.risk.manager._metrics", MagicMock()):
        await init_db()

        cb = CircuitBreaker()
        await cb.close()
        assert await cb.is_open() is False

        cfg = RiskSettings(max_daily_loss=1e9, max_drawdown_pct=0.05)
        from orion.execution.risk.manager import RiskManager

        rm = RiskManager(config=cfg)
        rm.current_equity = 1000.0
        rm.starting_equity = 1000.0
        rm.peak_equity = 1000.0
        rm.positions["SPY"] = {"qty": 10.0, "avg_entry": 100.0}

        with patch(
            "orion.core.circuit_breaker.db_write",
            AsyncMock(side_effect=RuntimeError("breaker db down")),
        ):
            # Sell 10 @ 90 => realized pnl = -100 => 10% drawdown, breaches the 5% limit.
            await rm.process_fill("SPY", qty=10.0, price=90.0, side="sell", fill_id="mock_dd_breaker_fail")

        # The breaker failed to persist, but the fill's own risk-state effect
        # must still be durable and the fill must be retry-safe (processed).
        assert rm.current_equity == pytest.approx(900.0)
        assert "mock_dd_breaker_fail" in rm.processed_fill_ids

        async with async_session_factory() as session:
            state = await session.get(RiskState, "global_risk_v1")
            assert state is not None
            assert state.current_equity == pytest.approx(900.0)
