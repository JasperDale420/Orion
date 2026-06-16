# Flow Push Design — UW Flow Events Pushed from Data-Gateway to Orion (R1 / A2)

Status: DESIGN (read-only discovery deliverable for A2 of `2026-06-11-redesign-plan.md`).
Implementation is B1, behind `ORION_FLOW_SOURCE=poll|shadow|push`. No code changed by this task.

## 0. TL;DR

- **Push does NOT exist today.** The Gateway WS server only fans out Alpaca
  bars/quotes/trades/news via the `StreamMultiplexer`; UW flow is REST-polled
  every 5 min and published to the Redis stream `heber:events` for Heber —
  it never touches the WS fan-out path. A consumer that subscribes
  `{"provider":"uw","feeds":["flow"]}` today gets silently mapped to Alpaca
  SIP and receives nothing (see §1.3). So a **minimal additive Gateway change
  is required** (§2).
- **Event-id parity is automatic, not recomputed.** The UW poller already
  stamps each flow envelope with a content-derived BLAKE2b `event_id`
  (`gateway/core/envelope.py:compute_event_id`). That same id is written
  unchanged into Heber Silver's `event_id` column and is what Orion's poll
  path reads back (`payload["event_id"]`). If the push path delivers **the
  same envelope object the poller already built**, the `event_id` is byte
  identical and Orion's `DeduplicationEngine` collapses push+poll duplicates
  with zero new logic (§4).
- Orion changes are small and additive: teach `GatewayStreamClient` to
  subscribe to the flow channel and emit `UW_FLOW` BronzeEvents, route them
  into the existing `_run_cycle` event list, and gate the whole thing on
  `ORION_FLOW_SOURCE` (§3).
- The WS-down degrade mode + permanent Heber poll fallback already exist and
  compose cleanly: in `push` mode the poll path becomes the gap-filler the
  degrade mode already relies on (§7).

---

## 1. Current-state map (file:line, both repos)

### 1.1 Gateway WS server — protocol (Alpaca-only fan-out)
- `Data-Gateway/gateway/api/websocket.py:25` `websocket_endpoint` — `/ws` route.
  Handshake: client sends `{"action":"auth","key":...}`; server replies
  `{"type":"auth_result","status":"ok",...}` (`:54`). Auth parsing at `:255-288`.
- Message actions handled (`_handle_message`, `:397`): `heartbeat` (`:406`),
  `ping` (`:410`), `subscribe` (`:413`), `unsubscribe` (`:588`), `status`
  (`:682`); unknown → `GW-E3001` (`:692`).
- **Subscribe envelope** (`:413-586`): `{"action":"subscribe","provider":<str=alpaca>,
  "feeds":[...] | "feed":<str>, "symbols":[...]}`. There is **no channel/topic
  concept** beyond `provider`+`feeds`+`symbols`. Permissions enforced:
  provider (`_has_provider_permission`, `:699`), feed (`_has_feed_permission`,
  `:726`), max symbols, `ws_subscriptions_max`.
- **What it pushes today:** ONLY Alpaca market data. The subscribe path routes
  every feed through `AlpacaStreamType.from_feed(feed)` and
  `multiplexer.client_subscribe(...)` with `bars/quotes/trades/news` derived by
  substring match on the feed name (`:490-506`). The wire message for delivered
  data is `{"type":"data","feed":...,"symbol":...,"event_id":...,"envelope":{...},
  "data":{...}}` (Gateway side: `gateway/main.py:142-151`).
- **Decisive gap:** `AlpacaStreamType.from_feed` (`gateway/core/stream.py:117-144`)
  is a dict `.get(feed, cls.STOCKS_SIP)` — any unknown feed (incl. `"flow"`,
  `"flow_alerts"`) **falls back to STOCKS_SIP**, and since `"bars"/"quotes"/
  "trades"/"news"` are not substrings of `flow`, all four subscription lists are
  `None` → `client_subscribe` is a no-op ack. The multiplexer only knows the
  four feed names `("bars","quotes","trades","news")`
  (`stream.py:170 _FEED_NAMES`, `:178 ClientSubscription`). **UW has no WS
  fan-out path at all.**

### 1.2 Gateway WS fan-out machinery (the pattern to reuse)
- `StreamMultiplexer` (`gateway/core/stream.py:813`) maintains per-feed
  `symbol → {client_id}` indices (`SubscriptionManager`, `:181`) and, on each
  upstream Alpaca message, wraps it (`fast_wrap_streaming_event`, `:1282`),
  runs `on_envelope` once for the Heber sink (`:1305`), then fans out via
  `on_broadcast(envelope_json, client_ids)` (`:1326-1331`).
- `on_broadcast` is wired to `ConnectionManager.broadcast_to_connection_ids`
  (`gateway/main.py:283`). `ConnectionManager.broadcast` / `broadcast_to_connection_ids`
  (`gateway/core/connections.py:172`, `:238`) send a pre-serialized JSON message
  to a set of authenticated connections — **this is a generic primitive, not
  bars-specific.** This is what a flow channel reuses.

### 1.3 Gateway UW flow ingest path (poll → Redis → Heber)
- UW is **REST-poll only**; `supports_streaming=False`
  (`gateway/providers/uw/_base.py:100`). Flow fetched via
  `UnusualWhalesProvider.get_flow_alerts(limit)` (`gateway/providers/uw/flow.py:66`).
- `UWPoller._poll_loop` (`gateway/core/uw_poller.py:404`) polls flow every
  `DEFAULT_POLL_INTERVAL=300`s during market hours
  (`_should_poll_flow`, `:295`; loop call `:422-426`).
- `_poll_flow_alerts` (`:558`) → `_poll_single_feed(feed="flow_alerts",
  dedupe_prefix="uw:flow", ...)` (`:503`) → `_build_feed_envelopes` calls
  `wrap_event(..., provider="unusual_whales", feed="flow_alerts", source="rest")`
  (`:480`) → `_publish_envelopes` (`:206`) publishes each envelope to the Redis
  stream **`HEBER_STREAM = "heber:events"`** (`:40`, `:252`) via
  `sink_registry.publish_all_batch` (`:254`).
- **Event envelope / schema** (`gateway/core/envelope.py:35 EventEnvelope`,
  built by `wrap_event` `:291`): fields `event_id, provider, feed, source,
  instrument_type, instrument_key, symbol, ts_event, ts_ingest, schema_version,
  lineage, quality_flags, payload`. For flow, `instrument_key` is
  `option:OCC:<contract>` (from `payload["option_chain"]`, `:355`).
- **event_id semantics** (`compute_event_id`, `:133`): BLAKE2b-128 (32 hex
  chars) of `provider|feed|instrument_key|ts_event.isoformat()|<unique_fields>`.
  For `flow`/`flow_alerts` the unique fields are
  `[expiry, strike, put_call, premium, volume]`
  (`FEED_UNIQUE_FIELDS`, `:187-200`). Content-derived → stable across
  reconnect/replay (that is the whole point — see the `fast_wrap` docstring
  `:457-492`).
- **Sink → Heber → Silver passthrough:** Heber's writer reads `heber:events`,
  and `Transformer` writes `envelope.event_id` straight into the Silver
  `event_id` column (`Heber/heber/writer/transformer.py:327`) and dedups on it
  (`:250-257`). Silver `FlowAlertRecord` (`Heber/heber/models/silver.py:148`)
  has primary key `event_id` (inherited from `SilverBase.event_id`,
  `silver.py:27`). **So the Gateway-minted blake2b id is the Silver row id.**

### 1.4 Orion — current consumer assumptions
- `Orion/src/orion/connectors/gateway_stream_client.py:32 GatewayStreamClient`.
  - Auth handshake `:143-163` matches Gateway. Subscribe is **hardcoded to
    bars**: `{"action":"subscribe","provider":"alpaca","feed":"bars",
    "symbols":[...]}` (`_send_subscribe`, `:201-210`).
  - Receive loop `:331` only processes bar messages (`_is_bar_message`, `:293`;
    `_process_bar_message`, `:372`). It already prefers the Gateway envelope id:
    `event_id = data.get("event_id") or envelope.get("event_id") or
    self._generate_event_id(...)` (`:411`). No flow handling exists.
  - Degrade/restart: `restart()` (`:459`), `is_running` flips False after
    `MAX_RECONNECT_ATTEMPTS=10` (`:27`).
- Ingestion loop `Orion/src/orion/ingestion/service.py`:
  - `_run_cycle` (`:253`): drains WS bars (`:275`), then `flow_events =
    await self._poll_heber_flow(trace_id)` (`:280`), merges, then
    `_normalize_and_dedupe` (`:288`) → `_persist_events` → `_process_features_and_rules`.
  - `_poll_heber_flow` (`:478`): reads Heber Silver `flow_alerts` via
    `get_heber_reader().read_flow(start_time=_last_flow_poll_ts - overlap,
    asof_time=now)` (`:498-503`); watermark `_last_flow_poll_ts` seeded at init
    to `now - initial_flow_lookback_minutes` (`service.py:73`,
    `config.py:181`); overlap `flow_poll_overlap_seconds=120` (`config.py:191`).
  - **Born-stale drop** (`:511-531`): `freshness_cutoff = now -
    max_data_lag_seconds` (600s, `config.py:178`); rows with
    `event_ts < freshness_cutoff` are dropped at ingest (suppresses the
    startup catch-up burst of doomed candidates).
  - `_heber_row_to_event` (`:560`): builds `BronzeEvent(event_type="UW_FLOW",
    source="UW")`. **event_id selection** (`:591-604`):
    `raw_event_id = payload.get("event_id")`; if present use it verbatim; else
    deterministic fallback `uwflow_<sha1(ticker|ts_event|executed_at|premium|
    put_call|strike|expiry|volume)>`. `ts_event` from the Silver `ts_event`
    column (`:607-613`).
  - **Dedup** `Orion/src/orion/processing/deduper.py:19 DeduplicationEngine`:
    `dedupe_batch` (`:55`) keys purely on `event_id` (in-memory FIFO cache
    `:16` + bulk DB `SELECT ... WHERE event_id IN (...)` `:80`). Plus
    `BronzeEvent` ON CONFLICT at persist. **Identical event_ids → collapsed.**
  - Degrade mode: `_check_gateway_stream_health` (`:389`) enters DEGRADED when
    `gateway_stream.is_running` is False post-connect, fires one Discord alert
    (`dedupe_key="gateway_ws_down"`), keeps polling Heber flow regardless
    (`:400`), retries `restart()` each cycle.
  - `_active_event_source_profile` (`:469`) currently advertises
    `"flow_source":"heber_silver"`.

### 1.5 Other Gateway WS consumers (additivity constraints)
- **Orbit** `Orbit/src/data/gateway_client.py:674 GatewayWebSocketClient`:
  subscribe message shape is `{"action":"subscribe","provider":<p>,
  "feeds":[...],"symbols":[...]}` (`:810`) and it ALREADY calls
  `stream(providers={"uw":["flow"]}, symbols=[...])` (`:691`, `:707`, docstring
  `:24`). **Today that subscribe is a silent no-op** (per §1.1) — Orbit
  receives no UW flow over WS. A flow channel would *start working* for Orbit,
  which is a strict improvement, but means the channel name MUST be
  `provider="uw", feed/feeds contains "flow"` to match Orbit's existing call.
- **Cerberus** `Cerberus/src/data/client.py:591 subscribe(feeds, symbols)` —
  uses `feeds`+`symbols`; reads `message.get("feed")` on delivery (`:546`).
  Alpaca feeds only; unaffected.
- **Kairos** `Kairos/.../live_monitor.py:274` — `{"action":"subscribe",
  "symbols":[...]}` for option contracts/underlyings (Alpaca). Unaffected.
- **3Roses / WhaleHunter** — no Gateway WS client found.
- **Heber** — consumes the Redis `heber:events` stream, not WS. Unaffected by
  a WS change.
- **Conclusion:** the only consumer that even references UW-over-WS is Orbit,
  and it's currently dead. Any change is additive: new feed routing, no change
  to the existing Alpaca subscribe/deliver path.

---

## 2. Minimal additive Gateway change

**A Gateway change IS required** (push does not exist). Keep it additive: a new
provider-aware flow channel that fans out the *same envelopes the UW poller
already builds*, reusing the existing `ConnectionManager.broadcast` primitive.

### 2.1 Design: a `FlowFanout` registered as the UW poller's flow tap
1. **New module `gateway/core/flow_fanout.py`** — a tiny subscription registry:
   `symbol → {connection_id}` plus an `ALL` bucket for symbol-less subscribers.
   API: `subscribe(connection_id, symbols)`, `unsubscribe(...)`,
   `client_disconnect(connection_id)`, and
   `async deliver(envelope: dict)` which looks up
   `symbol = envelope["symbol"]` (the underlying ticker for flow), unions the
   per-symbol set with the `ALL` set, and calls
   `connections.broadcast_to_connection_ids(wire_msg, connection_ids)`.
   The wire message MUST mirror the bars shape so clients share one decoder:
   `{"type":"data","feed":"flow_alerts","symbol":...,"event_id":...,
   "envelope":<the full envelope>,"data":<envelope["payload"]>}`.
2. **Tap the UW poller.** In `UWPoller._poll_flow_alerts` /
   `_build_feed_envelopes`, the envelopes are already in hand right before
   `_publish_envelopes` writes them to `heber:events`. Add an optional
   `on_flow_envelope: Callable[[dict], Awaitable[None]] | None` hook on
   `UWPoller` (default None → zero behavior change). After a successful publish
   of the flow batch, `await on_flow_envelope(env)` for each published flow
   envelope. **Critically: fan out only AFTER (or independent of) the Redis
   publish, and only flow.** Darkpool/tide/EOD are untouched.
   - Wire it in `gateway/main.py` `start_uw_poller(...)` (`:369`): pass
     `on_flow_envelope=flow_fanout.deliver` when the multiplexer/connection
     manager exist. Guard behind a new setting
     `GATEWAY_WS_FLOW_FANOUT_ENABLED` (default True is fine; it only does work
     when a client has subscribed).
3. **Route the subscribe action.** In `websocket.py:_handle_message` subscribe
   branch, BEFORE the Alpaca multiplexer block, special-case
   `provider in {"uw","unusual_whales"}` AND any requested feed in
   `{"flow","flow_alerts"}`: register the connection with `flow_fanout` for the
   requested `symbols` (empty list ⇒ `ALL`), return a normal
   `{"type":"subscription_ack","status":"ok","provider":"uw","feeds":["flow_alerts"]}`.
   Mirror in the `unsubscribe` branch and in the `finally` disconnect cleanup
   (`websocket.py:96-103`) call `flow_fanout.client_disconnect(connection_id)`.
   - Permissions: gate on the existing `_has_provider_permission` (`uw` /
     `unusual_whales`, `:699-708`) and add `flow` to the feed-permission
     normalizer (`_normalize_feed_permission`, `:711`) so `ws_subscriptions_max`
     and provider ACLs still apply. Orion's Gateway client key must carry the
     `unusual_whales` provider permission (verify in the client registry; if
     absent, that's a one-line client-permission addition, not a code change).

### 2.2 Why this shape
- **Zero new event-id logic** — fan-out ships the exact envelope already minted
  for Heber, so `event_id` is identical to the Silver-poll path (§4).
- **Additive** — the Alpaca multiplexer, its subscribe routing, and all other
  consumers are untouched. Orbit's currently-dead `uw/flow` subscribe begins to
  work (improvement, not regression).
- **No backpressure coupling to Heber** — fan-out reads the same envelope but
  delivers on the WS broadcast path, which already has its own per-send
  semaphore + benign-close handling (`connections.py:217-235`). A slow WS client
  cannot stall the Redis publish (publish happens first).
- **Contract test** — extend `tests/test_envelope_heber_contract.py` style with
  a new `test_flow_fanout.py`: assert the fan-out wire envelope's `event_id`
  equals the envelope published to `heber:events` for the same flow record.

### 2.3 Alternative considered (rejected for B1)
A separate Redis consumer inside Orion reading `heber:events` directly would
also skip the parquet hop, but (a) it puts Orion on Gateway's internal Redis
(new coupling, new failure mode) and (b) duplicates Heber's consumer-group
semantics. The WS fan-out reuses the channel Orion already holds open for bars
and the auth/degrade machinery already battle-tested. Documented here so the
implementer doesn't re-derive it.

---

## 3. Orion-side changes

### 3.1 `GatewayStreamClient` (connectors/gateway_stream_client.py)
- Add `on_flow_callback: Callable[[BronzeEvent], None] | None` alongside
  `on_bar_callback` (`:48`).
- Add `_subscribed_flow_symbols: set[str]` and a `subscribe_flow(symbols)` /
  `_send_subscribe_flow(symbols)` that sends
  `{"action":"subscribe","provider":"uw","feeds":["flow_alerts"],"symbols":[...]}`
  (or empty symbols for ALL). Re-send on reconnect alongside the bar
  resubscribe in `_reconnect_with_backoff` (`:184-187`) and `start()`/`restart()`.
- Receive loop (`_receive_loop`, `:331`): add `_is_flow_message(data)` (feed in
  `{"flow","flow_alerts"}`) and `_process_flow_message(data)` that builds a
  `BronzeEvent` IDENTICAL in shape to the poll path's `_heber_row_to_event`
  output (see §4 for the exact field mapping) — same `event_id`, same
  `event_type="UW_FLOW"`, same `source="UW"`, same enriched `payload` keys
  (`ticker`, `put_call` first-letter, `premium_usd`, `dte`, `aggressor_ind`).
  Reuse the poll path's helpers by extracting `_heber_row_to_event`'s payload
  enrichment into a shared static helper both call sites use, so the two paths
  cannot drift.

### 3.2 Ingestion wiring (ingestion/service.py)
- New flag `ORION_FLOW_SOURCE` on `system_settings` (config.py):
  `Literal["poll","shadow","push"]`, default `"poll"` in code (deploy sets
  `"shadow"` per the redesign plan B1).
- `__init__`: when source is `shadow`/`push`, create the stream client with
  `on_flow_callback` set to a method that pushes onto an
  `asyncio.Queue[BronzeEvent]` (mirror the bar `_event_queue`/`drain_events`
  pattern, `gateway_stream_client.py:62`, `:507`), and call
  `subscribe_flow([])` (ALL) after `gateway_stream.start()` (`service.py:146`).
- `_run_cycle` (`:280`) becomes source-aware:
  - `poll`: unchanged — `flow_events = await self._poll_heber_flow(...)`.
  - `push`: `flow_events = self.gateway_stream.drain_flow_events()`; the Heber
    poll still runs as the **gap-filler** but its output is fed through the same
    born-stale + dedup path (duplicates collapse), so a WS gap is silently
    back-filled. (Cheaper option: in `push` run the poll only when
    `is_degraded` or on a slow cadence; safest for B1 is keep both and let
    dedup collapse — matches the plan's "poll retained permanently as
    degrade/replay path".)
  - `shadow`: drain push events AND run the poll; record parity (§5); feed the
    UNION through `_normalize_and_dedupe` so dedup collapses the overlap and the
    pipeline sees each event once (no double candidates — the hard requirement).
- Apply the SAME born-stale `freshness_cutoff` drop (`:520`) to push events
  before they enter the batch, so push cannot resurrect the born-stale incident
  class.
- Update `_active_event_source_profile` (`:469`) `"flow_source"` to reflect the
  active mode.

---

## 4. Event-id parity analysis (HARD REQUIREMENT)

**Claim: in `push` and `shadow` modes a push flow event carries the EXACT same
`event_id` as the corresponding Heber-poll event, so the deduper collapses
them.** Proof by tracing both paths to the same source bytes:

| Stage | Poll path | Push path |
|---|---|---|
| id minted | `wrap_event(feed="flow_alerts")` → `compute_event_id` (`envelope.py:362`) | SAME `wrap_event` call in `UWPoller._build_feed_envelopes` — push reuses the *same envelope object* |
| id bytes | `blake2b("unusual_whales\|flow_alerts\|option:OCC:<occ>\|<ts_event.isoformat()>\|<expiry>\|<strike>\|<put_call>\|<premium>\|<volume>")` | identical (same function, same envelope) |
| transport | env → `heber:events` → Heber writer writes `envelope.event_id` → Silver `event_id` col (`transformer.py:327`) | env delivered over WS as `envelope.event_id` (top-level `"event_id"` and `envelope["event_id"]`) |
| Orion read | `_heber_row_to_event` → `payload.get("event_id")` (Silver col) (`service.py:591`) | `_process_flow_message` → `data.get("event_id") or envelope.get("event_id")` (same precedence as bars, `gateway_stream_client.py:411`) |
| Orion dedup | `DeduplicationEngine` keys on `event_id` (`deduper.py:80`) | same engine, same key |

**Result:** byte-identical `event_id` ⇒ the second arrival (whichever path
loses the race) is dropped by the in-memory cache, the DB `IN` query, or the
`BronzeEvent` ON CONFLICT. **No recomputation, no mapping table needed.**

### 4.1 Field mapping that MUST hold for the BronzeEvent (beyond event_id)
The push `BronzeEvent` must equal the poll `BronzeEvent` so downstream features
are identical regardless of which path delivered it:
- `event_id` ← `envelope["event_id"]` (see above).
- `event_type` = `"UW_FLOW"`, `source` = `"UW"` (constants, both paths).
- `ticker` ← `payload["underlying"]` (push) — note the poll path reads
  `payload["underlying"] or payload["symbol"]` (`service.py:574`); the Gateway
  flow envelope's `symbol` IS the underlying (`wrap_event` resolves
  `underlying`/`ticker`, `envelope.py:322-329`), so both resolve to the same
  ticker. **Use the same resolution order in `_process_flow_message`.**
- `event_ts_utc` ← envelope `ts_event` (push) vs Silver `ts_event` column
  (poll). Both originate from `wrap_event`'s `ts_event` (`envelope.py:342`,
  derived from `payload["timestamp"]`). Parity holds **as long as Heber writes
  `ts_event` from the envelope unchanged** — confirmed (`transformer.py:242`
  reads `row["ts_event"]`). The id itself already pins `ts_event` (it's in the
  hash), so any `ts_event` skew would ALSO change `event_id` — i.e. parity is
  self-enforcing: if `event_id`s match, `ts_event`s matched.
- Payload enrichment (`put_call` → first letter, `premium_usd`, `dte`,
  `aggressor_ind`, `service.py:579-589`): must be applied identically by the
  shared helper (§3.1). These do NOT affect `event_id` (not in the hash), so a
  drift here would not cause double candidates, but WOULD cause feature
  inconsistency — hence the shared-helper requirement.

### 4.2 Where parity could break (flagged)
1. **id-less fallback divergence.** The poll path has a deterministic
   `uwflow_<sha1(...)>` fallback for rows where Silver has no `event_id`
   (`service.py:594-604`). The push path will essentially never hit this — the
   Gateway envelope ALWAYS has a blake2b `event_id`. **Risk:** if a Silver row
   ever lost its `event_id` (Heber bug / schema gap), the poll path mints a
   `uwflow_*` id while push carries the blake2b id → the SAME logical event gets
   TWO ids → a double candidate in shadow/push. **Mitigation:** in
   `shadow` mode, log any poll event whose id starts with `uwflow_` as a
   `parity_unmatchable` row; if the count is non-zero the cutover gate (§6) is
   NOT met. In practice `event_id` is the Silver PK and is non-null
   (`silver.py:27` required), so this should be zero — but it must be measured,
   not assumed.
2. **Heber-side enrichment fields** (`gex`, `vex`, `max_pain_*`,
   `silver.py:184-188`) are added by the UWPoller/Heber AFTER the envelope is
   built and are NOT in `event_id`. Push events won't carry them until Heber
   computes them. This does not affect dedup (not in the hash) but means
   push-delivered events may lack GEX enrichment that poll-delivered ones have.
   **Decision:** acceptable for B1 (these are optional `| None` features); flag
   in the design so the implementer knows push events can have null GEX. If
   needed later, the fan-out can be moved to after Heber enrichment, but that
   reintroduces a hop — out of scope.

---

## 5. Shadow-mode design (per-cycle parity metrics)

In `shadow` mode, each ingestion cycle drains push events AND polls Heber, then
computes parity on the `event_id` sets BEFORE the union is deduped:

- `push_ids = {e.event_id for e in push_events}`
- `poll_ids = {e.event_id for e in poll_events}` (after born-stale drop, same
  cutoff applied to both for a fair comparison)
- `push_count = len(push_ids)`, `poll_count = len(poll_ids)`
- `missed_by_push = poll_ids - push_ids` (events poll saw that push didn't —
  the metric that gates cutover)
- `missed_by_poll = push_ids - poll_ids` (push-only events, expected to be
  near-real-time arrivals poll hasn't read yet)
- `latency_delta`: for ids in `push_ids ∩ poll_ids`, compare push arrival
  wall-clock (when `on_flow_callback` fired) vs poll arrival (cycle time). Push
  should lead by ~tens of seconds (the parquet round-trip). Record
  `median_latency_improvement_seconds`.

### 5.1 Destination table
New table `flow_push_parity` (storage/models, normal post-baseline migration —
same discipline as `service_liveness` in A1):
`id, cycle_ts_utc, push_count, poll_count, missed_by_push_count,
missed_by_poll_count, parity_unmatchable_count, median_latency_improvement_s,
missed_by_push_ids (JSON, capped), trace_id, created_at`.
One row per cycle. Write is best-effort / swallow-never-crash (parity logging
must never take down ingestion).

### 5.2 Daily Discord summary
A small job (cron/launchd, or folded into the EOD trigger `service.py:294`)
aggregates the day's `flow_push_parity` rows and posts:
total push vs poll counts, total `missed_by_push` (excluding born-stale and
`parity_unmatchable`), median latency improvement, and a GREEN/RED verdict
against the cutover gate. Reuse `send_discord_alert`.

---

## 6. Cutover criteria + rollback

**Cutover (`shadow` → `push`, redesign C4):** after **≥ 3 consecutive market
days** in `shadow` with ALL of:
1. `missed_by_push` (events poll caught that push missed, excluding born-stale)
   = **0** across those days;
2. `parity_unmatchable_count` (the `uwflow_*` divergence, §4.2) = 0;
3. `median_latency_improvement_s` > 0 (push demonstrably leads poll);
4. no WS-flow-related disconnect storm in the period (degrade-mode entries
   bounded / explained).

**Flip:** set `ORION_FLOW_SOURCE=push`. Poll demoted to degrade/replay
gap-filler (still runs; dedup collapses its output). Keep `flow_push_parity`
logging for ≥ 1 week post-cutover (C4).

**Rollback:** set `ORION_FLOW_SOURCE=poll` (single env flip + ingestion
restart) → behavior reverts to today's exact Heber-poll path. The Gateway
fan-out can stay enabled (it's inert if no client subscribes); to fully disable
Gateway-side, unset `GATEWAY_WS_FLOW_FANOUT_ENABLED`. No data migration, no
schema rollback — the change is purely a delivery-path switch in front of an
unchanged dedup/persist pipeline.

---

## 7. Risks

1. **WS disconnect gaps.** Already handled by composition: `GatewayStreamClient`
   reconnects with backoff (`:175`); after exhaustion `is_running=False` and
   ingestion enters DEGRADED (`service.py:389`) firing one Discord alert and
   retrying `restart()` each cycle. In `push` mode the Heber poll path is
   **retained as the gap-filler** — exactly the role it plays for bars today —
   so a WS gap is back-filled by the next poll and dedup collapses the overlap.
   Net: a WS flow outage degrades to today's poll latency, never to data loss.
   The born-stale cutoff still applies to both paths so a long gap can't dump a
   stale catch-up burst.
2. **Gateway restart behavior.** Multiplexer/poller state is in-memory and
   resets on restart (`stream.py` SequenceTracker note; `flow_fanout`
   subscriptions are per-connection and die with the socket). On Gateway
   restart, Orion's WS drops → reconnect → `subscribe_flow` re-sent (must be in
   the reconnect resubscribe path). During the gap, poll covers it. The UW
   poller re-seeds its own dedup cache from Redis (`uw_poller.py:126-131`,
   `_load_redis_duplicate_ids`), so no duplicate Heber writes — and since
   event_ids are content-stable, even a re-published flow envelope post-restart
   carries the same id and is collapsed everywhere.
3. **Backpressure.** Fan-out delivers AFTER the Redis publish and uses the
   broadcast semaphore (`connections.py:220`), so a slow Orion WS reader cannot
   stall Heber publishing. On the Orion side, push events land in an
   `asyncio.Queue` drained once per cycle (same as bars); if Orion stalls, the
   queue grows but the poll path + born-stale drop bound staleness. Consider a
   bounded queue + drop-oldest for flow to avoid unbounded growth on a wedged
   cycle (bars use unbounded today — match or improve, don't regress).
4. **Ordering.** UW flow has no strict ordering requirement (each alert is an
   independent event keyed by content). Push may deliver an event before poll
   or vice-versa; dedup makes order irrelevant for correctness. Feature
   computation already treats flow events as an unordered batch per cycle
   (`_process_features_and_rules`, `service.py:676`). No ordering guarantee is
   needed or assumed.
5. **Provider permission gap (operational).** If Orion's Gateway API key lacks
   the `unusual_whales` provider permission, the flow subscribe returns
   `GW-E2006` and push silently delivers nothing — which `shadow` mode would
   surface as `push_count=0, missed_by_push=poll_count` (loud, caught before
   cutover). Verify the key's permissions during B1 bring-up.
6. **Additive-only cross-repo blast radius.** The Gateway change adds a feed
   route + a fan-out tap; it does not alter the Alpaca subscribe/deliver path
   or the Redis publish. Orbit's dead `uw/flow` subscribe begins working
   (improvement). Extend the Gateway contract test so a future refactor can't
   silently break flow-id parity (`event_id` published to Redis == `event_id`
   fanned out over WS).

---

## 8. Implementation checklist (zero further discovery)

Gateway (additive, one PR):
- [ ] `gateway/core/flow_fanout.py` — subscription registry + `deliver()` using
      `ConnectionManager.broadcast_to_connection_ids`.
- [ ] `UWPoller` — optional `on_flow_envelope` hook fired per published flow
      envelope (flow only; after Redis publish).
- [ ] `gateway/main.py` — construct `FlowFanout`, pass `on_flow_envelope` to
      `start_uw_poller`; setting `GATEWAY_WS_FLOW_FANOUT_ENABLED`.
- [ ] `websocket.py` `_handle_message` — route `provider in {uw,unusual_whales}`
      + feed in {flow,flow_alerts} to `flow_fanout` (subscribe/unsubscribe);
      disconnect cleanup in the `finally` block; `_normalize_feed_permission`
      add `flow`.
- [ ] `tests/test_flow_fanout.py` — parity: fanned-out `event_id` ==
      `heber:events` `event_id` for the same record.
- [ ] Ensure Orion's client key has `unusual_whales` provider permission.

Orion (additive, behind flag):
- [ ] `config.py` — `ORION_FLOW_SOURCE` (default `poll`).
- [ ] `gateway_stream_client.py` — `on_flow_callback`, `subscribe_flow`,
      `_is_flow_message`, `_process_flow_message`, flow resubscribe on
      reconnect/restart, `drain_flow_events`.
- [ ] Extract shared flow payload-enrichment helper used by BOTH
      `_heber_row_to_event` and `_process_flow_message`.
- [ ] `ingestion/service.py` — source-aware `_run_cycle`; apply born-stale
      cutoff to push events; `_active_event_source_profile` reflects mode.
- [ ] `flow_push_parity` table + migration (autogen diff empty); shadow-mode
      metric computation; daily Discord summary.
- [ ] Tests: parity (push id == poll id for same record), shadow dedup (union
      yields one candidate), degrade composition (WS down → poll back-fills).
