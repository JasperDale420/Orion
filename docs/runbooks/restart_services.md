# Restart Services Runbook

## Native services (canonical — launchd)

`ingestion`, `execution`, `position-monitor` and `data-quality` run natively via
launchd (`~/Library/LaunchAgents/com.empire.orion.*.plist`, wrappers in
`scripts/run_*_native.sh`). The wrapper runs `uv sync` and exec's
`.venv/bin/python -m …` from `/Users/jacobmcmillan/Empire/Orion` (the `master`
checkout) — a restart is how new code is deployed.

```bash
# Restart (SIGKILL + relaunch; last-exit -9 in `launchctl list` is expected)
launchctl kickstart -k gui/$(id -u)/com.empire.orion.ingestion
launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution
launchctl kickstart -k gui/$(id -u)/com.empire.orion.position-monitor
```

Order: ingestion first (it hydrates bar history on start — under 2 minutes since
`HeberReader` prunes by `dt=` partition; it was 7–13 minutes before), then
execution and position-monitor. Avoid restarting inside the 15 minutes before
the open.

Verify:

```bash
launchctl list | grep com.empire.orion
ps -o pid,lstart,command -p "$(pgrep -f 'orion.ingestion$')" -p "$(pgrep -f 'orion.main_execution')" -p "$(pgrep -f 'orion.main_position_monitor')"
psql "postgresql://orion:orion_password@localhost:5440/orion_db" -Atc "select key,status,details,last_updated_utc from system_status where key in ('GLOBAL_CIRCUIT_BREAKER','global_health','degraded_discovery') order by key;"
tail -n 50 logs/execution_native.log | grep -E 'RISK_BASELINE_UNVERIFIED|Risk State Loaded|CRITICAL'
```

`feature_enrichment` is a docker service that bind-mounts the checkout, so new
code needs only `docker compose restart feature_enrichment` (rebuild only when
dependencies change).

## Docker support services

## Prerequisites
- Docker and Docker Compose installed
- Access to the Orion repository

## Restart All Services

```bash
cd /path/to/Orion
docker compose down
docker compose up -d --build
```

## Restart Specific Service

```bash
# List running services
docker compose ps

# Restart a specific service
docker compose restart <service-name>

# Example: restart ingestion
docker compose restart ingestion
```

## Verify Services Running

```bash
# Check container status
docker compose ps

# Check logs for a service
docker compose logs -f --tail=100 <service-name>
```

## Health Check Endpoints

| Service | Health Endpoint |
|---------|-----------------|
| API | `GET /health` |
| Ingestion | Logs `IngestionService.run starting` |

## Rollback to Previous Version

```bash
# Pull specific tag
git checkout <tag-or-commit>
docker compose down
docker compose up -d --build
```
