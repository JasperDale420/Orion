# Repository AI Instructions

This file is shared by Claude Code and Codex. Follow every instruction here regardless of which agent is active.

## Primary repository guidance

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

Docker (TimescaleDB + support services):
```bash
docker compose up timescaledb -d                          # DB only
docker compose up -d                                      # default profile: timescaledb, feature_enrichment, heber-sync
docker compose --profile docker up -d                      # also run ingestion/execution/position-monitor/data-quality in Docker
                                                            # (native launchd is canonical for these — never run both copies of a role)
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
| `api/` | FastAPI admin API (solvers, metrics, experiments, flows, rollups, dashboard, admin/circuit-breaker) |
| `analysis/` | Regime detection (multi-axis: vol, vix, trend, risk, session), cross-validation, metrics |
| `clients/` | Heber reader (parquet), Gateway trading client (Alpaca proxy) |
| `connectors/` | Gateway WebSocket stream client, UW data connectors (greek exposure, IV rank, market tide, max pain, VIX proxy) |
| `core/` | Circuit breaker, feature registry, solver DSL/router/validation, universe manager, health monitor, PnL tracker |
| `enrichment/` | Heber-context enrichment helper used by feature enrichment |
| `execution/` | Execution engine, risk manager (`execution/risk/`), position manager/monitor, rate limiter, correlation adjuster, signal preflight |
| `ingestion/` | Real-time ingestion service (Gateway WS -> Bronze -> Silver -> candidates) |
| `jobs/` | Scheduled jobs: nightly backfill, quality guardrails, DLQ consumer, data quality checker, gateway contract probe, bucket metrics, deadman watchdog |
| `labeler/` | Triple-barrier labeling (checkpoints, greeks, schema guard) |
| `ml/` | LightGBM scorer, exit classifier, feature store, model registry, darkpool features |
| `processing/` | Feature engine, signal engine, rule engine, backtest engine, normalizer, rollup builder, deduper |
| `scripts/` | Ad-hoc audit/debug scripts (`audit_gold.py`, `audit_silver.py`, `check_pgvector.py`, `query_events.py`) |
| `shared/` | Logger, metrics (Prometheus), DB utils, decorators, DLQ utils |
| `storage/` | SQLAlchemy models, DB engine/session, lakehouse writer, watermarks |

`agents/` (meta-search/EOD-review/weekly-aggregator agents) and `rag/` (embeddings + pgvector search) no longer exist under `src/orion/` — the LLM solver-evolution machinery they held was deleted; see `CHANGELOG.md` ("Delete the LLM solver-evolution machinery"). The vendored `unusualwhales/` client is also gone (no `CHANGELOG.md` entry found for it, so its removal isn't dated here).

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

Solvers are parameterized strategy configurations ("DNA") stored in the `solvers` table. Lifecycle stages: `research → shadow → paper → limited_live → scaled_live`, moved via the `/promotions/{id}/approve|reject` API (manual review, not automatic).

The LLM-driven solver generator (MetaSearchAgent: mutation, backtesting, auto-proposed promotions) was deleted — see `CHANGELOG.md` ("Delete the LLM solver-evolution machinery"). Its replacement is mechanical: `jobs/bucket_metrics.py` computes nightly per-bucket/per-rule realized performance (win rate, expectancy, profit factor, exit-reason mix) and posts advisory sizing-up/halting verdicts to Discord — the verdicts alert, they never act automatically.

Key models still in use: `SolverConfig` (Pydantic DSL with risk, features, exit logic), `SolverMetrics`, `PromotionRecommendation`. `MetaExperiment` and `SolverEdits` remain as DB models but nothing currently writes to them — the jobs that populated them (`solver_promoter.py`, `gatekeeper.py`, `run_meta_loop.py`) were removed with the meta-search machinery.

### Docker Compose Services

launchd is canonical for the ingestion/execution roles; the `docker`-profile
copies below are the rollback path (never run both for the same role — the
service-lease guard rejects whichever starts second). `docker-compose.yml`
now defines exactly seven services — the old `legacy-labels`/`tools`/`scheduled`
profiles and the meta-search/labeling/indexer/MCP services they gated were
removed along with the LLM solver-evolution machinery (see `CHANGELOG.md`).

| Service | Module | Profile |
|---------|--------|---------|
| `timescaledb` | `pgvector/pgvector:pg16` image | default |
| `feature_enrichment` | `orion.main_feature_enrichment` | default |
| `heber-sync` | alpine rsync sidecar (Heber cache) | default |
| `ingestion` | `orion.ingestion` | docker (native launchd canonical) |
| `execution` | `orion.main_execution` | docker (native launchd canonical) |
| `position-monitor` | `orion.main_position_monitor --interval 60` | docker (native launchd canonical) |
| `data-quality` | `orion.main_data_quality --scheduled` | docker (native launchd canonical) |

## TimescaleDB

Default connection: `postgresql+asyncpg://orion@localhost:5432/orion_db` (Docker maps port 5440:5432).

Async engine via SQLAlchemy `create_async_engine` with `pool_pre_ping=True`, `pool_recycle=1800`. Session factory: `async_sessionmaker(expire_on_commit=False)`. The DB image is `pgvector/pgvector:pg16` for historical reasons (it backed the now-deleted RAG embeddings); no current model uses the `Vector` column type (`scripts/check_pgvector.py` is the only remaining reference).

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
| `meta_experiments` / `solver_edits` | Meta-search experiment tracking (models still exist; nothing writes to them since the meta-search machinery was deleted) |
| `promotion_recommendations` | Stage promotion workflow (now populated/approved manually, not by an LLM agent) |
| `positions` / `fills` / `orders` | Execution state |
| `risk_snapshots` | Risk manager state persistence |
| `signal_live` | Live signal tracking |
| `trade_journal_entries` | Trade journal |
| `dead_letter_queue` | Failed event processing |
| `audit_log` | API request audit trail |
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

All config via Pydantic Settings in `src/orion/config.py`. Three settings classes:

- **`SystemSettings`** (`ORION_*` prefix) — DB URL, Gateway URL, Heber paths, universe, ML model dir/staleness policy, circuit breaker, broker-routing scaffolding, metrics
- **`RiskSettings`** (`ORION_RISK_*` prefix) — Daily loss limits, position limits, Greeks limits, sector concentration, 0DTE winddown, correlation sizing, fixed-premium sizing, per-bucket/per-underlying caps, option liquidity gates
- **`HeuristicWeights`** (`ORION_HEURISTIC_*` prefix) — Score increments for the heuristic scorer fallback (premium tiers, sweep/ask-side bonuses, vol/OI, low-premium penalty)

(`MetaSearchSettings` and `AgentSettings` — LLM model / AI-Gateway config — were deleted with the meta-search/agent machinery; see `CHANGELOG.md`.)

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

- **RiskManager** (`execution/risk/manager.py`) — Daily loss limits, max positions, Greeks limits, sector concentration, 0DTE winddown, correlation-adjusted sizing
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

---

## Karpathy Coding Guidelines

_Source: [andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills) — behavioral guidelines to reduce common LLM coding mistakes. Bias toward caution over speed; for trivial tasks, use judgment._

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Data Analysis Review

Any data-analysis conclusion — backtest results, strategy-performance claims, Optuna/WFO output, dataset QA, or other statistical/quantitative findings — must be adversarially reviewed before it reaches the user. Challenge the methodology: overfitting, look-ahead/leakage, cherry-picked windows, confounds, unsupported causal claims. Not a proofread pass.

**Reviewer:** `/codex:adversarial-review` with `gpt-5.6-terra` at high reasoning effort, run synchronously with the claim plus its method/data scope. **Fallback** on rate-limit, timeout, auth error, or empty/errored output: `glm-5.2` via opencode (`opencode run -m zai-coding-plan/glm-5.2`), same instructions. These ids are approved policy — don't substitute them; if one is deprecated or unreachable, stop and ask the user, never swap silently. This reviewer + fallback is the single source of truth for both blocks. **If every reviewer is unavailable, do not present the conclusion — stop and surface it to the user. Never silently skip.**

Report the review's findings verbatim alongside the analysis, with your disposition on each — the user, not you, judges what is "material." Withhold or qualify any conclusion the review invalidates.

## Adversarial Review of Code Changes & Plans

Same reviewer, fallback, and "all reviewers down" rule as Data Analysis Review. Run it synchronously and read the result before continuing (overrides any "spawn then stop" default). Give the reviewer the task/acceptance criteria AND the artifact — a concrete plan or the exact diff; if the tool only sees the diff, paste the requirement in so it reviews intent, not just lines.

**Required** (reviewed once, at the highest-leverage point — the plan for multi-step work, the diff otherwise): changes to logic, control flow, schemas, cross-repo contracts, any edit beyond a truly trivial one, any multi-step plan before executing it, and anything safety-critical — order submission, risk limits, position sizing, paper/live toggles, credentials, kill switches, broker auth/cancel paths (non-exhaustive). Safety-critical always requires it.

**Exempt / don't loop:** comment-, doc-, or format-only edits, renames, single-line non-logic changes. Don't re-review the reviewer's own output — but divergence from a reviewed plan is re-reviewable, and new issues a fix introduces are reviewable. A reviewer you believe is wrong gets escalated to the user, not silently overridden.

**Then act on it:** rank findings by severity and report all of them verbatim with your disposition. Critical/high findings must be fixed or stop the work; never commit a safety-critical change carrying an unresolved finding, and never self-classify a safety-critical finding as immaterial — escalate it. The user judges "material."

## Additional repository guidance

The guidance below was retained from the prior `AGENTS.md`. If it conflicts with the primary guidance above, follow the primary guidance.

Project-specific AI agent instructions for Orion.

## Project Overview

Orion is a real-time trading backend in the Empire ecosystem. It ingests market data from Unusual Whales and Alpaca, stores it in Bronze/Silver/Gold layers, generates signals, and runs execution/risk workflows.

## Architecture

Orion follows a vertical-slice backend design under `src/orion/`:
- `api/`: FastAPI admin and operational endpoints
- `ingestion/` + `processing/`: event ingestion, normalization, feature/rule pipelines
- `core/`: orchestration, solvers, safety checks, shared domain logic
- `execution/`: preflight checks, risk checks, and order submission paths
- `storage/`: SQLAlchemy models for Bronze/Silver/Gold and related state tables

Key runtime services are launched by `main_*` entry points: `main_execution.py`, `main_feature_enrichment.py`, `main_position_monitor.py`, `main_data_quality.py` (ingestion runs via `python -m orion.ingestion`, not a `main_*` file).

## Development Commands

```bash
# Install dependencies
uv sync

# Run tests / lint / type checks
uv run pytest -q
ruff check .
mypy .

# Run support services in Docker (see "## Commands" above for the full picture)
docker compose up -d

# Run a specific service locally
uv run python -m orion.ingestion
uv run python -m orion.main_execution

# Database migrations
uv run alembic upgrade head
uv run alembic downgrade -1
```

## Key Patterns

- Keep business logic in slice/domain modules, not in transport layers.
- Use `structlog` from Orion logger modules for structured logging.
- Use explicit exception handling with context-rich logs at service boundaries.
- Treat idempotency and deterministic identifiers as first-class concerns.
- Preserve paper/safe defaults for execution paths unless explicitly requested otherwise.

## Important Files

- `/Users/jacobmcmillan/Empire/Orion/src/orion/config.py` — environment-driven system/risk/heuristic-weight settings
- `/Users/jacobmcmillan/Empire/Orion/src/orion/api/main.py` — FastAPI endpoints
- `/Users/jacobmcmillan/Empire/Orion/src/orion/ingestion/__main__.py` — ingestion entry point
- `/Users/jacobmcmillan/Empire/Orion/src/orion/main_execution.py` — execution entry point
- `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml` — multi-service local deployment

## Testing

- Test framework: `pytest`
- Preferred command: `uv run pytest -q`
- Keep tests deterministic (no live network in unit tests).
- For behavior changes, write/adjust tests before implementation and keep them as regression coverage.

## Common Pitfalls

- Mixing legacy and new ingestion/data-access paths during migration work.
- Missing env vars causing startup/runtime failures.
- Accidentally enabling live trading modes instead of paper defaults.
- Allowing schema drift between producer/consumer contracts.
