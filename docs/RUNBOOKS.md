# Operational Runbooks

Standard operating procedures for Orion system maintenance and incident response.

Canonical runbook:
- `/Users/jacobmcmillan/Empire/Orion/docs/RUNBOOK.md`

## Service Health Checks

### Quick Health Check

```bash
# Check all containers
docker compose ps

# Check API health
curl -s http://localhost:8000/health | jq

# Check database connection
docker compose exec execution python - <<'PY'
import asyncio
from sqlalchemy import text
from orion.storage.db import async_session_factory


async def main() -> None:
    async with async_session_factory() as session:
        await session.execute(text("select 1"))
        print("DB OK")


asyncio.run(main())
PY
```

### Log Inspection

```bash
# Admin API logs live in the terminal running the local API process.

# Ingestion logs
docker compose logs -f ingestion --tail=100

# Filter for errors
docker compose logs execution 2>&1 | rg -i error | tail -50
```

---

## Common Incidents

### 1. Circuit Breaker Open

**Symptoms:** No signals being generated, logs show "Circuit breaker OPEN"

**Resolution:**
```bash
# Check circuit breaker status
curl -s http://localhost:8000/health | jq '.circuit_breaker'

# Close circuit breaker (requires investigation first!)
docker compose exec execution python -c "
from orion.core.circuit_breaker import CircuitBreaker
import asyncio
asyncio.run(CircuitBreaker().close())
"
```

### 2. UW API Rate Limited

**Symptoms:** Logs show `PROVIDER_RATE_LIMIT`, ingestion stalled

**Resolution:**
1. Wait for rate limit window to reset (~1 minute)
2. Reduce `UW_FETCH_LIMIT` in environment
3. Check if multiple instances are polling

### 3. Database Connection Pool Exhausted

**Symptoms:** `TimeoutError` in logs, API returning 500s

**Resolution:**
```bash
# Restart to reset pool
echo "Restart the local API process if it is running outside compose"

# Long-term: increase pool size in config
# DATABASE_POOL_SIZE=10
```

### 4. Solver Not Routing

**Symptoms:** Signals not being generated for specific tickers

**Resolution:**
```bash
# Check solver status
curl -s -H "x-api-key: $ORION_API_KEY" http://localhost:8000/solvers | jq '.[] | {id: .solver_id, status: .status, stage: .stage}'

# Check if solver has metrics
curl -s -H "x-api-key: $ORION_API_KEY" "http://localhost:8000/metrics?solver_id=<ID>" | jq
```

---

## Alert Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| API latency p99 | >500ms | >2000ms | Scale up, check DB |
| Error rate | >1% | >5% | Check logs, circuit breaker |
| Ingestion lag | >5 min | >15 min | Check UW API, restart |
| DB connections | >80% | >95% | Increase pool, restart |
| Memory usage | >80% | >95% | Check for leaks, restart |

---

## Scheduled Maintenance

### Daily
- [ ] Review error logs
- [ ] Check ingestion watermarks
- [ ] Verify solver metrics updated

### Weekly
- [ ] Run integration tests
- [ ] Review DLQ for patterns
- [ ] Check disk space

### Monthly
- [ ] Update dependencies (Dependabot)
- [ ] Review and prune old data
- [ ] Audit API access logs

---

## Emergency Contacts

| Role | Responsibility |
|------|----------------|
| On-call | Initial triage, runbook execution |
| Tech Lead | Escalation, rollback decisions |
| DBA | Database issues, migrations |
