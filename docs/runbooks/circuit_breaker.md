# Circuit Breaker Runbook

## Overview

The circuit breaker is a global kill switch backed by the `SystemStatus` table. When OPEN, all connectors skip polling to prevent further damage during incidents.

## Check Circuit Breaker Status

```sql
SELECT * FROM system_status ORDER BY updated_at DESC LIMIT 1;
```

Expected output when CLOSED (normal):
```
status: CLOSED
reason: NULL
updated_at: <timestamp>
```

## Open Circuit Breaker (Emergency Stop)

**Use this when you need to immediately halt all trading/polling activity.**

### Via Database

```sql
INSERT INTO system_status (status, reason, updated_at)
VALUES ('OPEN', 'Manual halt - <your reason>', NOW())
ON CONFLICT (status) DO UPDATE SET
  status = 'OPEN',
  reason = 'Manual halt - <your reason>',
  updated_at = NOW();
```

### Via Python

```python
from orion.core.circuit_breaker import CircuitBreaker

breaker = CircuitBreaker()
await breaker.open("Manual halt - investigating issue X")
```

## Close Circuit Breaker (Resume Operations)

### Via Database

```sql
UPDATE system_status
SET status = 'CLOSED', reason = NULL, updated_at = NOW()
WHERE status = 'OPEN';
```

### Via Python

```python
from orion.core.circuit_breaker import CircuitBreaker

breaker = CircuitBreaker()
await breaker.close()
```

## Verify Connectors Respecting Breaker

When circuit breaker is OPEN, you should see logs like:
```
Circuit breaker OPEN, skipping UW flow poll
Circuit breaker OPEN, skipping UW alerts fetch
Circuit breaker OPEN, skipping UW darkpool fetch
```

## When to Open Circuit Breaker

- API rate limits exhausted
- Unusual market conditions
- Suspected data corruption
- During maintenance windows
- After detecting anomalous trading behavior
