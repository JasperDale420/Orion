# Orion: Real-Time UW + Alpaca Data Lake & Signal Engine

Orion is a scalable, real-time trading backend that ingests market data from **Unusual Whales (UW)** and **Alpaca**, normalizing it into a multi-layered data lake (Bronze/Silver/Gold) powered by **TimescaleDB**. It features an event-driven architecture using **Redpanda** for messaging, a modular **Feature Engine**, and a **Rule Engine** for signal generation, alongside a meta-search layer for automated strategy evolution.

## Current Capabilities

*   **Multi-Source Ingestion**: Real-time polling of UW Options Flow, UW Dark Pool Prints, UW Alerts, and Alpaca 1-minute aggregations.
*   **Data Lakehouse**:
    *   **Bronze Layer**: Raw JSON event storage (TimescaleDB).
    *   **Silver Layer**: Normalized, typed SQL tables (`SilverOptionFlow`, `SilverAlpacaBar`, etc.).
    *   **Gold Layer**: Feature-enriched signals and candidate trades (`GoldSignal`, `CandidateTrade`).
*   **Event Streaming**: Integrated with Redpanda (Kafka-compatible) for event propagation.
*   **Feature Engineering**: On-the-fly calculation of technical indicators (RSI, SMA, Volatility) and flow-based features.
*   **Multi-Axis Regime Detection**: VIX-aware regime system with 5 axes (Trend, Vol, Risk, Session, VIX).
*   **ML Feature Enrichment**: GEX, Market Tide, Max Pain, IV Rank data for 60+ label columns.
*   **Pattern Mining**: LightGBM-based rule extraction from price target outcomes.
*   **Strategy Engine**:
    *   **Solver Abstraction**: Modular strategy configuration (`SolverConfig`) combining Entry/Exit logic, Risk parameters, and Universe definition.
    *   **Meta-Search**: Automated strategy optimization (LLM-driven proposals and heuristic mutation).
    *   **Regime-Aware Risk**: Position sizing multipliers based on market regime.
*   **Execution**: Alpaca API integration for order placement and position management.
*   **Observability**: Structured JSON logging and Dead Letter Queue (DLQ) for failed events.
*   **CI/CD**: Github Actions pipeline for automated testing.

## Non-Capabilities / Explicit Non-Goals

*   **Web Frontend**: Orion is a backend-only system. No UI is provided.
*   **High-Frequency Trading (HFT)**: While "real-time", the system is designed for second-to-minute latency, not microsecond execution.
*   **Crypto/Forex**: strictly Equities and Options on US markets.
*   **Production Live Trading Warranty**: While execution logic exists (`main_execution.py`), the system is primarily in a **Research/Paper** verification stage. Use real money at your own risk.

## Architecture Overview

## Data Ingestion & Universe Management
The ingestion service (`src/orion/main_ingest.py`) is responsible for:
1.  **Polling Unusual Whales** for Flow and Alerts.
2.  **Maintaining an Active Universe** of tickers.
3.  **Polling Alpaca** for 1-minute OHLCV bars for all valid tickers.

### Universe Persistence & Expiry Tracking
Orion uses a smart tracking system for options:
- **Dynamic Universe**: Tickers from alerts are automatically tracked.
- **Expiry Awareness**: Tickers associated with Option Alerts are tracked **until the option expires**, regardless of the 8-hour daily TTL.
- **Persistence**: Active option contexts are hydrated from the database on startup, ensuring that long-dated setups (e.g., LEAPS) are never lost due to a restart.

### Feature Engine
- **Files**: `src/orion/processing/feature_engine.py`
- **Role**: Computes realtime technicals (RSI, SMA, VWAP) and Flow Aggregates.

**1. Ingestion Service** (`orion_ingestion` in Docker):
*   **Ingest**: Connects to Unusual Whales & Alpaca APIs.
*   **Process**: Runs `FeatureEngine` and `RuleEngine` in-stream to generate signals.
*   **Store**: Persists Raw Events (Bronze), Signals (Silver), and Candidates (Gold) to TimescaleDB.

**2. Execution Service** (`orion_execution` in Docker):
*   **Poll**: Listens for new `CandidateTrade` rows in TimescaleDB.
*   **Decide**: Runs `SignalEngine` (Policy Layer) to validate candidates against portfolio risk.
*   **Execute**: Submits orders to Alpaca.

**3. Infrastructure**:
*   **TimescaleDB**: Primary SQL Store.
*   **Redpanda**: Event streaming (optional lateral bus).
*   **MinIO**: Lakehouse storage.

## Repository Structure

```text
├── src/orion
│   ├── agents/              # LLM Agents (EOD Review, Meta-Search)
│   ├── analysis/            # Regime detection, risk management
│   ├── api/                 # HTTP API definitions
│   ├── config.py            # Pydantic configuration models
│   ├── connectors/          # API Clients (Alpaca, UW, VIX, Redpanda)
│   ├── core/                # Core Domain Logic (Solver, Universe, Router)
│   ├── execution/           # Order Management & Position Tracking
│   ├── ml/                  # Machine Learning (Pattern Mining, LightGBM)
│   ├── main_ingest.py       # Ingestion Entry Point
│   ├── main_execution.py    # Execution Entry Point
│   ├── main_eod.py          # EOD Agent Entry Point
│   ├── main_feature_enrichment.py  # UW Feature Polling
│   ├── main_price_target_labeler.py  # ML Label Generation
│   ├── main_pattern_miner.py  # Pattern Mining
│   ├── processing/          # ETL, Features, Normalization, Rules
│   └── storage/             # SQLAlchemy Models (Gold/Silver/Bronze/ML)
├── config/                  # YAML configs (regime_risk.yaml, etc.)
├── tests/                   # Pytest Suite (Unit, Integration)
├── docker-compose.yml       # Infrastructure + Services
└── pyproject.toml           # Dependencies & Tool Config
```

## Setup & Installation

### Prerequisites
*   Python 3.12+
*   Docker & Docker Compose

### 1. Clone & Install
```bash
git clone <repo_url>
cd Orion
pip install -r requirements.txt
pip install -e .
```

### 2. Infrastructure & Services (Production Mode)
Start the full stack (Ingestion, Execution, Database):
```bash
docker-compose up -d --build
```
This runs the system in the background.

### 3. Environment Variables
Copy the example env file and fill in your API keys:
```bash
cp .env.example .env
```
Key variables:
*   `ORION_UW_API_KEY`: Unusual Whales API Key.
*   `ORION_ALPACA_API_KEY` / `ORION_ALPACA_SECRET_KEY`: Alpaca Credentials.
*   `POSTGRES_USER` / `POSTGRES_PASSWORD`: DB Credentials (default: `orion` / `orion_password`).
*   `OPENAI_API_KEY`: Required for Meta-Search/LLM agents.

### Legacy Labels Profile (Current Migration Default)

When you run the `legacy-labels` Docker profile, Orion now defaults to:

- Legacy local label loops off (to avoid re-expanding local label storage during migration)
- Pattern-miner/model-training controls on (so local model files and model metadata still work)

If you need to explicitly override:

- Global legacy gate: `ORION_ENABLE_LEGACY_LABEL_PIPELINES`
- Pattern miner run gate: `ORION_ENABLE_LEGACY_PATTERN_MINER`
- Pattern miner training gate: `ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING`
- Exit classifier training gate: `ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING`
- Pattern miner training source: `ORION_PATTERN_MINER_TRAINING_SOURCE` (`heber_gold` or `legacy_sql`)
- Exit classifier training source: `ORION_EXIT_CLASSIFIER_TRAINING_SOURCE` (`legacy_sql` or `heber_gold`)
  - Defaults are now `heber_gold` in both `docker-compose.yml` and centralized `SystemSettings`.

## How to Run (Legacy / Dev Mode)

### Development Mode
Run the ingestion pipeline locally (creates local Python process):
```bash
python src/orion/main_ingest.py
```

Run the execution engine (Paper Trading):
```bash
# Ensure DB is running via docker-compose up -d timescaledb first
ORION_STAGE="paper" python src/orion/main_execution.py
```

### Strategy Evolution (Meta-Search)
Run the AI Meta-Search agent to propose improvements to your strategy:
```bash
# Runs a one-off optimization cycle
docker-compose run --rm meta-search
```
This requires `OPENAI_API_KEY` in your `.env`.

### Running Tests
See `TESTING.md` for detailed instructions.
```bash
pytest
```

### ML Feature Validation
Validate that all 130+ ML features are correctly calculated:
```bash
# Run all validations (sanity checks + data source audit)
docker-compose run --rm ingestion python -m orion.jobs.validate_features --all

# Spot-check a single record
docker-compose run --rm ingestion python -m orion.jobs.validate_features --spot-check <EVENT_ID>

# Backfill missing features for historical labels
docker-compose run --rm ingestion python -m orion.jobs.backfill_ml_features --batch-size 100
```

## Configuration

Configuration is managed via `pydantic-settings` in `src/orion/config.py`. All settings can be overridden by environment variables.

| Section | Env Prefix | Example |
| :--- | :--- | :--- |
| **System** | `ORION_` | `ORION_STAGE=live` |
| **Risk** | `ORION_RISK_` | `ORION_RISK_MAX_POSITIONS=5` |
| **Agents** | `ORION_AGENT_` | `ORION_AGENT_MODEL_NAME=gpt-4` |
| **Meta** | `ORION_META_` | - |

## Error Handling & Logging

*   **Structured Logging**: Uses `structlog` (via `orion.shared.logger`) for JSON-formatted logs.
    *   **Dev Mode**: Set `ORION_LOG_FORMAT=human` for colorized, readable logs during local development.
*   **Dead Letter Queue (DLQ)**: Failed ingestion events are captured in the `dead_letter_queue` table in Postgres for replay/inspection.
*   **APIs**: Response errors from Alpaca/UW are logged with backoff/retry via `tenacity`.

## Testing Status

*   **Framework**: `pytest`
*   **Coverage**: ~35% (Core logic covered; Integration tests pending).
*   **Status**: Unit tests for Compliance, Solvers, and Feature Engines are passing.

## Safety & Risk Notes

> [!WARNING]
> **Trading Risk**: This system is capable of executing real financial trades. Ensure `ORION_STAGE` is set to `paper` unless you explicitly intend to risk capital.

> [!IMPORTANT]
> **Database**: The `docker-compose.yml` mounts a volume for TimescaleDB retention. Ensure this volume is backed up if using in production.

## Risk Management

Orion includes comprehensive risk controls for options trading:

### Portfolio Greeks Limits
| Setting | Default | Description |
|---------|---------|-------------|
| `ORION_RISK_MAX_PORTFOLIO_DELTA` | 500 | Absolute portfolio delta limit |
| `ORION_RISK_MAX_PORTFOLIO_GAMMA` | 100 | Absolute portfolio gamma limit |
| `ORION_RISK_MAX_PORTFOLIO_VEGA` | 200 | Absolute portfolio vega limit (IV crush protection) |
| `ORION_RISK_MAX_POSITION_DELTA` | 100 | Per-position delta limit |
| `ORION_RISK_MAX_POSITION_VEGA` | 50 | Per-position vega limit |

### Correlation-Aware Position Sizing
When enabled, reduces position size for assets highly correlated with existing holdings:

| Setting | Default | Description |
|---------|---------|-------------|
| `ORION_RISK_CORRELATION_SIZE_SCALING` | `false` | Enable/disable correlation sizing |
| `ORION_RISK_CORRELATION_THRESHOLD` | 0.70 | Correlation above this triggers penalty |
| `ORION_RISK_CORRELATION_PENALTY_FACTOR` | 0.30 | Minimum size multiplier at max correlation |
| `ORION_RISK_CORRELATION_LOOKBACK_DAYS` | 30 | Days of price history for correlation calc |
| `ORION_RISK_MIN_BARS_FOR_CORRELATION` | 20 | Skip adjustment if insufficient data |

**How it works**: If a new trade has average correlation > 0.70 with existing positions, its size is scaled down linearly from 1.0 to 0.30 as correlation approaches 1.0.

**Rollout**: Start with paper trading (`ORION_RISK_CORRELATION_SIZE_SCALING=true`) and monitor logs for `event: correlation_size_adjustment`.

### Model Freshness Validation
| Setting | Default | Description |
|---------|---------|-------------|
| `ORION_MAX_MODEL_AGE_DAYS` | 14 | Skip ML models older than this |

## Known Gaps & TODOs

*   **Integration Tests**: Mocking in `test_solver_router.py` is currently fragile; robust integration tests are needed.
*   **Redpanda Idempotency**: Producer currently uses a "fire-and-forget" approach with basic error logging.
*   **Live Trading Validation**: The "Live" stage logic is currently identical to "Paper" with different API endpoints; rigorous safety gates needed before real capital deployment.

## Contribution Notes

1.  **Vertical Slices**: Changes should implement end-to-end functionality (API -> DB -> UI/CLI) rather than horizontal layers.
2.  **Fail Loudly**: Do not swallow exceptions; let the system crash or log explicitly to DLQ.
1.  **Tests**: All new features must include unit tests. Run `pytest` before submitting PRs.

## Development Workflow

### 1. Installation
Install dev dependencies:
```bash
pip install .[dev]
pre-commit install
```

### 2. Hygiene (Pre-commit)
Run the quality suite (linting, secrets, types) manually:
```bash
pre-commit run --all-files
```
This runs `ruff`, `black`, `mypy`, `bandit`, and `detect-secrets`.

### 3. Testing & Coverage
Run the full suite with coverage:
```bash
make test-coverage
```
This generates `htmlcov/` (human readable) and `coverage.xml` (machine readable).

### 4. CI Pipeline
On every PR, GitHub Actions triggers:
- **Lint & Test**: Runs `pre-commit` and `pytest`.
- **SonarCloud**: Scans code using `coverage.xml`. *Requires SONAR_TOKEN secret.*
- **Dependabot**: Checks for dependency updates weekly.
