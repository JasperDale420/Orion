# Orion — Deployment Guide

How Orion is started, stopped, restarted, and monitored.

**launchd is canonical.** The trading-critical and scheduling-critical roles run
native via launchd; Docker Compose runs only the stateless support services in
its default profile, with the native roles' docker copies gated behind
`--profile docker` so a stray `docker compose up -d` can never start a second
instance.

| Role | Canonical runner | Notes |
|---|---|---|
| `ingestion` | **native** (launchd) | also fires the single canonical EOD review at 01:05 UTC |
| `execution` | **native** (launchd) | |
| `position-monitor` | **native** (launchd) | RB.4 — close-executor for the shared Alpaca account |
| `data-quality` | **native** (launchd) | RB.4 — `--scheduled` market-hours loop |
| `timescaledb` | docker (default profile) | Postgres 16 + pgvector |
| `feature_enrichment` | docker (default profile) | |
| `heber-sync` | docker (default profile) | host-cache rsync sidecar |
| `ingestion`/`execution`/`position-monitor`/`data-quality` docker copies | **profile-gated** (`docker`) | escalation/fallback only |

`pattern-miner`, `indexer`, `mcp-server`, and `eod-agent` no longer exist as
docker-compose services — they were removed along with the LLM
solver-evolution machinery (see `CHANGELOG.md`). `docker-compose.yml` now
defines exactly seven services and one profile (`docker`).

Native and docker copies of the same role are mutually exclusive: Orion's
service-lease table enforces it, and the lease owner-IDs differ (`*_native` vs
`*_compose`) so the second to start always loses. The docker copies live behind
`--profile docker` precisely so they cannot start by accident.

- **Why native:** bypasses the Docker Desktop 16 GiB VM ceiling that caused OOMs
  as Heber Gold grew.

> **Live-trading reminder:** Orion places real options orders. Verify
> `ALPACA_PAPER=true` and `ORION_STAGE=paper` (the defaults) before any
> restart on a production-like host. See
> [`code-standards.md`](code-standards.md#safety-critical-code).

## launchd agents (native)

All plists live in `scripts/launchd/` and install into
`~/Library/LaunchAgents/`.

| Plist | Wrapper | Role |
|---|---|---|
| `com.empire.orion.execution.plist` | `scripts/run_execution_native.sh` | `orion.main_execution` |
| `com.empire.orion.ingestion.plist` | `scripts/run_ingestion_native.sh` | `orion.ingestion` |
| `com.empire.orion.position-monitor.plist` | `scripts/run_position_monitor_native.sh` | `orion.main_position_monitor` — RB.4 close-executor (KeepAlive) |
| `com.empire.orion.data-quality.plist` | `scripts/run_data_quality_native.sh` | `orion.main_data_quality --scheduled` — RB.4 data-quality loop (KeepAlive) |
| `com.empire.orion.launchd-health.plist` | `scripts/run_launchd_health_probe.sh` | Once-per-minute audit of all `com.empire.orion.*` jobs |
| `com.empire.orion.market-open-dataflow-check.plist` | `scripts/run_market_open_dataflow_check.sh` | Bronze-freshness guard a few minutes after the cash open |
| `com.empire.orion.deadman.plist` | `scripts/run_deadman_watchdog.sh` | Every-5-min dead-man watchdog — service-liveness absence + pipeline-depth stage freshness (calendar-aware) |
| `com.empire.orion.orphan-close.plist.DISABLED-260526` | inline bash | **Disabled.** One-shot orphan-position closer; preserved as a reference (see [Orphan-close history](#orphan-close-history)) |

### Install / status / stop

```bash
# Install (idempotent)
mkdir -p /Users/jacobmcmillan/Empire/Orion/logs
cp scripts/launchd/com.empire.orion.execution.plist        ~/Library/LaunchAgents/
cp scripts/launchd/com.empire.orion.ingestion.plist        ~/Library/LaunchAgents/
cp scripts/launchd/com.empire.orion.launchd-health.plist   ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.execution.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.ingestion.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.launchd-health.plist

# Status (exit code, restart counter, last exit reason)
launchctl print gui/$(id -u)/com.empire.orion.execution
launchctl print gui/$(id -u)/com.empire.orion.ingestion
launchctl print gui/$(id -u)/com.empire.orion.launchd-health

# Restart safely (SIGKILLs python, KeepAlive restarts within ThrottleInterval=30s)
launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution
launchctl kickstart -k gui/$(id -u)/com.empire.orion.ingestion

# Stop / uninstall
launchctl bootout gui/$(id -u)/com.empire.orion.execution
launchctl bootout gui/$(id -u)/com.empire.orion.ingestion
launchctl bootout gui/$(id -u)/com.empire.orion.launchd-health
```

### Why the wrappers `exec` python directly

Both `run_execution_native.sh` and `run_ingestion_native.sh` end with:

```bash
exec "${PROJECT_ROOT}/.venv/bin/python" -m orion.<entrypoint> >> "${LOG_FILE}" 2>&1
```

**Never** change this to `uv run python …`. `uv run` spawns python as a child
of the uv wrapper process, so launchd ends up managing uv, not python. A
SIGKILL from `launchctl kickstart -k` kills uv but leaves python orphaned
(reparented to PID 1). The orphan keeps holding the service lease for up to
`SERVICE_LEASE_STALE_SECONDS=120` s, so the freshly-started instance blocks for
~2 min waiting for the lease to expire. Direct exec makes launchd manage python
itself, so the kill reaches python and no orphan survives.

If an orphan ever does linger:

```bash
launchctl bootout  gui/$(id -u)/com.empire.orion.execution
pkill -9 -f orion.main_execution
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.execution.plist
```

### Log files

```
logs/execution_native.log             # structured JSON (empire_core.logger)
logs/execution_native.stdout.log      # launchd-captured stdout
logs/execution_native.stderr.log      # launchd-captured stderr
logs/ingestion_native.log             # ditto for ingestion
logs/ingestion_native.stdout.log
logs/ingestion_native.stderr.log
logs/launchd_health.log               # alert rows, JSON one per line
logs/launchd_health.stdout.log
logs/launchd_health.stderr.log
logs/orphan_close.log                 # populated only when orphan plist fires
```

Tail the structured log first; only fall back to stdout/stderr when something
crashed before logger init.

## Retired meta jobs

`com.empire.orion.meta-search`, `com.empire.orion.meta-weekly`, `eod_agent`,
`meta_labeler`, and `price_target_labeler` are retired. Do not reload their
old plists. The launchd-health probe no longer requires them, and the dead-man
watchdog ignores stale liveness rows left behind by those archived jobs.

## Launchd health probe

`com.empire.orion.launchd-health` runs every 60 s and:

1. Shells out to `launchctl list`, filters to `com.empire.orion.*`.
2. Classifies each entry (healthy / stopped with non-zero exit / not loaded / suspicious).
3. Appends a JSON row to `logs/launchd_health.log` for anything not healthy.
4. POSTs to the Discord webhook (`DISCORD_WEBHOOK_URL`, sourced from `.env` by
   the wrapper), deduped per `(job, exit_code)` so a stuck job pages at most
   hourly rather than every minute.

Rows with a live PID are healthy even if launchd remembers an older non-zero
exit code. Exit-127 is escalated to CRITICAL because it cannot self-heal — a
human must edit the plist (typically a hardcoded binary path that doesn't exist
on the host). One-shot alert jobs such as `deadman` and
`market-open-dataflow-check` use exit 2 to mean "alert already handled by that
job"; launchd-health does not re-page those.

The probe exists because of the 2026-05-22 incident
([below](#orphan-close-history)) — a silent exit-127 loop went unnoticed for
4.5 hours.

## Dead-man watchdog (unified liveness)

`com.empire.orion.deadman` runs every 5 minutes (`StartInterval=300`, plus
`RunAtLoad` for an immediate first pass) and is the **unified absence guard**.
It performs two independent checks:

1. **Service liveness.** Each current long-running service
   (`ingestion`, `execution`, `position_monitor`, `feature_enrichment`,
   scheduled jobs such as `reconcile_pnl`) upserts a row into the
   `service_liveness` table at the end of every successful work cycle via
   `orion.shared.liveness.publish_liveness` — advancing `last_success_ts_utc`,
   incrementing `cycle_count`, and recording its own declared
   `cadence_budget_seconds`. The watchdog reads every row and fires a Discord
   alert (dedupe key `deadman_<service>`, 15-min window) when
   `now - last_success_ts_utc` exceeds that service's budget. **A service the
   watchdog has never seen is never alerted on** — registration happens on
   first publish, so absence it cannot attribute stays silent. Retired
   meta/labeler rows are ignored so archived jobs do not page forever.

2. **Pipeline-depth stage freshness (NYSE-session-gated, REAL data).** During a
   live NYSE regular session it asserts per-stage freshness on the actual
   pipeline tables — `max(bronze_events.received_ts_utc)` (budget 300s),
   `max(silver_signals.created_at_utc)` (600s),
   `max(gold_feature_events.created_at_utc)` (1200s) — and logs today's
   `candidate_trades` count (informational only, never an alert). This catches
   every stall class in the incident history (redis flap, gold-poller OOM,
   born-stale, WS death) with **zero contamination risk**: no synthetic events
   are ever injected. The session gate is **calendar-aware**
   (`exchange_calendars` XNYS via `is_nyse_session_open`), so market holidays
   and early closes suppress the stage checks — this is what fixed the overnight
   false-alert that got the watchdog booted out on 2026-06-11. Outside a live
   session the stage checks are informational only; service-liveness checks
   (#1) still run.

The job runs in a **separate process with its own lightweight async engine**, so
it still reads liveness/pipeline state even when the main stack is wedged. It is
a periodic **one-shot** (no `KeepAlive`): exit 2 means an alert was dispatched,
and the next 5-minute fire is sufficient — a non-zero exit must not restart-loop.

> **Not in `REQUIRED_LABELS`.** Per the launchd-health probe's documented rule,
> only always-on `RunAtLoad`+`KeepAlive` daemons belong in `REQUIRED_LABELS`
> (a missing row = a daemon that should be running but isn't). The dead-man
> watchdog is a `StartInterval` one-shot whose idle/absent state between fires is
> normal, so requiring it would false-alarm — exactly like `orphan-close`. It is
> therefore intentionally excluded.

```bash
mkdir -p /Users/jacobmcmillan/Empire/Orion/logs
cp scripts/launchd/com.empire.orion.deadman.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.deadman.plist

# Verify (runs >= 1, last exit code 0 — NOT 127)
launchctl print gui/$(id -u)/com.empire.orion.deadman | grep -E "runs =|last exit code"

# Run once manually
scripts/run_deadman_watchdog.sh
```

## Docker Compose

```bash
# Database only (Postgres 16 + pgvector)
docker compose up timescaledb -d

# Default profile — stateless support services only:
# timescaledb, feature_enrichment, heber-sync.
# The trading roles (ingestion, execution, position-monitor, data-quality) run
# NATIVE via launchd and are NOT here.
docker compose up -d

# Profile-gated docker copies of the native roles.
# Use only for escalation/fallback when the native runner is down; the service
# leases prevent co-execution with the native instance.
docker compose --profile docker up -d

# Logs
docker compose logs -f execution
docker compose logs --tail=100 feature_enrichment
docker compose logs --no-color 2>&1 | rg -i "error|exception|traceback"

# Restart one service
docker compose restart execution

# Stop everything
docker compose down
```

If running execution natively, **do not** also bring up the docker `execution`
service. The native wrapper sets `ORION_LEASE_OWNER_ID=orion_execution_native`;
the compose stanza sets `orion_execution_compose`. The second one to start
exits non-zero with a lease-conflict error. The same applies to `ingestion`,
`position-monitor`, and `data-quality`; their docker copies are profile-gated
(`--profile docker`) so they cannot start without an explicit opt-in.

## RB.4 native-migration parity checklist

Before stopping a docker copy and cutting a role (`position-monitor`,
`data-quality`) over to its native launchd runner, verify parity. **Never run
two live close-executors at once** — the `--dry-run --once` flags exist exactly
so the parity check can run read-only while the docker copy still holds the
lease:

1. **Capture the docker baseline first** (while it is still running): the docker
   copy's tracked-position snapshot (tickers / qty / avg_entry) to a dated file.
2. Plist loaded and last exit status clean; label present in `launchctl list`.
3. Lease owner identity correct (`*_native`, not `*_compose`).
4. `DB_URL` points at `localhost:5440`; same gateway account identity;
   `ORION_STAGE=paper`.
5. **Run the native runner once as `--dry-run --once`** (no lease, no daemon,
   cannot submit closes) and compare its tracked-position snapshot against the
   docker baseline from step 1. Same tickers/qty → parity holds.
6. A forced Discord test alert is delivered.
7. Only after parity holds: stop the docker copy, bootstrap the native live
   daemon, and confirm `docker ps` shows no orion copy and the native
   `service_liveness` rows keep advancing.

Mismatch at step 5 → abort the cutover: leave the role docker-profile-runnable
and investigate before retrying.

## Database migrations

```bash
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe-change"
uv run alembic downgrade -1                # single step back
```

Always read and edit `--autogenerate` output before committing. Migrations
must be reviewable in isolation.

As of the 2026-06-11 baseline squash, the chain starts from a single baseline
revision (`baseline_2026_06_11`) that creates the full schema (including
`CREATE EXTENSION vector`), so a fresh empty database is provisioned entirely
by `alembic upgrade head`. The pre-baseline incremental migrations are kept for
reference under `archive/2026-06-11_alembic-pre-baseline/` and must not be
moved back into `alembic/versions/`.

## Health checks

```bash
# Admin API (port 8000)
curl -s http://localhost:8000/health

# Heber catalog
curl -s http://localhost:8085/api/v1/health

# Data-Gateway (port 8080)
curl -s http://localhost:8080/health

# TimescaleDB (port 5440)
psql "postgresql://orion:orion_password@localhost:5440/orion_db" -c "select now();"
```

For end-to-end pipeline freshness use `tests/e2e/test_live_data_flow.py` (see
[`testing-guide.md`](testing-guide.md)).

## Operator scripts (`scripts/`)

| Script | Purpose |
|---|---|
| `run_execution_native.sh` | Wrapper for `com.empire.orion.execution` |
| `run_ingestion_native.sh` | Wrapper for `com.empire.orion.ingestion` |
| `run_position_monitor_native.sh` | Wrapper for `com.empire.orion.position-monitor` (RB.4) |
| `run_data_quality_native.sh` | Wrapper for `com.empire.orion.data-quality` (RB.4) |
| `run_launchd_health_probe.sh` | Wrapper for the launchd-health probe |
| `run_market_open_dataflow_check.sh` | Wrapper for the market-open bronze-freshness check |
| `close_orphaned_positions.py` | Emergency orphan-position closer (one-shot) |
| `reset_circuit_breaker.py` | Manually close a stuck circuit breaker |
| `backfill_features.py`, `backfill_fills_from_alpaca.py` | Historical backfills |
| `backtest_exit_rules.py`, `backtest_param_sweep.py` | Local backtests |
| `bootstrap_solver.py`, `seed_solvers.py` | Seed the solvers table |
| `canary_watch.sh`, `watchdog.sh` | Operational guards |
| `db_backup.sh` | DB snapshot |
| `diagnose_data.py`, `diagnose_data_gaps.py`, `verify_activity.py`, `verify_ingestion_sleep.py` | Data-quality probes |
| `import_whalehunter_*.py`, `raw_flow_backfill.py` | One-off data imports |
| `validate_whalehunter_*.py` | Vendor data validation |
| `sync-heber-cache.sh` | Manual rebuild of the host Heber cache |
| `trade_attribution_report.sh` | Multi-system attribution report |
| `run_system_burnin.sh` | Pre-deploy burn-in test |

## Common operations

| Operation | Command |
|---|---|
| Hot-restart execution | `launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution` |
| See last lease holder | `psql … -c "select * from system_status where component like 'service_lease%';"` |
| Stale lease quick clear | Wait 120 s after killing all owners; row goes stale automatically |
| Close stuck circuit | `uv run python scripts/reset_circuit_breaker.py` |
| Emergency close orphans | `uv run python scripts/close_orphaned_positions.py --min-value 50` |
| Fresh DB | `docker compose down -v timescaledb && docker compose up timescaledb -d && uv run alembic upgrade head` |

## Disaster recovery

For full incident playbook see `docs/disaster_recovery_runbook.md` (preserved)
and `docs/ROLLBACK.md`. Outline:

1. Scope: single service vs full stack.
2. Snapshot DB if data risk exists (`scripts/db_backup.sh`).
3. Roll back to last known-good image / commit.
4. Re-run health checks + smoke test.
5. Document root cause in `CHANGELOG.md` and the relevant `predict/<date>-*/RCA.md`.

## Orphan-close history

Why a one-shot orphan-close plist exists, and why it's now disabled:

- **2026-05-22:** 20 options positions worth ~$522K mark value, all expiring
  5/22, were left unattended because the live exit monitor regressed.
- A one-shot launchd plist was authored to fire `close_orphaned_positions.py`
  at 9:35 AM ET on 5/22.
- First version of the plist hardcoded `/opt/homebrew/bin/uv`, which doesn't
  exist on this host. Every fire exited 127 silently for 4.5 hours, costing
  ~$67K of additional unrealized loss before the failure was noticed.
- Fix: switched the path to `/Users/jacobmcmillan/.local/bin/uv` (where `uv`
  actually lives), used local-time `StartCalendarInterval` (codex review caught
  that launchd interprets it in local time, not UTC), and added the
  `launchd-health` probe so this class of silent failure can never recur
  unobserved.
- The plist is now renamed `…orphan-close.plist.DISABLED-260526` and kept as a
  reference, not booted.

The full RCA lives under `predict/260513-2030-restart-loop-rca/` and
`scripts/close_orphaned_positions.py` has the inline incident write-up.

## Related

- [`system-architecture.md`](system-architecture.md) — process topology
- [`configuration-guide.md`](configuration-guide.md) — env vars consumed by each role
- [`testing-guide.md`](testing-guide.md) — E2E pipeline verification
- [`code-standards.md`](code-standards.md) — safety-critical rules
- `RUNBOOK.md`, `RUNBOOKS.md`, `runbooks/`, `disaster_recovery_runbook.md`,
  `ROLLBACK.md` — preserved older playbooks
