# Runbook

## Service Topology

Two deployment modes coexist — only one may be active per role at a time. The service-lease table enforces mutual exclusion (native uses `*_native` owner IDs, Docker uses `*_compose`; second to start raises RuntimeError).

| Role | Native (launchd) — **canonical for trading** | Docker Compose |
|---|---|---|
| Ingestion | `com.empire.orion.ingestion` | `ingestion` (profile: docker) |
| Execution | `com.empire.orion.execution` | `execution` (profile: docker) |
| Launchd health probe | `com.empire.orion.launchd-health` | — |
| Feature enrichment | — | `feature_enrichment` (default) |
| Position monitor | — | `position-monitor` (profile: docker) |
| Data quality | — | `data-quality` (profile: docker) |
| TimescaleDB | — | `timescaledb` (always) |
| MCP server | — | `mcp-server` (default) |
| RAG indexer | — | `indexer` (default) |

**Rule:** never run native + Docker in the same role simultaneously. Stop one before starting the other.

## Startup

### 1. Start support services (Docker)

```bash
docker compose up -d timescaledb feature_enrichment indexer mcp-server
```

Wait for TimescaleDB to be healthy:

```bash
docker compose ps timescaledb
```

### 2. Start trading services (native, via launchd)

```bash
launchctl kickstart -k gui/$(id -u)/com.empire.orion.ingestion
launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution
```

### 3. Check status

```bash
launchctl print gui/$(id -u)/com.empire.orion.ingestion
launchctl print gui/$(id -u)/com.empire.orion.execution
launchctl list | grep com.empire.orion
```

## Shutdown

Stop native services:

```bash
launchctl stop gui/$(id -u)/com.empire.orion.ingestion
launchctl stop gui/$(id -u)/com.empire.orion.execution
```

Stop Docker support services:

```bash
docker compose down
```

## Health Checks

### Native service health (live logs)

```bash
tail -f logs/execution_native.log | jq .
tail -f logs/ingestion_native.log | jq .
tail -f logs/launchd_health.log
```

### API health

```bash
curl -s http://localhost:8000/health
```

### Ingestion freshness

```bash
# Check most recent bronze row
psql -p 5440 -U orion -d orion_db -c "SELECT max(received_ts_utc) FROM bronze_events;"
```

### Service list

```bash
launchctl list | grep com.empire.orion
docker compose ps
```

## Common Issues & Troubleshooting

### Exit 127 on launchd service

**Symptom:** `launchctl print` shows last exit code 127; service never starts.
**Cause:** wrong `uv` path in ProgramArguments. Must be `~/.local/bin/uv`, not `/opt/homebrew/bin/uv`.
**Fix:** edit the plist in `scripts/launchd/`, correct the path, reload:
```bash
launchctl unload ~/Library/LaunchAgents/com.empire.orion.<role>.plist
launchctl load ~/Library/LaunchAgents/com.empire.orion.<role>.plist
```

### 429 storm / stale-cancel flood

**Symptom:** `gateway_trading_http_error` + `GW-E4001` flood in `logs/execution_native.log`; same order "Cancel rejected" many times.
**Cause:** stale-cancel give-up loop. Per-order `_CancelState` backoff will self-heal within 5 minutes.
**Check:** `grep stale_cancel_gave_up logs/execution_native.log | tail -20`
**If persists:** restart execution — `launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution`

### Split-brain (native + Docker running same role)

**Symptom:** RuntimeError `another lease owner holds service_lease_*` in logs.
**Cause:** both native and Docker copies of ingestion or execution are running.
**Fix:** stop the unwanted one:
```bash
launchctl stop gui/$(id -u)/com.empire.orion.<role>
# or
docker rm -f orion_<role>
```

### Born-stale candidates (>600s age at entry)

**Symptom:** all candidates skipped with "Data Lag" or "stale at fetch".
**Cause:** Heber Gold sync not running or stale; overnight catch-up burst of old flow events.
**Check:**
```bash
ls -lt ~/.heber-cache/data/gold/ | head
docker logs orion_heber_sync 2>&1 | tail -30
```

### Missing env vars

**Symptom:** immediate crash or config errors at startup.
**Fix:** verify `.env` (repo root) against `.env.example`. Do not use absolute paths — the `.env` file lives at the repo root relative to where you run scripts.

### DB connection errors

**Symptom:** SQLAlchemy connection errors/timeouts.
**Fix:** ensure `timescaledb` Docker container is healthy:
```bash
docker compose ps timescaledb
docker compose up -d timescaledb
```

### Provider auth failures

**Symptom:** repeated 401/403 from UW or Alpaca.
**Fix:** rotate keys in `.env`, restart the relevant native service.

## Disaster Recovery

1. Confirm scope (single service vs full stack failure).
2. Snapshot DB state if data risk exists.
3. Roll back to last known-good commit.
4. Re-run health checks: `curl -s http://localhost:8000/health` and check `logs/` for errors.
5. Document root cause in `CHANGELOG.md`.

See `docs/ROLLBACK.md` for detailed rollback guidance.

## Maintenance

- **Daily:** check `logs/launchd_health.log` for exit-127 alerts — these mean a plist has wrong ProgramArguments.
- **Weekly:** run `uv run pytest tests/unit` and `ruff check .`; review error rates in structured logs.
- **Monthly:** dependency review (`uv sync`) and Alembic drift check (`uv run alembic current`).

For full operational reference including deployment details, see [`deployment-guide.md`](deployment-guide.md).
