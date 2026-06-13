# Orion — Testing Guide

How to run tests, write tests, and choose markers. The Empire-wide test rules
in `CLAUDE.md` apply too; this doc covers Orion specifics.

## Quick start

```bash
cd /Users/jacobmcmillan/Empire/Orion

uv sync                                    # install deps
uv run pytest                              # all tests
uv run pytest -m unit                      # fast units only
uv run pytest tests/unit                   # by directory
uv run pytest tests/integration            # integration only
uv run pytest tests/e2e                    # e2e (needs TimescaleDB on :5440)
uv run pytest -k "test_name"               # single test by name
uv run pytest -v --tb=short --cov=orion    # verbose + coverage
```

Full quality gate:

```bash
uv run pytest && ruff check . && mypy .
```

## Layout

```
tests/
├── conftest.py          # sets DB_URL=sqlite, ORION_STAGE=test BEFORE imports
├── unit/                # fast, isolated, no I/O, no network
├── integration/         # real (in-memory) DB, component wiring
├── e2e/                 # full pipeline against real TimescaleDB
├── agents/              # LLM agent tests (mostly mocked)
├── api/                 # FastAPI route tests
├── clients/             # Heber / Gateway client tests
├── connectors/          # WS + UW connector tests
├── core/                # circuit breaker, solver DSL, service-lease, etc.
├── execution/           # safety-critical — risk, preflight, fill processor
├── ingestion/           # ingestion service tests
├── enrichment/, ml/, processing/, jobs/, labeler/, rag/, shared/, storage/
└── contracts/           # cross-system contract tests
```

## Markers (declared in `pyproject.toml`)

| Marker | Speed | Use for |
|---|---|---|
| `unit` | <10 ms | Pure logic — solvers, parsers, formatters, rule evaluation |
| `integration` | <1 s | DB wiring, queue contracts, mocked third-parties |
| `e2e` | seconds–minutes | Full pipeline against real TimescaleDB |
| `slow` | >1 s | Opt-in; excluded by default — use sparingly |

`slow` tests are excluded by default via `addopts = -m "not slow"` in
`pyproject.toml`. Run them with `uv run pytest -m slow`, or include them
alongside everything else with `uv run pytest -m ""`.

Markers are auto-applied by directory (`tests/conftest.py`): `tests/unit/**` →
`unit`, `tests/e2e/**` → `e2e`, and everything else (`integration/`,
`contracts/`, and the component dirs) → `integration`; an explicit marker on a
test or module still wins and is never overridden.

Set the marker explicitly: `pytestmark = pytest.mark.unit` at module top, or
`@pytest.mark.integration` per test. `addopts = --strict-markers` enforces it.

## conftest hooks

`tests/conftest.py` runs **before** any orion imports and sets:

- `DB_URL=sqlite+aiosqlite:///:memory:`
- `ORION_STAGE=test`
- `asyncio_mode=auto` (via `pyproject.toml`)

This is why `from orion...` imports in tests Just Work — never reorder imports
above the conftest setup. Async fixtures get a function-scoped loop.

## Mocking conventions

```python
import pytest
from unittest.mock import patch, AsyncMock

@pytest.fixture
def mock_gateway():
    with patch("orion.clients.gateway_trading_client.GatewayTradingClient") as m:
        m.return_value.submit_order = AsyncMock(return_value={"id": "abc"})
        yield m

async def test_execution_submits(mock_gateway, candidate, db_session):
    engine = ExecutionEngine(client=mock_gateway.return_value, ...)
    await engine.execute(candidate)
    mock_gateway.return_value.submit_order.assert_awaited_once()
```

- Mock **at the boundary** — patch `GatewayTradingClient`, `HeberReader`,
  `AlpacaClient`, never the underlying `httpx.Client`.
- HTTP mocking: use `respx` or `aioresponses` when you need real-shaped
  requests, not for trivial single-call tests (a plain `AsyncMock` is fine).

## E2E tests (real TimescaleDB)

Two tests bypass the in-memory SQLite binding:

- `tests/e2e/test_smoke_e2e.py` — 9-stage pipeline smoke test (bronze → silver
  → features → rollups → rules → candidates → ML scoring → signal engine →
  execution with mocked broker). Always passes if pipeline logic is intact.
- `tests/e2e/test_live_data_flow.py` — queries each stage for freshness.
  During market hours, fails if any stage is stale or empty; outside market
  hours, passes with diagnostic output.

Both call `db.configure_db()` to point at real TimescaleDB and restore the
SQLite binding before yielding, so the autouse teardown doesn't drop real
tables.

Requirements:

```bash
docker compose up timescaledb -d    # Postgres 16 + pgvector on host port 5440
uv run pytest tests/e2e -v -s
# or standalone diagnostic:
uv run python tests/e2e/test_live_data_flow.py
```

## Coverage

```bash
uv run pytest --cov=orion --cov-report=term-missing --cov-report=html
open htmlcov/index.html
```

Coverage config in `pyproject.toml`:

- Branch coverage on, source = `src/`.
- Omits `tests/*`, `scripts/*`, `*/__init__.py`.
- Excludes `pragma: no cover`, `__repr__`, `__main__` blocks, `NotImplementedError`,
  `TYPE_CHECKING`.

## Safety-critical test expectations

For every change to `execution/`, `core/circuit_breaker.py`,
`core/service_lease.py`, or anything else listed in
[`code-standards.md`](code-standards.md#safety-critical-code), add tests
covering:

- The risk boundary you changed (e.g. exact `max_daily_loss` trip point).
- The recovery path (e.g. circuit-breaker close, lease reacquire).
- The negative case (e.g. fill from a non-`orion_` `client_order_id` is
  rejected).

Drawdown kill-switch and lease-takeover tests already exist — extend them, do
not duplicate.

## CI

`.github/workflows/` runs:

- `pre-commit` (ruff, black, mypy, detect-secrets, bandit).
- Full `pytest`.
- SonarQube quality gate (`sonar-project.properties`).

Match local commands to CI before pushing: `uv run pytest && ruff check . && mypy .`.

## Related

- [`code-standards.md`](code-standards.md)
- [`configuration-guide.md`](configuration-guide.md) — env vars that change test behaviour
- Older guide preserved: `../TESTING.md` (Poetry-era, kept for legacy commands)
