# Alerting Configuration

## Overview

This document describes the recommended alert configurations for monitoring Orion services.

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

## Log-Based Alerts

### CloudWatch Log Metric Filters

```json
{
  "filterPattern": "{ $.level = \"ERROR\" }",
  "metricName": "OrionErrors",
  "metricNamespace": "Orion/Logs"
}
```

### Alert Conditions

| Condition | Threshold | Action |
|-----------|-----------|--------|
| ERROR logs | > 5 in 5 min | Investigate |
| CRITICAL logs | Any | Immediate response |
| Circuit breaker OPEN | Any | Check reason, resolve |
| API 5xx rate | > 1% | Investigate |

## Notification Channels

Configure in your alerting system:

1. **Slack**: `#orion-alerts` channel
2. **PagerDuty**: For critical alerts during trading hours
3. **Email**: For warning-level alerts

## Response Procedures

See [Runbooks](runbooks/) for detailed response procedures.
