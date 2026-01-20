# Disaster Recovery Runbook

Orion Trading System — Production Incident Response

---

## Emergency Contacts

| Role | Contact |
|------|---------|
| Primary On-Call | System Admin |
| Broker Support | Alpaca: support@alpaca.markets |
| Data Provider | Unusual Whales: support@unusualwhales.com |

---

## Immediate Actions

### 1. Trading Halt (< 1 min)
```bash
# Kill all trading services
docker compose down orion_execution orion_position_monitor

# Or via environment flag
export ORION_STAGE=halt
docker compose restart orion_execution
```

### 2. Position Sync Check
```bash
# Verify positions match broker
docker compose exec orion_db psql -c "
SELECT ticker, qty, avg_entry_price FROM gold_positions
WHERE status = 'OPEN' ORDER BY created_at DESC;"

# Compare with Alpaca positions
curl -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
     -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
     https://paper-api.alpaca.markets/v2/positions
```

### 3. Check Order Status
```bash
# Find pending/stuck orders
docker compose exec orion_db psql -c "
SELECT id, ticker, status, created_at FROM gold_orders
WHERE status NOT IN ('FILLED', 'CANCELLED')
ORDER BY created_at DESC LIMIT 20;"
```

---

## Common Failure Modes

### A. Database Connection Failure
**Symptoms**: Log errors mentioning `psycopg2.OperationalError`

**Resolution**:
1. Check DB container health: `docker compose ps`
2. Restart DB: `docker compose restart orion_db`
3. Test connection: `docker compose exec orion_db psql -c "SELECT 1;"`
4. If persistent, check disk space: `df -h`

### B. Broker API Unavailable
**Symptoms**: `ConnectionError` or `429 Too Many Requests`

**Resolution**:
1. Check Alpaca status: https://status.alpaca.markets
2. Review rate limiter state in logs
3. If rate-limited, wait 60 seconds then resume
4. Switch to paper mode temporarily: `ORION_STAGE=paper`

### C. Data Feed Interruption
**Symptoms**: No new signals in `silver_uw_flow`

**Resolution**:
1. Check UW API status
2. Review ingestion logs: `docker compose logs orion_ingestion`
3. Verify API key valid with manual test
4. Restart ingestion: `docker compose restart orion_ingestion`

### D. Position Mismatch
**Symptoms**: Local positions don't match broker

**Resolution**:
1. Halt trading immediately
2. Run position sync: 
   ```python
   from orion.execution.risk_manager import RiskManager
   rm = RiskManager()
   await rm.sync_with_broker()
   ```
3. Manually reconcile any differences
4. Resume only after verification

### E. Greeks Calculation Failure  
**Symptoms**: `None` delta/gamma values

**Resolution**:
1. Check Alpaca market data connection
2. Verify option symbols are valid
3. Fallback to Black-Scholes: Greeks checks disabled automatically

---

## Recovery Procedures

### Full System Restart
```bash
# 1. Stop all services
docker compose down

# 2. Verify clean shutdown
docker ps  # Should show nothing

# 3. Start infrastructure first
docker compose up -d orion_db
sleep 10

# 4. Verify DB health
docker compose exec orion_db pg_isready

# 5. Start services in order
docker compose up -d orion_ingestion
docker compose up -d orion_labeler
docker compose up -d orion_execution
docker compose up -d orion_position_monitor
```

### Data Backfill After Outage
```bash
# Backfill missed labels
python -m orion.jobs.backfill_ml_features --days 1

# Verify feature coverage
docker compose exec orion_db psql -c "
SELECT COUNT(*), 
       COUNT(delta_at_entry) as with_greeks
FROM price_target_labels
WHERE created_at > NOW() - INTERVAL '24 hours';"
```

---

## Escalation Matrix

| Severity | Response Time | Action |
|----------|---------------|--------|
| P1 - Trading Impact | < 5 min | Halt trading, page on-call |
| P2 - Data Loss Risk | < 15 min | Investigate, possible halt |
| P3 - Degraded Service | < 1 hour | Monitor, prepare mitigation |
| P4 - Minor Issue | < 4 hours | Log ticket, schedule fix |

---

## Post-Incident Checklist

- [ ] Root cause identified
- [ ] Timeline documented
- [ ] Positions verified against broker
- [ ] P&L reconciled
- [ ] Logs preserved
- [ ] Fix deployed
- [ ] Monitoring enhanced if needed
