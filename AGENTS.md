# AGENTS.md

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

Key runtime services are launched by `main_*` entry points (for example: ingest, execution, eod, rollups, pattern miner).

## Development Commands

```bash
# Install dependencies
poetry install

# Run tests / lint / type checks
pytest -q
ruff check .
mypy .

# Run full local stack
docker compose up -d --build

# Run a specific service locally
python -m orion.main_ingest
python -m orion.main_execution

# Database migrations
alembic upgrade head
alembic downgrade -1
```

## Key Patterns

- Keep business logic in slice/domain modules, not in transport layers.
- Use `structlog` from Orion logger modules for structured logging.
- Use explicit exception handling with context-rich logs at service boundaries.
- Treat idempotency and deterministic identifiers as first-class concerns.
- Preserve paper/safe defaults for execution paths unless explicitly requested otherwise.

## Important Files

- `/Users/jacobmcmillan/Empire/Orion/src/orion/config.py` — environment-driven system/risk/agent settings
- `/Users/jacobmcmillan/Empire/Orion/src/orion/api/main.py` — FastAPI endpoints
- `/Users/jacobmcmillan/Empire/Orion/src/orion/main_ingest.py` — ingestion entry point
- `/Users/jacobmcmillan/Empire/Orion/src/orion/main_execution.py` — execution entry point
- `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml` — multi-service local deployment

## Testing

- Test framework: `pytest`
- Preferred command: `pytest -q`
- Keep tests deterministic (no live network in unit tests).
- For behavior changes, write/adjust tests before implementation and keep them as regression coverage.

## Common Pitfalls

- Mixing legacy and new ingestion/data-access paths during migration work.
- Missing env vars causing startup/runtime failures.
- Accidentally enabling live trading modes instead of paper defaults.
- Allowing schema drift between producer/consumer contracts.
