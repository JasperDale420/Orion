# CLAUDE.md

Orion is a real-time data lake and signal engine for options trading. It ingests live market data via Data-Gateway, generates trading signals through a solver ensemble, and executes options orders through Alpaca (via Data-Gateway proxy). Uses TimescaleDB (PostgreSQL) for storage and Heber for Silver/Gold parquet reads.

## Commands

```bash
uv sync                          # install deps
uv run pytest                    # all tests
uv run pytest tests/unit         # unit tests only
uv run pytest tests/integration  # integration tests
uv run pytest tests/e2e          # end-to-end tests
uv run pytest -m unit            # by marker
uv run pytest -k "test_name"     # single test by name
ruff check .                     # lint
ruff format .                    # format
mypy .                           # type check (strict, excludes archive/qlib-main/scripts/tests/alpaca)
```

E2E pipeline verification (requires TimescaleDB running on port 5440):
```bash
uv run pytest tests/e2e/test_smoke_e2e.py -v -s          # 9-stage pipeline smoke test
uv run pytest tests/e2e/test_live_data_flow.py -v -s      # live data flow health check
uv run python tests/e2e/test_live_data_flow.py             # standalone diagnostic
```

Docker (TimescaleDB + all services):
```bash
docker compose up timescaledb -d                          # DB only
docker compose up -d                                      # core services
docker compose --profile legacy-labels up -d              # include legacy labeling
docker compose --profile tools up -d                      # include meta-search
```

Alembic migrations:
```bash
uv run alembic upgrade head      # apply migrations
uv run alembic revision --autogenerate -m "description"
```

## Architecture

### Package Layout (`src/orion/`)

| Package | Purpose |
|---------|---------|
| `api/` | FastAPI admin API (solvers, metrics, experiments, flows, rollups, dashboard, RAG search) |
| `agents/` | LLM-powered agents: EOD review, meta-search (solver evolution), weekly aggregator |
| `analysis/` | Regime detection (multi-axis: vol, vix, trend, risk, session), cross-validation, metrics |
| `clients/` | Heber reader (parquet), Gateway trading client (Alpaca proxy), MCP server, TradingRAG |
| `connectors/` | Gateway WebSocket stream client, UW data connectors (greek exposure, IV rank, market tide, max pain, VIX proxy) |
| `core/` | Circuit breaker, feature flags, solver DSL/router/validation, universe manager, health monitor, PnL tracker |
| `execution/` | Execution engine, risk manager, position manager/monitor, rate limiter, correlation adjuster, signal preflight |
| `ingestion/` | Real-time ingestion service (Gateway WS -> Bronze -> Silver -> candidates) |
| `jobs/` | Scheduled jobs: nightly backfill, quality guardrails, DLQ consumer, data quality checker, gateway contract probe, meta loop |
| `labeler/` | Triple-barrier labeling (checkpoints, greeks, schema guard) |
| `ml/` | LightGBM scorer, exit classifier, drift monitor, feature store, pattern miner, model registry, darkpool features |
| `processing/` | Feature engine, signal engine, rule engine, backtest engine, normalizer, rollup builder, deduper |
| `rag/` | Embeddings, vector store (pgvector), indexer for trade knowledge |
| `shared/` | Logger, metrics (Prometheus), DB utils, decorators, DLQ utils |
| `storage/` | SQLAlchemy models, DB engine/session, lakehouse writer, watermarks |
| `unusualwhales/` | Vendored UnusualWhales API client |

### Data Pipeline

```
Data-Gateway (WS :8080)
    ↓ GatewayStreamClient
Bronze (BronzeEvent in TimescaleDB)
    ↓ NormalizationEngine + DeduplicationEngine
Silver (SilverSignal in TimescaleDB)
    ↓ FeatureEngine + RuleEngine
Gold (CandidateTrade → SignalEngine → StrategyDecision)
    ↓ ExecutionEngine (via Gateway → Alpaca)
Orders + PositionMonitor
```

### Signal Pipeline

1. **RuleEngine** — Evaluates flow rules (BullishSweep, BearishPutPressure, ZeroDTESweep, SwingEntry, ShortSwingEntry) against SilverSignals to produce CandidateTrades
2. **SignalEngine** — Applies multi-axis regime filter, ML pre-filter (LightGBM scorer), then routes to Solver ensemble
3. **SolverRouter** — Selects active solvers for context (ticker, regime, stage); weighted consensus vote by info_ratio
4. **ExecutionEngine** — Pre-trade risk checks, order submission via Data-Gateway trading client

### Solver System (PRDv2)

Solvers are parameterized strategy configurations ("DNA") stored in the `solvers` table. Lifecycle stages: `research → shadow → paper → limited_live → scaled_live`. The MetaSearchAgent generates solver variants via LLM-guided mutation, backtests them, and proposes promotions.

Key models: `SolverConfig` (Pydantic DSL with risk, features, exit logic), `SolverMetrics`, `MetaExperiment`, `SolverEdits`, `PromotionRecommendation`.

### Docker Compose Services

| Service | Module | Profile |
|---------|--------|---------|
| `timescaledb` | TimescaleDB:latest-pg16 | default |
| `ingestion` | `orion.ingestion` | default |
| `feature_enrichment` | `orion.main_feature_enrichment` | default |
| `execution` | `orion.main_execution` | default |
| `position-monitor` | `orion.main_position_monitor` | default |
| `eod-agent` | `orion.main_eod` | default |
| `indexer` | `orion.rag.indexer` | default |
| `mcp-server` | Shared-MCP-Server | default |
| `price_target_labeler` | `orion.main_price_target_labeler` | legacy-labels |
| `pattern-miner` | `orion.main_pattern_miner` | legacy-labels |
| `nightly-backfill` | `orion.jobs.nightly_backfill` | legacy-labels |
| `quality-guardrails` | `orion.jobs.quality_guardrails` | legacy-labels |
| `option_quote_tracker` | `orion.main_option_quote_tracker` | legacy-labels |
| `meta-search` | `orion.main_meta` | tools |
| `meta-weekly` | `orion.main_meta_weekly` | scheduled |
| `dashboard-reset` | `orion.jobs.daily_dashboard_reset` | scheduled |

## TimescaleDB

Default connection: `postgresql+asyncpg://orion@localhost:5432/orion_db` (Docker maps port 5440:5432).

Async engine via SQLAlchemy `create_async_engine` with `pool_pre_ping=True`, `pool_recycle=1800`. Session factory: `async_sessionmaker(expire_on_commit=False)`. pgvector extension enabled on init for RAG embeddings.

### Key Tables

| Table | Purpose |
|-------|---------|
| `bronze_events` | Raw ingested events (UW flow, Alpaca bars) |
| `silver_signals` | Normalized, deduplicated signals |
| `candidate_trades` | Gold-layer trade candidates from rule engine |
| `strategy_decisions` | Signal engine decisions (EXECUTE/SKIP with trace) |
| `exit_decisions` | Exit rule triggers with P&L tracking |
| `gold_ticker_rollup` | Aggregated OHLCV bars (5m, 1h, 1d) |
| `gold_feature_events` | Point-in-time feature vectors for ML |
| `candidate_labels` / `labels_event` / `labels_window` | Triple-barrier labels |
| `solvers` | Strategy DNA (config, stage, performance snapshot) |
| `solver_metrics` / `solver_runs` | Evaluation results |
| `meta_experiments` / `solver_edits` | Meta-search experiment tracking |
| `promotion_recommendations` | Stage promotion workflow |
| `positions` / `fills` / `orders` | Execution state |
| `risk_snapshots` | Risk manager state persistence |
| `signal_live` | Live signal tracking |
| `trade_journal_entries` | Trade journal |
| `dead_letter_queue` | Failed event processing |
| `audit_log` | API request audit trail |
| `rag_documents` | Indexed RAG documents with pgvector embeddings |
| `system_status` / `ingest_watermarks` / `job_cursor_state` / `runtime_config` | System state |

## Data-Gateway Dependency

Orion depends on Data-Gateway for all external data and order routing:

- **Market data**: `GatewayStreamClient` connects to Gateway WebSocket
  (`ws://data-gateway:8080/ws` inside containers, `ws://localhost:8080/ws`
  from the host) for real-time bars. Containers reach Gateway via the
  external `data-gateway_default` network attached in `docker-compose.yml` —
  do not revert to `host.docker.internal:8080` (Docker Desktop DNS is flaky
  enough that we historically saw thousands of `[Errno -3] Temporary failure
  in name resolution` events).
- **UW connectors**: Greek exposure, IV rank, market tide, max pain, VIX proxy — all fetch via Gateway REST
- **Order execution**: `ExecutionEngine` routes through `GatewayTradingClient` which proxies to Alpaca
- **Earnings sync**: `sync_earnings` job fetches via Gateway

### Heber Dependency

Orion reads Silver/Gold parquet from a **host-side cache** at `~/.heber-cache/data`,
bind-mounted into containers as `/Volumes/heber/data` (read-only). The cache is
populated by the `heber-sync` sidecar in `docker-compose.yml`, which rsyncs:
- Silver (today + yesterday) for: flow_alerts, bars, darkpool, market_tide,
  greek_exposure, iv_rank, max_pain
- Gold (last 30 days, all `dataset=*/project=*/version=*/dt=*`)

`HeberReader` uses `pyarrow.parquet` for direct dataset reads; catalog health
is checked via HTTP (`http://localhost:8085/api/v1`).

If Orion's ML scorer or feature_enrichment is missing recent Gold data, check
`docker logs orion_heber_sync` — Gold partitions older than the cutoff or
missing entirely from `~/.heber-cache/data/gold/` mean the sync isn't running
or hasn't caught up.

## Configuration

All config via Pydantic Settings in `src/orion/config.py`. Four settings classes:

- **`SystemSettings`** (`ORION_*` prefix) — DB URL, Gateway URL, Heber paths, universe, ML, RAG, metrics
- **`RiskSettings`** (`ORION_RISK_*` prefix) — Daily loss limits, position limits, Greeks limits, sector concentration, 0DTE winddown, correlation sizing
- **`MetaSearchSettings`** (`ORION_META_*` prefix) — Scoring weights for solver evaluation
- **`AgentSettings`** (`ORION_AGENT_*` prefix) — LLM model, AI-Gateway URL/key

Key env vars (beyond `ORION_*` prefix):
- `DB_URL` / `ORION_DB_URL` — Database connection string
- `DATA_GATEWAY_URL` / `GATEWAY_URL` — Data-Gateway base URL
- `DATA_GATEWAY_API_KEY` / `GATEWAY_API_KEY` — Gateway auth
- `UW_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER`
- `HEBER_CATALOG_URL`, `HEBER_DATA_ROOT`
- `ORION_STAGE` — Runtime stage: `paper` (default), `live`, `test`

Regime risk multipliers configured in `config/regime_risk.yaml`.

## Test Setup

Tests use in-memory SQLite (`sqlite+aiosqlite:///:memory:`) via `conftest.py` which sets `DB_URL` and `ORION_STAGE=test` before any imports. `pytest-asyncio` with `asyncio_mode = "auto"`.

### Markers

- `unit` — fast, isolated, no I/O
- `integration` — real DB, file I/O, component interactions
- `e2e` — full system flow
- `slow` — tests >1s

### E2E Tests (Real TimescaleDB)

Two E2E tests connect to the real TimescaleDB (port 5440) instead of in-memory SQLite:

- **`test_smoke_e2e.py`** — Injects simulated data and verifies all 9 pipeline stages produce output: bronze → silver → features → rollups → rules → candidates → ML scoring → signal engine → execution (mocked broker). Always runs; proves pipeline logic works.
- **`test_live_data_flow.py`** — Queries the DB for the most recent row at each stage and reports freshness. During market hours, fails if any stage is stale/empty. Outside market hours, passes with diagnostic output.

Both tests override the conftest SQLite binding via `db.configure_db()` and restore it before yielding to prevent the autouse teardown from dropping real tables.

## Logging

`shared/logger.py` delegates to `empire_core.logger`. Maps `ORION_LOG_FORMAT` → `EMPIRE_LOG_FORMAT` and `ORION_LOG_DIR` → `EMPIRE_LOG_DIR`. Injects `run_id` into context on first call. Use `setup_struct_logger(name)` throughout.

## Error Handling

`OrionError` extends `EmpireError`. Subclasses: `ProviderError`, `StorageError`, `ExecutionError`, `ModelInferenceError`, `FeatureComputationError`. Error codes in `ErrorCode` enum. API returns standard Empire error envelope.

## Position Attribution (Shared Alpaca Account)

The Alpaca paper account is shared by multiple trading systems via Data-Gateway. Orion identifies its own orders and positions:

- **Order ID prefix**: All Orion orders use `client_order_id = "orion_" + uuid`. Defined as `ORDER_ID_PREFIX` in `execution/execution_engine.py`.
- **Position filtering**: `_sync_risk_from_gateway` queries the `orders` table for tickers with `orion_`-prefixed orders, then only loads those positions into the risk manager.
- **Fill validation**: `FillProcessor` skips any fill whose `client_order_id` doesn't start with `orion_`.
- **Schema**: `OrderRecord.system` column (default `"orion"`) for persistent attribution.

When modifying order submission or risk sync, preserve the `ORDER_ID_PREFIX` filtering to avoid counting other systems' positions.

## Safety-Critical Code

- **RiskManager** (`execution/risk_manager.py`) — Daily loss limits, max positions, Greeks limits, sector concentration, 0DTE winddown, correlation-adjusted sizing
- **ExecutionEngine** (`execution/execution_engine.py`) — Options-only (rejects candidates without `option_symbol`), routes through Data-Gateway
- Paper mode default (`ORION_STAGE=paper`, `ALPACA_PAPER=True`)
- Regime filter blocks trading in SHOCK/extreme VIX conditions
- Circuit breaker in `core/circuit_breaker.py`
- Kill switch via `drawdown_kill_switch` test coverage

## Commit & Changelog Discipline

- Commit often with small, atomic changes
- Update `CHANGELOG.md` for every behavioral change, bug fix, or feature
- Format: `## [Unreleased]` at top, entries grouped by `Added`, `Changed`, `Fixed`, `Removed`
- Write entries from user perspective
