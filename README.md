# Orion

Real-time data lake + signal engine for **US options trading** in the Empire
monorepo. Ingests live market data via Data-Gateway, generates trade candidates
through a solver ensemble, and **places options orders on Alpaca** (via the
Data-Gateway trading proxy). Stores state in TimescaleDB; reads historical
parquet from a host-side Heber cache.

> **Live-trading system.** Default posture is paper (`ORION_STAGE=paper`,
> `ALPACA_PAPER=true`). See [`docs/code-standards.md`](docs/code-standards.md#safety-critical-code)
> before touching execution code.

## Documentation

| Doc | Purpose |
|---|---|
| [`docs/project-overview-pdr.md`](docs/project-overview-pdr.md) | Mission, scope, solver lifecycle, runtime stages |
| [`docs/codebase-summary.md`](docs/codebase-summary.md) | Module-by-module map of `src/orion/` |
| [`docs/system-architecture.md`](docs/system-architecture.md) | Data flow, Mermaid diagram, execution boundary |
| [`docs/code-standards.md`](docs/code-standards.md) | Conventions, safety-critical rules, position attribution |
| [`docs/testing-guide.md`](docs/testing-guide.md) | Markers, E2E tests, mocking patterns |
| [`docs/configuration-guide.md`](docs/configuration-guide.md) | Env-var matrix across the three Settings classes |
| [`docs/deployment-guide.md`](docs/deployment-guide.md) | launchd, Docker Compose, orphan-close history |
| [`docs/api-reference.md`](docs/api-reference.md) | FastAPI admin/dashboard endpoints |
| [`PRD.md`](PRD.md) | Full product requirements |
| [`CHANGELOG.md`](CHANGELOG.md) | Behavioural changes |

Older docs preserved under `docs/` (DATABASE_SCHEMA, DATA_CONTRACTS,
DATA_RETENTION, ARCHITECTURE, API_REFERENCE, RUNBOOK, RUNBOOKS, ROLLBACK,
alerting, disaster_recovery_runbook, `runbooks/`, `rca/`, `audits/`).

## Tech stack

- Python 3.12+
- `uv` for env + dep management
- FastAPI + SQLAlchemy (async)
- TimescaleDB (Postgres + pgvector), Alembic migrations
- structlog (via `empire_core.logger`)
- Docker Compose for local orchestration; launchd for native execution/ingestion
- pyarrow + pandas for Heber parquet reads
- LightGBM for ML scoring
- Alpaca + Unusual Whales (via Data-Gateway)

## Quick start

```bash
cd /Users/jacobmcmillan/Empire/Orion

# 1) Install dependencies
uv sync

# 2) Configure environment
cp .env.example .env
# edit .env — at minimum set DB_URL, UW_API_KEY, ALPACA_API_KEY,
# ALPACA_SECRET_KEY, ORION_API_KEY

# 3) Start TimescaleDB (+ optionally the full stack)
docker compose up timescaledb -d
docker compose up -d                        # full default profile

# 4) Run migrations
uv run alembic upgrade head

# 5) Run tests
uv run pytest -m unit                       # fast
uv run pytest                                # all (needs DB for e2e)
```

Run a single service locally:

```bash
uv run python -m orion.ingestion
uv run python -m orion.main_execution
uv run uvicorn orion.api.main:app --reload --port 8000
```

## Common commands

```bash
uv sync                       # install deps
uv run pytest                 # all tests
uv run pytest -m unit         # fast units only
uv run pytest -k "test_name"  # single test
ruff check .                  # lint
ruff format .                 # format
mypy .                        # type check (strict for selected modules)
uv run alembic upgrade head   # apply migrations
```

Full quality gate:

```bash
uv run pytest && ruff check . && mypy .
```

## Native (launchd) lifecycle

```bash
# Install (idempotent)
cp scripts/launchd/com.empire.orion.execution.plist        ~/Library/LaunchAgents/
cp scripts/launchd/com.empire.orion.ingestion.plist        ~/Library/LaunchAgents/
cp scripts/launchd/com.empire.orion.launchd-health.plist   ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.execution.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.ingestion.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.launchd-health.plist

# Hot restart
launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution

# Status
launchctl print gui/$(id -u)/com.empire.orion.execution
```

Don't run docker `execution`/`ingestion` simultaneously with the native
agents — the service-lease guard will reject whichever started second. Full
operational detail in [`docs/deployment-guide.md`](docs/deployment-guide.md).

## Required environment

Minimum `.env` (see [`.env.example`](.env.example) for full list and
[`docs/configuration-guide.md`](docs/configuration-guide.md) for context):

- `DB_URL` — TimescaleDB connection string
- `UW_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
- `ALPACA_PAPER=true` (default — keep it)
- `ORION_STAGE=paper` (default — keep it)
- `ORION_API_KEY` — admin API auth
- `DATA_GATEWAY_URL`, `DATA_GATEWAY_API_KEY`
- `HEBER_DATA_ROOT`, `HEBER_CATALOG_URL`

## Related repos

- **Data-Gateway** — REST/WS proxy for UW + Alpaca and the order router
- **Heber** — lakehouse Orion reads parquet from (`~/.heber-cache/data`)
- **AI-Gateway** — LLM proxy used by EOD review and MetaSearchAgent
- **EmpireUI** — dashboard that consumes Orion's admin API
- **Athena** — post-trade analyst (consumes Orion's trade journal)

## House rules

- Use `uv`, never Poetry. (Older docs reference Poetry; they're stale.)
- Default to paper mode. Never weaken risk guards or the `orion_`
  `client_order_id` filter.
- Commit small, atomic changes; update `CHANGELOG.md` with every behavioural
  change.
- Don't change LLM model IDs without explicit user permission (monorepo rule).

See `CLAUDE.md` for the authoritative agent guidance.
