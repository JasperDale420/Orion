# Orion — Code Standards

Conventions enforced by review and tooling. The Empire-wide rules in
`/Users/jacobmcmillan/Empire/CLAUDE.md` apply too; this doc only calls out
Orion specifics.

## Language & tooling

- **Python 3.12+** (`requires-python = ">=3.12"` in `pyproject.toml`).
- **Package manager: `uv`.** Never use Poetry or pip directly. (Old docs
  reference Poetry — those are stale; follow this guide.)
- **Linter: `ruff`** — extends `ruff-base.toml` at the Orion repo root, then
  selects `E`, `F`, `I`, `W`, `B`. `line-length = 120`. Ignored:
  `E402`, `E501`, `B008`, `I001`.
- **Formatter: `ruff format`** (also `black>=26.1` in dev deps for legacy parity).
- **Type checker: `mypy --strict`** by default, with broad `ignore_errors`
  overrides for `orion.*`. Strict mypy is **required** for:
  - `orion.clients.heber_reader`
  - `orion.main_feature_enrichment`
  - `orion.jobs.gateway_contract_probe`

  Excludes: `archive/`, `qlib-main/`, `scripts/`, `tests/`, `alembic/`.
- **Pre-commit:** see `.pre-commit-config.yaml` (ruff, black, mypy, detect-secrets, bandit).

## Project layout

- **Source root:** `src/orion/` (hatchling: `packages = ["src/orion"]`).
- **Tests:** `tests/` mirrors `src/orion/` with subdirs `unit/`, `integration/`,
  `e2e/`, plus per-package dirs.
- **Long-running entrypoints:** top-level `main_*.py` modules in `src/orion/`.
  Add new ones at the same level, register in `docker-compose.yml` and (if
  long-running) write a launchd plist under `scripts/launchd/`.
- **Never** put working files, scratch tests, or experiments at the monorepo
  root or at `Orion/` root. Use `proposals/`, `predict/`, or `archive/`.

## Logging

```python
from orion.shared.logger import setup_struct_logger
logger = setup_struct_logger(__name__)
logger.info("event_name", key1=v1, key2=v2)
```

- `setup_struct_logger` delegates to `empire_core.logger` and injects `run_id`
  into the context on first call.
- **Never** call `structlog.configure()` or `logging.basicConfig()` outside of
  `empire_core.logger`.
- For exception logging: `logger.error("...", exc_info=True)` or the
  `log_error()` helper which extracts `EmpireError` fields.
- `ORION_LOG_FORMAT` (`json` / `human`) and `ORION_LOG_DIR` map to the
  `EMPIRE_LOG_*` equivalents.

## Errors

```python
from orion.core.errors import OrionError, ErrorCode, ProviderError
raise ProviderError(
    "UW flow fetch failed",
    code=ErrorCode.PROVIDER_TIMEOUT,
    details={"ticker": ticker, "endpoint": "/flow/alerts"},
)
```

- All Orion-raised exceptions extend `OrionError(EmpireError)`.
- Subclasses: `ProviderError`, `StorageError`, `ExecutionError`,
  `ModelInferenceError`, `FeatureComputationError`.
- Error codes live in the `ErrorCode` enum — add new codes there, don't
  freelance strings.
- API auto-translates `OrionError` to the standard envelope (`success: false,
  error: {code, message, details}`) via the FastAPI exception handler in
  `api/main.py`.
- **Never** return `None` to signal failure; raise.
- **Never** swallow exceptions with bare `except Exception`. Catch the
  specific subclass.

## HTTP clients

Use the shared `empire_core.http_client` factories — never instantiate
`httpx.Client` directly:

```python
from empire_core.http_client import create_async_http_client, http_retry, raise_for_status

@http_retry  # 3 attempts, exponential backoff 1-10s
async def fetch_flow(client, ticker):
    resp = await client.get(f"/flow/alerts?ticker={ticker}")
    raise_for_status(resp)  # structured error logging
    return resp.json()
```

## Database

- Async only for runtime: `async_sessionmaker(expire_on_commit=False)` from
  `storage/db.py`.
- Configure via Pydantic Settings (`ORION_DB_*`) — never hardcode URLs.
- Always use context managers (`async with async_session() as session:`).
- Never create a module-level engine in code that tests import — tests
  rebind to in-memory SQLite via `conftest.py`.
- Migrations: alembic. Generate with `uv run alembic revision --autogenerate -m
  "..."`, then **read and edit** the generated file before committing.

## Safety-critical code

Extra caution when touching these modules — all changes must preserve
existing guards and add tests for new ones:

| Module | Guard |
|---|---|
| `execution/execution_engine.py` | Options-only check; `ORDER_ID_PREFIX = "orion_"`; routes through Gateway |
| `execution/risk/manager.py` | Daily loss limit, max positions, Greeks limits, sector concentration, 0DTE winddown, correlation-adjusted sizing |
| `execution/signal_preflight.py` | Schema + freshness gates before execution |
| `execution/fill_processor.py` | Filters fills by `orion_` prefix |
| `core/circuit_breaker.py` | Per-strategy + global breakers |
| `core/service_lease.py` | Single-instance guard; TTL `SERVICE_LEASE_STALE_SECONDS=120` |
| `core/market_schedule.py` | Trading-hours boundary |
| `core/promotion_rules.py` | Solver lifecycle gating |

Rules:

- **Never weaken** kill switches, daily loss limits, position caps, or paper-mode
  defaults without an explicit instruction from the user.
- **Paper mode is the default** — every config and wrapper script must default
  to it.
- **Test risk boundaries explicitly.** Tests must exist for `max_daily_loss`,
  `max_positions`, `max_portfolio_delta`, 0DTE winddown, and the circuit-breaker
  open/close transitions.

## Position attribution

The Alpaca paper account is shared by multiple Empire trading systems. Orion's
isolation depends on three layers — **preserve all three:**

1. `ORDER_ID_PREFIX = "orion_"` in `execution/execution_engine.py`. Every order
   gets `client_order_id = "orion_" + uuid`.
2. `_sync_risk_from_gateway` queries the `orders` table for tickers with
   `orion_`-prefixed orders, then only loads those positions into the risk
   manager.
3. `FillProcessor` rejects fills whose `client_order_id` doesn't start with
   `orion_`. `OrderRecord.system` defaults to `"orion"`.

If you change order submission or risk-sync code, run the integration tests
that exercise the multi-system attribution path before merging.

## Test discipline

- Markers: `unit`, `integration`, `e2e`, `slow` — set the right one. `slow` is
  excluded by default; opt in with `-m slow`.
- Unit tests must be fully isolated: no DB, no network, no filesystem.
- Integration tests use the in-memory SQLite test binding from
  `tests/conftest.py` (sets `DB_URL` and `ORION_STAGE=test` before imports).
- E2E tests override the SQLite binding via `db.configure_db()` and target real
  TimescaleDB on port 5440 — don't run them blind, they assume the DB is up.
- Asyncio: `asyncio_mode = "auto"`; don't decorate every test.

Full test docs: [`testing-guide.md`](testing-guide.md).

## Commits & changelog

- Small atomic commits. Don't pile up multi-file diffs.
- Every behaviour-affecting change updates `CHANGELOG.md` under
  `## [Unreleased]`, grouped by `Added` / `Changed` / `Fixed` / `Removed`.
- Write entries from the user's perspective: *what* changed, not *how*.

## Karpathy guidelines

The four rules from `CLAUDE.md` (think before coding; simplicity first; surgical
changes; goal-driven execution) apply repo-wide. The shortest summary:
**every changed line should trace directly to the user's request.**

## Related

- [`testing-guide.md`](testing-guide.md)
- [`configuration-guide.md`](configuration-guide.md)
- [`system-architecture.md`](system-architecture.md)
- Repo `CLAUDE.md` — authoritative agent guidance
- Monorepo `CLAUDE.md` — cross-repo conventions
