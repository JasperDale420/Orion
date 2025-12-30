# Troubleshooting Runbook

## Common Issues

### 1. Connectors Not Polling

**Symptoms**: No new events in bronze_events table

**Check**:
```sql
-- Check watermarks
SELECT * FROM watermarks ORDER BY last_seen_ts_utc DESC;

-- Check circuit breaker
SELECT * FROM system_status;
```

**Fix**:
- If circuit breaker OPEN: Close it (see [circuit_breaker.md](circuit_breaker.md))
- If watermark stale: Reset watermark
- Check API credentials in `.env`

### 2. High Memory Usage

**Symptoms**: Container OOM kills, slow responses

**Check**:
```bash
docker stats
```

**Fix**:
- Restart the affected service
- Check for memory leaks in feature history (`max_history_len=100` should cap it)
- Review recent code changes

### 3. API Rate Limits

**Symptoms**: 429 errors in logs, missing data

**Check**:
```bash
docker-compose logs -f orion-ingestion | grep -i "rate\|429"
```

**Fix**:
- Open circuit breaker temporarily
- Wait for rate limit reset
- Review connector polling intervals

### 4. Database Connection Errors

**Symptoms**: `connection refused`, `too many connections`

**Check**:
```sql
SELECT count(*) FROM pg_stat_activity;
```

**Fix**:
- Restart services to release connections
- Increase `max_connections` in postgres config
- Check for connection leaks

### 5. Missing Features/Indicators

**Symptoms**: RSI/SMA columns are NULL

**Check**:
```sql
SELECT ticker, COUNT(*) 
FROM silver_alpaca_bars 
GROUP BY ticker;
```

**Fix**:
- Ensure `hydrate_history()` was called on startup
- Check if enough bars exist (need 14+ for RSI, 20+ for SMA)

## Log Locations

| Service | Log Command |
|---------|-------------|
| All | `docker-compose logs -f` |
| Ingestion | `docker-compose logs -f orion-ingestion` |
| API | `docker-compose logs -f orion-api` |

## Escalation

If issue persists after troubleshooting:
1. Check CHANGELOG.md for recent changes
2. Review Git history for related commits
3. Open GitHub issue with logs and reproduction steps
