# Orion Redesign Implementation Plan — 2026-06-11

Implements redesigns R1 (flow off the lakehouse round-trip), R3 (unified liveness
contract), R4 (single deployment model), R5 (measurement-first solver shrink),
R6 (Mapped[] ORM migration), R7 (plain Postgres + baseline migration).
EXCLUDED by owner decision: R8 (off-machine watchdog) and the dedicated Alpaca
account (only 3 paper keys exist, all in use) — but the SDK must be ready to go
the moment a key frees up.

Ground rules (carried over from the audit remediation):
- Live paper-trading system. Hot-path changes ship behind flags with shadow
  modes; nothing cuts over without measured parity.
- Every wave ends with a gpt-5.5 xhigh adversarial review of the diff; confirmed
  findings fixed before commit/push. CI must stay green.
- Parallel subagents get disjoint file scopes; no commits by agents; CHANGELOG
  per wave; conventional commits in logical chunks.

## Current facts the plan is built on

- alpaca-py 0.43.2 is already a dependency and importable. "SDK readiness" =
  dormant config scaffolding + documented enablement path, not installation.
- Gateway WS protocol today: auth + `{"action": "subscribe", symbols: [...]}`
  for bars. Whether Gateway can push UW flow events over WS is UNKNOWN —
  discovery task A2 answers it before any R1 code is written.
- Docker still runs orion_position_monitor and orion_data_quality in the
  default profile alongside the native launchd stack; execution + ingestion are
  native-canonical (post split-brain incident). TimescaleDB runs in docker.
- Alembic chain is incremental-only; fresh DBs bootstrap via init_db create_all
  + stamp (CI does this today). Two heads were merged at e9ffae1b54c5.
- Zero TimescaleDB features used anywhere; real dependency is Postgres+pgvector
  (CI already runs pgvector/pgvector:pg16).
- Trade journal realized-PnL write-back exists since 2026-06-08 but has never
  been reconciled against broker truth; meta-search/meta-weekly went live
  2026-06-10 with Discord run alerts; solver promotions already require manual
  approval via promotion_recommendations.

## Wave A — foundations

ORDERING (adversarial-review finding): A3 (baseline squash) runs FIRST and
lands alone — A1 adds a new persisted table, and generating a baseline while
a parallel branch adds models is a migration/version-skew trap. A2 (read-only
discovery in another repo) and A4 (config-only scaffolding, no DB) are truly
independent and run in parallel with A3. A1 starts only after A3 is committed,
adding service_liveness as a normal post-baseline migration with an autogen
diff == empty check before any stamp.

### A1 (R3): Unified liveness contract + dead-man watchdog + canary
- New table `service_liveness` (service, last_success_ts_utc, cycle_count,
  last_error, updated_at) + tiny publisher helper in `shared/`.
- Wire publishers into the long-running loops: ingestion cycle, execution loop,
  position monitor, feature enrichment, meta-search, meta-weekly, EOD trigger.
  One UPDATE per cycle, swallow-never-crash semantics (like lease renewal).
- New `jobs/deadman_watchdog.py` + launchd plist (every 5 min): alerts Discord
  on ABSENCE — any registered service whose last_success_ts is older than its
  declared cadence budget. Replaces nothing yet; the four existing mechanisms
  (launchd probe, market-open check, heartbeats, leases) stay until this has a
  week of clean operation, then we retire overlapping checks in Wave C.
- Pipeline-depth assertion (REVISED per adversarial review — the original
  synthetic-canary design risked contaminating trading state: fake flow would
  have reached Silver/features/ML paths that sit outside any single rule-engine
  guard). NO synthetic events are ever injected into production tables.
  Instead the watchdog asserts per-stage freshness on REAL data during market
  hours: max(bronze.received_ts), max(silver.ts), max(gold_feature_events.ts),
  and candidates-seen-today, each against a per-stage staleness budget.
  This detects every stall class in the incident history (redis flap, gold
  poller, born-stale, WS death) with zero contamination risk. Outside market
  hours the stage checks are informational only (same convention as
  market_open_dataflow_check).
- service_liveness ships as a normal post-baseline alembic migration (A1
  depends on A3); `alembic autogen` diff must be empty after it lands.
- Files: storage/models (one new) + migration, shared/liveness.py, the loop
  call sites, jobs/deadman_watchdog.py, scripts/launchd/, tests.

### A2 (R1 discovery — READ-ONLY): Gateway flow-push design
- Read Data-Gateway source (../Data-Gateway): WS server protocol, how UW flow
  is ingested and published to Redis for Heber, whether a WS flow channel
  exists or what it would take to add one (additive, must not disturb other
  Gateway consumers: 3Roses, Cerberus, Kairos, Orbit, WhaleHunter, Heber).
- Deliverable: proposals/2026-06-11-flow-push-design.md with: exact Gateway
  changes (if any), Orion changes, event schema/id parity with the Heber poll
  path (dedup must collapse push+poll duplicates), shadow-mode comparison
  design, cutover criteria, rollback. No code changes.

### A3 (R7a): Alembic baseline squash
- Generate a true baseline migration from the current models (create_all
  parity), collapse the 35-migration incremental chain (archive old files),
  and stamp existing DBs. CI e2e bootstrap switches back to plain
  `alembic upgrade head`; init_db keeps create_all for tests but fresh real
  DBs become migration-driven.
- Risk: stamping the live local DB; verified non-destructive (stamp only).

### A4 (Alpaca SDK readiness — dormant): dedicated-account scaffolding
- Confirm alpaca-py import in CI (one test).
- Add dormant SystemSettings fields: orion_dedicated_alpaca_key/secret/paper
  (default None/unset) and a documented `ORION_BROKER_MODE=gateway|direct`
  flag that today only validates and logs; `direct` raises NotImplemented with
  a pointer to the enablement doc.
- docs/configuration-guide.md section: "Enabling a dedicated Alpaca account"
  — exact steps for the day a 4th key frees up (env vars, what attribution
  code becomes removable, kill-switch seeding simplification).
- Explicitly NO behavioral change while keys are unavailable.

### A.R — adversarial review gate, commit, push, CI green.

## Wave B — the meat (after A review)

### B1 (R1): Flow push implementation behind a flag (depends on A2)
- Implement per the A2 design: Gateway-side channel if needed (cross-repo
  commit in Data-Gateway, additive only), Orion GatewayStreamClient flow
  subscription, and `ORION_FLOW_SOURCE=poll|shadow|push` (default `shadow`
  after deploy, `poll` in code).
- Shadow mode: consume push events AND keep polling; log per-cycle parity
  (push_count, poll_count, missed_by_push, missed_by_poll, latency delta) to a
  table + daily Discord summary. The dedup layer collapses duplicates so
  shadow double-delivery cannot create double candidates (event-id parity is a
  hard A2 requirement).
- Cutover (C4) only after >= 3 market days with: push missing 0 events that
  poll caught (excluding born-stale), and median latency improvement measured.
- Heber poll path is retained permanently as the degrade/replay path (it
  already backs the WS-down degrade mode).

### B2 (R6 phase 1): Mapped[] migration — safety-critical models
- Convert models_execution, models_risk, models_gold (StrategyDecision,
  CandidateTrade), models_dlq to SQLAlchemy 2.0 `Mapped[]`/`mapped_column`.
  Schema-identical (verify with alembic autogen diff == empty against the
  baseline). Remove the now-unneeded type: ignore comments in execution/core;
  tighten the mypy tier for those modules.
- The win: the detached-instance bug class (DLQ incident) and the Column-vs-
  value Pyright noise become statically visible.

### B3 (R5): Measurement before evolution
- Journal-vs-broker reconciliation job: daily, compares journal realized PnL
  totals against Gateway account activity for orion_-attributed fills; result
  goes into the EOD Discord summary (match/mismatch + drift amount).
- Per-solver and per-rule realized-PnL attribution rollup (solver_id and
  rule_id already flow through candidates/decisions/journal) — table + section
  in the EOD report.
- RECOMMENDATIONS ONLY in this wave (REVISED per adversarial review: the
  plan itself says journal PnL has never been broker-reconciled, so demoting
  solvers on that data would act on untrusted inputs). The EOD report gains a
  "demotion candidates" section listing solvers without positive reconciled
  expectancy AND their sample sizes — no stage flags change in Wave B.
- Actual demotion is a separate, explicitly-gated task (D1): requires >= 14
  consecutive days of clean journal-vs-broker reconciliation AND a minimum
  per-solver sample size (>= 20 closed trades) before any stage flag moves.
  Meta-search keeps running (it has alerts now); promotions stay
  manual-approval. NO new evolution machinery until D1's gate is met.

### B4 (R4 phase 1): Finish the native migration for default-profile services
- Move orion_position_monitor (safety-critical, currently docker!) and
  orion_data_quality to launchd native (canonical pattern: ~/.local/bin/uv,
  PATH, run_*.sh, REQUIRED_LABELS where always-on). Stop docker copies; gate
  ALL Orion services in docker-compose behind `--profile docker` so a stray
  `docker compose up -d` cannot resurrect the split-brain (leases stay as
  defense-in-depth).
- Cutover gate is FUNCTIONAL PARITY, not a heartbeat (REVISED per
  adversarial review): before stopping each docker copy, verify on the native
  process — launchd label loaded + exit 0; lease owner identity; DB URL points
  at localhost:5440 and broker identity matches (same Gateway account id);
  ORION_STAGE=paper; the native monitor's tracked-position snapshot equals the
  docker copy's (same tickers/qty); a forced test alert reaches Discord; and
  after stopping docker, `docker ps` shows no orion_* copy and the native
  liveness row keeps advancing. Scripted as a checklist job, run per service.

### B.R — adversarial review gate, commit, push, CI green.

## Wave C — consolidation (after B review)

### C1 (R4 phase 2): Single deployment story
- Resolve the dual EOD scheduling divergence (docker eod-agent vs native
  ingestion trigger — known from the 2026-06-10 diagnosis): one canonical EOD
  path, the other deleted.
- docker-compose shrinks to: timescaledb (+ optionally tools). deployment-
  guide.md rewritten around launchd-as-canonical. Retire the overlapping
  bespoke watchdogs that the dead-man watchdog (A1) now covers, after its
  one-week soak.

### C2 (R7b): Postgres image swap — after-hours, with rollback
- Replace timescale/timescaledb:latest-pg16 with pgvector/pgvector:pg16 in
  docker-compose: pg_dump from old volume, restore into new volume, old volume
  retained untouched for rollback. Scheduled OUTSIDE market hours with the
  trading stack stopped; smoke e2e + live_data_flow as the post-swap check.
  This is the only task in the plan with planned downtime.

### C3 (R6 phase 2): Mapped[] for remaining models + mypy tier promotion
- Remaining storage/models_* files; promote orion.storage.* into the checked
  mypy tier; evaluate promoting execution/core from pragmatic to stricter.

### C4 (R1 cutover): ORION_FLOW_SOURCE=push
- Flip after B1's shadow criteria are met; poll demoted to degrade/replay.
  Keep shadow-parity logging for 1 more week post-cutover.

### C.R — final adversarial review, CHANGELOG, push, CI green, retro doc.

## Wave D — gated follow-through (no calendar date; gate-driven)

### D1 (R5 action): Solver demotions
- Unlocks only when: 14 consecutive clean reconciliation days AND per-solver
  sample >= 20 closed trades. Demotions are stage-flag changes from the
  broker-reconciled attribution table, listed in the EOD report for a week
  before applying. Reversible; no code deletion.

## Explicitly out of scope
- R8 (off-machine watchdog/backup) — owner deferred.
- Dedicated Alpaca account activation — blocked on key availability; A4 makes
  it a config flip + small PR when unblocked.
- ExecutionEngine split — still deliberately deferred.
- Deleting the meta/solver-evolution machinery — R5 shrinks usage, not code.

## Known risks the plan accepts
- B1 touches candidate generation on a live system → flag + shadow + parity
  gate; poll path never removed.
- Cross-repo Gateway change (if A2 requires one) affects 6 sibling consumers →
  additive-only, Gateway contract test extended.
- C2 downtime → after-hours, dump/restore, old volume retained.
- A3 squash rewrites migration history → old files archived, not deleted;
  existing DBs only ever `stamp`ed.
