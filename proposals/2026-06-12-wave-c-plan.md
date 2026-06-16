# Wave C Implementation Plan — finish the actionable redesign (2026-06-12)

Scope chosen by owner: **everything actionable tonight** — RB.4 (native migration of
position_monitor + data_quality), RC.1 (single deployment story + dual-EOD fix),
RC.2 (Postgres image swap), RC.3 (Mapped[] phase 2 + strict-tier promotion), and the
**live deploy** that turns Wave B's measurement machinery on (migrations, restarts,
`ORION_FLOW_SOURCE=shadow`).

**Explicitly out of scope (data-gated, cannot be done tonight):**
- RC.4 flow cutover to push — gated on shadow-parity data that only starts accumulating
  after tonight's deploy.
- RD.1 solver demotions — gated on 14 clean reconciliation days with n≥20.
Their clocks START tonight; that is the point of the deploy step.

Process (unchanged from Wave B): this plan → gpt-5.5-xhigh adversarial review →
owner reviews findings → fix → implement → adversarial review of the diff → owner
vets findings before fixes → commit only on a clean round → push → CI green → deploy.

Timing: it is Thursday evening ET; market is closed; Friday is a trading day.
Everything through W6 must be done and verified tonight, or the deploy aborts to the
documented rollback and the trading stack restarts on current master (Wave B code is
already on master and is itself deploy-worthy; the only loss is Wave C features).

---

## Discovered facts the plan builds on (from tonight's discovery pass)

1. **Deployment split**: ingestion + execution + meta-search + meta-weekly are
   native-canonical (launchd, service leases, localhost:5440/8080). position-monitor,
   data-quality, eod-agent, feature_enrichment, pattern-miner, indexer, heber-sync,
   timescaledb run docker (default profile). position-monitor and data-quality have
   **no single-instance lease** today.
2. **Dual EOD is live**: Path A = native ingestion `_check_eod_trigger` at 01:05 UTC
   (≈20:05 ET) targeting `last_closed_trading_date()`; Path B = docker `eod-agent`
   (`orion.main_eod`) at close+30min (16:30 ET). No cross-path dedup; double-run is
   possible. Path B uniquely processes solver mutation proposals
   (`solver_mutation_processor.process_solver_mutations`); Path A uniquely posts the
   shadow flow-parity Discord summary.
3. **Broken one-shot watchers**: `com.empire.orion.launchd-health` (StartInterval 60)
   and `com.empire.orion.market-open-dataflow-check` last-exit=2;
   `com.empire.orion.deadman` is deliberately booted out (needs market-hours awareness
   before re-enable, per owner decision 2026-06-11).
4. **Mapped[] phase 2 is small**: only `models_ml.py` (3 classes), `models_silver.py`
   (1 class), `models_rag.py` (1 class, pgvector `Vector(768)`) remain legacy. Both
   parity tests are generic and reusable; strict-tier promotion is a pyproject list
   addition.
5. **Postgres swap is real but has one landmine the discovery agent missed**: live DB
   is PG 16.11, timescaledb 2.26 installed with **0 hypertables / 0 continuous
   aggregates / 0 application objects depending on it**, pgvector 0.8.1 active
   (`vector_documents.embedding_vec`), DB size 3.2 GB, 42 tables. **BUT**
   `postgresql.conf:747` sets `shared_preload_libraries = 'timescaledb'` — the
   pgvector image does not ship that library, so an unprepared in-place swap fails at
   startup. CI already runs the full e2e suite on `pgvector/pgvector:pg16`. Orion is
   the only consumer of port 5440.
6. **Wave B is on master, CI green, but NOT live**: migrations b1/b3/b4 are unapplied
   on the live DB and the native services run pre-Wave-B code. Shadow parity and
   reconciliation produce nothing until deploy.
7. position-monitor's compose fallback still names the revoked plaintext gateway key;
   must verify the running container env actually carries the rotated key from `.env`
   (and the native wrapper will).

---

## W1 — RC.3: Mapped[] phase 2 + strict-tier promotion (pure code, no runtime risk)

Convert `models_ml.py`, `models_silver.py`, `models_rag.py` to SQLAlchemy 2.0 typed
declarations, schema-identical.

- `models_silver.py` / `models_ml.py`: mechanical (JSON columns, Index in
  `__table_args__`, no FKs/server_defaults).
- `models_rag.py`: the pgvector column. Annotation decision: `embedding_vec:
  Mapped[Any] = mapped_column(Vector(768), nullable=True)` unless pgvector-python
  documents a better Mapped type — do NOT invent a fancier annotation; `Mapped[Any]`
  is honest (the Python-side value is a list/ndarray depending on driver) and keeps
  strict mypy clean without lying about types.
- Add the 5 new classes to `CONVERTED_MODELS` in `test_typed_declarative_parity.py`.
- `test_baseline_parity.py` needs no changes (generic over Base.metadata).
- Promote `orion.storage.models_ml`, `models_silver`, `models_rag` — plus the
  already-converted-but-unpromoted `models`, `models_attribution`, `models_audit`,
  `models_flow_parity`, `models_liveness`, `models_signals`, `models_solvers`,
  `models_trade_journal` — to the strict tier IF they pass strict mypy as-is;
  any module that doesn't pass cleanly gets fixed if trivial, otherwise left
  unpromoted with a one-line note (do not balloon scope chasing strictness in
  non-safety-critical modules).

Verify: `uv run pytest tests/storage/ -q`, `uv run mypy src/orion`, full suite.

## W2 — RB.4: native migration of position_monitor + data_quality

Code changes:
1. **Single-instance leases** (defense-in-depth, same pattern as ingestion/execution):
   add service-lease acquisition keyed by `ORION_LEASE_OWNER_ID` to
   `main_position_monitor` and `main_data_quality` startup. Docker copies get
   `..._compose` owner ids in compose env; native wrappers set `..._native`. This is
   what makes the cutover safe against a stray `docker compose up -d`.
   **Lease/parity interaction rule (plan-review round 1):** `--dry-run` and `--once`
   modes SKIP lease acquisition entirely — they are read-only (cannot submit closes),
   so the parity check can run while the docker copy still holds the lease, and a
   KeepAlive daemon can never relaunch-thrash against a held lease during the gate.
   The live daemon (no flags) acquires the lease and fails loudly on conflict.
2. **Native wrappers** `scripts/run_position_monitor_native.sh` and
   `scripts/run_data_quality_native.sh`, cloned from the run_ingestion_native.sh
   discipline: explicit PATH, `~/.local/bin/uv` (NEVER homebrew uv), source `.env`
   FIRST then pin `DB_URL` (localhost:5440), `GATEWAY_URL` **and** `DATA_GATEWAY_URL`
   (localhost:8080), exec venv python directly (not `uv run`) so SIGKILL reaches the
   process and can't orphan a lease-holder.
3. **launchd plists** `com.empire.orion.position-monitor` and
   `com.empire.orion.data-quality` (RunAtLoad + KeepAlive, logs to logs/).
   data_quality keeps its internal market-hours/hourly scheduling (`--scheduled`),
   so KeepAlive daemon is correct for both.
4. Add both labels to `REQUIRED_LABELS` in `launchd_health_probe`.
5. **Compose gating**: move position-monitor, data-quality, AND eod-agent (see W3)
   behind `--profile docker`, mirroring ingestion/execution. Compose's default
   profile shrinks toward: timescaledb + feature_enrichment + pattern-miner +
   indexer + heber-sync.

Functional-parity gate (executed during W6 deploy, NOT at code time — written here as
the checklist the deploy must follow): label loaded + clean exit status; lease owner
identity correct; DB_URL=localhost:5440; same gateway account identity; ORION_STAGE=paper;
**native position-monitor first run in `--dry-run --once` mode and its tracked-position
snapshot compared against the docker copy's last snapshot BEFORE the docker copy is
stopped** (never two live exit-executors at once — the dry-run flag exists for exactly
this); forced Discord test alert delivered; after docker-stop, `docker ps` shows no
orion position-monitor/data-quality and the native liveness rows keep advancing.

## W3 — RC.1: one canonical EOD path + watcher repair

**Decision proposed: canonical EOD = Path A (native ingestion trigger).** Rationale:
native-canonical direction; 20:05 ET fires after the fills/journal write-backs have
settled (16:30 ET is too early for reconciliation — same-day GTC exits and the EOD
session boundary make the later fire strictly safer); Path A already targets
`last_closed_trading_date()`; Path A already posts the shadow parity summary.

Code:
1. **Solver mutations become recommendations-only (owner decision, plan-review round 1).**
   The adversarial review verified `process_solver_mutations` →
   `MetaSearchAgent.refine_and_promote` AUTO-PROMOTES solvers (sets `stage='paper'`,
   `is_active=True`, creates paper solvers) — it is NOT recommendation-only, so it
   cannot be mechanically moved onto the canonical path. Instead: the canonical EOD
   path persists mutation proposals as `PromotionRecommendation` rows (table already
   exists) and applies ZERO stage changes — symmetric with RD.1's demotion gating
   ("measurement before evolution"). The auto-apply is retired with the eod-agent;
   promotions happen via the existing manual recommendation workflow until
   reconciliation has a clean track record. Today's Path A already silently DROPS
   proposals, so this strictly preserves more signal than the status quo on the
   surviving path.
2. Gate `eod-agent` behind `--profile docker` (retire from default profile; keep the
   service definition for manual runs).
3. **Idempotency guard at the agent level**: `run_review` records
   `(trading_date)` into `system_status`/`job_cursor_state` and refuses a duplicate
   run for the same date unless `force=True` — protects against ANY future second
   scheduler, not just the one we're removing.
4. **Watcher repair**: diagnose why launchd-health and market-open-dataflow-check
   last exited 2 (likely the env/uv-path class of failure; both predate the .env
   sourcing fix); fix their wrappers to the same discipline as W2 wrappers.
5. **Deadman re-enable**: add market-hours awareness — outside NYSE sessions
   (xcals), per-stage freshness checks are suppressed and only service-liveness
   budgets with explicit `LIVENESS_CADENCE_BUDGET_SECONDS`-style generous budgets
   alert; then `launchctl bootstrap` it back in during W6. The overnight false-alert
   gap (2026-06-11) was the reason it was booted.
6. Rewrite `docs/deployment-guide.md` around launchd-as-canonical (which services run
   where, how to restart each, the profile gating, the parity-gate checklist).

## W4 — RC.2: Postgres image swap (docker-compose + docs; execution happens in W6)

Choice: **in-place volume swap with a full dump as the rollback artifact** (not
dump/restore-into-new-volume as Wave A sketched — discovery showed both images are
PG16 on the same pgdata format and zero app objects depend on timescaledb, so
restore-into-new-volume buys nothing except a slower window; the dump still gives
disaster rollback, and image-revert gives instant rollback).

Pre-swap (on the OLD image, still running):
1. `pg_dump -Fc orion_db` to a dated file on the host (3.2 GB DB → expect a few
   minutes; verify non-zero size + `pg_restore --list` sanity).
2. `DROP EXTENSION timescaledb;` (verified: 0 hypertables, 0 dependents).
3. Clear the preload landmine: `ALTER SYSTEM SET shared_preload_libraries = '';`
   AND verify postgresql.conf:747 — the timescale tuner wrote it into
   postgresql.conf directly, and postgresql.auto.conf (ALTER SYSTEM) wins only if
   the conf line is later overridden; edit the conf line itself inside the volume
   (`docker exec ... sed`) so the new image cannot see `timescaledb` there at all.
4. Compose edit: `image: pgvector/pgvector:pg16`, keep service name `timescaledb`,
   container name `orion_timescaledb`, volume, port 5440, healthcheck — zero churn
   for every consumer.

Rollback story (three layers): (a) revert compose image line → old image still reads
the volume fine (with or without the dropped extension); (b) the pg_dump restores
into any PG16+pgvector; (c) nothing else in the stack changed identity.

Docs: fix stale "hypertables, time-partitioned" claims in system-architecture.md;
update CLAUDE.md/README/testing-guide command examples; drop the obsolete CI comment
block about timescale-vs-pgvector.

## W5 — Review + ship gate (code only, no deploy yet)

1. Full local gates: ruff, mypy (196+ files), full pytest, `alembic heads` single head.
2. gpt-5.5-xhigh adversarial review of the entire Wave C diff (same loop: findings →
   owner → fix → re-review until a clean round).
3. Commit in logical chunks (W1 / W2 / W3 / W4 separable), changelog entries, push,
   CI green. **No deploy before CI is green.**

## W6 — Operational deploy (tonight, market closed; me at the wheel, checkpointed)

Order matters; each step has a verification before the next. Reordered after
plan-review round 1 so the position-monitor parity gate completes BEFORE any native
close-executor daemon exists, and the docker baseline is captured BEFORE quiesce.

1. **Snapshot**: pg_dump rollback artifact (W4 pre-swap step 1).
2. **Docker position-monitor baseline** (while it is still running): capture its
   tracked-position snapshot (tickers/qty/avg_entry from its logs or a `--once
   --dry-run` docker exec) to a dated file. This is the parity reference.
3. **Quiesce**: `launchctl bootout` ingestion/execution/meta-search/meta-weekly;
   `docker compose down` (full stack — releases all service leases). Verify: no
   orion processes, no orion containers, no open orion close orders at the broker
   (`get_orders` check) — ABORT if a close is resting.
4. **DB swap** (W4 steps 2–4): drop extension → clear preload (conf line + ALTER
   SYSTEM) → compose image edit → `docker compose up timescaledb -d` → verify
   `select version()`, `select extname from pg_extension` (plpgsql + vector only),
   table count 42, spot-row queries on fills/trade_journal.
5. **Migrations**: `uv run alembic upgrade head` against localhost:5440 → verify
   `alembic current` = b4 head and the four new tables exist.
6. **Shadow flip**: set `ORION_FLOW_SOURCE=shadow` in `.env` (gitignored).
7. **Position-monitor parity gate — BEFORE its daemon exists**: run the native
   monitor MANUALLY as `--dry-run --once` (no lease, no daemon, cannot execute);
   compare its tracked-position snapshot against step 2's docker baseline. Same
   tickers/qty → gate passes. Mismatch → ABORT the RB.4 cutover (leave
   position-monitor docker-profile-runnable, continue the rest of the deploy).
8. **Restart natives onto new code**: bootstrap ingestion, execution, meta-search,
   meta-weekly, data-quality (new), and — only after step 7 passed — the
   position-monitor live daemon; plus repaired health-probe + market-open-check +
   deadman. Bring docker default profile up (feature_enrichment, pattern-miner,
   indexer, heber-sync — NOT position-monitor/data-quality/eod-agent, now
   profile-gated). Verify each lease acquired by the `_native` owner; ABORT a
   service if a competing lease is live after 2× TTL (240s).
9. **System verification**: e2e smoke test against live DB
   (`tests/e2e/test_smoke_e2e.py`), `test_live_data_flow.py` diagnostic (outside
   market hours it reports freshness without failing), service_liveness rows
   advancing for ALL natives, a flow_push_parity row appearing within ~2 cycles,
   forced Discord test alert, `launchctl list | grep orion` all clean exits.
10. **Morning guard**: the market-open dataflow check (now repaired) fires 09:40 ET
    and pages if bronze is stale — that is the after-the-fact safety net for anything
    tonight's checks can't see.

Abort criteria at any step: revert compose image / `launchctl bootout` new services /
restart old stack (Wave B code is NOT deployed in that case either — the stack returns
to exactly tonight's pre-deploy state; migrations are additive-only so an applied
b1/b3/b4 is safe to leave even on rollback).

---

## Task breakdown / parallelism

- W1, W2, W3 are independent code tasks → three parallel opus subagents.
- W4's compose/docs edits are tiny → folded into W3's agent or done by me.
- W5 review loop and W6 deploy are sequential, owner-checkpointed.

## Success criteria

- All four task groups merged on master, CI green, after a clean adversarial round.
- Tonight: live DB on pgvector image at b4 head; six native launchd services healthy
  with leases; zero orion docker services in the default profile except
  timescaledb/feature_enrichment/pattern-miner/indexer/heber-sync; exactly ONE EOD
  scheduling path; deadman watchdog re-enabled with market-hours awareness.
- Tomorrow's 20:05 ET EOD produces the FIRST persisted reconciliation row
  (RD.1 clock day 1) and shadow parity rows accumulate all session (RC.4 gate data).
