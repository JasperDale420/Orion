# Data-Gateway Investigation — 2026-06-08

Read-only investigation (no live services modified) into two issues affecting Orion.
Gateway repo: `/Users/jacobmcmillan/Empire/Data-Gateway` (container `data-gateway`, `http://localhost:8080`).

---

## ISSUE 1 — `MU260612P00790000` close rejections (HIGH) — **NOT a Gateway bug**

### Conclusion
The Gateway `/positions/{symbol}` endpoint is a live, **uncached** pass-through to Alpaca and was correct
at every moment. The `40310000 "cash-secured put"` rejections were caused by **Orion submitting a second
full-size SELL-to-close while its own prior close order was still OPEN and reserving the entire 2-lot
long** — so Alpaca priced the new sell as *opening* a cash-secured short ($158,000 = 2 × $790 × 100).

### Evidence the read is live/uncached
- `gateway/api/alpaca/trading.py:762-774` — `get_position` uses `_execute_trading_call` with **no cache**
  (only assets/calendar are cached, `:895-987`).
- `gateway/providers/alpaca/trading.py:336-358` — calls `self._trading_client.get_open_position()` directly;
  no Redis / snapshot / stale-on-error fallback.
- `gateway/providers/alpaca/_base.py:128-145` — a **single** alpaca-py `TradingClient` serves both
  `get_open_position` and `submit_order`; they cannot read from different sources.
- `gateway/api/alpaca/common.py:167-216` — the cache wrapper has no stale-on-error path and isn't used by positions.

### Smoking gun — order lifecycle (verified live via Gateway order history)
| order (coid) | side | qty | filled | limit | status | submitted (UTC) | canceled (UTC) |
|---|---|---|---|---|---|---|---|
| `a0e6ef2c…` | buy | 1 | 1 | 5 | filled | 2026-06-04 18:22:14 | — |
| `8a562284…` | buy | 1 | 1 | 5 | filled | 2026-06-04 18:22:32 | — |
| `234e2858…` | sell | 2 | **0** | **19.4** | canceled | 2026-06-08 13:30:55 | **2026-06-08 14:00:56** |
| `1cf1aa97…` | sell | 2 | 2 | 6 | filled | 2026-06-08 14:00:57 | — |

The sell-to-close at an off-market **19.4** limit (stale mark) sat OPEN ~30 minutes reserving both
contracts. The 09:31:57 / 09:33:03 ET rejections were *new* full-size sells submitted into that window:
`sellable = long(2) − open_sell(2) = 0` → priced as an opening cash-secured short → `40310000`. The stuck
order finally cancelled at 14:00:56; the replacement at limit 6 was submitted 0.2s later and filled,
genuinely closing the long. The later `DELETE → 404 POSITION_NOT_FOUND` reflects the position being flat
only **after** 14:00:57 — not a phantom.

**Sibling round-trip ruled out:** full order history for the contract is only `orion_`-prefixed (2 buys
06-04, 2 sells 06-08). The "vanishing long" was Orion's own long, reserved by Orion's own stuck order.

### Recommended fix (Orion, not Gateway)
- In `_live_position_qty` / the close path (`src/orion/execution/execution_engine.py`), compute
  **net-sellable = `long_qty − Σ(open same-side close-order qty, incl. pending_cancel)`** before resubmitting,
  and **defer** (don't submit a new full-size close) until any prior close order has actually cleared at the
  broker — `_cancel_resting_orion_orders` issues a cancel but the order can sit in `pending_cancel` still
  reserving the position, which is the race that produced the `40310000`s.
- Secondary: the first close limit (19.4) was off-market because of a **stale mark** — a sanity bound on the
  close limit would stop the un-fillable resting order from existing in the first place.
- **Needs market-hours validation** (the race is timing-dependent on broker cancel latency).

Confidence: **HIGH**.

---

## ISSUE 2 — WebSocket "timed out during opening handshake" (MEDIUM)

### Conclusion
Orion's `websockets.connect()` set no `open_timeout` → default **10s**
(`src/orion/connectors/gateway_stream_client.py:127-131`). The Gateway is slow to complete
`websocket.accept()` under market-open load because:
- `gateway/core/connections.py:67-91` — `connect()` takes `self._lock` (`:72`) **before**
  `await websocket.accept()` (`:84`); the same lock is held by `disconnect`/`authenticate`, so accept is
  needlessly serialized behind the registry.
- `Dockerfile:64-68` — single uvicorn worker, **no `--workers`/`--limit-concurrency`/`--backlog`**. One
  event loop serves the REST trading proxy + WS accepts + broadcast fan-out (`connections.py:172-235`,
  `asyncio.gather` over all connections).
- The codebase documents the contention: `gateway/api/alpaca/trading.py:149-155` ("outstanding asyncio
  tasks/timers contend with the WebSocket keepalive task for event-loop CPU"). The same starvation that
  times out keepalive delays `accept()`.

Not auth (auth runs *after* accept, `websocket.py:34,41-47`) and not the 1000-client cap (`config.py:174`;
only ~11 concurrent opens seen). Caveat: container logs retain only back to `2026-06-07T18:27Z`, so the
06-05/06-07 *morning* events rolled off; retained 06-07 shows net reconnect churn (32 closed vs 11 opened).

### Recommended fix
- **Gateway (real fix):** move `await websocket.accept()` *before* taking `self._lock` in
  `ConnectionManager.connect()`, holding the lock only for the dict mutation / capacity check. Add uvicorn
  `--limit-concurrency` / `--backlog`.
- **Orion (stopgap, applied):** `open_timeout=30` in `gateway_stream_client.connect()`.

Confidence: **MEDIUM-HIGH** on mechanism, **MEDIUM** on sole cause.

---

_Investigation by background subagent; order lifecycle independently re-verified against the live Gateway
order history before acting._
