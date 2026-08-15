# Alerting Configuration

## Overview

Orion uses Discord for all operational alerts. Prometheus metrics are exposed for optional scraping.

## Notification Channels

Orion uses Discord for all operational alerts, configured via `DISCORD_WEBHOOK_URL` env var.

### Discord alert types

| Alert | Source | Cadence |
|---|---|---|
| Circuit breaker OPEN | `jobs/deadman_watchdog.py` | Immediate, then 15-minute reminders while open |
| Stale active service or bronze/silver stage | `jobs/deadman_watchdog.py` | Market-aware, 15-minute persistent dedup |
| Watchdog checks degraded | `jobs/deadman_watchdog.py` | 15-minute persistent dedup |
| Required launchd daemon missing | `jobs/launchd_health_probe.py` | Per label, 1-hour persistent dedup |
| Idle launchd job unexpected non-zero exit | `jobs/launchd_health_probe.py` | Per `(label, exit_code)`, 1-hour persistent dedup |
| Market-open feed/Gateway failure | `jobs/market_open_dataflow_check.py` | 09:40, 10:00, and 10:30 ET checks; CRITICAL only |
| Gateway bar stream down/recovered | `ingestion/service.py` | State transition only |
| Unprotected/partially protected position | `execution/execution_engine.py` | Per option symbol, 15-minute in-process dedup |
| Position re-protected | `execution/execution_engine.py` | One recovery notification per option symbol |
| Stale-cancel give-up | `execution/execution_engine.py` | Once per order; known shared Gateway-permission failures do not page |
| Broker truth unavailable for PnL reconciliation | `jobs/reconcile_pnl.py` | Reconciliation failure only |
| Bucket performance verdict | `jobs/bucket_metrics.py` | Nightly only when `consider_halting` or `consider_sizing_up` |
| Flow-push shadow parity RED | `ingestion/service.py` | Daily while `ORION_FLOW_SOURCE=shadow`; GREEN is log-only |

All Discord alerts are best-effort — webhook failures are logged but non-fatal.

Required labels monitored by the launchd health probe: `execution`, `ingestion`, `position-monitor`, `data-quality`.

The launchd probe does not alert on a running job's retained previous exit
status. It also does not duplicate exit code `2` from `deadman` or
`market-open-dataflow-check`, because those checks send their own incident
notification. Retired `meta_search` and `meta_weekly` database liveness rows
are ignored by the dead-man watchdog.

### Log-only status

- Healthy launchd, market-open, and feed checks
- Nightly bucket metrics without an actionable verdict
- GREEN flow-push shadow parity
- Feature-table freshness (the live path does not write that table)
- Market-session service and pipeline staleness outside market hours

### Alert severity levels

- **CRITICAL** — Trading halted or attribution-blind position: circuit breaker open, stale-cancel give-up on active position
- **WARNING** — Degraded operation: non-critical launchd failure, watchdog degraded, unprotected bracket

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
