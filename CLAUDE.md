# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Orion is a real-time options trading backend. It ingests market data from Unusual Whales and Alpaca, processes it through a Bronze/Silver/Gold medallion architecture on TimescaleDB, runs feature engineering and ML scoring, and executes trades via Alpaca. Currently in Research/Paper stage.

## Commands

```bash
# Testing
make test                    # Run all tests (pytest with --cov=src)
make test-unit               # Unit tests only (tests/unit/)
make test-integration        # Integration tests only (tests/integration/)
make test-eod                # E2E tests only (tests/e2e/)
make test-coverage           # Tests with HTML/XML coverage reports
pytest tests/unit/test_foo.py           # Single test file
pytest tests/unit/test_foo.py::test_bar # Single test function

# Linting
make lint                    # ruff check + black --check
ruff check . --fix           # Auto-fix lint issues
black .                      # Auto-format
pre-commit run --all-files   # Full suite: ruff, black, mypy, bandit, detect-secrets

# Database migrations
alembic upgrade head         # Apply all migrations
alembic revision --autogenerate -m "description"  # New migration

# Docker (local infra)
docker compose up timescaledb redpanda minio -d   # Start infrastructure
docker compose up execution -d                     # Start a specific service
```

## Test Environment

Tests use in-memory SQLite via `aiosqlite` (not Postgres). The `tests/conftest.py` sets critical environment variables **before any orion imports**:
- `ORION_STAGE=test`, `DB_URL=sqlite+aiosqlite:///:memory:`, mock API keys
- Autouse fixtures: `setup_test_db` (creates/drops all tables per test), `mock_redpanda_producer` (prevents network calls)
- All tests are async by default (`asyncio_mode = "auto"`)
- Unit tests must have NO network calls and NO database I/O

## Code Style

- Line length: 120 (ruff and black)
- Python 3.12+ target
- Ruff rules: E, F, I, W, B (ignores E402, E501, B008)
- Mypy: strict mode with `ignore_missing_imports`

## Architecture

### Data Flow

```
Ingestion → Bronze (raw JSON) → Silver (normalized) → Features/Signals → Rules → Candidates
→ Regime + ML Scoring → Solver Ensemble → StrategyDecision → Risk Preflight → Execution
```

### Medallion Storage (`src/orion/storage/`)

- **Bronze** (`BronzeEvent`): Raw events with JSON payload. Idempotent via `ON CONFLICT DO NOTHING` on `event_id`.
- **Silver**: Normalized domain models — `SilverOptionFlow`, `SilverAlpacaBar`, `SilverDarkPool`, `SilverUWAlert`, `SilverSignal`, `SilverOptionQuote`.
- **Gold**: Enriched/aggregated — `CandidateTrade`, `StrategyDecision`, `GoldTickerRollup`, `GoldFeatureEvent`, `CandidateLabel`, `ExitDecision`.

Dual storage: PostgreSQL for operational queries + S3 Parquet lakehouse (partitioned `v1/{source}/{event_type}/date={date}/`) for analytics.

### Services (Entry Points)

Each `src/orion/main_*.py` is a standalone async service run via `python -m orion.main_*`:
- `main_execution` — Trade execution loop (fetch candidates → decide → risk check → execute)
- `main_labeler` / `main_price_target_labeler` — ML label generation from forward returns
- `main_feature_enrichment` — Background GEX, Market Tide, IV Rank, VIX, Regime fetching
- `main_eod` — End-of-day LLM review agent
- `main_meta` / `main_meta_weekly` — LLM-driven strategy meta-search
- `main_pattern_miner` — LightGBM pattern discovery
- `main_position_monitor` — Open position monitoring loop
- `main_rollups` — OHLCV aggregation (5m, 1h, 1d)
- Ingestion runs via `python -m orion.ingestion`

### Solver System (`src/orion/core/`)

Strategies are data, not code. `SolverDSL` defines strategy "DNA" as Pydantic-validated configs (rules, features, model, risk params, exit logic). At runtime:
1. `SolverRouter.select_solvers()` picks top-k solvers by context (regime, ticker, stage, drawdown)
2. `SolverPipeline.execute()` runs each solver: rule check → feature generation → model inference
3. `SignalEngine` combines results via weighted ensemble consensus (`score = Σ(p_take × weight) / Σ(weight)`, threshold 0.5)

Strategies promote through stages: `research → shadow → paper → limited_live → scaled_live` with metric gates.

### Processing Pipeline (`src/orion/processing/`)

- `FeatureEngine`: Maintains in-memory history buffers per ticker, computes RSI/SMA/flow aggregates, produces `SilverSignal` records
- `RuleEngine`: Evaluates `TradingRule` instances against signals to produce `CandidateTrade`s
- `SignalEngine`: Top-level orchestrator — regime detection → ML pre-filter → solver ensemble → decision
- Rules defined in `processing/rules/` (e.g., `ZeroDTESweepRule`, `SwingEntryRule`)

### Execution (`src/orion/execution/`)

- `ExecutionEngine`: Translates decisions to Alpaca orders (equity and options paths)
- `RiskManager`: Pre-trade checks (daily loss, drawdown kill switch, Greeks limits, sector exposure, 0DTE winddown, correlation sizing). State persisted in `RiskState` table.
- `SignalPreflight`: Validates data lag, circuit breaker, limit price, rollup availability
- `CircuitBreaker`: DB-backed global kill switch via `SystemStatus` table

### Connectors (`src/orion/connectors/`)

Protocol-based design: `PollingConnector` and `StreamingConnector` protocols in `base.py`. Implementations: `AlpacaStreamConnector` (WebSocket), `AlpacaMarketConnector` (REST fallback), `GatewayStreamClient` (UW data via WebSocket), `VixConnector`.

### Configuration (`src/orion/config.py`)

Pydantic `BaseSettings` with env var prefixes:
- `RiskSettings` (prefix `ORION_RISK_`) — trade limits, Greeks, sectors, 0DTE, correlation
- `SystemSettings` (prefix `ORION_`) — API keys, stage, universe, watchlist
- `MetaSearchSettings` (prefix `ORION_META_`) — scoring weights
- `AgentSettings` (prefix `ORION_AGENT_`) — LLM model config

All instantiated as module-level singletons. Copy `.env.example` to `.env` for local development.

### Key Patterns

- **Deterministic IDs**: `CandidateTrade` IDs are SHA256 of `(ticker, timestamp, rule_id)` for idempotency
- **Retry decorators** (`shared/decorators.py`): `@db_retry` (3 attempts, exp 1-10s), `@api_retry` (3 attempts, exp 2-10s)
- **Point-in-time fidelity**: Greeks captured at ingestion time, not retroactively computed
- **Persistence**: All writes use `INSERT ... ON CONFLICT DO NOTHING` in 1000-row batches
- **Event provenance**: Every event carries `ingest` metadata (connector, run_id, trace_id)
