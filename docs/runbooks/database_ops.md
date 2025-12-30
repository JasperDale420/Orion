# Database Operations Runbook

## Connection

```bash
# Via Docker
docker exec -it orion-postgres psql -U orion -d orion

# Direct (if exposed)
psql -h localhost -p 5432 -U orion -d orion
```

## Common Queries

### Check Recent Events

```sql
-- Last 10 bronze events
SELECT event_id, event_type, ticker, event_ts_utc, created_at_utc 
FROM bronze_events 
ORDER BY created_at_utc DESC 
LIMIT 10;
```

### Check Watermarks

```sql
-- View all watermarks (connector progress)
SELECT * FROM watermarks ORDER BY last_seen_ts_utc DESC;
```

### Check DLQ (Dead Letter Queue)

```sql
-- Events that failed processing
SELECT * FROM dlq_events 
WHERE processed = false 
ORDER BY created_at_utc DESC 
LIMIT 20;
```

### Check Solver Status

```sql
-- Active solvers
SELECT solver_id, status, stage, created_at_utc 
FROM solvers 
WHERE status = 'active';
```

## Maintenance

### Vacuum Tables

```sql
VACUUM ANALYZE bronze_events;
VACUUM ANALYZE silver_signals;
VACUUM ANALYZE gold_feature_events;
```

### Reset Watermark (Backfill)

```sql
-- Reset UW flow watermark to 24 hours ago
UPDATE watermarks 
SET last_seen_ts_utc = NOW() - INTERVAL '24 hours'
WHERE key = 'uw_flow';
```

## Backup

```bash
# Dump database
docker exec orion-postgres pg_dump -U orion orion > backup.sql

# Restore
docker exec -i orion-postgres psql -U orion orion < backup.sql
```
