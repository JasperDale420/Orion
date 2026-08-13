"""Fail-closed gate: risk state persisted under the pre-multiplier accounting
convention must not silently arm the kill switch.

Option realized P&L used to be recorded without the 100x contract multiplier,
so any `risk_state` row written before that fix holds `current_daily_loss` /
`current_equity` at 1/100th scale. Restoring those values and then combining
them with correctly-scaled new fills leaves the daily-loss and drawdown gates
understated. Until an operator records a verified baseline, order admission
fails closed.

Provenance lives on the row (`RiskState.accounting_version`) rather than in a
separate flag, so a rolled-back or stale writer cannot leave the data on the old
convention while an external marker claims the new one.
"""

import os

os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

from unittest.mock import patch

import pytest
from sqlalchemy import text

from orion.core.service_lease import SERVICE_LEASE_STALE_SECONDS
from orion.execution.risk.baseline import BaselineRefused, record_risk_baseline
from orion.execution.risk.manager import RISK_ACCOUNTING_VERSION, RiskManager
from orion.storage.db import async_session_factory, init_db

OPTION = "AAPL260708C00312500"


async def _reset(*, with_state_row: bool, version: int | None = None, open_positions: int = 0) -> None:
    await init_db()
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM risk_state"))
        if with_state_row:
            await session.execute(
                text(
                    "INSERT INTO risk_state (id, current_daily_loss, current_equity, starting_equity, "
                    "peak_equity, open_positions_count, accounting_version) "
                    "VALUES ('global_risk_v1', 38.79, 99961.21, 100000.0, 100011.33, :pos, :ver)"
                ),
                {"ver": version, "pos": open_positions},
            )
        await session.commit()


@pytest.mark.asyncio
async def test_legacy_unstamped_row_blocks_orders():
    """The real production situation: a pre-fix row with a NULL version."""
    await _reset(with_state_row=True, version=None)

    rm = RiskManager()
    await rm.initialize()

    assert rm.baseline_unverified is True
    assert rm.check_order(OPTION, 1, 500.0, "buy") is False


@pytest.mark.asyncio
async def test_stale_version_blocks_orders():
    await _reset(with_state_row=True, version=RISK_ACCOUNTING_VERSION - 1)

    rm = RiskManager()
    await rm.initialize()

    assert rm.baseline_unverified is True
    assert rm.check_order(OPTION, 1, 500.0, "buy") is False


@pytest.mark.asyncio
async def test_current_version_allows_orders():
    await _reset(with_state_row=True, version=RISK_ACCOUNTING_VERSION)

    rm = RiskManager()
    await rm.initialize()

    assert rm.baseline_unverified is False
    assert rm.check_order(OPTION, 1, 500.0, "buy") is True


@pytest.mark.asyncio
async def test_fresh_install_is_not_gated():
    """No persisted row means no legacy contamination — do not block a new DB."""
    await _reset(with_state_row=False)

    rm = RiskManager()
    await rm.initialize()

    assert rm.baseline_unverified is False
    assert rm.check_order(OPTION, 1, 500.0, "buy") is True


@pytest.mark.asyncio
async def test_state_load_failure_fails_closed():
    """A transient DB failure must not admit orders against a legacy ledger.

    Seeded with a CURRENT-version row so the assertion is non-vacuous: a
    successful read would clear the gate, so only the injected failure can
    leave it raised.
    """
    await _reset(with_state_row=True, version=RISK_ACCOUNTING_VERSION)

    # Sanity: this row clears the gate when the read succeeds.
    ok = RiskManager()
    await ok.initialize()
    assert ok.baseline_unverified is False

    rm = RiskManager()
    with patch("orion.storage.db.async_session_factory", side_effect=RuntimeError("db down")):
        await rm.initialize()

    assert rm.baseline_unverified is True
    assert rm.check_order(OPTION, 1, 500.0, "buy") is False


@pytest.mark.asyncio
async def test_gated_manager_does_not_forge_provenance_on_save():
    """While gated, persisting state must NOT stamp the current version.

    Otherwise the legacy figures would be relabelled as current and the gate
    would silently lower itself on the next restart.
    """
    await _reset(with_state_row=True, version=None)

    rm = RiskManager()
    await rm.initialize()
    assert rm.baseline_unverified is True

    await rm._save_state()

    async with async_session_factory() as session:
        version = (await session.execute(text("SELECT accounting_version FROM risk_state"))).scalar()
    assert version is None, "gated save must leave provenance untouched"

    rm2 = RiskManager()
    await rm2.initialize()
    assert rm2.baseline_unverified is True


@pytest.mark.asyncio
async def test_gated_writer_cannot_clobber_a_concurrently_recorded_baseline():
    """Inter-process race: A holds legacy figures, B records the baseline, A saves.

    Without a guard, A overwrites the money columns from its stale ledger while
    leaving B's v2 stamp intact — so the next restart trusts legacy values.
    A's save must be discarded instead.
    """
    await _reset(with_state_row=True, version=None)

    # Process A: starts against the legacy row, stays gated.
    a = RiskManager()
    await a.initialize()
    assert a.baseline_unverified is True
    a.current_daily_loss = 38.79  # stale, 1/100th-scale
    a.current_equity = 99961.21

    # Process B: operator records the verified baseline.
    await record_risk_baseline(starting_equity=250000.0, note="operator")

    # Process A saves after B — must not persist its stale ledger.
    await a._save_state()

    async with async_session_factory() as session:
        row = (
            await session.execute(text("SELECT accounting_version, current_equity, current_daily_loss FROM risk_state"))
        ).one()
    assert row[0] == RISK_ACCOUNTING_VERSION
    assert row[1] == pytest.approx(250000.0), "B's verified equity must survive A's stale save"
    assert row[2] == pytest.approx(0.0)

    # And a restart must come up trusting B's baseline, not A's figures.
    c = RiskManager()
    await c.initialize()
    assert c.baseline_unverified is False
    assert c.current_equity == pytest.approx(250000.0)


@pytest.mark.asyncio
async def test_recording_baseline_clears_the_gate():
    await _reset(with_state_row=True, version=None)

    rm = RiskManager()
    await rm.initialize()
    assert rm.baseline_unverified is True

    await record_risk_baseline(starting_equity=100000.0, note="verified by operator")

    rm2 = RiskManager()
    await rm2.initialize()
    assert rm2.baseline_unverified is False
    assert rm2.check_order(OPTION, 1, 500.0, "buy") is True
    assert rm2.current_daily_loss == pytest.approx(0.0)
    assert rm2.starting_equity == pytest.approx(100000.0)


async def _set_lease(service_id: str, age_seconds: float) -> None:
    """Plant a service lease row of a given age."""
    from datetime import UTC, datetime, timedelta

    from orion.core.service_lease import _service_lease_key

    ts = datetime.now(UTC) - timedelta(seconds=age_seconds)
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM system_status WHERE key = :k"), {"k": _service_lease_key(service_id)})
        await session.execute(
            text("INSERT INTO system_status (key, status, details, last_updated_utc) VALUES (:k, 'RUNNING', :d, :t)"),
            {"k": _service_lease_key(service_id), "d": "run_id=other", "t": ts},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_baseline_refused_while_a_risk_writer_is_live():
    """Quiescence: recording while execution runs would be clobbered by it."""
    await _reset(with_state_row=True, version=None)
    await _set_lease("execution", age_seconds=5)

    with pytest.raises(BaselineRefused, match="still live"):
        await record_risk_baseline(starting_equity=100000.0)


@pytest.mark.asyncio
async def test_baseline_allowed_once_lease_is_stale():
    """A stopped service leaves a stale lease; that must not block forever."""
    await _reset(with_state_row=True, version=None)
    await _set_lease("execution", age_seconds=SERVICE_LEASE_STALE_SECONDS + 60)

    await record_risk_baseline(starting_equity=100000.0)

    rm = RiskManager()
    await rm.initialize()
    assert rm.baseline_unverified is False


@pytest.mark.asyncio
async def test_rerunning_baseline_on_current_row_is_refused():
    """Guard against using this as a daily kill-switch reset button."""
    await _reset(with_state_row=True, version=RISK_ACCOUNTING_VERSION)

    with pytest.raises(BaselineRefused, match="already on accounting version"):
        await record_risk_baseline(starting_equity=100000.0)


@pytest.mark.asyncio
async def test_baseline_refused_while_positions_are_open():
    await _reset(with_state_row=True, version=None, open_positions=3)

    with pytest.raises(BaselineRefused, match="open position"):
        await record_risk_baseline(starting_equity=100000.0)


@pytest.mark.asyncio
async def test_baseline_rejects_nonpositive_equity():
    await _reset(with_state_row=True, version=None)

    with pytest.raises(BaselineRefused):
        await record_risk_baseline(starting_equity=0.0)


@pytest.mark.asyncio
async def test_gate_still_processes_fills():
    """Entries are refused, but an in-flight fill must still update the ledger."""
    await _reset(with_state_row=True, version=None)

    rm = RiskManager()
    await rm.initialize()

    await rm.process_fill(OPTION, 1, 2.00, "buy", fill_id="gate_buy_1")
    outcome = await rm.process_fill(OPTION, 1, 3.00, "sell", fill_id="gate_sell_1")
    assert outcome.realized_pnl == pytest.approx(100.0)
