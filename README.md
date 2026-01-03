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
