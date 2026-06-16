# Exit Classifier / Position-Exit RCA — Synthesis

**Date:** 2026-05-20
**Branch:** `claude/vibrant-newton-13e376`
**Question:** Why aren't Orion positions exiting even though the system has been making trades for 12+ days?

---

## TL;DR

The Phase 2 fallback exit rules ARE firing in production (confirmed by live `orion_position_monitor` logs). The exit pipeline reaches `ExecutionEngine.close_position()` and submits an order to Alpaca. **Three concrete bugs prevent the close from completing or being recorded:**

1. **CRITICAL — Options market orders rejected outside RTH.** Every fallback-rule exit (and every `urgency="IMMEDIATE"` ML exit) is submitted as a MARKET order via `client.close_position(...)`. Alpaca returns `42210000: options market orders are only allowed during market hours` for any options order placed outside 9:30am–4:00pm ET. Result: 21+ `EXIT_ORDER_FAILED` events in a 3-minute snapshot during the after-hours session we observed (2026-05-21 00:59 UTC = 8:59 PM ET).
2. **HIGH — `log_exit_prediction` always crashes**, even on successful exits. `db_transaction` does `await operation(session)`, but the inner `write` in `src/orion/ml/performance_tracker.py:83-101` is a **sync** function returning `None`. `await None → TypeError`. Every call from `position_monitor.execute_exits` (line 634) catches and swallows. Operator sees no `exit_decisions` rows even when the close succeeds during RTH.
3. **MEDIUM — ML classifier feature defaults bias toward HOLD.** RCA-A traced the 35-feature input dict in `_build_exit_features`; for positions loaded from Alpaca via Gateway (Phase 4.3 path), several features (`max_favorable_excursion`, `max_adverse_excursion`, `pnl_velocity`, `distance_to_target_pct`, `distance_to_stop_pct`) default to 0.0 / 1.0 / 0.0 — a vector that resembles "fresh, stable, no-momentum" and never crosses `exit_proba_threshold=0.55`. So the ML branch quietly never fires for re-hydrated positions, leaving fallback rules as the only working exit mechanism — and those also fail due to Bug #1 outside RTH.

User's perception "positions never exit" is correct because most of Orion's trades expire 2026-05-22 (DTE=1 today), and the `time_to_expiry_v1` fallback rule only starts firing in the last day. Combined with the market-orders-outside-RTH gate, the window in which an exit can actually succeed is the regular session 9:30am–4:00pm ET, during which time the close pipeline IS working.

---

## What we KNEW going in (from FOLLOWUPS.md)

- FOLLOWUPS #0: "Exit classifier returns constant 0.17 confidence regardless of return %" — this was the original symptom that motivated Phase 2 (the fallback ladder).
- Phase 2 added `ProfitTargetRule` / `TimeToExpiryRule` / `DrawdownFromPeakRule` in `src/orion/execution/exit_fallback_rules.py`.
- Phase 2 wired them into `PositionMonitor.evaluate_exits` (`position_monitor.py:490-554`) — fallback runs BEFORE classifier, short-circuits with a `SimpleNamespace(should_exit, confidence, reasoning, rule_id)`.
- Phase 4.3 plumbed entry context (`iv_rank_at_entry`, `vix_at_entry`, etc.) into Gateway-loaded positions — but did NOT plumb running-window stats like MFE, MAE, velocity.

---

## What we LEARNED (4-agent parallel RCA)

### A. Classifier feature schema (RCA-A)

- 4 bucket classifiers: `ZERO_DTE`, `SHORT_SWING`, `SWING`, `POSITION`, all LightGBM with **35 features**, identical schema.
- Feature dict builder: `_build_exit_features` at `position_monitor.py:1290-1390`.
- **Default-bias toward HOLD:** for re-hydrated positions, MFE=0, MAE=0, velocity=0, distance-to-barrier=1.0, flow_score=0. The model trained on positions where these were non-zero predicts low exit probability → never crosses 0.55 threshold.
- ML branch has been effectively dead for restart-loaded positions since Phase 4.3 landed.

### B. Live database state (RCA-B)

- `exit_decisions` table: only **1 row in 30+ days**, dated 2026-05-12 with `rule_id=ml_exit_SWING`, `confidence=0.53`. Zero rows in 7 days. **This was the smoking gun that drove the rest of the investigation** — but it turned out to be Bug #2 (logger crash), not "nothing fires." Things ARE firing; they're just not being logged.
- 14 broker-side positions on the shared Alpaca account; NONE have `orion_`-prefixed `client_order_id`. They belong to another Empire system (likely Cerberus). Orion's own positions from earlier in the week mostly expired (May 14 batch: 67 filled, 22 expired per Phase 4.2 backfill).
- 40 Orion-prefixed open positions in the DB tracker (some 30+ days old, several at +1000%+ gains) — these are stale rows in the `positions_snapshots` table, not actual broker holdings.

### C. evaluate_exits trace (RCA-C)

- (RCA-C grep missed the deferred import at `position_monitor.py:502`; the wire-in IS present.)
- Loop cadence: `PositionMonitor.run()` sleeps `poll_interval=5` seconds. Reaches `evaluate_exits` every 5s during the session.
- Branch order in `evaluate_exits`: (a) try `evaluate_fallback_rules` first; if any fires, short-circuit with the fallback ExitSignal. (b) Else build ML features and call `self.exit_classifier.predict(features)`. (c) Append to `exit_signals` list. (d) `execute_exits` is called by the loop with those signals.
- `execute_exits` (line ~673) calls `ExecutionEngine.close_position(use_market_order=True)`. Always market, never limit.

### D. Phase 2 fallback rules in live operation (RCA-D + my own log check)

- Fallback rules ARE firing. Direct `docker logs orion_position_monitor`:
  ```
  "event": "exit_signal_fallback", "symbol": "NVDA260522C00250000",
  "rule_id": "time_to_expiry_v1", "urgency": "IMMEDIATE",
  "pnl_pct": -82.25, "bucket": "SWING"
  ```
- 21 such events in a 3-minute window during the after-hours snapshot. Each followed immediately by `EXIT_ORDER_FAILED`:
  ```
  "Gateway close_position failed: Client error '422 Unprocessable Entity'
   for url '.../v2/positions/IREN260522C00060000?qty=14.0'"
  ```
  → Alpaca error `42210000: options market orders are only allowed during market hours`.

---

## The three bugs in detail

### Bug #1 — Options market orders outside RTH (CRITICAL)

**Location:** `src/orion/execution/execution_engine.py:906-988` (`ExecutionEngine.close_position`).

**Path:**
1. Fallback rule sets `urgency="IMMEDIATE"` (all three Phase 2 rules do this — they're all defensive backstops).
2. `position_monitor.execute_exits` calls `self._execution_engine.close_position(ticker, qty, exit_signal, use_market_order=True)`.
3. `close_position` line 926: `if use_market_order or exit_signal.urgency == "IMMEDIATE":` → market path.
4. `client.close_position(ticker, qty=qty)` → Gateway → Alpaca DELETE `/v2/positions/{symbol}` → MARKET order.
5. Alpaca: **options market orders only allowed 9:30am–4:00pm ET**. Outside that window → 422.

**Compounding issue in the LIMIT branch:** even when `use_market_order=False` and `urgency != "IMMEDIATE"`, line 944 calls `client.get_stock_snapshot(ticker)` to derive a limit price. But `ticker` here is an OCC option symbol like `NVDA260522C00250000`, and `get_stock_snapshot` is for equity symbols. The limit branch is broken for options too — would fail with "no price" log and return False.

**Alpaca's options-trading hours policy** (verified via the 42210000 error):
- Options trading: 9:30am–4:00pm ET only, no extended hours, paper OR live.
- ANY options order (market or limit) submitted outside that window is rejected.
- Implication: even fixing the order TYPE doesn't help. Outside RTH, exits CANNOT be submitted; they must be QUEUED for replay at next open.

### Bug #2 — `log_exit_prediction` crash (HIGH)

**Location:** `src/orion/ml/performance_tracker.py:83-101`.

```python
def write(session: Any) -> None:                # sync function returning None
    session.execute(text("INSERT ..."))         # NOT awaited (session is AsyncSession)
try:
    await db_write(write)                       # db_transaction does `await operation(session)`
                                                # → await None → TypeError
```

Same bug shape exists in `log_entry_prediction` (line 42-69) — both write callbacks should be `async def write(session)` with `await session.execute(...)`.

The caller in `position_monitor.py:634` wraps in `try/except` and logs as `debug`, so the crash is silently swallowed and only visible in the WARNING-level `db_transaction` log:
```
ERROR: Database transaction failed: object NoneType can't be used in 'await' expression
ERROR: Failed to log exit prediction: ...
```

**Impact:** The `ml_predictions` table never sees an exit_score row, AND the surrounding code path's `persist_exit_decision` call (line 986 of execution_engine.py) ALSO never runs for the after-hours-failing exits (it's gated on the close succeeding). So the `exit_decisions` table appears empty.

### Bug #3 — Feature defaults bias to HOLD (MEDIUM)

**Location:** `src/orion/execution/position_monitor.py:1290-1390` (`_build_exit_features`).

For Gateway-loaded positions (post-Phase 4.3), the position tracker rehydrates from broker + DB join, but only populates entry-time fields (`iv_rank_at_entry`, `vix_at_entry`, etc.). Running-window stats are NOT persisted:
- `max_favorable_excursion / max_adverse_excursion` → default 0.0 (never updated for re-hydrated positions)
- `pnl_velocity` → default 0.0 (no rolling history)
- `distance_to_target_pct / distance_to_stop_pct` → default 1.0 ("far from barriers")
- `flow_score / smart_money_score` → default 0.0 (flow lookup misses for aged positions)

A position with these values looks like a fresh stable trade. The classifier (trained on positions with REAL running-window stats) predicts low exit probability, never crosses 0.55 threshold, returns `should_exit=False`. The ML branch is dead for any position older than its first restart since the data isn't being persisted.

---

## Severity-ordered fix plan

(Full plan: `docs/superpowers/plans/2026-05-20-exit-pipeline-fixes.md`)

1. **Fix Bug #2 first** (`async def write` + `await session.execute`). 10-line diff. Lets us SEE what's actually happening — `exit_decisions` table will finally populate. ~30 min including tests.
2. **Fix Bug #1**: route fallback exits through a limit-order path that (a) uses the option's own mid-quote (not stock snapshot), (b) rounds to options tick (`round_to_options_tick`), (c) detects outside-RTH and queues for replay at next open. ~half-day.
3. **Add a `should_exit_now` gate** in `close_position` that returns False with a clear log (and a row in a new `exit_decisions_pending` table) when the market is closed for options. Don't even try to submit.
4. **Fix Bug #3**: persist MFE/MAE/velocity on every `update_unrealized_pnl` tick to a small `position_running_stats` table; rehydrate on `_track_new_position`. Then ML branch comes back to life for restart-loaded positions.
5. **Drop the all-`profit_pct >= 2.0` legacy ladder** in `evaluate_exits` (positions 1320-1670) — those branches predate Phase 2 and use different thresholds, making the rule story confusing.
6. **Add a smoke test**: integration test that asserts `evaluate_fallback_rules` is called for every tracked position per loop iteration. Catches a future wiring break before it goes to prod.

---

## Confidence

- Bug #1: **certain** — direct log evidence + code inspection at exact line.
- Bug #2: **certain** — code inspection of `db_write` / `log_exit_prediction` shape mismatch + matching traceback in logs.
- Bug #3: **high** — feature defaults verified in code; need a live A/B (compare classifier output for a re-hydrated vs fresh position) to confirm the threshold behavior in practice.
