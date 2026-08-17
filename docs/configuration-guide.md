# Orion — Configuration Guide

All runtime configuration is **Pydantic Settings** in `src/orion/config.py`.
Three classes own the supported environment variables.

| Class | Prefix | Concern |
|---|---|---|
| `SystemSettings` | `ORION_` plus explicit aliases below | DB, Gateway, Heber, universe, ML, alerts, metrics |
| `RiskSettings` | `ORION_RISK_` | Daily loss, position caps, Greeks, sector concentration, 0DTE |
| `HeuristicWeights` | `ORION_HEURISTIC_` | Flow pre-filter weights |

`.env.example` at the repo root is the local configuration template — copy it
to `.env` and edit.

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

## Enabling a dedicated Alpaca account (when a 4th paper key frees up)

Today Orion routes every order through Data-Gateway into a **shared** Alpaca
paper account pooled across six trading systems. Only three paper keys exist and
all three are in use, so Orion cannot have its own account yet. The config below
is **scaffolded and dormant** — nothing reads it, and the default behavior is
unchanged. The moment a fourth key frees up, enabling a dedicated account is a
small config + client task, not a rewrite.

### The dormant env vars

These are `SystemSettings` fields (`ORION_` prefix). They are deliberately named
`ORION_ALPACA_*` so they never collide with the shared-account `ALPACA_API_KEY`
/ `ALPACA_SECRET_KEY` vars above.

| Variable | Default | Notes |
|---|---|---|
| `ORION_ALPACA_API_KEY` | — (None) | Dedicated-account key. Unset = dormant. |
| `ORION_ALPACA_SECRET_KEY` | — (None) | Dedicated-account secret. Unset = dormant. |
| `ORION_ALPACA_PAPER` | `true` | Keep `true` — paper-mode default holds. |
| `ORION_BROKER_MODE` | `gateway` | `gateway` = today's shared-account path. `direct` = future dedicated path. |

`ORION_BROKER_MODE` is validated at settings load: `direct` (not yet
implemented) and any unknown value are **coerced to `gateway` with a CRITICAL
log** rather than raising. SystemSettings instantiates at import in every
service — including the dead-man watchdog — so a raising validator would turn a
stray `.env` value into a fleet-wide boot kill switch with no surviving
alerter. The CRITICAL log line is the signal to fix the env var.

### Order of operations (the day a key frees up)

1. **Implement the direct broker client first** (separate future task): a thin
   alpaca-py `TradingClient` adapter behind the same interface
   `GatewayTradingClient` exposes, wired so `ExecutionEngine` selects it when
   `broker_mode == "direct"`. alpaca-py `0.43.2` is already an installed,
   importable dependency (`alpaca.trading.client.TradingClient`,
   `alpaca.data`) — see `tests/unit/test_alpaca_sdk_ready.py`.
2. **Set the dedicated credentials**: `ORION_ALPACA_API_KEY`,
   `ORION_ALPACA_SECRET_KEY`, `ORION_ALPACA_PAPER=true` in the native wrapper /
   `.env`.
3. **Flip `ORION_BROKER_MODE=direct`** only after step 1 lands. Until then the
   load-time guard keeps `direct` from starting a half-mode.

### Payoff — what becomes deletable

A dedicated account means Orion is the only writer, so the shared-account
machinery built to survive a pooled account can be removed (blast radius for
future-you):

- **`orion_` attribution filtering** — `src/orion/execution/attribution.py`
  (`ORDER_ID_PREFIX`, `is_orion_order`, the `orion_%` client-order-id LIKE
  filter) and the `_sync_risk_from_gateway` position filter in
  `src/orion/execution/execution_engine.py`. With a sole-owner account, every
  position/fill is Orion's — no need to tag and filter.
- **Shared-equity seeding gymnastics** — `seed_equity_baseline` and the
  one-shot `_equity_seeded` / `_peak_equity_seeded` capping to
  `config.allocated_equity` in `src/orion/execution/risk/manager.py`. A
  dedicated account reports Orion-only equity directly, so the slice-capping
  (built to stop sizing off the ~$1M pool) is unnecessary.
- **DTBP backoff** — `_dtbp_backoff_until` / `_DTBP_BACKOFF_SECONDS` and the
  `40310000` handling in `src/orion/execution/execution_engine.py`. Day-trade
  buying-power exhaustion was driven by sibling systems on the shared account;
  a dedicated account removes the cross-system contention.
- **Wash-block handling** — the `42210000` intent-mismatch / native-close
  escalation path in `src/orion/execution/execution_engine.py`. Self-block and
  sibling-driven wash conflicts on the shared account go away when Orion owns
  the account.

These stay in place until `broker_mode` is actually flipped to `direct`; this
section is the map for removing them then.

## UW flow delivery

| Variable | Default | Purpose |
|---|---|---|
| `ORION_FLOW_SOURCE` | `poll` | UW flow delivery path: `poll` = Heber Silver (today's data), `shadow` = Gateway WS + Heber parity logging, `push` = push-primary + poll fallback |
| `ORION_INITIAL_FLOW_LOOKBACK_MINUTES` | `60` | How far back to seed flow on startup |
| `ORION_FLOW_POLL_OVERLAP_SECONDS` | `30` | Overlap window to avoid gaps between polls |
| `ORION_GOLD_FEATURE_LOOKBACK_DAYS` | `7` | Bounds ML Gold feature reads to last N days' partitions (prevents born-stale candidates from overnight catch-up bursts) |

## Risk envelope (`ORION_RISK_*`)

| Variable | Wrapper default | Purpose |
|---|---|---|
| `ORION_RISK_MAX_DAILY_LOSS` | `2000` | USD; native execution-wrapper default |
| `ORION_RISK_MAX_POSITIONS` | `20` | Native execution-wrapper default |
| `ORION_RISK_ALLOCATED_EQUITY` | `100000` | Orion's slice of the shared paper account — sizing % computes off this, not pooled equity |

Other `ORION_RISK_*` knobs (see `config.py`): Greeks limits (`MAX_PORTFOLIO_DELTA`,
`MAX_PORTFOLIO_GAMMA`, `MAX_PORTFOLIO_VEGA`), sector concentration, 0DTE
winddown thresholds, correlation-sizing weights.

Per-regime risk multipliers live in `config/regime_risk.yaml` (not env-vars).

## Signal / ML tuning

| Variable | Wrapper default | Purpose |
|---|---|---|
| `ORION_ML_PREFILTER_THRESHOLD` | `0.05` | LightGBM score gate before solver vote |
| `ORION_ML_STALE_MODEL_POLICY` | `warn` | What to do if ML scorer is stale: `skip` = use heuristic fallback, `warn` = load and warn, `bypass` = skip ML gating |
| `ORION_HEURISTIC_CAP_LIVE` | `0.65` | Maximum heuristic pre-filter score in live mode |
| `ORION_CIRCUIT_BREAKER_ENABLED` | `false` | Per-process broker-error breaker. The global breaker has no disable switch — see [circuit_breaker runbook](runbooks/circuit_breaker.md) |
| `ORION_REQUIRE_ROLLUPS_FOR_SIGNALS_LIVE` | `false` | Forces fresh-rollup gate on live signals |

## Exit fallback rules

Deterministic exits independent of the ML exit classifier (triggers when classifier is unavailable):

| Variable | Default | Purpose |
|---|---|---|
| `ORION_EXIT_FALLBACK_PROFIT_TARGET_PCT` | `1.00` | Exit when position gains ≥100% (doubles in value) |
| `ORION_EXIT_FALLBACK_MIN_DTE` | `1` | Exit when option DTE < this value |
| `ORION_EXIT_FALLBACK_MAX_DRAWDOWN_FROM_PEAK_PCT` | `0.50` | Exit when position drawdown from peak exceeds 50% |

## Heuristic weights

Scoring weights for the flow heuristic pre-filter (used when LightGBM is unavailable or bypassed). All have `ORION_HEURISTIC_` prefix. Defaults are tuned — change only with solver-evaluation evidence.

See `config.py` → `HeuristicWeights` for the full field list.

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

Native files under the log dir:

- `execution_native.log`, `ingestion_native.log` (structured Python output)
- `execution_native.stdout.log`, `execution_native.stderr.log` (launchd captures)
- `launchd_health.log` (alert rows, JSON one-per-line)
- `orphan_close.log` (only when the one-shot orphan plist runs)

`com.empire.orion.log-rotation` checks these daily, rotates files above 100 MiB,
and retains compressed archives for 14 days.

## Resolving common config issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: another lease owner holds service_lease_*` | Docker + native running same role | Stop one; verify with `docker compose ps` and `launchctl print gui/$(id -u)/com.empire.orion.<role>` |
| `[Errno -3] Temporary failure in name resolution` | Container reverted to `host.docker.internal:8080` | Use the external `data-gateway_default` network — see `docker-compose.yml` |
| `503` from `/flows` | Heber read unavailable | Check `heber-sync` and the host cache |
| ML scorer using stale features | Heber Gold sync stuck | `docker logs orion_heber_sync`; inspect `~/.heber-cache/data/gold/` |
| Born-stale candidates (>600s age at entry) | `ORION_GOLD_FEATURE_LOOKBACK_DAYS` too large or Heber Gold sync stale | Check `~/.heber-cache/data/gold/` freshness; default 7 days is optimal |
| `Failed to load model ... version` | Artifact was trained with an incompatible scikit-learn release | Retrain under the current lock; Orion rejects it and uses heuristic scoring meanwhile |
| API returns config-error for auth routes | `ORION_API_KEY` unset | Set it in `.env`, restart |

## Related

- `.env.example` — full env-var template
- `config/regime_risk.yaml` — regime → risk multiplier table
- [`deployment-guide.md`](deployment-guide.md) — where each setting is consumed
- [`code-standards.md`](code-standards.md) — env-var naming rules
