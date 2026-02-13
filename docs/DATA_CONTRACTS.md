# Data Contracts

## Overview

Orion consumes and produces normalized event data across ingestion, storage, and execution workflows. During migration, contracts must stay compatible with Data Gateway and Heber expectations.

## Schemas

### Event Envelope

Producer: Orion ingestion (and upstream gateway sources)
Consumers: Orion processors/execution, Heber pipelines, audits
Format: JSON

Core fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `event_id` | string | yes | Deterministic unique ID |
| `source` | string | yes | Data source (`UW`, `ALPACA`, etc.) |
| `event_type` | string | yes | Domain event type |
| `event_ts_utc` | ISO timestamp | yes | Event time in UTC |
| `received_ts_utc` | ISO timestamp | yes | Ingest receive time |
| `trading_date` | date | yes | Trading date partition |
| `session` | string | yes | `PRE` / `REG` / `POST` / `CLOSED` |
| `ticker` | string | optional | Symbol context |
| `payload` | object | yes | Raw or normalized payload |
| `ingest` | object | yes | Connector/run/trace metadata |

### Normalized Flow and Rollup Records

Producer: Orion processing jobs
Consumers: Signal generation, model training, monitoring, audits
Format: SQL rows and derived JSON APIs

Representative entities:
- Silver options flow rows
- Gold ticker rollups
- Candidate trade records

## Versioning

- Treat schema changes as explicit contract changes.
- Update producer and consumer docs in the same PR.
- Record contract-impacting changes in `/Users/jacobmcmillan/Empire/Orion/CHANGELOG.md`.

## Validation

- Validate at ingestion boundaries before persistence.
- Reject malformed/corrupt payloads.
- Preserve detailed structured logs (`exc_info=True`) for invalid record diagnostics.
