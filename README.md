# Orion

Orion is a real-time trading backend in the Empire ecosystem. It ingests market data, stores normalized data, generates signals, and runs execution/risk workflows.

## Overview

Orion sits downstream of Data Gateway and Heber migration work. It runs ingestion, enrichment, strategy evaluation, and execution services with strong logging and operational controls.

For architecture and operations details, use:
- `/Users/jacobmcmillan/Empire/Orion/docs/ARCHITECTURE.md`
- `/Users/jacobmcmillan/Empire/Orion/docs/RUNBOOK.md`
- `/Users/jacobmcmillan/Empire/Orion/docs/API_REFERENCE.md`

## Tech Stack

- Python 3.12+
- FastAPI + SQLAlchemy
- TimescaleDB (Postgres), Redpanda, MinIO
- Structlog for structured logs
- Poetry for dependency management
- Docker Compose for local orchestration

## Prerequisites

- Python 3.12+
- Poetry
- Docker + Docker Compose
- API credentials for Unusual Whales and Alpaca (paper keys recommended)

## Quick Start

```bash
# 1) Install dependencies
poetry install

# 2) Configure environment
cp /Users/jacobmcmillan/Empire/Orion/.env.example /Users/jacobmcmillan/Empire/Orion/.env

# 3) Start infrastructure/services
docker compose up -d --build

# 4) Run tests
poetry run pytest -q
```

Run a single service locally:

```bash
poetry run python -m orion.ingestion
poetry run python -m orion.main_execution
```

## Configuration

All runtime settings are environment-driven via `/Users/jacobmcmillan/Empire/Orion/src/orion/config.py`.

Start with:
- `/Users/jacobmcmillan/Empire/Orion/.env.example`

Core values you must set:
- `DB_URL`
- `UW_API_KEY`
- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- `ORION_API_KEY`

## Testing

- Guide: `/Users/jacobmcmillan/Empire/Orion/TESTING.md`
- Fast run: `poetry run pytest -q`
- Full quality gate: `poetry run pytest -q && poetry run ruff check . && poetry run mypy .`

## Architecture

Architecture document:
- `/Users/jacobmcmillan/Empire/Orion/docs/ARCHITECTURE.md`

Data contracts:
- `/Users/jacobmcmillan/Empire/Orion/docs/DATA_CONTRACTS.md`

## Related Repos

- Data-Gateway (data access and request proxy)
- Heber (storage/lakehouse)
- Shared-MCP-Server (external service tools)
- Empire dashboard (operations UI)

## Documentation Index

- Product requirements: `/Users/jacobmcmillan/Empire/Orion/PRD.md`
- Architecture: `/Users/jacobmcmillan/Empire/Orion/docs/ARCHITECTURE.md`
- Runbook: `/Users/jacobmcmillan/Empire/Orion/docs/RUNBOOK.md`
- API reference: `/Users/jacobmcmillan/Empire/Orion/docs/API_REFERENCE.md`
- Testing: `/Users/jacobmcmillan/Empire/Orion/TESTING.md`
- Contributing: `/Users/jacobmcmillan/Empire/Orion/CONTRIBUTING.md`
- Security: `/Users/jacobmcmillan/Empire/Orion/SECURITY.md`
- Developer notes: `/Users/jacobmcmillan/Empire/Orion/DEVELOPER_NOTES.md`
