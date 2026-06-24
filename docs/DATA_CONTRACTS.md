# Data Contracts

## Overview

Orion consumes and produces normalized event data across ingestion, storage, and execution workflows. Contracts must stay compatible with Data-Gateway and Heber expectations.

## Schemas

### EventEnvelope

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

### SilverSignal

Producer: `NormalizationEngine` + `DeduplicationEngine`
Consumers: `FeatureEngine`, `RuleEngine`, model training
Table: `silver_signals`

Key fields:

| Field | Type | Description |
|---|---|---|
| `event_id` | string | From source BronzeEvent (dedup key) |
| `source` | string | `UW_FLOW`, `ALPACA_BAR`, etc. |
| `ticker` | string | Underlying symbol |
| `event_type` | string | Normalized signal type |
| `event_ts_utc` | timestamp | Event time |
| `trading_date` | date | Partition date |
| `session` | string | PRE / REG / POST / CLOSED |
| `payload` | JSONB | Normalized fields (provider-agnostic) |

### CandidateTrade

Producer: `RuleEngine` (flow rules: BullishSweep, BearishPutPressure, ZeroDTESweep, SwingEntry, ShortSwingEntry)
Consumers: `SignalEngine` (regime filter → ML pre-filter → solver ensemble)
Table: `candidate_trades`

Key fields:

| Field | Type | Description |
|---|---|---|
| `candidate_id` | UUID | Unique candidate ID |
| `ticker` | string | Underlying symbol |
| `option_symbol` | string | OCC option contract (required — execution rejects without it) |
| `rule_name` | string | Rule that generated this candidate |
| `signal_ts_utc` | timestamp | When the signal fired |
| `trading_date` | date | Partition |
| `side` | string | BUY / SELL |
| `meta` | JSONB | Rule-specific context (premium, sweep %, aggression) |

### StrategyDecision

Producer: `SignalEngine` → solver ensemble
Consumers: `ExecutionEngine`, EOD agent, audit
Table: `strategy_decisions`

Key fields:

| Field | Type | Description |
|---|---|---|
| `decision_id` | UUID | Unique decision ID |
| `candidate_id` | UUID | FK → candidate_trades |
| `status` | string | EXECUTE / SKIP |
| `solver_ids` | JSONB | Solvers that voted |
| `consensus_score` | float | Weighted vote score |
| `skip_reason` | string | Why skipped (regime, ML, risk, etc.) |
| `trace` | JSONB | Full decision trace for audit |
| `decided_at_utc` | timestamp | Decision time |

## Versioning

- Treat schema changes as explicit contract changes.
- Update producer and consumer docs in the same PR.
- Record contract-impacting changes in `CHANGELOG.md` (repo root).

## Validation

- Validate at ingestion boundaries before persistence.
- Reject malformed/corrupt payloads.
- Preserve detailed structured logs (`exc_info=True`) for invalid record diagnostics.
