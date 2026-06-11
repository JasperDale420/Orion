# Orion — Deployment Guide

How Orion is started, stopped, restarted, and monitored. Two coexistent modes:

- **Native (launchd)** — preferred for `execution` and `ingestion`. Bypasses the
  Docker Desktop 16 GiB VM ceiling that caused OOMs as Heber Gold grew.
- **Docker Compose** — TimescaleDB, ingestion (alternative), feature
  enrichment, position monitor, EOD agent, RAG indexer, MCP server, and
  optional profile services (`legacy-labels`, `tools`, `scheduled`).

Only one mode at a time per role. Mutual exclusion is enforced by Orion's
service-lease table; the lease owner-IDs differ
(`*_native` vs `*_compose`) so the second to start always loses.

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
| `com.empire.orion.meta-search.plist` | `scripts/run_meta_search.sh` | `orion.main_meta --scheduled` — daily solver evolution, self-fires 18:00 ET weekdays |
| `com.empire.orion.meta-weekly.plist` | `scripts/run_meta_weekly.sh` | `orion.main_meta_weekly --scheduled` — weekly evolution + promotions, self-fires Fri 17:30 ET |
| `com.empire.orion.launchd-health.plist` | `scripts/run_launchd_health_probe.sh` | Once-per-minute audit of all `com.empire.orion.*` jobs |
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

## Meta-search & meta-weekly (native)

`com.empire.orion.meta-search` and `com.empire.orion.meta-weekly` are the
production schedulers for solver evolution. They replace the Docker
`meta-search` / `meta-weekly` services, which sat behind the `scheduled`
compose profile that was never brought up — so meta-search had **no**
production scheduler before this (see
`proposals/2026-06-10-eod-meta-diagnosis.md`).

Both are **long-running KeepAlive daemons**, not `StartCalendarInterval`
one-shots. `main_meta.py --scheduled` and `main_meta_weekly.py --scheduled`
each run an internal 60-second poll loop that self-fires at a fixed time and
otherwise sleeps:

- **meta-search** — daily, 18:00 ET on weekdays. Evolves the base solver
  (`ORION_META_BASE_SOLVER`, default `diversified_baseline_v1`).
- **meta-weekly** — Friday 17:30 ET. Runs the weekly evolution, then a solver
  promotion sweep. The fire time is hard-coded in `run_scheduled()`; the plist
  intentionally imposes no calendar time of its own (that would fight the
  in-process scheduler).

Because the process owns its schedule, launchd's only job is to keep the loop
alive across reboots/crashes — exactly like `execution`/`ingestion`. Both are
therefore in the launchd-health probe's `REQUIRED_LABELS`: a missing row means
the scheduler loop is dead and the next fire silently never happens.

Each fire posts a **Discord notification** (via `orion.shared.alerts`):
a success summary (experiments / reports analyzed, mutations applied, solvers
promoted) or a failure with the exception class. Configure the webhook with
`ORION_DISCORD_WEBHOOK_URL`; absent it, the alert no-ops and the run still logs
normally.

```bash
# Install + load
cp scripts/launchd/com.empire.orion.meta-search.plist  ~/Library/LaunchAgents/
cp scripts/launchd/com.empire.orion.meta-weekly.plist  ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.meta-search.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.meta-weekly.plist

# Verify (PID present, LastExitStatus 0 — not 127)
launchctl list | grep -E 'com.empire.orion.meta'

# Disable / uninstall
launchctl bootout gui/$(id -u)/com.empire.orion.meta-search
launchctl bootout gui/$(id -u)/com.empire.orion.meta-weekly
rm ~/Library/LaunchAgents/com.empire.orion.meta-search.plist
rm ~/Library/LaunchAgents/com.empire.orion.meta-weekly.plist
```

> After `bootout`, both labels are removed from `REQUIRED_LABELS`' expected
> set only in code — if you disable them but leave `REQUIRED_LABELS` unchanged,
> the health probe will fire a CRITICAL "not loaded" alert every minute. Disable
> the daemons and the `REQUIRED_LABELS` entries together, or leave them loaded.

Logs: `logs/meta_search_native.log` / `logs/meta_weekly_native.log` (structured),
plus the launchd-captured `*.stdout.log` / `*.stderr.log`.

## Launchd health probe

`com.empire.orion.launchd-health` runs every 60 s and:

1. Shells out to `launchctl list`, filters to `com.empire.orion.*`.
2. Classifies each entry (healthy / non-zero exit / not loaded / suspicious).
3. Appends a JSON row to `logs/launchd_health.log` for anything not healthy.
4. POSTs to `SLACK_WEBHOOK_URL` if that env var is set on the plist.

Exit-127 is escalated to CRITICAL because it cannot self-heal — a human must
edit the plist (typically a hardcoded binary path that doesn't exist on the
host).

The probe exists because of the 2026-05-22 incident
([below](#orphan-close-history)) — a silent exit-127 loop went unnoticed for
4.5 hours.

## Docker Compose

```bash
# Database only
docker compose up timescaledb -d

# Default profile (ingestion, feature_enrichment, execution, position-monitor,
# eod-agent, indexer, mcp-server, timescaledb, heber-sync)
docker compose up -d

# Include legacy labeling profile (pattern-miner, nightly-backfill,
# quality-guardrails, option_quote_tracker)
docker compose --profile legacy-labels up -d

# Include meta-search profile
docker compose --profile tools up -d

# Scheduled jobs (meta-weekly, daily-dashboard-reset)
docker compose --profile scheduled up -d

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
exits non-zero with a lease-conflict error.

## Database migrations

```bash
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe-change"
uv run alembic downgrade -1                # single step back
```

Always read and edit `--autogenerate` output before committing. Migrations
must be reviewable in isolation.

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
| `run_launchd_health_probe.sh` | Wrapper for the launchd-health probe |
| `close_orphaned_positions.py` | Emergency orphan-position closer (one-shot) |
| `reset_circuit_breaker.py` | Manually close a stuck circuit breaker |
| `backfill_features.py`, `backfill_fills_from_alpaca.py` | Historical backfills |
| `backtest_exit_rules.py`, `backtest_param_sweep.py` | Local backtests |
| `bootstrap_solver.py`, `seed_solvers.py` | Seed the solvers table |
| `retrain_0dte.py`, `run_nightly_retrain.sh`, `run_training.py`, `run_exit_training.py` | Model training |
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
