# Orion — Project Overview / PDR

Orion is Empire's real-time data lake and signal engine for **US options trading**.
It ingests live market data via Data-Gateway, generates trade candidates through a
**solver ensemble**, and **places options orders on Alpaca** (via the Data-Gateway
trading proxy). Storage is TimescaleDB (PostgreSQL + pgvector); historical reads
come from a host-cached slice of the Heber lakehouse.

> **Execution boundary:** Orion is a live-trading system. It submits real options
> orders against a shared Alpaca paper/live account. See
> [`system-architecture.md`](system-architecture.md#execution-boundary) and
> [`code-standards.md`](code-standards.md#safety-critical-code) for the safety
> contract that must be preserved when modifying execution code.

## Mission

Run an always-on options strategy lab that turns raw options-flow + bar data into
auditable trading decisions. The system must:

1. **Ingest** every UW flow alert and Alpaca bar in near-real-time
   (Bronze → Silver → Gold layers in TimescaleDB).
2. **Score** candidates through a multi-axis regime filter + LightGBM pre-filter +
   solver-ensemble vote.
3. **Trade** options-only (rejects equity candidates) via Data-Gateway → Alpaca,
   under a tight risk envelope (daily loss limit, Greeks limits, position caps,
   correlation-adjusted sizing).
4. **Monitor** open positions continuously and exit per per-strategy rules.
5. **Learn** — a nightly mechanical job (`jobs/bucket_metrics.py`) computes realized
   per-bucket/per-rule performance and posts advisory sizing-up/halting verdicts to
   Discord (verdicts alert, they never act); humans approve
   `paper → limited_live → scaled_live` promotions through the admin API. (The
   earlier LLM-driven `MetaSearchAgent` that auto-generated solver variants was
   deleted — see `CHANGELOG.md`, "Delete the LLM solver-evolution machinery".)

## Sub-system Scope

| Concern | Owner module | Notes |
|---|---|---|
| Real-time WS ingestion | `orion.ingestion` | Bronze writer + normalizer + deduper |
| Feature enrichment | `orion.main_feature_enrichment` | Silver → Gold features |
| Candidate generation | `orion.processing.rule_engine` | UW-flow rules → CandidateTrade |
| Signal scoring | `orion.processing.signal_engine` | Regime filter + ML scorer + solver vote |
| Order placement | `orion.execution.execution_engine` | Options-only; routes via Gateway |
| Position monitoring | `orion.main_position_monitor` | Continuous exit-rule evaluation |
| EOD close-of-books | fired nightly by `orion.ingestion` | Realizes expired positions + P&L reconciliation (replaced the old LLM `EODReviewAgent`) |
| Bucket performance review | `orion.jobs.bucket_metrics` | Nightly mechanical per-bucket/rule metrics, advisory-only (replaced `orion.agents` + `orion.main_meta`, both removed) |
| Admin API | `orion.api.main` | FastAPI on port 8000 — see [`api-reference.md`](api-reference.md) |
| Triple-barrier labels | `orion.labeler` | For ML training |

## Position in the Empire Monorepo

```
Data-Gateway (WS+REST, port 8080)  ←──── UW, Alpaca
        │
        ▼
   ORION (ingest → signal → execute → monitor)
        │              ▲
        ▼              │
  TimescaleDB     Heber lakehouse (host parquet cache)
        │
        ▼
  EmpireUI / Athena (post-trade analysis)
```

Orion is one of several Empire trading systems sharing the same Alpaca account.
Position attribution uses an `orion_` `client_order_id` prefix and a `system`
column on `OrderRecord` — never weaken that filter, or you'll mistake Cerberus /
3Roses / Kairos positions for Orion's. See
[`code-standards.md`](code-standards.md#position-attribution).

## Runtime Stages

`ORION_STAGE` controls posture:

| Stage | Default? | Real orders? | Used for |
|---|---|---|---|
| `test` | tests only | no | pytest in-memory SQLite |
| `paper` | **default** | paper account only | dev, shadow runs, default native/Docker |
| `live` | opt-in | real money | scaled solvers only — humans approve via API |

The default must remain `paper`. Order submission code defends this in
multiple layers (config default, env wrapper, RiskManager guard).

## Solver Lifecycle

Solvers are parameterized strategy configs ("DNA") stored in the `solvers` table.
Each transitions through:

```
research → shadow → paper → limited_live → scaled_live
```

- New solver variants are currently authored by hand and seeded (`scripts/seed_solvers.py`);
  the `MetaSearchAgent` that used to auto-propose variants via LLM-guided mutation was
  deleted (see `CHANGELOG.md`).
- `research → shadow → paper` and `paper → limited_live → scaled_live` all require human
  approval through `POST /promotions/{id}/approve` (see [`api-reference.md`](api-reference.md));
  nothing promotes a solver automatically.

## Where to go next

- **High-level architecture & data flow:** [`system-architecture.md`](system-architecture.md)
- **Module-by-module tour:** [`codebase-summary.md`](codebase-summary.md)
- **How to run / operate:** [`deployment-guide.md`](deployment-guide.md)
- **Env-var matrix:** [`configuration-guide.md`](configuration-guide.md)
- **HTTP endpoints:** [`api-reference.md`](api-reference.md)
- **Tests:** [`testing-guide.md`](testing-guide.md)
- **Coding rules:** [`code-standards.md`](code-standards.md)
- **Older / deeper docs (preserved):** `ARCHITECTURE.md`, `DATABASE_SCHEMA.md`,
  `DATA_CONTRACTS.md`, `DATA_RETENTION.md`, `RUNBOOK.md`, `RUNBOOKS.md`,
  `ROLLBACK.md`, `alerting.md`, `disaster_recovery_runbook.md`, plus
  `runbooks/` and `rca/`.
- **Full PRD:** [`../PRD.md`](../PRD.md).
