# Data Retention Policy

This document defines the data retention periods and pruning strategies for Orion.

## Data Layers

### Bronze Layer (Raw Events)

| Data Type | Retention | Rationale |
|-----------|-----------|-----------|
| UW Flow Events | 90 days | Raw event replay window |
| UW Darkpool Events | 90 days | Analysis lookback period |
| UW Alerts | 30 days | Short-lived actionable data |
| Alpaca Bars | 365 days | Backtesting requirements |
| Alpaca Orders | Indefinite | Audit trail |

### Silver Layer (Normalized)

| Data Type | Retention | Rationale |
|-----------|-----------|-----------|
| Normalized Events | 60 days | Feature computation window |
| Feature Snapshots | 30 days | Rolling analysis |
| Signal Logs | 90 days | Signal effectiveness review |

### Gold Layer (Aggregated)

| Data Type | Retention | Rationale |
|-----------|-----------|-----------|
| Rollups | Indefinite | Performance metrics history |
| Solver Metrics | Indefinite | Model tracking |
| Experiments | Indefinite | A/B test records |

### Audit & System

| Data Type | Retention | Rationale |
|-----------|-----------|-----------|
| Audit Logs | 2 years | Compliance |
| DLQ Events | 30 days | Error investigation window |
| System Status | 7 days | Real-time only |

## Implementation

### Automated Pruning

Add to scheduled jobs (cron or APScheduler):

```python
# Example pruning job
async def prune_bronze_events():
    """Delete bronze events older than retention period."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete
    from orion.storage.models import BronzeEvent
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    stmt = delete(BronzeEvent).where(BronzeEvent.event_ts_utc < cutoff)
    await session.execute(stmt)
    await session.commit()
```

### Manual Cleanup

```sql
-- Bronze events older than 90 days
DELETE FROM bronze_events 
WHERE event_ts_utc < NOW() - INTERVAL '90 days';

-- DLQ events older than 30 days
DELETE FROM dlq_events 
WHERE created_at < NOW() - INTERVAL '30 days';
```

## Data Classification

| Classification | Examples | Handling |
|----------------|----------|----------|
| **Public** | Ticker symbols, public prices | No restrictions |
| **Internal** | Solver configs, metrics | Access controlled |
| **Sensitive** | API keys, trading orders | Encrypted, audit logged |
| **PII** | User identifiers (if any) | GDPR/CCPA compliant |

## Compliance Notes

- **Financial Data**: Trading orders and fills are retained indefinitely for regulatory compliance
- **Audit Logs**: 2-year minimum per SOX/financial regulations
- **Right to Deletion**: Currently no PII stored; update if user accounts added
