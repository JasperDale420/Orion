# Orion — Codebase Summary

Map of `src/orion/` as it exists today. All paths are relative to that root
unless noted. For the runtime data flow that ties these together, see
[`system-architecture.md`](system-architecture.md).

## Top-level entrypoints (`src/orion/main_*.py`)

| Module | Role | Run by |
|---|---|---|
| `main_execution.py` | Signal → preflight → order submission loop | launchd `com.empire.orion.execution`; Docker fallback via profile `docker` |
| `main_feature_enrichment.py` | Silver → Gold features | docker compose `feature_enrichment` |
| `main_position_monitor.py` | Continuous exit-rule evaluation on open positions | launchd `com.empire.orion.position-monitor`; Docker fallback via profile `docker` |
| `main_data_quality.py` | Scheduled data-quality probe | launchd `com.empire.orion.data-quality`; Docker fallback via profile `docker` |

Ingestion has its own `python -m orion.ingestion` entrypoint
(`ingestion/__main__.py`), run natively by `com.empire.orion.ingestion` and
in Docker by the `ingestion` service.

## Package layout (`src/orion/`)

| Package | Purpose |
|---|---|
| `api/` | FastAPI admin API — solvers, metrics, retained experiment records, flows, rollups, dashboard, and circuit-breaker controls. See [`api-reference.md`](api-reference.md). Auth via `x-api-key` (`ORION_API_KEY`). |
| `analysis/` | Multi-axis regime detection (vol/vix/trend/risk/session), cross-validation, evaluation metrics. |
| `clients/` | `HeberReader` (parquet, predicate-pushdown, negative-cache for empty Gold datasets (300s TTL), single-threaded reads for SIGABRT safety) and `GatewayTradingClient` (Alpaca proxy). |
| `connectors/` | `GatewayStreamClient` (WebSocket bars/quotes), UW connectors: greek exposure, IV rank, market tide, max pain, VIX proxy. |
| `core/` | Circuit breaker, solver DSL/router/validation, universe manager, health monitor, PnL tracker, service-lease guard, market schedule, and promotion rules. |
| `enrichment/` | Helpers for feature-enrichment job. |
| `execution/` | Execution engine, risk subpackage, position manager/monitor, rate limiter, correlation adjuster, signal preflight, fill processor, attribution. **Safety-critical.** |
| `ingestion/` | Real-time WS ingestion service (Gateway WS → Bronze → Silver → candidates). |
| `jobs/` | Operational jobs: close-of-books reconciliation and bucket metrics, backfills, quality checks, DLQ handling, Gateway probes, dead-man/launchd health, daily dashboard reset, rollups, and feature validation. |
| `labeler/` | Triple-barrier labeling: checkpoint, greeks, schema guard. |
| `ml/` | LightGBM scorer, exit classifier, feature store, model registry, and derived/darkpool features. |
| `processing/` | FeatureEngine, SignalEngine, RuleEngine, backtest engine, normalizer, rollup builder, deduper. |
| `shared/` | `setup_struct_logger`, Prometheus metrics, DB utils, decorators, DLQ utils. |
| `storage/` | SQLAlchemy ORM models, async engine/session, lakehouse writer, watermark store. |
| `scripts/` | Repo-internal utility scripts (note: top-level `scripts/` is the operator-facing set; see [`deployment-guide.md`](deployment-guide.md)). |

## Key modules to read first

When onboarding, read these in order to understand the loop:

1. **`config.py`** — The three Settings classes (`SystemSettings`, `RiskSettings`,
   `HeuristicWeights`). Single source of truth for env-vars;
   see [`configuration-guide.md`](configuration-guide.md).
2. **`ingestion/service.py`** — Long-running ingestion loop. Acquires
   `service_lease_ingestion`, drains Gateway WS, writes Bronze, normalizes,
   dedupes, writes Silver. After processing, `FeatureEngine` hydrates from
   Heber on cold-start (5-day bars), maintains an LRU store capped at 500
   tickers, and computes RSI(14) and SMA(20) via pandas_ta.
3. **`processing/rule_engine.py`** — Maps SilverSignals to `CandidateTrade`s via
   named rules (`BullishSweep`, `BearishPutPressure`, `ZeroDTESweep`,
   `SwingEntry`, `ShortSwingEntry`).
4. **`processing/signal_engine.py`** — Regime filter → LightGBM pre-filter →
   `SolverRouter` weighted vote → `StrategyDecision` (EXECUTE / SKIP, with full
   trace).
5. **`execution/execution_engine.py`** — `ExecutionEngine` (options-only). Owns
   `ORDER_ID_PREFIX = "orion_"` — never weaken; it is the only thing that keeps
   Orion from grabbing other systems' positions.
6. **`execution/risk/manager.py`** — Daily loss limit,
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
- **HTTP:** use `empire_core.http_client` so retries and structured error logs
  stay consistent.
- **DB:** `storage/db.py` exposes `async_session()` and `configure_db()`. Async
  engine uses `pool_pre_ping=True`, `pool_recycle=1800`,
  `expire_on_commit=False`. pgvector enabled at init.
- **Launchd health:** `jobs/launchd_health_probe.py` runs every minute. It detects
  exit 127 and missing required daemons (execution, ingestion, position-monitor,
  data-quality), with a one-hour Discord dedupe window per `(label, exit_code)`.
  Run by `com.empire.orion.launchd-health`.
- **Service lease:** `core/service_lease.py` is Orion's single-instance guard.
  Each long-running entrypoint (`ingestion`, `main_execution`,
  `main_position_monitor`, `main_data_quality`) claims a row in `SystemStatus` keyed by
  `ORION_LEASE_OWNER_ID` (TTL `SERVICE_LEASE_STALE_SECONDS=120`). This is what
  prevents the native (`*_native`) and docker (`*_compose`) versions from
  trampling each other.

## Top-level files of note

| File | Purpose |
|---|---|
| `pyproject.toml` | uv-managed; `name = "orion"`; pkg lives at `src/orion`. Defines pytest markers (`unit`, `integration`, `e2e`, `slow`), strict-mypy overrides, ruff config (extends `ruff-base.toml`). |
| `Makefile` | Wraps common targets. |
| `docker-compose.yml` | Default support services plus profile-gated `docker` copies of the four native roles. |
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
