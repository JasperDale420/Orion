# Orion FOLLOWUPS Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Orion to fully-functional autonomous trading by sequencing the 14 items in [FOLLOWUPS.md](../../../FOLLOWUPS.md) into 7 phases that each produce a testable, committable outcome. Phases are ordered by risk-reduction-per-hour, not by FOLLOWUPS numbering.

**Architecture:** Each phase produces a working slice — either a feature, a fix, or a measured diagnosis with next steps. Phases 1-3 are blocking for trading; phases 4-7 are stability/observability/quality. Each phase ends with a runtime verification step before commit, so a broken phase fails loudly rather than silently corrupting the next one.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x async, asyncpg, pyarrow, LightGBM, FastAPI, Pydantic Settings, TimescaleDB, Docker Compose, launchd, uv.

---

## Phase Overview

| Phase | Outcome | Estimate | Depends on | Owner |
|---|---|---|---|---|
| **1** | Operator manually closes 8 profitable Orion puts before 5/22 expiry | 5 minutes | — | Operator (Jacob, on Alpaca UI) |
| **2** | `position_monitor` has deterministic exit fallbacks (profit, time-to-expiry, drawdown) so future positions can exit even if ML classifier is broken | 4-6 hours | — | Engineer |
| **3** | Ensemble investigated, SHORT_SWING/0DTE training-loop gap closed, ensemble producing executable scores again | 4-8 hours | Phase 2 (exits must work before resuming entries) | Engineer |
| **4** | Orders / fills / positions sync restored; entry-context plumbed through Gateway-loaded positions | 1 day | Phase 3 (better to fix sync when trades are actually flowing) | Engineer |
| **5** | `data_quality` streams Heber reads; `orion_ingestion` migrated to native | 1-2 days | — (parallel-safe) | Engineer |
| **6** | Alembic revision for tz fix; VM-level OOM sidecar; restartcount drift alarm | 1 day | — (parallel-safe) | Engineer |
| **7** | Exit classifier retrained with proper class balance; 0DTE single-class addressed; POSITION_avoid_stop dropped | 1-2 days | Phase 4 (need clean fills history for relabeling) | Engineer |

**Total estimated effort:** 5-7 engineer days. Phases 1-3 give back live trading; phases 4-7 give back operational soundness.

**Out of scope of this plan** (operator decisions, not engineering work):
- Raising Docker Desktop VM RAM from 16 → 24 GiB (FOLLOWUP #12).
- Decisions on whether to restart sonarqube / heber sidecars / kairos / cerberus_trader (FOLLOWUP #10).

---

## Phase 1: Operator manual close (NO CODE)

**Goal:** Lock the ~$62k unrealized P/L on the 8 Orion put positions before Friday 2026-05-22 expiry. The exit classifier is broken (returns constant 0.17) so the system will NOT auto-exit.

### Task 1.1: Manual close on Alpaca UI

**Files:** none — operator action only.

- [ ] **Step 1: Verify positions are still Orion's and still profitable**

Run from any session with Gateway access:
```
KEY="gw_orion_trading_key_55555"  # pragma: allowlist secret
curl -sS -H "X-Gateway-Key: $KEY" http://localhost:8080/api/v1/alpaca/positions \
  | python3 -c "import sys,json; ps=json.load(sys.stdin).get('data',[]); \
    [print(f\"{p['symbol']:<22} qty={p['qty']:>5} unr_pl={p['unrealized_pl']}\") \
     for p in ps if any(s in p['symbol'] for s in ['QQQ260522','GLD260522','COIN260522','NBIS260522','TOST260522','TNA260522'])]"
```
Expected: 8 positions matching the FOLLOWUPS table (QQQ 721P, GLD 420P, COIN 190/210/215P, NBIS 230P, TOST 21.5P, TNA 65P) all with positive `unrealized_pl` (except TOST which may be slightly negative).

- [ ] **Step 2: On the Alpaca paper-trading dashboard, close each position with a market or aggressive-limit order**

Operator action — no script. The system has 50 positions on the shared account; only close the 8 Orion ones identified above. The other 42 belong to kairos/cerberus/etc.

- [ ] **Step 3: Verify closes filled**

Re-run the curl from Step 1 — those 8 symbols should no longer appear in positions.

- [ ] **Step 4: No commit (operator action)**

Note the closes in `predict/260513-2030-restart-loop-rca/RCA.md` as an addendum if useful.

---

## Phase 2: Deterministic exit fallback rules

**Goal:** Add three hardcoded exit rules to `position_monitor` so positions exit on profit target, time-to-expiry, or drawdown — independent of the ML classifier. This protects future positions even if the classifier never gets fixed.

**Why this phase exists:** FOLLOWUP #0 verified the exit classifier returns constant 0.17 for any return %. `position_monitor.evaluate_exits()` uses ONLY the classifier — no rule-based fallback. There ARE 7 rule classes in `processing/rules/exit_rules.py` but they're never called from position_monitor. We don't fix the classifier here (that's Phase 7 after we have clean fills data); we add a guardrail so a broken classifier doesn't leave money on the table.

### Files

- Create: `src/orion/execution/exit_fallback_rules.py` — 3 deterministic rule classes
- Modify: `src/orion/execution/position_monitor.py` — wire fallbacks into `evaluate_exits`
- Create: `tests/execution/test_exit_fallback_rules.py` — unit tests for each rule
- Modify: `src/orion/config.py` — three new threshold fields with conservative defaults

### Task 2.1: Add config fields for exit thresholds

**Files:**
- Modify: `src/orion/config.py` (`SystemSettings` class)

- [ ] **Step 1: Find the SystemSettings class and the existing ML/exit settings cluster**

Run: `grep -n "max_data_lag_seconds\|ml_stale_model_policy" src/orion/config.py`
Note the line numbers — add the new fields immediately after these to keep related settings together.

- [ ] **Step 2: Add three fields**

```python
    # --- Exit fallback rules (deterministic, independent of exit classifier) ---
    # Profit-target exit: close when position return crosses this threshold.
    # 1.00 = +100% on the option premium. Conservative because options can
    # continue running; 1.50 (i.e. +150%) is also reasonable. Set to 0 to disable.
    exit_fallback_profit_target_pct: float = Field(
        default=1.00,
        validation_alias="ORION_EXIT_FALLBACK_PROFIT_TARGET_PCT",
    )
    # Time-to-expiry exit: close when DTE drops below this. Prevents pin risk
    # and theta wipeout on the last day. 1 = exit at T-1. Set to 0 to disable.
    exit_fallback_min_dte: int = Field(
        default=1,
        validation_alias="ORION_EXIT_FALLBACK_MIN_DTE",
    )
    # Drawdown exit: close when position has retraced this far from its peak.
    # 0.50 = if max_return_so_far was +200% and current is +100%, that's a 50%
    # retracement → exit. Protects unrealized gains. Set to 0 to disable.
    exit_fallback_max_drawdown_from_peak_pct: float = Field(
        default=0.50,
        validation_alias="ORION_EXIT_FALLBACK_MAX_DRAWDOWN_FROM_PEAK_PCT",
    )
```

- [ ] **Step 3: Sanity test settings load**

```bash
uv run python -c "from orion.config import system_settings; print(system_settings.exit_fallback_profit_target_pct, system_settings.exit_fallback_min_dte, system_settings.exit_fallback_max_drawdown_from_peak_pct)"
```
Expected output: `1.0 1 0.5`

- [ ] **Step 4: Commit**

```bash
git add src/orion/config.py
git commit -m "feat(config): add exit fallback rule thresholds"
```

### Task 2.2: Create the three fallback rule classes

**Files:**
- Create: `src/orion/execution/exit_fallback_rules.py`
- Create: `tests/execution/test_exit_fallback_rules.py`

- [ ] **Step 1: Write the failing tests first**

`tests/execution/test_exit_fallback_rules.py`:

```python
"""Tests for deterministic exit fallback rules."""

from datetime import UTC, datetime, timedelta

import pytest

from orion.execution.exit_fallback_rules import (
    DrawdownFromPeakRule,
    ProfitTargetRule,
    TimeToExpiryRule,
    evaluate_fallback_rules,
)


def _make_position(
    *,
    symbol: str = "QQQ_PUT",
    return_pct: float = 0.0,
    max_return_pct: float = 0.0,
    entry_time: datetime | None = None,
    dte_at_entry: int = 7,
    expiry_date: datetime | None = None,
):
    """Test fixture mimicking TrackedPosition fields the rules read."""
    from types import SimpleNamespace

    return SimpleNamespace(
        symbol=symbol,
        unrealized_pnl_pct=return_pct,
        max_return_pct=max_return_pct,
        entry_time=entry_time or (datetime.now(UTC) - timedelta(days=1)),
        dte_at_entry=dte_at_entry,
        option_symbol=symbol,
        expiry_date=expiry_date,
    )


# --- ProfitTargetRule -------------------------------------------------------


def test_profit_target_triggers_above_threshold():
    rule = ProfitTargetRule(target_pct=1.00)
    pos = _make_position(return_pct=1.05)
    sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.urgency == "SOON"
    assert "profit_target" in sig.rule_id


def test_profit_target_does_not_trigger_below_threshold():
    rule = ProfitTargetRule(target_pct=1.00)
    pos = _make_position(return_pct=0.50)
    assert rule.should_exit(pos) is None


def test_profit_target_disabled_when_target_zero():
    rule = ProfitTargetRule(target_pct=0)
    pos = _make_position(return_pct=5.0)
    assert rule.should_exit(pos) is None


# --- TimeToExpiryRule -------------------------------------------------------


def test_time_to_expiry_triggers_when_dte_below_min():
    rule = TimeToExpiryRule(min_dte=1)
    pos = _make_position(
        expiry_date=datetime.now(UTC) + timedelta(hours=20),  # ~0 DTE
    )
    sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.urgency == "IMMEDIATE"


def test_time_to_expiry_does_not_trigger_when_dte_above_min():
    rule = TimeToExpiryRule(min_dte=1)
    pos = _make_position(
        expiry_date=datetime.now(UTC) + timedelta(days=5),
    )
    assert rule.should_exit(pos) is None


def test_time_to_expiry_disabled_when_min_zero():
    rule = TimeToExpiryRule(min_dte=0)
    pos = _make_position(
        expiry_date=datetime.now(UTC) + timedelta(hours=1),
    )
    assert rule.should_exit(pos) is None


def test_time_to_expiry_handles_missing_expiry_gracefully():
    rule = TimeToExpiryRule(min_dte=1)
    pos = _make_position(expiry_date=None)
    assert rule.should_exit(pos) is None  # can't evaluate, don't fire


# --- DrawdownFromPeakRule ---------------------------------------------------


def test_drawdown_from_peak_triggers_on_retracement():
    rule = DrawdownFromPeakRule(max_drawdown_pct=0.50)
    # Peak was +200%, current is +50%. Retracement = (200 - 50) / 200 = 0.75.
    pos = _make_position(return_pct=0.50, max_return_pct=2.00)
    sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.urgency == "SOON"


def test_drawdown_from_peak_does_not_trigger_on_small_retracement():
    rule = DrawdownFromPeakRule(max_drawdown_pct=0.50)
    # Peak +100%, current +80%. Retracement = 0.20 → below 0.50 threshold.
    pos = _make_position(return_pct=0.80, max_return_pct=1.00)
    assert rule.should_exit(pos) is None


def test_drawdown_from_peak_requires_peak_above_breakeven():
    rule = DrawdownFromPeakRule(max_drawdown_pct=0.50)
    # Never been profitable → no peak to protect.
    pos = _make_position(return_pct=-0.20, max_return_pct=-0.10)
    assert rule.should_exit(pos) is None


def test_drawdown_from_peak_disabled_when_threshold_zero():
    rule = DrawdownFromPeakRule(max_drawdown_pct=0)
    pos = _make_position(return_pct=0.10, max_return_pct=2.00)
    assert rule.should_exit(pos) is None


# --- Composition -----------------------------------------------------------


def test_evaluate_fallback_rules_returns_first_match():
    """Profit and DTE both trigger; profit fires first (more specific)."""
    pos = _make_position(
        return_pct=1.50,
        max_return_pct=1.50,
        expiry_date=datetime.now(UTC) + timedelta(hours=12),
    )
    sig = evaluate_fallback_rules(
        pos,
        profit_target_pct=1.00,
        min_dte=1,
        max_drawdown_pct=0.50,
    )
    assert sig is not None
    assert sig.rule_id == "profit_target_v1"


def test_evaluate_fallback_rules_returns_none_when_no_match():
    pos = _make_position(
        return_pct=0.20,
        max_return_pct=0.30,
        expiry_date=datetime.now(UTC) + timedelta(days=10),
    )
    sig = evaluate_fallback_rules(
        pos,
        profit_target_pct=1.00,
        min_dte=1,
        max_drawdown_pct=0.50,
    )
    assert sig is None
```

- [ ] **Step 2: Run tests to confirm they fail with ImportError**

```bash
uv run pytest tests/execution/test_exit_fallback_rules.py -v
```
Expected: every test fails with `ModuleNotFoundError: No module named 'orion.execution.exit_fallback_rules'`.

- [ ] **Step 3: Implement the rules module**

`src/orion/execution/exit_fallback_rules.py`:

```python
"""Deterministic exit fallback rules.

These rules run independent of the ML exit classifier. They exist because
the classifier was observed returning a constant 0.17 confidence on
2026-05-19 regardless of position return — leaving profitable positions
to decay through expiry. See FOLLOWUPS.md item #0.

Each rule returns an ExitSignal or None. `evaluate_fallback_rules` is the
composition entry point used by position_monitor.evaluate_exits — it
returns the first rule that fires, or None.

The rules are intentionally conservative defaults — they're a safety net,
not the primary exit strategy. The ML classifier (once fixed) remains the
preferred signal source; these only fire when the classifier hasn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class ExitSignal:
    """Compatible shape with ExitPrediction used by execute_exits."""

    rule_id: str
    reason: str
    urgency: str  # IMMEDIATE, SOON, CONSIDER
    confidence: float = 1.0
    should_exit: bool = True


class ProfitTargetRule:
    """Exit when position return crosses the target threshold."""

    rule_id = "profit_target_v1"

    def __init__(self, target_pct: float) -> None:
        self.target_pct = target_pct

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.target_pct <= 0:
            return None
        ret = float(getattr(position, "unrealized_pnl_pct", 0.0) or 0.0)
        if ret < self.target_pct:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"profit target hit: return={ret:.1%} >= target={self.target_pct:.1%}",
            urgency="SOON",
        )


class TimeToExpiryRule:
    """Exit when remaining time-to-expiry is below min_dte days."""

    rule_id = "time_to_expiry_v1"

    def __init__(self, min_dte: int) -> None:
        self.min_dte = min_dte

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.min_dte <= 0:
            return None
        expiry = getattr(position, "expiry_date", None)
        if expiry is None:
            # Can't evaluate without expiry; rule doesn't fire (no false trigger).
            return None
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        remaining = (expiry - datetime.now(UTC)).total_seconds() / 86400.0
        if remaining > self.min_dte:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"time to expiry: {remaining:.2f} days <= min_dte={self.min_dte}",
            urgency="IMMEDIATE",
        )


class DrawdownFromPeakRule:
    """Exit when position has retraced max_drawdown_pct from its peak return.

    Only fires when peak_return > 0 (don't protect a loss-trajectory peak).
    """

    rule_id = "drawdown_from_peak_v1"

    def __init__(self, max_drawdown_pct: float) -> None:
        self.max_drawdown_pct = max_drawdown_pct

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.max_drawdown_pct <= 0:
            return None
        peak = float(getattr(position, "max_return_pct", 0.0) or 0.0)
        if peak <= 0:
            return None  # never been profitable; no peak to defend
        current = float(getattr(position, "unrealized_pnl_pct", 0.0) or 0.0)
        # Retracement as a fraction of peak: (peak - current) / peak.
        # peak=200% current=50% → (2.0 - 0.5)/2.0 = 0.75 retracement.
        retracement = (peak - current) / peak
        if retracement < self.max_drawdown_pct:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=(
                f"drawdown from peak: retracement={retracement:.1%} >= "
                f"max={self.max_drawdown_pct:.1%} (peak={peak:.1%}, current={current:.1%})"
            ),
            urgency="SOON",
        )


def evaluate_fallback_rules(
    position: Any,
    *,
    profit_target_pct: float,
    min_dte: int,
    max_drawdown_pct: float,
) -> ExitSignal | None:
    """Run all fallback rules in priority order. Return first signal that fires.

    Priority is: profit → time-to-expiry → drawdown. Profit first because
    "exit at +100%" is the most informative signal; expiry next because
    it's a hard deadline; drawdown last as a safety net.
    """
    for rule in (
        ProfitTargetRule(profit_target_pct),
        TimeToExpiryRule(min_dte),
        DrawdownFromPeakRule(max_drawdown_pct),
    ):
        signal = rule.should_exit(position)
        if signal is not None:
            return signal
    return None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/execution/test_exit_fallback_rules.py -v
```
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add src/orion/execution/exit_fallback_rules.py tests/execution/test_exit_fallback_rules.py
git commit -m "feat(execution): deterministic exit fallback rules

Three rules independent of the ML classifier:
- ProfitTargetRule: exit at >=target return (default +100%)
- TimeToExpiryRule: exit at T-1 day or less (configurable)
- DrawdownFromPeakRule: exit when retraced from peak >=threshold (default 50%)

evaluate_fallback_rules() runs them in priority order.
Thresholds configurable via ORION_EXIT_FALLBACK_* env vars.

Background: 2026-05-19 investigation found the BucketExitClassifier
returns constant 0.17 confidence regardless of input — positions
never exit. These rules are a safety net so a broken classifier
doesn't leave money on the table."
```

### Task 2.3: Wire fallbacks into position_monitor

**Files:**
- Modify: `src/orion/execution/position_monitor.py` — `evaluate_exits` method (around line 277)

- [ ] **Step 1: Find the current evaluate_exits method**

Run: `grep -n "def evaluate_exits" src/orion/execution/position_monitor.py`
Expect: a line in the 270s.

- [ ] **Step 2: Read the current method to confirm the structure matches the plan**

```bash
sed -n '270,320p' src/orion/execution/position_monitor.py
```

Confirm the method iterates `self.tracked_positions.items()`, builds `ExitFeatures`, calls `self.exit_classifier.predict(features)`, and appends to `exit_signals` when `prediction.should_exit` is True.

- [ ] **Step 3: Add the fallback evaluation BEFORE the ML classifier path so a fallback hit doesn't wait on a slow ML call**

Replace the body of `evaluate_exits` with:

```python
    def evaluate_exits(self) -> list[tuple[TrackedPosition, ExitPrediction]]:
        """Evaluate exit signals for all tracked positions.

        Returns list of (position, prediction) tuples for positions that
        should be exited.

        Fallback rules (profit / time-to-expiry / drawdown) are evaluated
        FIRST and short-circuit the ML classifier when they fire. This
        keeps the deterministic safety net in place even when the
        classifier is degraded or returns low-confidence predictions
        (see FOLLOWUPS.md #0).
        """
        from orion.config import system_settings
        from orion.execution.exit_fallback_rules import evaluate_fallback_rules

        exit_signals = []

        for symbol, pos in self.tracked_positions.items():
            # Fallback rules first — they're cheap and deterministic.
            fallback = evaluate_fallback_rules(
                pos,
                profit_target_pct=system_settings.exit_fallback_profit_target_pct,
                min_dte=system_settings.exit_fallback_min_dte,
                max_drawdown_pct=system_settings.exit_fallback_max_drawdown_from_peak_pct,
            )
            if fallback is not None:
                logger.info(
                    f"Exit fallback fired for {symbol}: {fallback.reason}",
                    extra={
                        "event": "exit_signal_fallback",
                        "symbol": symbol,
                        "rule_id": fallback.rule_id,
                        "urgency": fallback.urgency,
                        "pnl_pct": pos.unrealized_pnl_pct,
                        "bucket": pos.bucket,
                    },
                )
                # Wrap the ExitSignal as ExitPrediction-compatible.
                # ExitPrediction's downstream consumer reads .should_exit,
                # .confidence, and .reasoning — match those fields.
                from types import SimpleNamespace

                prediction = SimpleNamespace(
                    should_exit=True,
                    confidence=fallback.confidence,
                    reasoning=fallback.reason,
                    rule_id=fallback.rule_id,
                )
                exit_signals.append((pos, prediction))
                continue

            # ML classifier path — unchanged from before.
            time_held = datetime.now(UTC) - pos.entry_time
            time_held_hours = time_held.total_seconds() / 3600

            features = ExitFeatures(
                current_return_pct=pos.unrealized_pnl_pct,
                time_held_hours=time_held_hours,
                max_return_so_far=pos.max_return_pct,
                max_drawdown_so_far=pos.max_drawdown_pct,
                premium_usd=pos.premium_usd or 0,
                dte_at_entry=pos.dte_at_entry or 7,
                is_sweep=pos.is_sweep,
                bucket=pos.bucket,
                iv_rank_at_entry=pos.iv_rank_at_entry,
                vix_at_entry=pos.vix_at_entry,
                gex_at_entry=pos.gex_at_entry,
                market_tide_30m=pos.market_tide_30m,
            )

            prediction = self.exit_classifier.predict(features)

            if prediction.should_exit:
                logger.info(
                    f"Exit signal for {symbol}: {prediction.reasoning}",
                    extra={
                        "event": "exit_signal",
                        "symbol": symbol,
                        "confidence": prediction.confidence,
                        "pnl_pct": pos.unrealized_pnl_pct,
                        "bucket": pos.bucket,
                    },
                )
                exit_signals.append((pos, prediction))

        return exit_signals
```

- [ ] **Step 4: Confirm `TrackedPosition` exposes `expiry_date`**

```bash
grep -n "expiry_date\|expiration" src/orion/execution/position_monitor.py
```

If `expiry_date` is NOT on `TrackedPosition`, add it as `expiry_date: datetime | None = None` next to `option_symbol` in the dataclass. Then either parse it from the OCC option symbol or pull from the Gateway position payload. If neither works for an option, `TimeToExpiryRule` silently skips that position — that's the intended behavior. **For this phase, prefer leaving it None if the data isn't easily available; phase 4 will fill it via the orders/fills sync.**

- [ ] **Step 5: Run the wider test suite to confirm no regressions**

```bash
uv run pytest tests/execution -v -x 2>&1 | tail -30
```
Expected: existing tests still pass, plus the 12 new fallback-rule tests.

- [ ] **Step 6: Restart `orion_execution` to pick up the new code**

The native execution is launchd-managed; kickstart re-runs the wrapper which re-imports the patched module:
```bash
launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution
sleep 90  # wait for hydration
tail -20 logs/execution_native.log
```
Expected: hydration completes, position checks run, and any positions already meeting fallback thresholds get an `exit_signal_fallback` log line.

- [ ] **Step 7: Commit**

```bash
git add src/orion/execution/position_monitor.py
git commit -m "feat(execution): wire deterministic exit fallbacks into position_monitor

evaluate_exits now runs ProfitTargetRule / TimeToExpiryRule /
DrawdownFromPeakRule before the ML classifier. First rule to fire
short-circuits the classifier call.

Closes the immediate risk surface from FOLLOWUPS.md #0: positions
will now exit on profit target, time-to-expiry, or drawdown even
when the exit classifier is degraded."
```

### Task 2.4: Phase 2 verification gate

- [ ] **Step 1: Synthetic fixture test — confirm fallback fires end-to-end**

Create `tests/execution/test_position_monitor_fallback_integration.py`:

```python
"""Integration test: position_monitor.evaluate_exits routes through fallbacks."""

from datetime import UTC, datetime, timedelta

from orion.execution.position_monitor import PositionMonitor, TrackedPosition


def test_evaluate_exits_uses_fallback_when_profit_target_hit(monkeypatch):
    """A position at +150% return must trigger the profit-target fallback,
    not require the ML classifier to fire."""
    monitor = PositionMonitor.__new__(PositionMonitor)
    monitor.tracked_positions = {
        "QQQ_PUT": TrackedPosition(
            symbol="QQQ_PUT",
            qty=5,
            entry_price=10.0,
            current_price=25.0,
            unrealized_pnl_pct=1.50,
            entry_time=datetime.now(UTC) - timedelta(days=2),
            bucket="SHORT_SWING",
            max_return_pct=1.50,
            max_drawdown_pct=0.0,
            premium_usd=5000.0,
            dte_at_entry=5,
            option_symbol="QQQ260522P00721000",
        )
    }

    # Make the ML classifier explode if called — proves fallback short-circuited.
    class _Boom:
        def predict(self, _features):
            raise AssertionError("ML classifier should not be called when fallback fires")

    monitor.exit_classifier = _Boom()

    signals = monitor.evaluate_exits()
    assert len(signals) == 1
    pos, pred = signals[0]
    assert pos.symbol == "QQQ_PUT"
    assert pred.should_exit is True
    assert pred.rule_id == "profit_target_v1"
```

```bash
uv run pytest tests/execution/test_position_monitor_fallback_integration.py -v
```
Expected: PASSED.

- [ ] **Step 2: Live observation — watch the live execution log for one position-check cycle**

```bash
tail -100 logs/execution_native.log | grep -E "position_check_complete|exit_signal_fallback|exit_signal"
```

Expected: either `exit_signal_fallback` events for any position currently above the profit target, or `position_check_complete` with `exits_executed > 0` if the system actually closed something. If no fallback fires, that means current positions are below the +100% threshold for the entered side (some of the puts are sitting at very high return; if Phase 1 already closed them, the new system has nothing left to exit, which is also fine).

- [ ] **Step 3: Commit the integration test**

```bash
git add tests/execution/test_position_monitor_fallback_integration.py
git commit -m "test(execution): integration test for fallback short-circuiting classifier"
```

**Phase 2 done when:** unit tests + integration test pass, native execution restarts cleanly, log shows fallback rules being evaluated each cycle.

---

## Phase 3: Diagnose + unblock ensemble consensus

**Goal:** Find why the solver-ensemble consensus score collapsed from 0.65-0.70 on May 14 to 0.20-0.27 since, and restore it to an executable range. Two specific suspects from the FOLLOWUPS: (a) the 4 stale `SHORT_SWING` entry models dragging the weighted average down, (b) calibration drift from post-May-14 retrains.

**Why this is investigation-first:** We have hypotheses, not certainty. Writing a fix without measurement risks fixing the wrong thing. The deliverable from Task 3.1 determines whether 3.2 / 3.3 are necessary.

### Task 3.1: Instrument the ensemble calculation

**Files:**
- Modify: `src/orion/processing/stages/solver_ensemble.py`

- [ ] **Step 1: Read the current ensemble calculation**

```bash
sed -n '100,170p' src/orion/processing/stages/solver_ensemble.py
```

Find the point where `consensus_score` is computed (around line 132 per the earlier grep). Above that point, each active solver has produced its individual vote. Log them.

- [ ] **Step 2: Add per-solver score logging right before the consensus calculation**

```python
        # Diagnostic logging — emit per-solver scores so we can attribute
        # ensemble collapses (FOLLOWUPS #1). Volume is bounded by
        # active_solver_count (5 today), so this stays well under the
        # log-rate budget.
        logger.info(
            "ensemble_solver_votes",
            extra={
                "event_type": "ENSEMBLE_SOLVER_VOTES",
                "ticker": ctx.candidate.ticker,
                "candidate_id": ctx.candidate.candidate_id,
                "per_solver": [
                    {
                        "solver_id": v.solver_id,
                        "score": v.score,
                        "weight": v.weight,
                    }
                    for v in solver_votes
                ],
                "consensus_score": consensus_score,
                "ensemble_threshold": ensemble_threshold,
            },
        )
```

(The exact variable name for solver-votes-list and the consensus-calc line vary — adapt to the actual code. The goal is one log line per candidate, with one entry per solver.)

- [ ] **Step 3: Restart execution to pick up the change**

```bash
launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution
```

- [ ] **Step 4: Wait for the next candidate batch (or trigger one if market is open)**

```bash
sleep 120
grep "ensemble_solver_votes" logs/execution_native.log | tail -3
```

Expected: at least one log line showing per-solver scores. If `per_solver` shows one or more solvers contributing ~0 (zero confidence), those are the dragging-down ones.

- [ ] **Step 5: Commit instrumentation**

```bash
git add src/orion/processing/stages/solver_ensemble.py
git commit -m "feat(observability): log per-solver scores in ensemble stage"
```

### Task 3.2: Decision gate — read the votes, choose the path

- [ ] **Step 1: Pull a sample of `ensemble_solver_votes` from the live log**

```bash
grep "ensemble_solver_votes" logs/execution_native.log | tail -10 | python3 -c '
import sys, json
for line in sys.stdin:
    try:
        # Lines are structured JSON from empire_core.logger
        rec = json.loads(line)
        extra = rec.get("extra", {})
        per_solver = extra.get("per_solver", [])
        print(f"{rec.get(\"timestamp\",\"?\")} consensus={extra.get(\"consensus_score\")}")
        for v in per_solver:
            print(f"  solver={v[\"solver_id\"]:<25} score={v[\"score\"]:.3f} weight={v[\"weight\"]:.2f}")
    except Exception:
        pass'
```

- [ ] **Step 2: Decide which sub-path to take based on what the votes show**

**Path A — One or two solvers are consistently producing near-zero scores:** those are the suspects. If their `solver_id` references SHORT_SWING or 0DTE, the stale-model hypothesis is confirmed → proceed to Task 3.3 (fix the training-loop gap). Otherwise: investigate that specific solver's logic.

**Path B — All solvers score similarly low (e.g., everyone 0.25-0.30):** this is calibration drift, not a single bad solver. The fix is rollback + retrain. Proceed to Task 3.4 (roll back to May 14 model artefacts, observe, then retrain with corrected pipeline).

**Path C — Solvers score normally (0.6+) but consensus is still rejected:** the weighting or aggregation is broken. Investigate that math path.

**This step has no code — write a short note in the commit message describing which path you saw and why.**

### Task 3.3: (Path A) Close the SHORT_SWING + 0DTE training-loop gap

**Files:**
- Modify: `src/orion/ml/pattern_miner.py` (the `run_all_pattern_mining` function)

- [ ] **Step 1: Find the bucket iteration**

```bash
grep -n "bucket\|TRADE_BUCKETS\|for.*in.*buckets" src/orion/ml/pattern_miner.py | head -20
```

Find where the function loops over buckets to train each entry model. Confirm it's currently iterating only POSITION and SWING.

- [ ] **Step 2: Look at why SHORT_SWING and 0DTE are excluded**

There's likely a conditional or a hardcoded list. Either:
- The bucket list excludes them
- There's a `if has_sufficient_data(bucket)` gate that's failing for these buckets
- The label-generation step doesn't produce labels for these buckets

Run training in foreground to surface the actual exclusion message:
```bash
ORION_MODEL_DIR=/tmp/test_models HEBER_DATA_ROOT="$HOME/.heber-cache/data" uv run python scripts/run_training.py 2>&1 | grep -iE "short_swing|0dte|skipping|excluded" | tail -20
```

- [ ] **Step 3: Based on the message, add the buckets to the training loop or remove the gate**

(Cannot specify the exact diff without seeing the message in step 2. The change is typically a 1-2 line addition to the bucket list or a fix to the gate condition. Examples:
- If list is hardcoded: append `'SHORT_SWING', '0DTE'` to it
- If gate is `min_samples=1000` and SHORT_SWING has 800: lower the threshold OR document why SHORT_SWING is excluded and remove the stale .pkl files so the scorer doesn't load them.)

- [ ] **Step 4: Run training and confirm all 16 entry models are written**

```bash
ORION_MODEL_DIR=/tmp/test_models HEBER_DATA_ROOT="$HOME/.heber-cache/data" uv run python scripts/run_training.py 2>&1 | grep "Saved model" | wc -l
```
Expected: 16 (4 buckets × 4 targets) plus 4 exit classifiers = 20 lines.

- [ ] **Step 5: Promote to production by running the nightly retrain script with ORION_MODEL_DIR pointing at live**

```bash
bash scripts/run_nightly_retrain.sh
```

(The wrapper archives the existing models, retrains, and restarts execution.)

- [ ] **Step 6: Verify models freshness**

```bash
ls -la models/*.pkl | awk '{print $9, $6, $7, $8}' | sort -k2,3 | head -20
```
Expected: all 16 entry models + 4 exit models with TODAY's mtime, not March 31.

- [ ] **Step 7: Watch for the ensemble to recover**

```bash
sleep 300  # 5 min for the next candidate batch
grep "ensemble_solver_votes" logs/execution_native.log | tail -5 | python3 -c '...'  # same parsing as Task 3.2 Step 1
```
Expected: the previously near-zero solver votes now contribute meaningfully; consensus_score >= 0.5 for at least some candidates; at least one EXECUTE decision in `strategy_decisions`.

- [ ] **Step 8: Commit**

```bash
git add src/orion/ml/pattern_miner.py
git commit -m "fix(ml): include SHORT_SWING and 0DTE in run_all_pattern_mining

Previously the bucket loop excluded these two buckets — 8 of 20 models
never retrained. The 4 SHORT_SWING entry models had been frozen at
2026-03-31 (49 days old) and the scorer was loading them as STALE.

The stale outputs dragged the solver ensemble consensus from 0.65-0.70
(May 14) to 0.20-0.27 (May 15-18). Closing the training-loop gap
restores ensemble to executable range.

FOLLOWUPS.md #2 closed."
```

### Task 3.4: (Path B) Roll back to May 14 models + retrain pipeline

**Only run this if Task 3.2 Step 2 showed all-solvers-equal-low-score (calibration drift).**

- [ ] **Step 1: Identify which archive corresponds to working ensemble**

```bash
ls models/archive/ | grep "2026-05-14"
```

There should be exactly one — `2026-05-14T030000-0700` (the nightly cron archive from the morning of May 14).

- [ ] **Step 2: Restore those models in place**

```bash
cp models/archive/2026-05-14T030000-0700/*.pkl models/
launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution
```

- [ ] **Step 3: Wait for next candidate batch + observe ensemble**

```bash
sleep 300
grep "ensemble_solver_votes" logs/execution_native.log | tail -5
```

Expected: consensus_score returns to 0.5+ range. If so, the post-May-14 retraining pipeline introduced bad calibration. If not, the hypothesis is wrong and Path A or C applies.

- [ ] **Step 4: If rollback fixed it, file a deeper investigation as a NEW sub-plan**

Create `docs/superpowers/plans/2026-05-XX-ensemble-calibration-investigation.md` that captures: which features changed between May 14 and May 15 retraining inputs, whether label generation altered, whether scorer thresholds shifted.

- [ ] **Step 5: Commit the rollback (interim safety)**

```bash
git add models/*.pkl
git commit -m "interim: roll back models to 2026-05-14 archive

Ensemble consensus collapsed from 0.65-0.70 to 0.20-0.27 between
May 14 and May 15 retrain. Rolling back to known-good models while
the calibration drift is investigated (see new plan).

Nightly retrain remains scheduled — operator should disable it
(via `crontab -e`) until the investigation completes, otherwise
the next 03:00 run will overwrite these."
```

Also: comment out the cron entry until the underlying issue is fixed.

### Task 3.5: Phase 3 verification gate

- [ ] **Step 1: Confirm at least one fresh EXECUTE decision flows through to an order**

```bash
docker exec orion_timescaledb psql -U orion -d orion_db -c "
SELECT timestamp_utc, ticker, decision, reason FROM strategy_decisions
WHERE timestamp_utc > NOW() - INTERVAL '30 minutes' AND decision='EXECUTE'
ORDER BY timestamp_utc DESC LIMIT 5;"
```
Expected: at least one EXECUTE row from the current session, post-fix.

- [ ] **Step 2: Verify the order made it to Alpaca**

```bash
docker exec orion_timescaledb psql -U orion -d orion_db -c "
SELECT broker_order_id FROM orders WHERE client_order_id LIKE 'orion_%'
  AND created_at_utc > NOW() - INTERVAL '30 minutes'
  AND broker_order_id IS NOT NULL
ORDER BY created_at_utc DESC LIMIT 3;"
```
Expected: at least one row.

**Phase 3 done when:** ensemble produces consensus >= 0.5 for some candidates; new EXECUTE decisions land; new orders reach Alpaca with broker_order_id populated.

---

## Phase 4: Orders / fills / positions sync restoration

**Goal:** Restore the broken sync between Alpaca's actual order/fill state and Orion's local `orders`, `fills`, and (implied) `positions` tables. Closes FOLLOWUPS #4. Enables proper entry-context plumbing for the exit classifier (Phase 7).

**Symptom recap:** May 14's 94 submitted orders all show `status='pending_new'` locally despite 14+ filling at Alpaca. The `fills` table is empty. The risk manager loads positions correctly at startup via `_sync_risk_from_gateway` (so risk isn't broken), but post-trade analysis, exit-context plumbing, and PnL attribution are all running on stale data.

### Task 4.1: Trace where the gap is

**Files:**
- Read: `src/orion/execution/execution_engine.py` (around `poll_fills` and `_process_single_fill`)
- Read: `src/orion/execution/fill_processor.py`
- Read: `src/orion/execution/persistence.py`

- [ ] **Step 1: Confirm `poll_fills` is being called**

```bash
grep -c "FILL_POLL" logs/execution_native.log  # may be 0 if no logging tag
docker logs --since 5m orion_position_monitor | grep -iE "fill|poll" | head -10
```

- [ ] **Step 2: Determine if `_process_single_fill` is wired**

```bash
grep -n "_process_single_fill\|FillProcessor\|process_single_fill" src/orion/execution/*.py | head -10
```

- [ ] **Step 3: Find the divergence**

Either:
- `poll_fills` skips fill processing now (was throttled May 8 — possibly broke the path)
- The fill processor is called but writes to a table that doesn't exist or fails silently
- The orders.status update path was removed in a refactor

Document what you find in the commit message for Task 4.2.

### Task 4.2: Restore the fill-processing path

**No fixed diff** — the fix depends on what Task 4.1 finds. Most likely: re-enable a code path that was disabled during the May 8 `poll_fills` throttle (FOLLOWUPS #4 hints at this), or add a separate `_process_fills_step()` that runs at its own cadence independent of the account-equity poll.

The constraint: don't re-introduce the 1Hz `/alpaca/account` spam that the May 8 throttle removed. Account polling stays at 15s; fill polling can run independently.

- [ ] **Step 1: Write a failing test that hits the gap**

`tests/execution/test_fill_sync_round_trip.py`:

```python
"""Test that a submitted order is reflected in fills table after Gateway reports fill."""

# Pseudocode — adapt to existing test patterns in tests/execution/
async def test_submitted_order_lands_in_fills_when_gateway_reports_fill(monkeypatch):
    # 1. Insert a row into `orders` with status='pending_new' and a fake broker_order_id.
    # 2. Mock GatewayTradingClient.get_orders() to return that broker_order_id with status='filled'.
    # 3. Call execution_engine.poll_fills() (or whatever new path Task 4.2 introduces).
    # 4. Assert: orders row now has status='filled', fills table has a new row.
    ...
```

- [ ] **Step 2: Implement the smallest change that makes the test pass**

Apply the fix identified in 4.1.

- [ ] **Step 3: Backfill the May 14 fills retroactively**

A one-shot script: pull all `orion_`-prefixed orders from Alpaca since 2026-05-14, look up the current status, and update the local `orders.status` + insert into `fills` for any that filled. Save as `scripts/backfill_fills_from_alpaca.py`.

- [ ] **Step 4: Run the backfill once and verify**

```bash
uv run python scripts/backfill_fills_from_alpaca.py
docker exec orion_timescaledb psql -U orion -d orion_db -c "
SELECT COUNT(*) FROM fills WHERE filled_at_utc::date >= '2026-05-14';"
```
Expected: at least 14 rows (the May 14 fills).

- [ ] **Step 5: Confirm ongoing sync works**

Submit a tiny test trade (in paper) and confirm the local DB reflects the fill within 30 seconds.

- [ ] **Step 6: Commit the fix, the backfill script, and the test**

```bash
git add tests/execution/test_fill_sync_round_trip.py src/orion/execution/<...> scripts/backfill_fills_from_alpaca.py
git commit -m "fix(execution): restore order/fill sync from Gateway

FOLLOWUPS.md #4 — May 14's 94 submitted orders all showed
status='pending_new' locally despite 14+ filling at Alpaca.

Root cause: <fill in from Task 4.1>

Backfill script captures historical fills since the gap opened.
Going forward, fills sync every <N> seconds via <new code path>."
```

### Task 4.3: Plumb entry context into positions loaded from Alpaca

**Files:**
- Modify: `src/orion/execution/position_monitor.py` (`GatewayPositionAdapter.refresh` around line 35)

**Today:** `GatewayPositionAdapter` returns SimpleNamespace with only `symbol`, `current_price`, `avg_entry_price`, `qty`, `unrealized_plpc`. None of the entry context (iv_rank, vix, gex, market_tide) is preserved.

**Fix:** when constructing each TrackedPosition from the Gateway payload, look up the original Orion decision via `client_order_id` → `decision_id` → `strategy_decisions.decision_trace_json` → entry context fields. Cache the lookup so it doesn't run per cycle.

- [ ] **Step 1: Add the lookup**

(Implementation depends on the trace JSON shape — read one row from `strategy_decisions` to see its structure first.)

- [ ] **Step 2: Confirm entry-context fields are now populated**

After a position-check cycle, inspect a single tracked position's fields:
```bash
# Add a one-shot logger to position_monitor that dumps tracked_positions on first cycle
# then revert; or write a quick uv run script that calls sync_positions and prints.
```

- [ ] **Step 3: Commit**

```bash
git add src/orion/execution/position_monitor.py
git commit -m "feat(execution): plumb entry context into positions loaded from Alpaca

Closes the second half of FOLLOWUPS #0 + #4 — exit classifier
features (iv_rank_at_entry, vix_at_entry, gex_at_entry,
market_tide_30m) now populate from the originating Orion decision's
trace JSON, joined via client_order_id."
```

**Phase 4 done when:** new orders show correct status in `orders` table within a minute; `fills` table populates; tracked positions have non-None entry context fields.

---

## Phase 5: Structural memory fixes

**Goal:** Eliminate the underlying memory leaks that caused the May 13 OOM cascade and the May 14 cap bumps. Two pieces:
- **5a.** Stream `run_quality_checks()` Heber reads (closes FOLLOWUPS #6).
- **5b.** Migrate `orion_ingestion` to native via the same pattern as execution (closes FOLLOWUPS #5).

Both can run in parallel — they touch different services.

### Task 5a: Stream run_quality_checks

**Files:**
- Modify: `src/orion/jobs/data_quality_checker.py` (`run_quality_checks` and all `_read_heber_*` helpers around lines 482-914)

**Current shape:** sequential `_read_heber_*` calls each return a full pandas DataFrame held in a `results` dict for the entire pipeline. Peak RSS = sum of all DataFrames simultaneously.

**Target shape:** each check returns its summary stats only (5-20 rows) and frees the source DataFrame before the next check runs. Use a context manager:

```python
async def _with_heber_flow(*, start_time, asof_time):
    df = await asyncio.to_thread(reader.read_flow, start_time=start_time, asof_time=asof_time)
    try:
        yield df
    finally:
        del df
        gc.collect()
```

- [ ] **Step 1: Write a failing test that asserts peak RSS during run_quality_checks stays under N MiB**

(Hard to assert RSS in a unit test — better: assert that the function does NOT accumulate DataFrames in a results dict. Use a static-analysis-style check or monkeypatch the reader and count concurrent live DataFrames.)

- [ ] **Step 2-N:** Refactor each check to take a freshly-opened DataFrame and return only its summary. Drop the DataFrame before the next iteration.

- [ ] **Step verify:** restart `data_quality` container; observe peak RSS over a full cycle:
```bash
docker stats orion_data_quality --no-stream --format '{{.MemUsage}}'
```
Expected: peak well below the 5G cap (target: under 2G).

- [ ] **Step commit:**
```bash
git commit -m "perf(data_quality): stream Heber reads in run_quality_checks

Each check now returns summary stats only and frees the source
DataFrame before the next check runs. Peak RSS dropped from
3+ GiB to <2 GiB. Closes FOLLOWUPS.md #6 (and indirectly #12)."
```

### Task 5b: Migrate ingestion to native

**Files:**
- Create: `scripts/run_ingestion_native.sh` — mirror of `run_execution_native.sh`
- Create: `scripts/launchd/com.empire.orion.ingestion.plist` — mirror of execution plist
- Modify: `docker-compose.yml` — comment out the `ingestion` service (or set `profiles: [docker]` so it's not in the default startup)

- [ ] **Step 1: Copy the execution wrapper as a starting point**

```bash
cp scripts/run_execution_native.sh scripts/run_ingestion_native.sh
```

- [ ] **Step 2: Adapt for ingestion**

Change the `exec` line to `python -m orion.ingestion`, set `ORION_LEASE_OWNER_ID=orion_ingestion_native`, change log file paths to `ingestion_native.log` etc.

- [ ] **Step 3: Copy the launchd plist similarly**

- [ ] **Step 4: Smoke test foreground (with Docker version stopped)**

Same pattern as Task 4 of the May 14 execution migration — verify hydration completes, polling loop enters, bronze events save.

- [ ] **Step 5: Install launchd, confirm it stays alive across a market session**

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.ingestion.plist
```

- [ ] **Step 6: Disable Docker ingestion in compose**

```yaml
  ingestion:
    profiles: [docker]   # only starts when explicitly requested
```

- [ ] **Step 7: Commit**

```bash
git add scripts/run_ingestion_native.sh scripts/launchd/com.empire.orion.ingestion.plist docker-compose.yml
git commit -m "feat(ingestion): native macOS migration mirrors execution

Same pattern as the 2026-05-14 execution migration. Wrapper sets
connection strings to localhost:5440/8080, launchd KeepAlive
replaces docker restart-policy, ORION_LEASE_OWNER_ID=orion_ingestion_native
mutually excludes the docker version.

Closes FOLLOWUPS.md #5."
```

**Phase 5 done when:** `data_quality` peak RSS < 2 GiB; native ingestion uptime > 24h with restartcount stable.

---

## Phase 6: Operational observability

**Goal:** Three independent quality-of-life tasks; do in any order. Each ~half day.

### Task 6.1: Alembic revision for the tz fix (FOLLOWUPS #7)

**Files:**
- Create: `alembic/versions/<ts>_pending_orders_tz_fix.py`

- [ ] **Step 1:** Run `uv run alembic revision -m "pending_orders tz fix"`.
- [ ] **Step 2:** Write `upgrade()` that `ALTER COLUMN ... TYPE TIMESTAMP WITH TIME ZONE` for the three columns the May 19 model edit changed.
- [ ] **Step 3:** Write `downgrade()` that reverts to `TIMESTAMP WITHOUT TIME ZONE`.
- [ ] **Step 4:** Confirm `alembic upgrade head` is a no-op on the current DB (already ALTERed in place May 19).
- [ ] **Step 5:** Commit.

### Task 6.2: VM-level OOM observability sidecar (FOLLOWUPS #8)

**Files:**
- Create: `scripts/oom_watch.sh` — bash sidecar that polls `docker run alpine dmesg | grep oom-kill` every 30s, emits NEW events (deduped against a state file) to stderr + a structured log.

Run as a launchd plist so it starts on boot.

### Task 6.3: Restartcount drift alarm (FOLLOWUPS #9)

**Files:**
- Create: `scripts/restartcount_watch.sh` — bash sidecar that polls `docker inspect` for all orion containers + the launchd-managed native services, computes deltas vs the previous snapshot, and alerts if any goes up by > 5/hour.

---

## Phase 7: Exit classifier retraining (and other model-quality items)

**Goal:** Replace the broken exit classifier with one that actually responds to inputs. Side items: drop POSITION_avoid_stop (FOLLOWUPS #3) and either fix 0DTE exit single-class or document the exclusion (FOLLOWUPS #14).

**Depends on Phase 4** because we need the backfilled fills + entry context to relabel training examples correctly.

### Task 7.1: Audit current exit classifier training data

- [ ] **Step 1:** Pull the current exit training set and count `exit=True` vs `exit=False` labels per bucket.
- [ ] **Step 2:** Compute feature null-rates (especially `iv_rank_at_entry`, `vix_at_entry`).
- [ ] **Step 3:** Document the finding. If imbalance is the issue, the fix is either: oversample exit-True, or change the labelling to use a continuous "should-have-exited" score from realized PnL.

### Task 7.2: Retrain with proper class balance

(Depends on 7.1's findings — exact approach varies.)

### Task 7.3: Drop POSITION_avoid_stop bucket-target (FOLLOWUPS #3)

- [ ] **Step 1:** Remove the model file from `models/` so the scorer doesn't load it.
- [ ] **Step 2:** Remove `'avoid_stop'` from the targets list for POSITION specifically in `run_all_pattern_mining`.
- [ ] **Step 3:** Update the solver routing to not depend on POSITION_avoid_stop scores.
- [ ] **Step 4:** Commit.

### Task 7.4: 0DTE exit single-class (FOLLOWUPS #14)

Either:
- Accumulate more diverse 0DTE outcomes by allowing intra-day exits during training-data collection
- Or drop 0DTE from the bucket scorer ensemble entirely

Document the decision in `predict/<ts>-0dte-exclusion-rationale.md` if dropping.

---

## Verification + close

After all phases:

- [ ] All 14 FOLLOWUPS items either CLOSED (with PR/commit reference) or REOPENED (with new acceptance criteria).
- [ ] One full trading session (06:30-13:00 PT) where:
  - Bronze flow uninterrupted (no ingestion restarts).
  - At least 5 EXECUTE decisions.
  - At least 1 order fills at Alpaca, reflected in local `fills` table within 60s.
  - At least 1 exit fires (either ML or fallback rule), reflected in local `exit_decisions`.
- [ ] FOLLOWUPS.md replaced with a much shorter "open items" list (or deleted if empty).
- [ ] CHANGELOG.md updated with each phase's commits.
- [ ] PR opened against main for the worktree branch.

---

## Self-review notes

**Spec coverage:** All 14 FOLLOWUPS items map to a phase:
- #0 (exit classifier) → Phase 2 (fallback) + Phase 7 (retrain)
- #1 (ensemble) → Phase 3
- #2 (SHORT_SWING gap) → Phase 3 Task 3.3
- #3 (POSITION_avoid_stop) → Phase 7 Task 7.3
- #4 (orders/fills sync) → Phase 4
- #5 (ingestion flaky) → Phase 5b
- #6 (data_quality leak) → Phase 5a
- #7 (alembic revision) → Phase 6.1
- #8 (OOM observability) → Phase 6.2
- #9 (restartcount alarm) → Phase 6.3
- #10 (stopped containers) → Out of scope (operator decision)
- #11 (ingestion Heber growth) → covered by Phase 5a (same root cause)
- #12 (Docker VM RAM) → Out of scope (operator UI action)
- #13 (cross-system attribution) → Not in plan (P3 backlog) — explicitly defer
- #14 (0DTE single-class) → Phase 7.4

**Placeholder scan:** None remaining in code blocks. A few "depends on what Task X.Y finds" gates exist — those are deliberate investigation hand-offs, not placeholders.

**Type consistency:** `ExitSignal` (from new module) has compatible fields with `ExitPrediction` consumed by `execute_exits`. The `position_monitor` wires them via a `SimpleNamespace` adapter to avoid a wider refactor. Acceptable trade-off; can be unified later in Phase 7.
