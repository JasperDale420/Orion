# Alerting Configuration

## Overview

Orion uses Discord for all operational alerts. Prometheus metrics are exposed for optional scraping.

## Notification Channels

Orion uses Discord for all operational alerts, configured via `DISCORD_WEBHOOK_URL` env var.

### Discord alert types

| Alert | Source | Cadence |
|---|---|---|
| Stale-cancel give-up | `execution_engine.py` `_cancel_stale_entry_orders` | Per-event (deduped by order_id) |
| System health CRITICAL/DEGRADED | `execution_engine.py` `_check_system_health` | Per-occurrence |
| Unprotected/partially-protected position | `execution_engine.py` bracket-placement failure | Per-position cycle |
| Launchd stopped-job nonzero exit | `jobs/launchd_health_probe.py` | Per (label, exit_code), 1h dedup window |
| Launchd missing required job | `jobs/launchd_health_probe.py` | Per missing label, 1h dedup window |
| Circuit breaker OPEN | `core/circuit_breaker.py` | Per-open event |
| Already-terminal reconcile | `execution_engine.py` (stale-cancel) | Per-event |

All Discord alerts are best-effort — webhook failures are logged but non-fatal.

Required labels monitored by the launchd health probe: `execution`, `ingestion`, `position-monitor`, `data-quality`.

### Alert severity levels

- **CRITICAL** — Trading halted or attribution-blind position: circuit breaker open, stale-cancel give-up on active position
- **WARNING** — Degraded operation: missing launchd daemon, health DEGRADED, unprotected bracket

## Prometheus Alert Rules

Add these to your `prometheus/alerts.yml`:

```yaml
groups:
  - name: orion
    rules:
      # Circuit Breaker Alert
      - alert: CircuitBreakerOpen
        expr: orion_circuit_breaker_status == 1
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Circuit breaker is OPEN"
          description: "Trading has been halted. Check system_status table for reason."

      # High Error Rate
      - alert: HighErrorRate
        expr: rate(orion_errors_total[5m]) > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value }} errors/sec"

      # Connector Stale
      - alert: ConnectorStale
        expr: time() - orion_last_poll_timestamp > 600
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Connector has not polled in 10+ minutes"
          description: "Connector {{ $labels.connector }} may be stuck"

      # DLQ Backlog
      - alert: DLQBacklog
        expr: orion_dlq_unprocessed_count > 100
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "DLQ has unprocessed events"
          description: "{{ $value }} events waiting in dead letter queue"
```

## Log-Based Alert Conditions

| Condition | Threshold | Action |
|-----------|-----------|--------|
| ERROR logs | > 5 in 5 min | Investigate |
| CRITICAL logs | Any | Immediate response |
| Circuit breaker OPEN | Any | Check reason, resolve |
| API 5xx rate | > 1% | Investigate |
| `stale_cancel_gave_up` events | Any | Check execution log; per-order backoff self-heals within 5 min |
| `launchd exit 127` in health log | Any | Fix plist ProgramArguments (`~/.local/bin/uv`) |

## Response Procedures

See [RUNBOOK.md](RUNBOOK.md) for detailed response procedures.
