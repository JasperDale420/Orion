# Predict Findings — Ranked by Priority

Sorted by composite `priority_score = severity*0.4 + confidence*0.2 + consensus*0.4`.

---

## Finding 1: Bracket SL/TP failures are silent — positions go un-protected with only a log line

**Severity:** HIGH
**Confidence:** HIGH
**Location:** [`execution_engine.py:600`](src/orion/execution/execution_engine.py:600), [`execution_engine.py:627-685`](src/orion/execution/execution_engine.py:627)
**Consensus:** 5/5 personas
**Priority Score:** 1.80
**Original IDs:** AR-4 ≡ RE-2 (merged); reinforced by DA-1 round 1

**Evidence:**
After successful entry, `_place_bracket_orders` wraps SL and TP submissions in independent `try/except` that only log. No metric, no SystemStatus update, no `_record_result(False)`. The decision is already marked TRUE on the entry order (line 584) before bracket placement, so the circuit breaker doesn't account for protection-level failures. A position can go open at the broker with no SL/TP and the only signal is a single ERROR log line.

**Recommendation:**
- Persist `position_unprotected: true` on the order record when both legs fail.
- Emit a Prometheus counter (`bracket_protection_failed_total`) for alerting.
- Optionally: emit a synthetic `_record_result(False)` so the circuit breaker accounts for protection-level outages.
- Optionally: position_monitor could shorten exit tolerance for unprotected positions.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Originally found as AR-4 |
| Security Analyst | confirm | Operational gap matters for incident response |
| Performance Engineer | confirm | Protection failure ≠ entry failure should not collapse to "success" |
| Reliability Engineer | confirm | Originally found as RE-2 |
| Devil's Advocate | confirm | Pushed for stronger remediation (close unprotected) |

---

## Finding 2: "Options-only is BUY" is a convention held across 4+ files — no type or runtime enforcement

**Severity:** HIGH
**Confidence:** HIGH
**Location:** [`execution_engine.py:419-422`](src/orion/execution/execution_engine.py:419), [`execution_engine.py:514`](src/orion/execution/execution_engine.py:514), [`execution_engine.py:451-455`](src/orion/execution/execution_engine.py:451), [`signal_preflight.py:109-114`](src/orion/execution/signal_preflight.py:109)
**Consensus:** 4/5 (PE abstain — outside performance domain)
**Priority Score:** 1.72
**Original ID:** DA-1

**Evidence:**
Four files independently hardcode `side = OrderSide.BUY` for the open path with comments asserting "Orion is options-only; SHORT means buy a put". The shorting guard at `execution_engine.py:457` checks `side == OrderSide.SELL and exposure <= 0` — when someone wires equity short-sale flow, they need to remember to: feed the right side at engine:457; remove hardcode at engine:514; update preflight at signal_preflight:114; update execute_options_order at engine:422. The recent CHANGELOG fix where this was wrong in 4 callsites simultaneously demonstrates the trap is real.

**Recommendation:**
Either:
- (a) Remove the equity-short comment+code paths entirely. Reject candidates without `option_symbol` earlier in the pipeline; never accept SELL on the open path.
- (b) Compute `side` once at decision time from `(direction, instrument_type)` in a single helper, thread it through. Both options eliminate the per-callsite reasoning.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | The lazy import + scattered comments confirm this is an architectural pattern |
| Security Analyst | confirm | Convention-only invariants are a class of bugs |
| Performance Engineer | abstain | Outside domain |
| Reliability Engineer | confirm | Defensive coding principle |
| Devil's Advocate | confirm | Originally found as DA-1 |

---

## Finding 3: Heber parquet reads have no time-filter pushdown — mitigation is the only line of defense against OOM

**Severity:** HIGH
**Confidence:** HIGH
**Location:** [`heber_reader.py:369-394`](src/orion/clients/heber_reader.py:369), [`heber_reader.py:459-483`](src/orion/clients/heber_reader.py:459), callers at [`heber_context.py:255-264`](src/orion/enrichment/heber_context.py:255), [`heber_context.py:336-345`](src/orion/enrichment/heber_context.py:336)
**Consensus:** 4/5 (SA abstain — security-tangential)
**Priority Score:** 1.72
**Original IDs:** AR-7 ≡ PE-4 (merged); supports DA-7

**Evidence:**
`_read_silver_dataset` calls `pq.read_table` with only `instrument_key` filters; time filtering happens AFTER load in `_apply_time_range_filter`. The 2026-04-22 OOM crash-loop RCA confirms this caused the production incident. The fix mitigated the symptom by gating callers (market-hours gate, ticker discovery moved to bronze) but the underlying read remains GB-scale-prone. If `prefer_heber_context_reads` is re-enabled, OOM recurs.

**Recommendation:**
Push `("dt", ">=", start_date)` and `("dt", "<=", end_date)` filters into `pq.read_table(filters=...)`. Hive partitioning means pyarrow can prune at file-list time — turning O(GB) reads into O(MB). Keep the market-hours gate as defense-in-depth.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Originally found as AR-7 |
| Security Analyst | abstain | Not directly security |
| Performance Engineer | confirm | Originally found as PE-4 |
| Reliability Engineer | confirm | Single-point-of-failure for memory safety |
| Devil's Advocate | confirm | Reinforces DA-7 (gate is single-point-of-failure) |

---

## Finding 4: Risk state lives in two parallel models (memory + RiskState DB) with subtle drift surfaces

**Severity:** HIGH
**Confidence:** HIGH
**Location:** [`risk/manager.py:47-66`](src/orion/execution/risk/manager.py:47), [`risk/manager.py:431-459`](src/orion/execution/risk/manager.py:431), [`risk/manager.py:466-490`](src/orion/execution/risk/manager.py:466)
**Consensus:** 4/5 (PE confirm via different angle)
**Priority Score:** 1.72
**Original ID:** AR-2

**Evidence:**
`pending_orders`, `processed_fill_ids`, `_partial_fill_tracker` are memory-only. `_save_state` writes loss/equity/positions but NOT pending_orders. Restart mid-cycle = pending exposure forgotten until next sync; risk checks immediately after restart calculate exposure from `positions` table only, ignoring in-flight orders. The `processed_fill_ids` rebuild from the DB happens implicitly via the `is_fill_processed` guard at `process_single_fill`, but `process_fill` itself only checks the in-memory set.

**Recommendation:**
- Persist `pending_orders` snapshot on every mutation (DB row or Redis hash), OR
- Block `check_order` for first ~30s after `initialize` until a Gateway sync completes (effectively flushes the gap).
- Add the DB-backed `is_fill_processed` check at the entry of `process_fill` itself (currently only at the upstream fill_processor layer).

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Originally found as AR-2 |
| Security Analyst | confirm | Reinforces SA-8 (idempotency race) |
| Performance Engineer | confirm | Restart-window has been observed under load |
| Reliability Engineer | confirm | Same architectural concern, different angle |
| Devil's Advocate | confirm | Underlies DA-4 (single-process invariant) |

---

## Finding 5: Single-process invariant is undocumented — multi-process deploy silently breaks 4 in-memory data structures

**Severity:** MEDIUM
**Confidence:** MEDIUM
**Location:** [`position_manager.py:70`](src/orion/execution/position_manager.py:70), [`risk/manager.py:50`](src/orion/execution/risk/manager.py:50), [`risk/manager.py:59`](src/orion/execution/risk/manager.py:59), [`fill_processor.py:29`](src/orion/execution/fill_processor.py:29)
**Consensus:** 4/5 (PE abstain)
**Priority Score:** 1.24
**Original ID:** DA-4

**Evidence:**
Four memory-only data structures coordinate cross-cycle behavior: `_closing_symbols`, `pending_orders`, `processed_fill_ids`, `_partial_fill_tracker`. Architecture implicitly assumes single-process. No comment, no startup assertion, no health check enforces it. A second process would: double-process fills (both check empty `processed_fill_ids` first), double-close positions (both pass `mark_closing` check), double-track pending orders.

**Recommendation:**
- Acquire a TimescaleDB advisory lock (`SELECT pg_try_advisory_lock(hashtext('orion_execution'))`) at engine startup; refuse to start if lock held.
- Document the invariant in CLAUDE.md and module docstrings.
- Long-term: back the in-memory structures with Redis or a dedicated DB table to enable horizontal scale-out.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Reinforces AR-2 (drift surfaces become drift bugs) |
| Security Analyst | confirm | Cross-process double-counting is a real attribution risk |
| Performance Engineer | abstain | Performance-neutral until activated |
| Reliability Engineer | confirm | Same concern as AR-5 (closing-guard per-process) |
| Devil's Advocate | confirm | Originally found as DA-4 |

---

## Finding 6: `peak_equity` resets to $100K on restart unless DB has a `RiskState` row — drawdown kill switch measures from wrong baseline

**Severity:** HIGH
**Confidence:** HIGH
**Location:** [`risk/manager.py:53`](src/orion/execution/risk/manager.py:53), [`execution_engine.py:195-196`](src/orion/execution/execution_engine.py:195), [`risk/manager.py:444-450`](src/orion/execution/risk/manager.py:444)
**Consensus:** 3/5 (PE/SA abstain — outside domain)
**Priority Score:** 1.64
**Original ID:** RE-3

**Evidence:**
- `RiskManager.__init__` sets `peak_equity = 100000.0` (hardcoded default).
- `_sync_risk_from_gateway` only re-seeds peak_equity from Gateway IF the current value still equals the magic default $100K (line 195).
- `RiskManager.initialize` loads from DB with `getattr(state, "peak_equity", 0.0) or max(...)`.

If the DB row is missing AND Gateway returns equity already <$100K (which it should during normal account drawdown — a $95K account is the case worth catching), `peak_equity` stays at $100K and the drawdown calc is broken from a fictional peak.

**Recommendation:**
Replace the magic-default detection with a `peak_equity_seeded: bool` flag. On first init, seed from `max(account.last_equity, account.equity)` unconditionally; on subsequent syncs, only update if `current_equity > peak_equity`.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | The "magic default" pattern is a code smell |
| Security Analyst | abstain | Outside domain |
| Performance Engineer | abstain | Outside domain |
| Reliability Engineer | confirm | Originally found as RE-3 |
| Devil's Advocate | confirm | Concrete correctness bug |

---

## Finding 7: `PositionMonitor.sync_positions` sets `entry_time = datetime.now(UTC)` for synced positions — biases ML exit features after every restart

**Severity:** HIGH
**Confidence:** HIGH
**Location:** [`position_monitor.py:155-178`](src/orion/execution/position_monitor.py:155)
**Consensus:** 3/5 (PE/SA abstain)
**Priority Score:** 1.64
**Original ID:** RE-5

**Evidence:**
When the position monitor sees a new position from broker, it sets `entry_time = datetime.now(UTC)  # Approximate`. The ML exit classifier feature `time_held_hours` derives from this. After every restart, every legacy position resets its time_held to ~0 — biasing exit predictions toward "hold longer" for positions that may have been open for hours.

**Recommendation:**
The async `_fetch_entry_context` already runs on new positions (line 156). Thread `decision.timestamp_utc` through and set `entry_time = entry_context.get("entry_time", datetime.now(UTC))`. Falls back to now() only when no decision row is found.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Same data already available — fix is small |
| Security Analyst | abstain | Outside domain |
| Performance Engineer | abstain | Outside domain |
| Reliability Engineer | confirm | Originally found as RE-5 |
| Devil's Advocate | confirm | "Approximate" comments are debt markers |

---

## Finding 8: 3% circuit breaker is hardcoded with no time component — single failure stays in deque all day during low volume

**Severity:** HIGH
**Confidence:** HIGH
**Location:** [`execution_engine.py:481-489`](src/orion/execution/execution_engine.py:481), [`execution_engine.py:49`](src/orion/execution/execution_engine.py:49), [`execution_engine.py:119-132`](src/orion/execution/execution_engine.py:119)
**Consensus:** 3/5 (SA/PE abstain)
**Priority Score:** 1.64
**Original ID:** DA-2 + DA-3

**Evidence:**
- Threshold `0.03` hardcoded; deque size 20.
- 1 failure in any 20 = 5% > 3% = breaker open.
- For low-volume periods (overnight, holidays), one 8 AM failure stays in the deque all day until 20 more orders push it out.
- Deque deliberately starts empty post-restart (anti-poison-pill) — but this means actual broker outages all day yesterday produce a clean breaker today.

**Recommendation:**
Replace deque with time-windowed counter: failures in last N minutes. Make threshold and window configurable via `RiskSettings`. Persist `broker_outcome_history` separately from `strategy_decisions` so the breaker has cross-restart memory of broker outcomes (not pre-flight rejections).

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Two-source-of-truth inversion is the right fix |
| Security Analyst | abstain | Outside domain |
| Performance Engineer | abstain | Outside primary perf concerns |
| Reliability Engineer | confirm | Strong-agree on broker-only history (RE round 1) |
| Devil's Advocate | confirm | Originally found as DA-2 + DA-3 |

---

## Finding 9: Static ticker fallback trades blindly — degraded discovery does not gate execution

**Severity:** HIGH
**Confidence:** MEDIUM
**Location:** [`heber_context.py:26`](src/orion/enrichment/heber_context.py:26), [`heber_context.py:174`](src/orion/enrichment/heber_context.py:174), [`main_feature_enrichment.py:323-329`](src/orion/main_feature_enrichment.py:323)
**Consensus:** 3/5 (AR/PE abstain)
**Priority Score:** 1.56
**Original IDs:** SA-7 + RE-6 (merged)

**Evidence:**
- `STATIC_TICKER_FALLBACK = [SPY, QQQ, TSLA, NVDA, AAPL, AMD, META, AMZN, GOOG, MSFT]`.
- If both bronze AND Heber discovery fail, feature_enrichment polls these tickers and the rest of the pipeline keeps emitting candidates.
- The `static_fallback` source name is the only alarm — warns after `_non_heber_warn_streak_threshold` (default 3 cycles ≈ 90s).
- Distinct issue: bronze returns empty (NOT exception) during post-market hours → falls through to static silently, but by design.

**Recommendation:**
- When source becomes `static_fallback`, set a `degraded_discovery: true` flag in SystemStatus.
- ExecutionEngine treats this flag as a hard block during the degradation window.
- Distinguish "bronze empty during market hours" from "bronze empty during post-market" — only the former should hit the Heber fallback.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | abstain | Operational concern, not architectural |
| Security Analyst | confirm | Originally found as SA-7 |
| Performance Engineer | abstain | Performance-neutral |
| Reliability Engineer | confirm | Originally found as RE-6 |
| Devil's Advocate | confirm | Reinforces DA-6 (no freshness assertion) |

---

## Finding 10: Rate limiter holds asyncio.Lock during sleep — pathologically serializes contention

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** [`rate_limiter.py:71-108`](src/orion/execution/rate_limiter.py:71)
**Consensus:** 3/5 (AR/SA abstain)
**Priority Score:** 1.32
**Original ID:** RE-4

**Evidence:**
`async with self._lock:` wraps the entire wait-loop including `await asyncio.sleep(...)`. If 5 callers contend at the limit, they queue on the lock and each waits for the FULL wait_time of the previous holder. Effective serial latency = N × per-slot wait, not max(individual wait_time). Under bursty signal periods (e.g., open of regular hours after a quiet pre-market), this multiplies queue latency by candidate-count.

**Recommendation:**
Standard token-bucket pattern: take the lock briefly to compute `wait_time`, release before `asyncio.sleep`, re-acquire to claim the slot. Or: switch to `asyncio.Semaphore` over a refilled bucket task. Expected wall-clock improvement: O(N→1) for N contenders.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | abstain | Implementation detail |
| Security Analyst | abstain | Outside domain |
| Performance Engineer | confirm | Round 1 escalation — "serial-thrashing bug" |
| Reliability Engineer | confirm | Originally found as RE-4 |
| Devil's Advocate | confirm | Defensive coding |

---

## Finding 11: ~~Drawdown kill switch opens CircuitBreaker, but `check_order` doesn't directly consult drawdown — 10s gap window~~ **DISPROVEN**

**Severity:** ~~MEDIUM~~ — N/A (DISPROVEN by code reading 2026-04-30)
**Confidence:** HIGH (originally) — empirical evidence overrides
**Location:** [`risk/manager.py:496-511`](src/orion/execution/risk/manager.py:496), [`execution_engine.py:803-815`](src/orion/execution/execution_engine.py:803)
**Consensus:** 3/5 (was) — empirical evidence rule applies

**Status: DISPROVEN.** `check_order` does directly consult drawdown:

```
check_order (risk/manager.py:130)
  → _check_loss_limits (risk/manager.py:146)
    → _drawdown_breached(cfg) (risk/manager.py:249)
      → reads self.peak_equity / self.current_equity from in-memory state
```

`process_fill` mutates `peak_equity` and `current_equity` in-memory before any subsequent `check_order` is called, so the same-process drawdown-breach gap RE-1 worried about does not exist. Regression coverage added in [`tests/execution/test_check_order_drawdown_direct.py`](tests/execution/test_check_order_drawdown_direct.py) to pin this behavior so future refactors can't silently re-introduce the imagined gap.

**Residual concern (lower-priority, NOT addressed by this finding):** the CB can be opened by 3 other code paths besides drawdown — [`health_monitor.py:65,97`](src/orion/core/health_monitor.py:65) and the operator API at [`api/main.py:799`](src/orion/api/main.py:799). For those, `check_order` does not consult CB state directly; only `_check_system_health` does, with a 10s cache. This is a smaller residual gap (10s lag for non-drawdown CB opens, only relevant when a sibling process or operator opens the CB while this process has a recently-cached health check).

**Persona Votes (post-empirical):**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm-original-but-mistaken | Code already does what was recommended |
| Security Analyst | abstain | Outside domain |
| Performance Engineer | abstain | Outside domain |
| Reliability Engineer | dispute | Original RE-1 phrasing was wrong; check_order does catch drawdown |
| Devil's Advocate | dispute | Empirical evidence overrides consensus |

---

## Finding 12: Five duplicated `orion_` prefix checks — adding a new code path means remembering all six callsites

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** [`execution_engine.py:25,143,227,503,705`](src/orion/execution/execution_engine.py:25), [`fill_processor.py:50`](src/orion/execution/fill_processor.py:50)
**Consensus:** 3/5 (PE/RE abstain)
**Priority Score:** 1.32
**Original ID:** AR-1

**Evidence:**
`ORDER_ID_PREFIX = "orion_"` is checked / written in six places. No single attribution helper. The recent CHANGELOG fix where this was wrong in `_sync_risk_from_gateway` (truthiness collapsing empty-set into accept-all) shows the duplication has already produced one production bug.

**Recommendation:**
Centralize attribution into `OrionAttribution` helper exposing `is_orion_owned(client_order_id)`, `mint_orion_order_id()`, `filter_to_orion(items, key)`. New paths default-deny. Optionally: DB foreign key from `fills.client_order_id` to `orders.client_order_id` so unrelated fills can't even be persisted.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Originally found as AR-1 |
| Security Analyst | confirm | Round 1 added FK-enforcement recommendation |
| Performance Engineer | abstain | Outside domain |
| Reliability Engineer | abstain | Outside domain |
| Devil's Advocate | confirm | Conceded fixing AR-1 reduces DA-4 risk |

---

## Finding 13: Race window: `_sync_risk_from_gateway` clears positions+exposures before re-populating

**Severity:** MEDIUM
**Confidence:** MEDIUM (DA challenged probability, not severity)
**Location:** [`execution_engine.py:218-220`](src/orion/execution/execution_engine.py:218)
**Consensus:** 2/5 (only AR/PE flagged; DA challenged, RE/SA outside)
**Priority Score:** 1.04
**Original ID:** PE-2

**Evidence:**
Lines 219-220: `self.risk_manager.positions = {}; self.risk_manager.ticker_exposures = {}` then loop populates. No lock; if `RiskManager.check_order` runs concurrently (`_calculate_projected_exposure`), it reads empty dicts and miscalculates. DA challenged the actual concurrency probability (sync only on init + poll_fills which doesn't repopulate positions today). Real but currently low-probability.

**Recommendation:**
Build new dicts locally and atomically swap via single assignment (Python guarantees atomic for single-name targets). Cheap, eliminates the future-proofing risk.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Defensive |
| Security Analyst | abstain | Outside domain |
| Performance Engineer | confirm | Originally found as PE-2 |
| Reliability Engineer | abstain | Probability low at present code |
| Devil's Advocate | dispute | Probability is low (one-shot init), severity overstated |

---

## Finding 14: Option chain payload has no schema validation — silent value coercion

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** [`execution_engine.py:330-356`](src/orion/execution/execution_engine.py:330)
**Consensus:** 2/5 (only SA/AR flagged)
**Priority Score:** 1.16
**Original ID:** SA-3

**Evidence:**
Chain contracts iterated as raw dicts. Field accesses use `.get()` with broad `except (TypeError, ValueError)`. A malformed Gateway response (e.g., `bid: "N/A"`) silently coerced to 0.0; the only safety net is the `option_price <= 0` fail-closed guard. The same logic that allowed the recent "wrong field name" bug (silent miss) could allow "different field shape" bugs.

**Recommendation:**
Validate the contract payload via a small Pydantic model (`OptionContractMid`) at the chain-iteration boundary. Coercion failures become structured errors instead of mid-pipeline 0.0s.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Schema-on-read is missing |
| Security Analyst | confirm | Originally found as SA-3 |
| Performance Engineer | abstain | Outside domain |
| Reliability Engineer | abstain | Less concerned given fail-closed semantics |
| Devil's Advocate | confirm | Reinforces DA-5 (silent pricing failures) |

---

## Finding 15: Legacy `sync_with_broker` path hardcodes $100K equity cap and overwrites daily-loss — directly contradicts Gateway-sync invariants

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** [`risk/manager.py:686-697`](src/orion/execution/risk/manager.py:686)
**Consensus:** 2/5 (AR/RE flagged)
**Priority Score:** 1.16
**Original ID:** AR-3

**Evidence:**
Legacy `sync_with_broker(connector)` uses `_ALLOCATED_EQUITY = 100_000.0`, `min(equity, _ALLOCATED_EQUITY)`, AND `current_daily_loss = max(0.0, last_equity - equity)` (line 697). The new `_sync_risk_from_gateway` uses real account equity unconditionally and explicitly does NOT overwrite daily-loss. If both paths run (e.g., test setup, restart races), risk state oscillates.

**Recommendation:**
Delete `sync_with_broker` if Gateway-sync is the canonical path. If kept, remove both the cap and the daily-loss overwrite — they directly re-create the bug fixed in 2026-04.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Originally found as AR-3 |
| Security Analyst | confirm | Reintroduces fixed kill-switch bug if invoked |
| Performance Engineer | abstain | Performance-neutral |
| Reliability Engineer | confirm | Round 1 strong-agree |
| Devil's Advocate | abstain | Out of scope unless invoked |

---

## Finding 16: `_partial_fill_tracker` has no eviction — long-lived process accumulates entries for never-completed orders

**Severity:** LOW
**Confidence:** HIGH
**Location:** [`fill_processor.py:29,71-77,103-105`](src/orion/execution/fill_processor.py:29)
**Consensus:** 2/5
**Priority Score:** 0.84
**Original ID:** PE-5

**Evidence:**
Entry created on partial fill, removed only on final fill. Order partial-then-canceled = entry forever. Memory leak rate is low but unbounded.

**Recommendation:**
Add TTL or periodic cleanup; index by `(order_id, fill_count)` and prune on order-status sync.

---

## Lower-priority findings (preserved for completeness)

| ID | Title | Severity | Consensus |
|----|-------|----------|-----------|
| SA-1 | None vs empty-set sentinel for ticker lookup | MEDIUM | 2/5 (security) |
| SA-2 | Hardcoded $100K equity is a coordination assumption | LOW | 1/5 (minority) |
| SA-4 | exchange_calendars import fail-open if missing | LOW | 1/5 (minority) |
| SA-5 | Heber catalog HTTP no auth header | LOW | 1/5 (minority) |
| SA-6 | `ledger.db` filename hardcoded in cwd | LOW | 1/5 (minority) |
| PE-1 | Health cache per-instance not module | LOW | 1/5 |
| PE-3 | UW connector startup spike (lower confidence) | MEDIUM | 1/5 |
| PE-6 | `_save_state` per-fill DB write amplification | LOW | 1/5 |
| PE-7 | `count(False)` O(n) | LOW | 1/5 (low-impact, preserved) |
| PE-8 | Correlation cache no eviction | LOW | 1/5 |
| AR-5 | PositionMonitor closing-guard per-process | MEDIUM | subsumed by Finding 5 |
| AR-6 | Lazy imports indicate leaky boundaries | LOW | 1/5 |
| AR-8 | Singleton lifecycle inconsistency | LOW | 1/5 |
| RE-7 | Health staleness cache 10s blackout window | MEDIUM | 1/5 |
| RE-8 | VIX 1d-change underflow numerical concern | LOW | 1/5 (minority but preserved) |
| DA-5 | Quiet pricing-failure outage tracking | MEDIUM | 1/5 |
| DA-6 | No Gold partition freshness assertion | HIGH | non-code/operational |
| DA-7 | Market-hours gate is single-point-of-failure | MEDIUM | subsumed by Finding 3 |
| DA-8 | Test suite assumes in-memory architecture | LOW | observational |

---

## Summary Statistics

- **Total unique findings:** 36 (after merges)
- **Confirmed (3+ personas):** 11
- **Probable (2 personas):** 4
- **Minority (1 persona):** 21 (all preserved)
- **Severity:** Critical: 0 | High: 9 | Medium: 12 | Low: 15
- **Anti-Herd Status:** ✓ PASSED (flip_rate 0.18, entropy 0.62)
