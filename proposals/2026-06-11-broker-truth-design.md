# Broker-Truth Design for Per-Orion Realized-PnL Reconciliation (O8)

**Date:** 2026-06-11
**Status:** Design (read-only investigation — no code changed)
**Scope:** Determine whether Orion can obtain a TRUSTWORTHY per-orion realized-PnL
broker truth for daily reconciliation on the SHARED Alpaca paper account.

---

## TL;DR Verdict

The user's chosen direction — **"use the Alpaca portfolio / realized-PnL endpoint
if it exists"** — is **INFEASIBLE on the shared account.** Every Alpaca
account-state surface (portfolio-history, positions) is **account-level only** and
cannot be filtered to orion's own fills (`client_order_id` prefix `orion_`). On an
account shared across 3Roses/Cerberus/Kairos/Orbit/WhaleHunter, any such number is
contaminated and unusable as per-orion truth.

**Recommended approach (ranked #1): orion-own-fills realized-PnL reconstruction.**
Orion already durably records EVERY fill (entry and exit) with side, qty, price,
timestamp, and `client_order_id` in its own `fills` table
(`models_execution.py::FillRecord`). The correct broker truth for a multi-day
close is: take orion's own closing fills and join them to orion's own entry fills
on the same symbol (FIFO/avg), computing `realized = exit_proceeds - entry_cost`.
This is per-orion-clean by construction (filtered to `orion_%`), correct for
multi-day SWING/POSITION holds, and needs **zero Gateway changes**. The current
same-day cashflow sum in `reconcile_pnl.py` is the actual bug; this replaces it.

---

## 1. Alpaca Surfaces — Viability Verdicts

Installed SDK in the Gateway venv: **alpaca-py 0.43.2**
(`/Users/jacobmcmillan/Empire/Data-Gateway/.venv/.../alpaca_py-0.43.2.dist-info`).

### 1a. Portfolio history (`GetPortfolioHistoryRequest` / `get_portfolio_history`) — ACCOUNT-LEVEL ONLY → UNUSABLE

- **SDK:** `TradingClient.get_portfolio_history()` exists
  (`alpaca/trading/client.py:350`), hits `GET /account/portfolio/history`.
- **Model:** `PortfolioHistory` (`alpaca/trading/models.py`) returns
  `timestamp[]`, `equity[]`, `profit_loss[]`, `profit_loss_pct[]`, `base_value`,
  `cashflow{}`. **There is no per-order, per-symbol, or per-client dimension.**
- **Gateway:** exposed at `GET /api/v1/alpaca/portfolio/history`
  (`gateway/api/alpaca/trading.py:961`; provider `trading.py:500`).
- **Verdict: CONTAMINATED on the shared account.** `profit_loss` is the whole
  account's PnL across all 5+ systems. There is no `client_order_id` filter on
  this endpoint at Alpaca, so it can NEVER be reduced to orion's fills. **This is
  the user's chosen direction, and it does not work here.**

### 1b. Per-position realized PnL — DOES NOT EXIST (only UNREALIZED, for OPEN positions)

- **Model `Position`** (`alpaca/trading/models.py`) carries `avg_entry_price`,
  `qty`, `cost_basis`, `market_value`, **`unrealized_pl` / `unrealized_plpc` /
  `unrealized_intraday_pl`** — and **no `realized_pl` field of any kind.**
- Alpaca only returns OPEN positions (`get_all_positions` /
  `get_open_position`); once a position is flat it disappears entirely. There is
  **no closed-position realized-PnL endpoint** in the Trading API.
- Even the unrealized number is account-level blended: on a shared ticker,
  `avg_entry_price` is the broker's blended cost across all systems' fills.
- **Verdict: Alpaca exposes NO per-position realized PnL. Unusable.**

### 1c. Account activities (`FILL`) — NOT in alpaca-py 0.43.2 TradingClient; Gateway route is DEAD

- **SDK fact:** `grep get_account_activities` in
  `alpaca/trading/client.py` → **0 matches.** The method exists ONLY on
  `alpaca/broker/client.py` (the Broker API, different creds/product). The
  earlier finding is **confirmed: alpaca-py 0.43.2 `TradingClient` has no
  `get_account_activities`.**
- **The `TradeActivity` MODEL does exist** (`alpaca/trading/models.py`): fields
  `transaction_time`, `type`, `price`, `qty`, `side`, `symbol`, `order_id`,
  `leaves_qty`, `cum_qty` — i.e. per-fill data with **`order_id` but NOT
  `client_order_id`** (orion attribution would require an `order_id` →
  `client_order_id` join).
- **Gateway is broken here, not just missing:**
  - Route IS registered: `GET /api/v1/alpaca/account/activities`
    (`gateway/api/alpaca/account.py:70`).
  - It calls `provider.get_account_activities(...)`
    (`gateway/providers/alpaca/trading.py:641`), which calls
    `self._trading_client.get_account_activities(...)` (`trading.py:650`).
  - But `self._trading_client` is a **`TradingClient`**
    (`gateway/providers/alpaca/_base.py:9,56`) — which has no such method.
  - **Therefore this route raises `AttributeError` → 5xx at runtime. It has
    never worked.** The Gateway docstring in `gateway_trading_client.py:295-309`
    and `gateway/api/alpaca/trading.py:555` already admit activities are not
    reachable via the installed SDK.
- **Raw REST reachability:** the underlying Alpaca endpoint
  `GET /v2/account/activities?activity_types=FILL` exists at the broker and IS
  reachable with the keys the provider already holds. Precedent: the DNE method
  does exactly this — raw `httpx.post(f"{self._trading_base_url}/v2/positions/.../do-not-exercise", headers={APCA-API-KEY-ID, APCA-API-SECRET-KEY})`
  (`gateway/providers/alpaca/trading.py:476-483`).
  **But the Gateway has NO generic passthrough** — only typed per-endpoint
  methods. So enabling activities requires a NEW provider method (raw httpx GET to
  `/v2/account/activities`, with date pagination via `after`/`until` on
  `transaction_time`, plus an `order_id`→`client_order_id` join for attribution).
- **Verdict: activities CAN be made reachable (raw REST), but only via new
  Gateway+provider code, and attribution needs an `order_id` join because the
  activity carries no `client_order_id`. More plumbing than option #1 for the
  same answer.**

---

## 2. Orion's Own Data — Entry Cost Basis & Exit Linkage

### Journal (`trade_journal_entries` / `TradeJournalEntry`)
`src/orion/storage/models_trade_journal.py:13-45`. One row per DECISION. It has a
SINGLE `filled_qty` / `filled_avg_price` / `filled_at_utc` triple and a single
`realized_pnl`. The entry-fill pointers are **OVERWRITTEN** by the exit fill in
some paths, and `realized_pnl` is back-filled by
`persistence.py::persist_realized_pnl_to_journal` (`persistence.py:448`) which
matches the **oldest still-open entry by `ticker` only** (admitted lossy for
multi-partial closes; `persistence.py:462-465`). **The journal does NOT cleanly
retain a separate entry cost basis vs exit price per closed position** — it is a
derived attribution surface, not a fills ledger. Not a reliable cost-basis source
on its own.

### Fills (`fills` / `FillRecord`) — THE KEY ASSET
`src/orion/storage/models_execution.py` (`FillRecord`). One row **per broker
order** (unique on `broker_order_id`), carrying:
`ticker`, `broker_order_id`, **`client_order_id`** (orion-attributable by
`orion_` prefix), **`filled_qty`**, **`filled_avg_price`**, **`side`**,
**`filled_at_utc`**, `raw_json`.

Every fill — **entry AND exit** — is persisted here unconditionally via
`persist_fill_record(fill)` on every processed fill
(`fill_processor.py:122`, regardless of open/close). So Orion already has a
durable, per-orion, side-tagged, priced, timestamped fills ledger. **This is
exactly the data needed to reconstruct realized PnL for a multi-day close:**
match orion's exit fills to orion's prior entry fills on the same symbol.

### In-memory cost basis (`RiskManager.positions[ticker]["avg_entry"]`) — NOT durable, and the restart path is CONTAMINATED
- The live realized-PnL math (`manager.py:694-723`) is correct:
  `pnl = (price - old_entry) * qty_closing`, where `old_entry` is built purely
  from orion's own fills via `process_fill`. This is the in-session truth and is
  per-orion-clean.
- BUT `_save_state` (`manager.py:589-612`) persists only scalar aggregates
  (`current_daily_loss`, `current_equity`, `peak_equity`, …) — **NOT the
  per-ticker `positions` book.** The avg_entry book is volatile.
- On restart, `sync_with_broker` re-seeds `self.positions[...]["avg_entry"]` from
  **account-level `get_all_positions()`** (`manager.py:985-998`). On a shared
  ticker that blended `avg_entry_price` mixes other systems' fills → **the
  cost-basis used for a post-restart close can be contaminated.** This is a second
  latent correctness bug, independent of the reconciliation job.

**Conclusion for §2:** the cleanest correct entry cost basis lives in Orion's own
`fills` table (durable, orion-filterable, per-fill), NOT in the journal and NOT in
the (volatile, restart-contaminated) in-memory risk book.

---

## 3. Recommendation (Ranked)

### (a) Portfolio / realized-PnL endpoint IF per-orion-attributable — **REJECTED (infeasible)**
Confirmed in §1a/§1b: no Alpaca Trading-API surface (portfolio-history or
positions) is per-orion-attributable. Account-level only → contaminated on the
shared account. **The user's chosen direction cannot yield per-orion truth and is
abandoned.**

### (b) Activities-based fill reconstruction joined to entry cost basis — viable but heavier
Requires: new Gateway provider method (raw `httpx` GET `/v2/account/activities`,
date-paginated on `transaction_time`), a fixed/replaced `/account/activities`
route, and an `order_id`→`client_order_id` join for attribution (activities lack
`client_order_id`). Same final answer as (c) but with broker round-trips and new
Gateway code. Keep as an optional independent cross-check, not the primary.

### (c) ★ RECOMMENDED: Orion-own-fills realized-PnL reconstruction
Reconstruct realized PnL entirely from Orion's own `fills` table — no
account-level surface, no Alpaca realized-PnL endpoint, no Gateway change.

**Why it wins:** correct for multi-day SWING/POSITION closes (joins exit to the
actual prior entry cost, not same-day cashflow); per-orion-clean by construction
(filter `client_order_id LIKE 'orion_%'`); uses data Orion already persists; and
it sidesteps the broken activities route and the contaminated portfolio endpoint.

**Implementation shape:**
- **Data source:** `fills` table (`FillRecord`), filtered to `client_order_id
  LIKE 'orion_%'`. Per symbol, order fills by `filled_at_utc`.
- **Algorithm:** maintain a per-symbol running lot book (FIFO or weighted-avg) over
  ALL orion fills up to and including day `d`. A buy adds to the lot book at its
  fill price; a sell (for a long) realizes `(sell_price - lot_cost) * qty *
  multiplier` and consumes lots. Realized PnL for day `d` = sum of realizations
  whose CLOSING fill `filled_at_utc` ∈ day `d`. Use the OCC multiplier (100) for
  option symbols (reuse `is_occ_option_symbol`, `is_orion_owned` from
  `execution/attribution.py`, already imported in `reconcile_pnl.py:97`).
- **Multi-day correctness:** because the lot book is built from the full orion fill
  history (not a same-day window), an exit today against an entry from days ago
  realizes `proceeds - entry_cost`, NOT full proceeds. This is the exact fix for
  the O8 bug in `reconstruct_broker_realized_pnl` (`reconcile_pnl.py:213-262`),
  which today sums same-day signed cashflow and over-counts multi-day closes.
- **Coverage / trust:** the fills table is the broker's own fills as Orion
  observed them (written from Gateway fill events + reconciler). Keep the existing
  `BROKER_UNAVAILABLE` discipline: if the fills ledger is provably incomplete for
  the day (e.g. an exit fill whose symbol has no prior orion entry lots — a
  "naked close"), flag that symbol as untrusted in `details` rather than emitting
  a wrong number. This mirrors the current `non_flat_symbols` flagging.
- **Orion changes:** rewrite `reconstruct_broker_realized_pnl` (and its feeder
  `_fetch_broker_coverage`) in `src/orion/jobs/reconcile_pnl.py` to read the
  `fills` table instead of paginating closed orders; drop the same-day cashflow
  sum. Pure function stays unit-testable against canned fill rows.
- **Gateway changes:** NONE.
- **Optional hardening (separate):** (i) make `_save_state` persist the per-ticker
  avg_entry book, or rebuild it from `fills` on startup instead of from
  account-level `get_all_positions()`, to fix the restart-contamination bug at
  `manager.py:985-998`; (ii) stop the journal from overwriting entry fill pointers
  with exit data so the journal cross-check stays meaningful.

### (d) Scope reconciliation to same-day round-trips + mark multi-day untrusted — fallback only
If (c) is deferred, the minimum correct stopgap is to make
`reconstruct_broker_realized_pnl` only trust symbols that are FLAT and fully
round-tripped within day `d` (entry and exit both today), and mark any symbol with
a multi-day component `untrusted` in `details` (suppress it from drift). This stops
the over-counting from producing false MISMATCH/MATCH, at the cost of leaving
multi-day closes unreconciled. Strictly inferior to (c); use only as a holding fix.

---

## Key file:line citations

**Orion**
- `src/orion/jobs/reconcile_pnl.py:213-262` — `reconstruct_broker_realized_pnl`, the
  same-day signed-cashflow sum that over-counts multi-day closes (the O8 bug).
- `src/orion/storage/models_execution.py` — `FillRecord` (`fills`): per-fill
  `client_order_id`, `filled_qty`, `filled_avg_price`, `side`, `filled_at_utc`.
- `src/orion/execution/persistence.py:375-445` — `persist_fill_record`, writes EVERY
  fill (entry+exit) to `fills`.
- `src/orion/execution/persistence.py:448-506` — `persist_realized_pnl_to_journal`,
  ticker-only oldest-open match (lossy; journal is not a clean cost-basis source).
- `src/orion/execution/fill_processor.py:94-122` — `process_fill` call + unconditional
  `persist_fill_record`.
- `src/orion/execution/risk/manager.py:684-723` — correct in-session realized-PnL math
  off orion-only avg_entry.
- `src/orion/execution/risk/manager.py:589-612` — `_save_state` persists scalars only
  (avg_entry book is volatile).
- `src/orion/execution/risk/manager.py:985-998` — restart re-seeds avg_entry from
  account-level `get_all_positions()` (shared-account contamination).
- `src/orion/clients/gateway_trading_client.py:295-330` — `get_account_activities`
  wrapper + docstring noting activities lack `client_order_id`.

**Data-Gateway (alpaca-py 0.43.2 in its .venv)**
- `gateway/api/alpaca/trading.py:961-979` — portfolio-history route (account-level).
- `gateway/providers/alpaca/trading.py:500-525` — provider `get_portfolio_history`.
- `gateway/api/alpaca/account.py:70-86` — `/account/activities` route (DEAD).
- `gateway/providers/alpaca/trading.py:641-656` — provider `get_account_activities`
  calling a TradingClient method that does not exist → AttributeError.
- `gateway/providers/alpaca/_base.py:9,56` — provider builds a `TradingClient`.
- `gateway/providers/alpaca/trading.py:476-483` — DNE raw-httpx precedent for reaching
  `/v2/...` directly (how activities COULD be wired).
- `.venv/.../alpaca/trading/client.py:350` — `get_portfolio_history`; no
  `get_account_activities` anywhere in `trading/client.py`.
- `.venv/.../alpaca/trading/models.py` — `Position` has `unrealized_pl` only (no
  realized); `PortfolioHistory` is account-level arrays; `TradeActivity` has
  `order_id` but no `client_order_id`.
- `.venv/.../alpaca/broker/client.py` — the ONLY place `get_account_activities`
  exists (Broker API, not the Trading client Orion uses).
