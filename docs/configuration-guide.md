# Orion — Configuration Guide

All runtime configuration is **Pydantic Settings** in `src/orion/config.py`.
Four classes, each with its own env-var prefix.

| Class | Prefix | Concern |
|---|---|---|
| `SystemSettings` | `ORION_` | DB, Gateway, Heber, universe, ML, RAG, metrics |
| `RiskSettings` | `ORION_RISK_` | Daily loss, position caps, Greeks, sector concentration, 0DTE |
| `MetaSearchSettings` | `ORION_META_` | Solver evaluation weights |
| `AgentSettings` | `ORION_AGENT_` | LLM model, AI-Gateway URL/key |

`.env.example` at the repo root lists every supported variable with safe
defaults — copy it to `.env` and edit.

## Stage selector

```bash
ORION_STAGE=paper      # default — safe for dev, shadow runs, Docker
ORION_STAGE=test       # set automatically by tests/conftest.py
ORION_STAGE=live       # real money — requires solver promotion + explicit override
```

The default must remain `paper`. Order submission code defends this in multiple
layers (config default, env-wrapper script default, RiskManager).

## Core endpoints

Canonical names are listed first; the *(legacy alias)* form is still accepted
so existing `.env` files keep working — prefer the recommended name in new config, and set only ONE name per pair: when both are set, the first-listed alias wins (`DB_URL` over `ORION_DB_URL`; `ORION_DISCORD_WEBHOOK_URL` over `DISCORD_WEBHOOK_URL`).

| Variable | Default | Notes |
|---|---|---|
| `ORION_DB_URL` (recommended) / `DB_URL` *(alias — wins if both set)* | `postgresql+asyncpg://orion@localhost:5432/orion_db` | Docker maps host `:5440` → container `:5432`. Native wrapper sets `:5440`. |
| `DATA_GATEWAY_URL` (recommended, wins if both set) / `GATEWAY_URL` *(alias)* | `http://data-gateway:8080` (container) / `http://localhost:8080` (native) | Data-Gateway base URL |
| `DATA_GATEWAY_API_KEY` (recommended, wins if both set) / `GATEWAY_API_KEY` *(alias)* | — | Gateway auth (wrapper defaults to `gw_orion_trading_key_55555` for local) |
| `DISCORD_WEBHOOK_URL` (recommended) / `ORION_DISCORD_WEBHOOK_URL` *(alias — wins if both set)* | — | Discord alert webhook |
| `HEBER_CATALOG_URL` | `http://localhost:8085/api/v1` | Heber catalog health endpoint |
| `HEBER_DATA_ROOT` | `/Volumes/heber/data` (container) / `~/.heber-cache/data` (native) | Parquet cache root |

## Provider credentials

| Variable | Required? | Notes |
|---|---|---|
| `UW_API_KEY` | yes | Unusual Whales |
| `ALPACA_API_KEY` | yes | Alpaca (paper or live) |
| `ALPACA_SECRET_KEY` | yes | Alpaca |
| `ALPACA_PAPER` | default `true` | Set `false` only for explicit live runs |
| `ORION_API_KEY` | yes (for API) | `x-api-key` header for `api/main.py`; unset means auth-required routes return server-config error |

## Risk envelope (`ORION_RISK_*`)

| Variable | Wrapper default | Purpose |
|---|---|---|
| `ORION_RISK_MAX_DAILY_LOSS` | `20000` | USD; kill switch trips at this drawdown |
| `ORION_RISK_MAX_POSITIONS` | `10` | Concurrent open positions |
| `ORION_RISK_ALLOCATED_EQUITY` | `100000` | Orion's slice of the shared paper account — sizing % computes off this, not pooled equity |

Other `ORION_RISK_*` knobs (see `config.py`): Greeks limits (`MAX_PORTFOLIO_DELTA`,
`MAX_PORTFOLIO_GAMMA`, `MAX_PORTFOLIO_VEGA`), sector concentration, 0DTE
winddown thresholds, correlation-sizing weights.

Per-regime risk multipliers live in `config/regime_risk.yaml` (not env-vars).

## Signal / ML tuning

| Variable | Wrapper default | Purpose |
|---|---|---|
| `ORION_ML_PREFILTER_THRESHOLD` | `0.05` | LightGBM score gate before solver vote |
| `ORION_CIRCUIT_BREAKER_ENABLED` | `false` | Per-strategy breaker |
| `ORION_GLOBAL_CIRCUIT_BREAKER_ENABLED` | `false` | Global kill on consecutive failures |
| `ORION_REQUIRE_ROLLUPS_FOR_SIGNALS_LIVE` | `false` | Forces fresh-rollup gate on live signals |

## Runtime identifiers

| Variable | Purpose |
|---|---|
| `ORION_RUN_ID` | Tagged into every structured log line. Default `native_execution` in the native wrapper. |
| `ORION_LEASE_OWNER_ID` | Single-instance guard key. **Must differ** between Docker (`orion_execution_compose`, `orion_ingestion_compose`) and native (`orion_execution_native`, `orion_ingestion_native`). |

If both Docker + native attempt the same role at the same time, the second to
start raises `RuntimeError` from `service_lease.acquire_service_lease` because
the lease row already exists. Lease TTL is 120 s (`SERVICE_LEASE_STALE_SECONDS`)
— after that the row is considered stale and reclaimable.

## Logging

| Variable | Default | Notes |
|---|---|---|
| `ORION_LOG_FORMAT` | `json` | Mapped to `EMPIRE_LOG_FORMAT`. Use `human` for console dev. |
| `ORION_LOG_DIR` | `./logs` | Mapped to `EMPIRE_LOG_DIR`. Native wrapper points at `Orion/logs/`. |
| `EMPIRE_LOG_LEVEL` | `INFO` | `DEBUG` / `WARNING` / `ERROR` accepted |

Daily-rotating files under the log dir:

- `execution_native.log`, `ingestion_native.log` (structured Python output)
- `execution_native.stdout.log`, `execution_native.stderr.log` (launchd captures)
- `launchd_health.log` (alert rows, JSON one-per-line)
- `orphan_close.log` (only when the one-shot orphan plist runs)

Retention: 14 days by default.

## Agent / LLM (`ORION_AGENT_*`)

| Variable | Purpose |
|---|---|
| `ORION_AGENT_MODEL` | LLM model id used by EOD review + MetaSearch |
| `ORION_AGENT_AI_GATEWAY_URL` | AI-Gateway endpoint (the Go service) |
| `ORION_AGENT_AI_GATEWAY_KEY` | AI-Gateway auth |

**Never change model IDs without explicit user permission** — this rule comes
from the monorepo `CLAUDE.md` and applies repo-wide.

## Meta-search (`ORION_META_*`)

Scoring weights used by `MetaSearchAgent` when ranking solver variants:
information ratio, max drawdown penalty, fill-rate weight, etc. See
`config.py` for the full list — defaults are tuned and should not be moved
without solver-evaluation evidence.

## Resolving common config issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: another lease owner holds service_lease_*` | Docker + native running same role | Stop one; verify with `docker compose ps` and `launchctl print gui/$(id -u)/com.empire.orion.<role>` |
| `[Errno -3] Temporary failure in name resolution` | Container reverted to `host.docker.internal:8080` | Use the external `data-gateway_default` network — see `docker-compose.yml` |
| `503` from `/search` or `/flows` | pgvector / embeddings / Heber down | Check `mcp-server`, `indexer`, `heber-sync` containers |
| ML scorer using stale features | Heber Gold sync stuck | `docker logs orion_heber_sync`; inspect `~/.heber-cache/data/gold/` |
| API returns config-error for auth routes | `ORION_API_KEY` unset | Set it in `.env`, restart |

## Related

- `.env.example` — full env-var template
- `config/regime_risk.yaml` — regime → risk multiplier table
- [`deployment-guide.md`](deployment-guide.md) — where each setting is consumed
- [`code-standards.md`](code-standards.md) — env-var naming rules
