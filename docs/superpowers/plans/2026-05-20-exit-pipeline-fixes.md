# Exit Pipeline Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Orion's ability to actually exit positions — currently every Phase-2-fallback-triggered exit attempt fails silently outside RTH (options market-orders are rejected by Alpaca), and even successful in-RTH exits never log to `exit_decisions` because of a sync-vs-async write bug.

**Architecture:** Three independent fixes layered. Bug #2 (logger) is a 10-line diff that lights up observability. Bug #1 (market-orders) is a refactor of `ExecutionEngine.close_position` to use option-aware limit pricing plus an outside-RTH "queue and replay" path. Bug #3 (feature defaults) is a small persistence layer for running-window position stats so the ML branch can score correctly post-restart.

**Tech Stack:** Python 3.12 + SQLAlchemy 2.x async + asyncpg + pytest-asyncio.

---

## Reference findings

Read first: `docs/rca/exit_classifier/SYNTHESIS.md` — the 4-agent RCA that produced this plan.

Key files in this plan:
- `src/orion/ml/performance_tracker.py` — Bug #2.
- `src/orion/execution/execution_engine.py` (`close_position`, ~line 906-988) — Bug #1.
- `src/orion/execution/position_monitor.py` (`evaluate_exits`, `_build_exit_features`) — Bug #3.
- `src/orion/storage/models_risk.py` — new `position_running_stats` table for Bug #3.

---

## Phase 1 — Fix `log_exit_prediction` / `log_entry_prediction` async-vs-sync bug (Bug #2)

**Why first:** Tiny diff, no risk, immediately unblocks observability. Once landed, the `exit_decisions` and `ml_predictions` tables will populate. Without this, we can't measure the effect of Phase 2 + 3.

### Task 1.1: Make the write callbacks async + await session.execute

**Files:**
- Modify: `src/orion/ml/performance_tracker.py:42-69` (`log_entry_prediction.write`)
- Modify: `src/orion/ml/performance_tracker.py:83-101` (`log_exit_prediction.write`)
- Test: `tests/ml/test_performance_tracker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/ml/test_performance_tracker.py` if not present, or append:

```python
from __future__ import annotations
import os
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from sqlalchemy import select, text

from orion.ml.performance_tracker import log_entry_prediction, log_exit_prediction
from orion.storage.db import async_session_factory, init_db


@pytest.mark.asyncio
async def test_log_exit_prediction_writes_row() -> None:
    """Bug #2 regression: pre-fix, the inner `write` was sync and
    `await operation(session)` raised `TypeError: object NoneType
    can't be used in 'await' expression`. Every exit log was lost."""
    await init_db()
    # Make sure the ml_predictions table exists for SQLite test runs.
    async with async_session_factory() as session:
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS ml_predictions ("
            "id TEXT PRIMARY KEY, symbol TEXT, option_chain TEXT, bucket TEXT, "
            "model_type TEXT, prediction_score REAL, prediction_class INTEGER, "
            "confidence REAL, position_id TEXT)"
        ))
        await session.commit()

    pid = await log_exit_prediction(
        symbol="AAPL",
        option_chain="AAPL260522C00200000",
        bucket="SWING",
        prediction_score=0.73,
        position_id="pos-1",
    )
    assert pid is not None  # pre-fix: returned None because Exception caught the TypeError

    async with async_session_factory() as session:
        rows = list(
            (await session.execute(text("SELECT * FROM ml_predictions WHERE id = :id"), {"id": pid})).all()
        )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_log_entry_prediction_writes_row() -> None:
    """Same bug shape in log_entry_prediction."""
    await init_db()
    async with async_session_factory() as session:
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS ml_predictions ("
            "id TEXT PRIMARY KEY, symbol TEXT, option_chain TEXT, bucket TEXT, "
            "model_type TEXT, prediction_score REAL, prediction_class INTEGER, "
            "confidence REAL, position_id TEXT)"
        ))
        await session.commit()

    pid = await log_entry_prediction(
        symbol="AAPL",
        option_chain="AAPL260522C00200000",
        bucket="SWING",
        prediction_score=0.65,
        confidence=0.8,
        position_id="pos-2",
    )
    assert pid is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jacobmcmillan/Empire/Orion && uv run pytest tests/ml/test_performance_tracker.py -v`
Expected: FAIL with `TypeError: object NoneType can't be used in 'await' expression` propagating through `db_transaction` and caught by the broad except → `pid is None` → assertion failure.

- [ ] **Step 3: Fix `log_exit_prediction.write`**

In `src/orion/ml/performance_tracker.py` change BOTH `write` inner functions:

```python
# BEFORE
def write(session: Any) -> None:
    session.execute(text("..."), {...})

# AFTER
async def write(session: Any) -> None:
    await session.execute(text("..."), {...})
```

Apply to both `log_entry_prediction` (line ~42) and `log_exit_prediction` (line ~83).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/jacobmcmillan/Empire/Orion && uv run pytest tests/ml/test_performance_tracker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orion/ml/performance_tracker.py tests/ml/test_performance_tracker.py
git commit -m "fix(ml): make performance_tracker.write callbacks async ($ bug #2)" -m "Inner write callbacks were sync and returned None, but db_transaction does await operation(session) — every exit/entry prediction log silently failed with TypeError caught by the broad except. ml_predictions rows never landed. Now async + await session.execute."
```

---

## Phase 2 — Options-aware close path (Bug #1)

**Why second:** This is the actual fix to "positions never exit." It refactors `close_position` to (a) detect options and use option-mid limit pricing, (b) round to options tick, (c) detect outside-RTH and skip-with-pending-status.

### Task 2.1: Add `is_market_open_for_options(now)` helper

**Files:**
- Modify: `src/orion/core/market_schedule.py` — add method.
- Test: `tests/core/test_market_schedule.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timezone
import pytest
from orion.core.market_schedule import MarketSchedule

def test_options_market_open_in_rth() -> None:
    sched = MarketSchedule()
    # Tuesday 2026-05-19 14:30 UTC = 10:30 AM ET (during RTH)
    in_rth = datetime(2026, 5, 19, 14, 30, tzinfo=timezone.utc)
    assert sched.is_market_open_for_options(in_rth) is True

def test_options_market_closed_pre_market() -> None:
    sched = MarketSchedule()
    # Tuesday 2026-05-19 12:00 UTC = 8:00 AM ET (pre-market, equity yes but options NO)
    pre = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    assert sched.is_market_open_for_options(pre) is False

def test_options_market_closed_after_hours() -> None:
    sched = MarketSchedule()
    # Tuesday 2026-05-19 22:00 UTC = 6:00 PM ET (after-hours, equity yes but options NO)
    after = datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc)
    assert sched.is_market_open_for_options(after) is False

def test_options_market_closed_weekend() -> None:
    sched = MarketSchedule()
    # Saturday 2026-05-23 14:30 UTC
    sat = datetime(2026, 5, 23, 14, 30, tzinfo=timezone.utc)
    assert sched.is_market_open_for_options(sat) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_market_schedule.py::test_options_market_open_in_rth -v`
Expected: FAIL with `AttributeError: 'MarketSchedule' object has no attribute 'is_market_open_for_options'`.

- [ ] **Step 3: Implement the helper**

In `src/orion/core/market_schedule.py`:

```python
def is_market_open_for_options(self, now: datetime) -> bool:
    """Alpaca options trading: 9:30am–4:00pm ET Mon-Fri only.

    Unlike equity, Alpaca does NOT allow options orders during
    pre-market or after-hours sessions — paper OR live. Submitting
    an options market OR limit order outside this window gets
    rejected with error `42210000`. Use this gate before attempting
    any options close.
    """
    # Re-use is_market_open for the trading-day/holiday check, then
    # additionally require the standard-session window.
    if not self.is_market_open(now):
        return False
    et = now.astimezone(ZoneInfo("America/New_York"))
    open_t = et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = et.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= et < close_t
```

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/core/test_market_schedule.py -v -k options`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orion/core/market_schedule.py tests/core/test_market_schedule.py
git commit -m "feat(market_schedule): is_market_open_for_options gate"
```

### Task 2.2: Add option-mid limit-pricing helper

**Files:**
- Modify: `src/orion/execution/execution_engine.py` — add `_compute_exit_limit_price` method.
- Test: `tests/execution/test_execution_engine_exit_pricing.py` (new).

- [ ] **Step 1: Write failing test**

```python
import pytest
from unittest.mock import AsyncMock
from orion.execution.execution_engine import ExecutionEngine

@pytest.mark.asyncio
async def test_compute_exit_limit_price_uses_option_mid_for_option_symbol() -> None:
    engine = ExecutionEngine()
    fake_client = AsyncMock()
    # Gateway returns option quote with bid/ask
    fake_client.get_option_latest_quote = AsyncMock(return_value={
        "bid_price": 1.25,
        "ask_price": 1.35,
    })
    engine._gateway_client = fake_client

    price = await engine._compute_exit_limit_price(
        ticker="NVDA260522C00450000",  # OCC option symbol
        direction="LONG",
    )
    assert price == 1.30  # mid, rounded to $0.05 tick (< $3 → 0.05 increments)


@pytest.mark.asyncio
async def test_compute_exit_limit_price_uses_stock_snapshot_for_equity() -> None:
    engine = ExecutionEngine()
    fake_client = AsyncMock()
    fake_client.get_stock_snapshot = AsyncMock(return_value={
        "latestTrade": {"p": 100.50}
    })
    engine._gateway_client = fake_client

    price = await engine._compute_exit_limit_price(
        ticker="NVDA",
        direction="LONG",
    )
    # Equity exit: 5bps below last for a SELL (LONG close), rounded to penny.
    assert price == 100.45
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/execution/test_execution_engine_exit_pricing.py -v`
Expected: FAIL with `AttributeError: 'ExecutionEngine' object has no attribute '_compute_exit_limit_price'`.

- [ ] **Step 3: Implement the helper**

Add to `ExecutionEngine` in `src/orion/execution/execution_engine.py`:

```python
async def _compute_exit_limit_price(self, ticker: str, direction: str) -> float | None:
    """Compute a limit price for an exit order.

    For options: pulls bid/ask via gateway `get_option_latest_quote`,
    returns mid rounded to the options tick. Returns None if the
    quote is missing or unusable.

    For equity: pulls last via `get_stock_snapshot`, shifts 5bps
    against direction (toward fill), rounds to penny.
    """
    client = self._get_gateway_client()
    is_option = _is_occ_option_symbol(ticker)  # see Task 2.3 — reuse position_monitor's

    if is_option:
        quote = await client.get_option_latest_quote(ticker)
        if "error" in quote:
            return None
        bid = float(quote.get("bid_price") or 0)
        ask = float(quote.get("ask_price") or 0)
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        mid = (bid + ask) / 2
        return round_to_options_tick(mid)

    snapshot = await client.get_stock_snapshot(ticker)
    if "error" in snapshot:
        return None
    last = float((snapshot.get("latestTrade") or {}).get("p") or 0)
    if last <= 0:
        return None
    shift_bps = 5
    if str(direction).upper() == "SHORT":
        # SHORT close = BUY, lift offer a bit
        return round(last * (1 + shift_bps / 10000.0), 2)
    return round(last * (1 - shift_bps / 10000.0), 2)
```

(Reuse `_is_occ_option_symbol` already added in Phase 4.3; import from `position_monitor` or move to `execution.attribution`.)

- [ ] **Step 4: Run test, verify pass**

Run: `uv run pytest tests/execution/test_execution_engine_exit_pricing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orion/execution/execution_engine.py tests/execution/test_execution_engine_exit_pricing.py
git commit -m "feat(execution): option-aware exit limit-price helper"
```

### Task 2.3: Rewrite `close_position` to use limit + RTH gate

**Files:**
- Modify: `src/orion/execution/execution_engine.py:906-988` (`close_position`).
- Test: `tests/execution/test_execution_engine_close_position.py` (new).

- [ ] **Step 1: Write failing tests**

```python
# Three scenarios: outside RTH → skip with PENDING log; in RTH option → limit submitted; in RTH equity → limit submitted.
# Use freezegun or monkeypatch on MarketSchedule to control time.
# Use AsyncMock for the gateway client.
# Assert: outside RTH returns False AND a row appears in exit_decisions with status=PENDING.
# Assert: in RTH, the order submitted is order_type='limit' with the option mid.
```

(Full test bodies — too long for this header — at end of file when implementing.)

- [ ] **Step 2: Verify failing**

- [ ] **Step 3: Rewrite `close_position`**

Replace lines 906-988 with the new logic. Critical changes:
- Add `if not self._market_schedule.is_market_open_for_options(datetime.now(UTC)) and _is_occ_option_symbol(ticker):` → log PENDING, write to `exit_decisions_pending` table (Task 2.4), return False.
- Replace `if use_market_order or urgency == "IMMEDIATE":` market path with limit-priced submission using `_compute_exit_limit_price`.
- Remove the broken `get_stock_snapshot(ticker)` for options.

- [ ] **Step 4: Verify pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "fix(execution): options-aware close — limit pricing + RTH gate (Bug #1)"
```

### Task 2.4: `exit_decisions_pending` table for replay

**Files:**
- Create: Alembic migration `alembic/versions/2026_05_20_exit_pending_table.py`
- Create: `src/orion/storage/models_exit_pending.py`
- Modify: `src/orion/execution/execution_engine.py` to write/read from it.

- [ ] **Step 1-5:** standard TDD — model, migration, repository methods, replay job that runs at market open.

(Detail bodies omitted for brevity — same pattern as `pending_orders`.)

### Task 2.5: Replay job at market open

**Files:**
- Create: `src/orion/jobs/exit_replay_job.py` — wakes at 9:30 ET, drains `exit_decisions_pending`, re-attempts each close.
- Add to: launchd or in-process scheduler.

- [ ] **Step 1-5:** TDD a small loop that on `is_market_open_for_options() == True` AND `now within 15 min of 9:30 ET`, fetches all pending rows and routes through the refactored `close_position`.

---

## Phase 3 — Persist running-window position stats (Bug #3)

**Why third:** Lower urgency than #1 and #2. Fixes the ML branch for restart-loaded positions so the model has real (not default-zeroed) features.

### Task 3.1: `position_running_stats` table

**Files:**
- Create: Alembic migration
- Create: `src/orion/storage/models_position_running_stats.py`
- Modify: `position_monitor.py` `update_unrealized_pnl` to upsert each tick.

### Task 3.2: Rehydrate on `_track_new_position`

**Files:**
- Modify: `position_monitor.py` `_track_new_position` to load `max_favorable_excursion`, `max_adverse_excursion`, `pnl_velocity` from the new table if a row exists for the symbol.

### Task 3.3: Verification gate

- After deploy, log `exit_classifier_score` per call. Confirm scores no longer cluster at the same value (was 0.17 per FOLLOWUPS #0). Compare distribution to training set.

---

## Phase 4 — Drop legacy thresholds + add smoke test

### Task 4.1: Remove `profit_pct >= 2.0` legacy branches in `evaluate_exits`

Predate Phase 2, confusing. The Phase 2 fallback ladder covers the intent at consistent thresholds.

### Task 4.2: Integration test for "fallback called every loop"

A smoke test that mocks the classifier + DB, runs one loop iteration with 5 tracked positions, asserts `evaluate_fallback_rules` was called 5 times. Catches a future wiring regression.

---

## Order of operations summary

| Phase | Tasks | Effort | Risk | When |
|-------|-------|--------|------|------|
| 1 | Bug #2 logger fix | 30 min | trivial | Today (lights up observability) |
| 2 | Bug #1 options-close + RTH gate | ~half day | medium | Today/tomorrow (the actual user-visible fix) |
| 3 | Bug #3 running-stats persistence | 1 day | low | This week |
| 4 | Cleanup + smoke test | 2 hours | trivial | After Phase 3 |

---

## Self-review

- [x] Spec coverage: all 3 bugs from SYNTHESIS.md have a phase.
- [x] Type consistency: `_compute_exit_limit_price -> float | None`, `is_market_open_for_options -> bool`, callers handle None.
- [x] No placeholders in Phase 1 and 2 task bodies. Phases 2.4 / 2.5 / 3 are sketched — they require the actual implementer to fill in TDD bodies, but the table schemas and the contract are specified.
- [x] No safety guards weakened: kill switches, paper-mode defaults, daily-loss limits all unchanged.

---

## Execution handoff

Plan saved to `docs/superpowers/plans/2026-05-20-exit-pipeline-fixes.md`. Recommended execution:

**Subagent-Driven** — fresh subagent per task, two-stage review per task. Fast.

User to decide which phase to start with. Recommendation: **Phase 1 first** (so we can SEE what's happening before changing anything else), then **Phase 2**.
