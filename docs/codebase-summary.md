# Orion — Codebase Summary

Map of `src/orion/` as it exists today. All paths are relative to that root
unless noted. For the runtime data flow that ties these together, see
[`system-architecture.md`](system-architecture.md).

## Top-level entrypoints (`src/orion/main_*.py`)

| Module | Role | Run by |
|---|---|---|
| `main_execution.py` | Signal → preflight → order submission loop | launchd `com.empire.orion.execution` + `docker compose up execution` |
| `main_feature_enrichment.py` | Silver → Gold features | docker compose `feature_enrichment` |
| `main_position_monitor.py` | Continuous exit-rule evaluation on open positions | docker compose `position-monitor` |
| `main_eod.py` | LLM-driven end-of-day review | docker compose `eod-agent` |
| `main_meta.py` | Solver evolution (LLM mutation + backtest) | `tools` profile only |
| `main_meta_weekly.py` | Weekly meta-aggregator | `scheduled` profile |
| `main_data_quality.py` | Standalone data-quality probe | ad-hoc |
| `main_option_quote_tracker.py` | Snapshot option quotes for labeling | `legacy-labels` profile |
| `main_pattern_miner.py` | Mine darkpool / flow patterns | `legacy-labels` profile |
| `main_price_target_labeler.py` | Triple-barrier label producer — **archived 2026-06-10** (`archive/2026-06-10_price-target-labeler/`); live feature-extraction helpers moved to `orion.labeler.feature_extraction` | removed |

Ingestion has its own `python -m orion.ingestion` entrypoint
(`ingestion/__main__.py`), run natively by `com.empire.orion.ingestion` and
in Docker by the `ingestion` service.

## Package layout (`src/orion/`)

| Package | Purpose |
|---|---|
| `api/` | FastAPI admin API — solvers, metrics, experiments, flows, rollups, dashboard, RAG search. See [`api-reference.md`](api-reference.md). Auth via `x-api-key` (`ORION_API_KEY`). |
| `agents/` | LLM agents: EOD review, MetaSearch (solver DNA evolution), weekly aggregator. |
| `analysis/` | Multi-axis regime detection (vol/vix/trend/risk/session), cross-validation, evaluation metrics. |
| `clients/` | `HeberReader` (parquet, predicate-pushdown), `GatewayTradingClient` (Alpaca proxy), TradingRAG client, MCP server. |
| `connectors/` | `GatewayStreamClient` (WebSocket bars/quotes), UW connectors: greek exposure, IV rank, market tide, max pain, VIX proxy. |
| `core/` | Circuit breaker, feature flags, solver DSL/router/validation, universe manager, health monitor, PnL tracker, service-lease guard, market schedule, drift trigger, promotion rules. |
| `enrichment/` | Helpers for feature-enrichment job. |
| `execution/` | Execution engine, risk subpackage, position manager/monitor, rate limiter, correlation adjuster, signal preflight, fill processor, attribution. **Safety-critical.** |
| `ingestion/` | Real-time WS ingestion service (Gateway WS → Bronze → Silver → candidates). |
| `jobs/` | Scheduled jobs: nightly backfill, quality guardrails, DLQ consumer, data-quality checker, Gateway contract probe, meta loop, daily dashboard reset. |
| `labeler/` | Triple-barrier labeling: checkpoint, greeks, schema guard. |
| `ml/` | LightGBM scorer, exit classifier, drift monitor, feature store, pattern miner, model registry, darkpool features. |
| `processing/` | FeatureEngine, SignalEngine, RuleEngine, backtest engine, normalizer, rollup builder, deduper. |
| `rag/` | Embeddings, pgvector store, indexer (trade-knowledge RAG). |
| `shared/` | `setup_struct_logger`, Prometheus metrics, DB utils, decorators, DLQ utils. |
| `storage/` | SQLAlchemy ORM models, async engine/session, lakehouse writer, watermark store. |
| `scripts/` | Repo-internal utility scripts (note: top-level `scripts/` is the operator-facing set; see [`deployment-guide.md`](deployment-guide.md)). |

## Key modules to read first

When onboarding, read these in order to understand the loop:

1. **`config.py`** — All four Settings classes (`SystemSettings`, `RiskSettings`,
   `MetaSearchSettings`, `AgentSettings`). Single source of truth for env-vars;
   see [`configuration-guide.md`](configuration-guide.md).
2. **`ingestion/service.py`** — Long-running ingestion loop. Acquires
   `service_lease_ingestion`, drains Gateway WS, writes Bronze, normalizes,
   dedupes, writes Silver.
3. **`processing/rule_engine.py`** — Maps SilverSignals to `CandidateTrade`s via
   named rules (`BullishSweep`, `BearishPutPressure`, `ZeroDTESweep`,
   `SwingEntry`, `ShortSwingEntry`).
4. **`processing/signal_engine.py`** — Regime filter → LightGBM pre-filter →
   `SolverRouter` weighted vote → `StrategyDecision` (EXECUTE / SKIP, with full
   trace).
5. **`execution/execution_engine.py`** — `ExecutionEngine` (options-only). Owns
   `ORDER_ID_PREFIX = "orion_"` — never weaken; it is the only thing that keeps
   Orion from grabbing other systems' positions.
6. **`execution/risk_manager.py`** (under `execution/risk/`) — Daily loss limit,
   max positions, Greeks limit, sector concentration, 0DTE winddown,
   correlation-adjusted sizing.
7. **`execution/position_monitor.py`** — Continuous exit loop.
8. **`api/main.py`** — Single-file FastAPI app with ~30 endpoints across solvers,
   metrics, experiments, candidates, rollups, flows, dashboard, admin.

## Cross-cutting infrastructure

- **Logging:** `shared/logger.py` is a thin shim over `empire_core.logger`. Use
  `setup_struct_logger(name)`; never call `structlog.configure()` directly.
  Maps `ORION_LOG_*` env vars onto `EMPIRE_LOG_*`. Auto-injects `run_id`.
- **Errors:** `core/errors.py` defines `OrionError(EmpireError)` and subclasses
  `ProviderError`, `StorageError`, `ExecutionError`, `ModelInferenceError`,
  `FeatureComputationError`. Error codes in `ErrorCode` enum.
- **HTTP:** wrap Gateway calls with `core.http_client` (`empire_core` shim) so
  retries + structured error logs are consistent.
- **DB:** `storage/db.py` exposes `async_session()` and `configure_db()`. Async
  engine uses `pool_pre_ping=True`, `pool_recycle=1800`,
  `expire_on_commit=False`. pgvector enabled at init.
- **Service lease:** `core/service_lease.py` is Orion's single-instance guard.
  Each long-running entrypoint (`ingestion`, `main_execution`,
  `main_position_monitor`) claims a row in `SystemStatus` keyed by
  `ORION_LEASE_OWNER_ID` (TTL `SERVICE_LEASE_STALE_SECONDS=120`). This is what
  prevents the native (`*_native`) and docker (`*_compose`) versions from
  trampling each other.

## Top-level files of note

| File | Purpose |
|---|---|
| `pyproject.toml` | uv-managed; `name = "orion"`; pkg lives at `src/orion`. Defines pytest markers (`unit`, `integration`, `e2e`, `slow`), strict-mypy overrides, ruff config (extends `ruff-base.toml`). |
| `Makefile` | Wraps common targets. |
| `docker-compose.yml` | Profiles: default, `legacy-labels`, `tools`, `scheduled`. |
| `Dockerfile` | Production image. |
| `alembic.ini` + `alembic/` | DB migrations. |
| `config/regime_risk.yaml` | Regime → risk-multiplier mapping. |
| `ledger.db` (+ `-wal`, `-shm`) | SQLite ledger (do not delete or commit). |
| `models/` | Trained model artifacts. |
| `proposals/` | LLM-generated solver proposals. |
| `CHANGELOG.md` | Behavioural-change log; required for every PR. |
| `CLAUDE.md` | Authoritative guidance for Claude Code agents. |
| `PRD.md` | Full product requirements. |

## Archived areas (do not edit without explicit ask)

- `archive/` — frozen waves (gateway-heber migration, label-stack, runtime
  consolidation). Reference only.
- `qlib-main/` — vendored Qlib snapshot.
- `predict/` — historical RCAs and post-mortems.
