"""The daily-loss limit is a per-trading-day control and must roll over.

``current_daily_loss`` was only ever mutated by fills, loaded verbatim from the
persisted ``risk_state`` row, and reset by the one-time operator baseline. Nothing
rolled it at a trading-day boundary, so ``max_daily_loss`` was a cumulative
net-realized-loss ratchet since the last baseline: a handful of full-loss days
halted every new entry permanently with no sanctioned reset.

The figure now carries the America/New_York trading date it belongs to
(``risk_state.daily_loss_date``). A new trading day starts the figure at zero —
on load, before the admission check reads it, and before a fill accumulates
into it — while a mid-day restart preserves the running total.
"""

from __future__ import annotations

import importlib.util
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from orion.config import RiskSettings
from orion.execution.risk.baseline import record_risk_baseline
from orion.execution.risk.manager import RISK_ACCOUNTING_VERSION, RiskManager, _trading_date
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_risk import RiskState

# Friday 2026-08-14 11:00 ET, mid-session.
FROZEN_NOW = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)
TODAY = date(2026, 8, 14)
YESTERDAY = date(2026, 8, 13)

LIMIT = RiskSettings(max_daily_loss=1000.0)


@pytest.fixture
def frozen_clock(monkeypatch):
    """Pin the manager's clock so the trading date is deterministic."""
    monkeypatch.setattr(RiskManager, "_now", lambda self: FROZEN_NOW)


def _rollover_payloads(caplog) -> list[dict]:
    """Structured payloads of RISK_DAILY_LOSS_ROLLOVER INFO logs.

    The structlog pipeline renders the record into a JSON string by the time
    caplog sees it, so the structured kwargs live inside the message JSON's
    ``extra`` block.
    """
    payloads: list[dict] = []
    for r in caplog.records:
        try:
            parsed = json.loads(r.getMessage())
        except (ValueError, TypeError):
            continue
        extra = parsed.get("extra", {})
        if isinstance(extra, dict) and extra.get("event_type") == "RISK_DAILY_LOSS_ROLLOVER":
            payloads.append(extra)
    return payloads


async def _seed_row(
    *,
    loss: float,
    day: date | None,
    version: int | None = RISK_ACCOUNTING_VERSION,
    updated_at: datetime | None = None,
) -> None:
    await init_db()
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM risk_state"))
        await session.execute(
            text(
                "INSERT INTO risk_state (id, updated_at_utc, current_daily_loss, current_equity, starting_equity, "
                "peak_equity, open_positions_count, accounting_version, daily_loss_date) "
                "VALUES ('global_risk_v1', :updated_at, :loss, 100000.0, 100000.0, 100000.0, 0, :ver, :day)"
            ),
            {"updated_at": updated_at, "loss": loss, "ver": version, "day": day},
        )
        await session.commit()


async def _persisted_row() -> RiskState:
    async with async_session_factory() as session:
        row = (await session.execute(select(RiskState).where(RiskState.id == "global_risk_v1"))).scalars().first()
    assert row is not None
    return row


# ── In-memory rollover on the admission path ────────────────────────────────


def test_daily_loss_resets_on_new_trading_day(frozen_clock):
    rm = RiskManager(config=LIMIT)
    rm.current_daily_loss = 1500.0
    rm._daily_loss_date = YESTERDAY

    assert rm.check_order("AAPL", 1, 100.0, "buy") is True
    assert rm.current_daily_loss == pytest.approx(0.0)
    assert rm._daily_loss_date == TODAY


def test_daily_loss_persists_within_same_day(frozen_clock):
    rm = RiskManager(config=LIMIT)
    rm.current_daily_loss = 1500.0
    rm._daily_loss_date = TODAY

    assert rm.check_order("AAPL", 1, 100.0, "buy") is False
    assert rm.current_daily_loss == pytest.approx(1500.0)


def test_fresh_manager_is_dated_today(frozen_clock):
    """A directly-constructed manager owns today's zero figure, so a loss set on
    it blocks exactly as before — never silently zeroed on the first check."""
    rm = RiskManager(config=LIMIT)
    rm.current_daily_loss = 1500.0

    assert rm._daily_loss_date == TODAY
    assert rm.check_order("AAPL", 1, 100.0, "buy") is False


# ── Persistence and load ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_loss_survives_mid_day_restart(frozen_clock):
    await _seed_row(loss=1500.0, day=TODAY)

    rm = RiskManager(config=LIMIT)
    await rm.initialize()

    assert rm.current_daily_loss == pytest.approx(1500.0)
    assert rm._daily_loss_date == TODAY
    assert rm.check_order("AAPL", 1, 100.0, "buy") is False


@pytest.mark.asyncio
async def test_stale_row_loads_as_zero(frozen_clock, caplog):
    await _seed_row(loss=1500.0, day=YESTERDAY)
    caplog.set_level(logging.INFO, logger="orion.execution.risk.manager")

    rm = RiskManager(config=LIMIT)
    await rm.initialize()

    assert rm.current_daily_loss == pytest.approx(0.0)
    assert rm._daily_loss_date == TODAY
    assert rm.check_order("AAPL", 1, 100.0, "buy") is True

    payloads = _rollover_payloads(caplog)
    assert len(payloads) == 1, "expected exactly one RISK_DAILY_LOSS_ROLLOVER log"
    assert payloads[0]["discarded_loss"] == pytest.approx(1500.0)
    assert payloads[0]["discarded_date"] == YESTERDAY.isoformat()
    assert payloads[0]["trading_date"] == TODAY.isoformat()
    assert payloads[0]["legacy_row"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "last_written",
    [
        pytest.param(None, id="never-stamped"),
        pytest.param(datetime(2026, 8, 13, 20, 0, tzinfo=UTC), id="yesterday-16:00-ET"),
        pytest.param(datetime(2026, 8, 14, 3, 30, tzinfo=UTC), id="yesterday-23:30-ET"),
    ],
)
async def test_legacy_row_without_date_loads_as_zero(frozen_clock, caplog, last_written):
    """A row written before the date column existed has no day identity, and
    its last write was an earlier session: it starts today at zero."""
    await _seed_row(loss=1500.0, day=None, updated_at=last_written)
    caplog.set_level(logging.INFO, logger="orion.execution.risk.manager")

    rm = RiskManager(config=LIMIT)
    await rm.initialize()

    assert rm.current_daily_loss == pytest.approx(0.0)
    assert rm._daily_loss_date == TODAY

    payloads = _rollover_payloads(caplog)
    assert len(payloads) == 1
    assert payloads[0]["discarded_loss"] == pytest.approx(1500.0)
    assert payloads[0]["discarded_date"] is None
    assert payloads[0]["legacy_row"] is True


@pytest.mark.asyncio
async def test_legacy_row_last_written_today_keeps_its_loss(frozen_clock, caplog):
    """Deployment window: the column is added mid-session, so the live row is
    NULL-dated but its figure was written TODAY and may already be at the
    limit. It must stay in force — the loss is retained under today's date,
    never zeroed on an unknown-date row that was current this session."""
    await _seed_row(loss=1500.0, day=None, updated_at=FROZEN_NOW - timedelta(hours=1))
    caplog.set_level(logging.INFO, logger="orion.execution.risk.manager")

    rm = RiskManager(config=LIMIT)
    await rm.initialize()

    assert rm.current_daily_loss == pytest.approx(1500.0)
    assert rm._daily_loss_date == TODAY
    assert rm.check_order("AAPL", 1, 100.0, "buy") is False
    assert _rollover_payloads(caplog) == [], "nothing was discarded"

    # The inferred date is persisted, so the next restart is unambiguous.
    await rm._save_state()
    row = await _persisted_row()
    assert row.daily_loss_date == TODAY
    assert row.current_daily_loss == pytest.approx(1500.0)


@pytest.mark.asyncio
async def test_save_state_persists_daily_loss_date(frozen_clock):
    """The rollover on load reaches the row on the next save."""
    await _seed_row(loss=1500.0, day=YESTERDAY)

    rm = RiskManager(config=LIMIT)
    await rm.initialize()
    rm.current_daily_loss = 250.0
    await rm._save_state()

    row = await _persisted_row()
    assert row.current_daily_loss == pytest.approx(250.0)
    assert row.daily_loss_date == TODAY


@pytest.mark.asyncio
async def test_gated_manager_does_not_stamp_daily_loss_date(frozen_clock):
    """While the accounting gate is raised the row's figures are untrusted
    legacy values; the date is stamped under the same rule as the version."""
    await _seed_row(loss=38.79, day=None, version=None)

    rm = RiskManager(config=LIMIT)
    await rm.initialize()
    assert rm.baseline_unverified is True

    await rm._save_state()

    row = await _persisted_row()
    assert row.accounting_version is None
    assert row.daily_loss_date is None


# ── Fill path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_fill_after_midnight_rolls_before_accumulating(frozen_clock):
    await init_db()
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM risk_state"))
        await session.commit()

    rm = RiskManager(config=LIMIT)
    rm.current_daily_loss = 1500.0
    rm._daily_loss_date = YESTERDAY
    rm.positions["AAPL"] = {"qty": 10.0, "avg_entry": 100.0}

    # Close 10 @ 80: realized -$200 on today's date.
    outcome = await rm.process_fill("AAPL", 10, 80.0, "sell", fill_id="close_after_midnight")

    assert outcome.realized_pnl == pytest.approx(-200.0)
    assert rm.current_daily_loss == pytest.approx(200.0), "yesterday's 1500 must not carry into today"
    assert rm._daily_loss_date == TODAY

    row = await _persisted_row()
    assert row.current_daily_loss == pytest.approx(200.0)
    assert row.daily_loss_date == TODAY


async def _manager_with_open_long() -> RiskManager:
    await init_db()
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM risk_state"))
        await session.commit()
    rm = RiskManager(config=LIMIT)
    rm.current_equity = 100000.0
    rm.current_daily_loss = 900.0
    rm._daily_loss_date = TODAY
    rm.positions["AAPL"] = {"qty": 10.0, "avg_entry": 100.0}
    return rm


# A fill that executed at 15:59 ET yesterday but is only being processed now
# (recovered after a restart / poll miss). Its P&L belongs to yesterday's
# session, whose budget is closed.
LATE_PRIOR_DAY_FILL_AT = datetime(2026, 8, 13, 19, 59, tzinfo=UTC)


@pytest.mark.asyncio
async def test_late_prior_day_gain_does_not_credit_today(frozen_clock, caplog):
    """A recovered prior-session GAIN must not buy headroom under today's limit."""
    rm = await _manager_with_open_long()
    caplog.set_level(logging.INFO, logger="orion.execution.risk.manager")

    outcome = await rm.process_fill("AAPL", 10, 150.0, "sell", fill_id="late_gain", filled_at=LATE_PRIOR_DAY_FILL_AT)

    assert outcome.realized_pnl == pytest.approx(500.0)
    assert rm.current_daily_loss == pytest.approx(900.0), "yesterday's gain must not lower today's loss"
    assert rm.current_equity == pytest.approx(100500.0), "equity is cumulative and still books it"
    assert rm._daily_loss_date == TODAY
    late = [
        json.loads(r.getMessage())["extra"]
        for r in caplog.records
        if '"RISK_LATE_FILL_PRIOR_SESSION"' in r.getMessage()
    ]
    assert len(late) == 1
    assert late[0]["fill_trading_date"] == YESTERDAY.isoformat()
    assert late[0]["realized_pnl"] == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_late_prior_day_loss_does_not_debit_today(frozen_clock):
    """Symmetric: a recovered prior-session LOSS belongs to that session's
    (closed) budget, not today's."""
    rm = await _manager_with_open_long()

    outcome = await rm.process_fill("AAPL", 10, 80.0, "sell", fill_id="late_loss", filled_at=LATE_PRIOR_DAY_FILL_AT)

    assert outcome.realized_pnl == pytest.approx(-200.0)
    assert rm.current_daily_loss == pytest.approx(900.0)
    assert rm.current_equity == pytest.approx(99800.0)


@pytest.mark.asyncio
async def test_same_day_fill_with_timestamp_accumulates(frozen_clock):
    rm = await _manager_with_open_long()

    await rm.process_fill("AAPL", 10, 80.0, "sell", fill_id="same_day", filled_at=FROZEN_NOW - timedelta(minutes=5))

    assert rm.current_daily_loss == pytest.approx(1100.0)


@pytest.mark.asyncio
async def test_fill_without_timestamp_accumulates_into_today(frozen_clock):
    """Callers that cannot supply a broker time book into the current session."""
    rm = await _manager_with_open_long()

    await rm.process_fill("AAPL", 10, 80.0, "sell", fill_id="no_ts")

    assert rm.current_daily_loss == pytest.approx(1100.0)


@pytest.mark.asyncio
async def test_fill_processor_passes_broker_fill_time_to_risk_accounting(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from orion.execution.fill_processor import FillProcessor

    processor = FillProcessor()
    risk_manager = MagicMock()
    risk_manager.process_fill = AsyncMock()
    monkeypatch.setattr("orion.execution.fill_processor.is_fill_processed", AsyncMock(return_value=False))
    monkeypatch.setattr("orion.execution.fill_processor.mark_fill_processed", AsyncMock())
    monkeypatch.setattr("orion.execution.fill_processor.persist_fill_record", AsyncMock())

    fill = {
        "id": "broker-late",
        "client_order_id": "orion_late",
        "symbol": "AAPL",
        "filled_qty": 1.0,
        "qty": 1.0,
        "filled_avg_price": 2.5,
        "side": "buy",
        "filled_at": "2026-08-13T19:59:00Z",
    }
    await processor.process_single_fill(fill, risk_manager, AsyncMock())

    kwargs = risk_manager.process_fill.await_args.kwargs
    assert kwargs["filled_at"] == LATE_PRIOR_DAY_FILL_AT


# ── Trading-date helper ─────────────────────────────────────────────────────


def test_trading_date_uses_new_york_calendar():
    # 03:30Z on Monday 2026-08-17 is still Sunday 23:30 in New York.
    assert _trading_date(datetime(2026, 8, 17, 3, 30, tzinfo=UTC)) == date(2026, 8, 16)
    # 04:30Z is Monday 00:30 in New York (EDT, UTC-4).
    assert _trading_date(datetime(2026, 8, 17, 4, 30, tzinfo=UTC)) == date(2026, 8, 17)


# ── Operator baseline ───────────────────────────────────────────────────────


class _FrozenDatetime(datetime):
    """``datetime`` whose ``now`` is pinned to FROZEN_NOW, for the baseline module."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return FROZEN_NOW if tz is None else FROZEN_NOW.astimezone(tz)


@pytest.mark.asyncio
async def test_baseline_stamps_daily_loss_date(monkeypatch):
    await _seed_row(loss=38.79, day=None, version=None)
    monkeypatch.setattr("orion.execution.risk.baseline.datetime", _FrozenDatetime)

    await record_risk_baseline(starting_equity=100000.0, note="operator")

    row = await _persisted_row()
    assert row.current_daily_loss == pytest.approx(0.0)
    assert row.daily_loss_date == TODAY


# ── Migration ───────────────────────────────────────────────────────────────


def test_migration_chains_off_b5():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "b6_risk_daily_loss_date.py"
    spec = importlib.util.spec_from_file_location("_orion_b6_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "b6_risk_daily_loss_date"
    assert module.down_revision == "b5_risk_accounting_version"
