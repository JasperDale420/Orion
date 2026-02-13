# Runbook

## Service Overview

Orion runs multiple long-lived services (ingestion, enrichment, execution, monitoring, and optional legacy workers). Core dependencies are TimescaleDB, MinIO, and external provider credentials.

## Startup / Shutdown

Start full stack:

```bash
docker compose up -d --build
```

Stop services:

```bash
docker compose down
```

Restart one service:

```bash
docker compose restart execution
```

## Health Checks

Quick checks:

```bash
docker compose ps
docker compose logs --tail=100 execution
```

API health (when API process is running on port 8000):

```bash
curl -s http://localhost:8000/health
```

## Common Issues & Troubleshooting

- Missing env vars on startup
  - Symptom: immediate crash or configuration errors.
  - Fix: verify `/Users/jacobmcmillan/Empire/Orion/.env` against `.env.example`.
- Provider auth failures
  - Symptom: repeated 401/403 from UW/Alpaca.
  - Fix: rotate keys and restart relevant services.
- DB connection issues
  - Symptom: SQLAlchemy connection errors/timeouts.
  - Fix: ensure `timescaledb` is healthy and `DB_URL` is correct.
- Redundant polling during migration
  - Symptom: duplicate requests/log noise.
  - Fix: review migration gates and ensure gateway-owned polling remains single-source.

## Monitoring & Alerting

Use container logs and structured log fields to triage failures quickly.

Useful commands:

```bash
docker compose logs -f feature_enrichment
docker compose logs -f execution
```

Focused error scan:

```bash
docker compose logs --no-color 2>&1 | rg -i "error|exception|traceback"
```

## Disaster Recovery

1. Confirm scope (single service vs full stack failure).
2. Snapshot/export DB state if data risk exists.
3. Roll back to last known-good image/commit.
4. Re-run health checks and smoke test critical paths.
5. Document root cause and remediation in changelog/audit notes.

Detailed rollback guidance:
- `/Users/jacobmcmillan/Empire/Orion/docs/ROLLBACK.md`

## Maintenance Tasks

- Daily: verify ingestion freshness and error rates.
- Weekly: run test and lint gates, review failures.
- Monthly: dependency review and migration drift check.

Legacy detailed runbooks are available in:
- `/Users/jacobmcmillan/Empire/Orion/docs/runbooks/`
