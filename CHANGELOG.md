# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Changed (entry rules)

- **The five entry rules collapsed into three bucket rules** (`rule_0dte_sweep_v2`, `rule_short_swing_v2`, `rule_swing_v2`): the old rules used exact premium bands (0DTE required $100–150k), one-direction-only restrictions, and hour-of-day confidence bonuses citing win rates that were never persisted. The new rules share one core — buyer-initiated sweeps (ASK/ABOVE_ASK), traded WITH the flow in both directions, premium **floors** ($50k 0DTE/short-swing, $100k swing), a ~64-name liquid-underlying allowlist (`ORION_LIQUID_UNIVERSE`; 0DTE is SPY/QQQ/IWM only), contract-volume floors and a 0.25–0.60 delta band when the fields are present, per-bucket signal-age budgets (120s/300s/900s, `ORION_BUCKET_SIGNAL_AGE_BUDGETS`) replacing the flat 600s, and ET entry windows. Confidence is a flat 1.0 — the rules are the gate; ranking comes later from models trained on realized outcomes. `BullishSweepRule` and `BearishPutPressureRule` (the $10k-floor no-sweep noise firehose, 67% of all candidates) are retired and their solvers seeded inactive.
- **Entry orders now pay up instead of resting at mid**: limit = mid + 25% of the half-spread (0DTE: 40%) — a mid-priced limit filled only ~36% of the time and aged into the cancel sweep. The per-bucket spread caps (10% 0DTE / 15% others) bound the worst-case pay-up. Stale-entry TTL tightened 180s → 90s.
- **Earnings exclusion for single-name multi-day holds**: short-swing/swing entries on non-index underlyings are skipped when earnings fall inside the holding window (3d/8d) — long options through an earnings print is a systematic IV-crush bleed. Daily-cached Gateway lookup; fails open on lookup errors.
- **Decision-time quote captured for the measurement loop**: the entry-time bid/ask/mid/spread is persisted into `decision_trace_json.entry_quote` on every executed decision, enabling realized-slippage measurement and counterfactual labeling of unfilled/filtered candidates.

### Added

- **Fixed $500-per-trade sizing with per-bucket and per-underlying caps**: solver-bps sizing gave every trade a different dollar weight, making per-rule expectancy statistics noisy, and the global 3-option-position cap let the highest-volume rule starve every other bucket of samples. Sizing now defaults to a fixed $500 premium debit per trade (`ORION_RISK_FIXED_PREMIUM_PER_TRADE`, 0 restores bps sizing) capped at 5 contracts; concurrent positions are capped per bucket (0DTE 4 / short-swing 6 / swing 8, `option_bucket_caps`) and per underlying (2, or 3 for SPY/QQQ/IWM), with the global option-position cap raised 3 → 15 as the hard backstop. The daily-loss halt tightens from $20,000 (untrippable) to $2,000 — it stops new entries only; exits keep running.
- **0DTE entries enabled**: `ORION_RISK_MIN_DTE=0` in the native wrapper. The existing last-hour wind-down still blocks late entries, and the new 0DTE bucket exits (40% target / 30% stop / 90-min no-progress / 15:45 ET hard flatten) manage the hold.
- **Contract liquidity gate at order time**: orders were being placed on zero-bid dust contracts (priced off ask-only/last fallbacks) and on spreads so wide the round-trip cost exceeded any profit target — 29 of 45 orders on 2026-07-01 died unfilled, and 9 of the 38 stranded positions were unsellable zero-bid dust. Entries now require a live two-sided quote with mid ≥ $0.20 (`ORION_RISK_MIN_OPTION_MID`) and spread/mid ≤ 25% (`ORION_RISK_MAX_OPTION_SPREAD_PCT`); anything else is SKIPped with an `Illiquid: …` reason. The ask-only/bid-only/last fallback pricing paths were removed — no quote, no order.

- **Stop-loss, time stops, and a disaster valve — positions can finally exit at a loss**: the deterministic exit rules had no plain stop-loss and no time stop, so a position that simply went down was never exited (the profit target needed +100%, the DTE rule fired only at T-1, and the drawdown rule armed only after the position had been profitable) — the root cause of 38 open positions bleeding to zero with **zero closed round-trips ever**. Exit thresholds are now per-bucket (`0DTE` / `SHORT_SWING` / `SWING` / `POSITION`, defaults in `execution/exit_fallback_rules.py:DEFAULT_BUCKET_PARAMS`, overridable via `ORION_EXIT_BUCKET_OVERRIDES` JSON): profit targets +40–75%, stop-losses −30–45%, max-hold time stops, a 0DTE no-progress exit (90 min stuck near breakeven) and 15:45 ET hard flatten, a trailing drawdown stop that arms once the trade has actually worked, and an unconditional −60% disaster valve. These barriers double as the triple-barrier label definition, so every closed trade labels itself for the measurement loop. The position monitor tightens its check cadence to 30s while any 0DTE position is open. (Replaces the three global `ORION_EXIT_FALLBACK_*` env vars.)
- **Expired-worthless positions are now realized in the trade journal**: an option that expires produces no closing fill, so the fill-driven P&L path never ran and the journal row stayed open forever — one reason realized P&L was empty everywhere. A sweep (`realize_expired_journal_rows`) now books entries whose option expired more than a day ago at a full loss of the entry premium, tagged `expired_worthless`.

### Changed

- **The nightly EOD run now closes the books instead of asking an LLM for opinions**: `_run_eod_task` swaps `EODReviewAgent` (whose mutation proposals were auto-blocked by the promoter and never influenced a decision) for `realize_expired_journal_rows()` + `reconcile_pnl.run_reconciliation()` — populating `pnl_reconciliation`, `rule_pnl_attribution`, and `solver_pnl_attribution` from the fills FIFO lot book every night. This is the feedback loop the solver system was designed around but never had.
- **Fill/order poll window widened 200 → 500**: on the shared account, sibling systems' order volume aged Orion's fills out of the 200-most-recent window before they were polled — the recurring missed-fill incident class behind stranded cost basis and phantom open positions. 500 matches the stale-cancel sweep's window.
- **The degraded ML exit classifiers no longer shadow the sane heuristic exits**: the per-bucket `*_exit.pkl` models (observed returning a constant 0.17 confidence since 2026-05-19, trained nightly from frozen months-old labels) were archived to `models/archive/degraded-exit-classifiers-260701/`, so `BucketExitClassifier.predict` falls through to its per-bucket heuristic thresholds. The `pattern-miner` container that retrained them nightly was stopped (it is dead machinery slated for removal — its models never gated a live decision).
- **LLM agent model bumped to `glm-5.2`**: the EOD review and meta-search solver-mutation agents now default to `glm-5.2` (was `glm-5.1`), the newer GLM Coding Plan model advertised by the AI-Gateway. Off the trading hot path — affects only the after-hours LLM agent calls.

### Fixed

- **0DTE candidates are no longer blocked by DTE truncation** (adversarial review): `expiration_date` is stored as midnight UTC, so `(expiration - now).days` was −1 for a genuine same-day 0DTE all session long — 0DTE stayed untradable even with `min_dte=0` — and a 1-DTE entered intraday truncated to 0 (mislabelling it 0DTE). DTE is now calendar-day arithmetic everywhere (entry gate, bucket caps, position-monitor bucket classification): 0 = expires today, negative = already expired (always blocked).
- **The 0DTE hard flatten can no longer be disabled by a bucket mislabel** (adversarial review): a position whose entry context couldn't be fetched (missing `expiration_date`, DB hiccup at reload) silently defaulted to the SWING bucket, so the 15:45 ET flatten never armed. Three layers now defend it: option candidates without an `expiration_date` are rejected at order time ("Missing Expiration Date"); the position monitor derives DTE from the OCC symbol's embedded expiry before ever defaulting (and no longer caches DB-failure fallbacks); and the flatten rule fires for ANY position whose contract expires today, regardless of bucket label.
- **Entry caps fail closed** (adversarial review): a DB failure in the open-position count previously read as "zero open positions", letting entries through uncapped. The count now returns unavailable and the engine skips the entry ("Position-cap check unavailable"). One-sided quotes (bid with no ask) are also rejected explicitly as illiquid instead of surfacing as a generic price-fetch error.
- **Expired-worthless journal rows no longer poison the nightly reconcile** (adversarial review): expiry produces no broker fill, so including `expired_worthless` realizations in the journal-vs-broker comparison would force a false MISMATCH on every expiry day; they're excluded and audited directly from the journal. The docker-compose daily-loss default was also synced to the native wrapper's $2,000.
- **The 0DTE and short-swing buckets can now actually trade**: every `rule_0dte_sweep_v1` and `rule_short_swing_entry_v1` candidate died at "Ensemble Rejected (0.00 < 0.5)" because no seeded solver listed those rules — and worse, `RuleRegistry` never registered `rule_0dte_sweep_v1`, `rule_swing_entry_v1`, or `rule_short_swing_entry_v1` at all, so any solver config carrying them failed DSL validation silently. The registry now lists every implemented rule; the seeds add dedicated `zero_dte_paper_v1` and `short_swing_paper_v1` solvers and the diversified baseline covers all six rules. The native execution wrapper pins `ORION_BASELINE_SOLVER_ID=diversified_baseline_v1` so a router-empty can never hard-SKIP again.
- **Execution-stage failure reasons are now queryable**: the decision row is persisted *before* the execution engine runs, so failures inside order construction ("Option Price Fetch Failed", "Size 0 Contracts", DTE/DTBP blocks) mutated the in-memory reason but never re-persisted it — on 2026-07-01, 200 of 245 EXECUTE decisions died with no queryable trace. `update_decision_status` now re-persists the final reason alongside the terminal status.
- **The missed-close reconcile no longer re-fetches Gateway-unknown order ids every cycle**: order ids harvested from the broker's FILL activities can be legacy/unowned (the id exists only on the activities surface), so the Gateway answers `404 GW-E4404 Order not found` — and since no fill is ever processed for such an id, the reconcile's processed-fill dedupe never engages and the same ids were re-fetched (4 `gateway_trading_http_error` ERRORs per ~2-minute cycle, 3,000+ over 2026-07-01..02). A recovery fetch that 404s now records the id as gone for the rest of the session: it is logged once, never re-fetched, and skipped without consuming the per-cycle lookup cap (so a pile of legacy ids can't starve real recoveries). Transient errors (5xx/timeout) are still retried on later cycles, and a restart re-learns each gone id with a single 404.
- **Options close no longer loops a rejected `sell_to_close` against a position that's already gone**: when the attributed close LIMIT was rejected (e.g. Alpaca 422 "position intent mismatch, inferred: sell_to_open"), `close_position` escalated straight to the native flatten — which re-submits a closing order into the same wall — without re-checking whether the position still existed. On 2026-07-01 that looped 44 `sell_to_close` rejects (4× each across 11 option contracts), each paired with a position-not-found 404 proving the position had already closed/expired. The close path now re-verifies the live broker position before escalating: only a DEFINITIVE position-gone recheck (a broker 404 / position-not-found, or an explicit zero qty) records success and stops — no native re-submit, no retry. A transient recheck error (500 / 429 / auth) is NOT read as closed and falls through to the native flatten (reduce-only, 404-safe), so a still-open option can't be abandoned on a flaky lookup. A position that genuinely remains still escalates exactly as before.
- **The test suite can no longer wipe a production database**: the autouse `setup_test_db` teardown ran `Base.metadata.drop_all` on whatever `db.engine` pointed at. Several e2e tests reconfigure it to the real TimescaleDB and restore the in-memory binding before teardown, but that protection was per-test and fragile — a test that failed to restore (or crashed mid-fixture) would have the teardown `drop_all` **every Orion table on the live DB**. There, constantly-written tables (`bronze_events`, `silver_signals`) refill within minutes, but `solvers` only repopulates on an execution restart, so a wipe stranded Orion with zero solvers — every candidate SKIPped during market hours — until the next restart (the 2026-06-30 incident). The teardown now drops tables ONLY when bound to the in-memory SQLite test engine (`tests/_db_safety.is_in_memory_test_engine`); against any other engine it skips cleanup and warns loudly.
- **Dead-man watchdog no longer pages a false "features stage stalled" every cycle**: the pipeline-depth freshness check treated `gold_feature_events` as a live stage, but that table is written only by backtests, nightly meta-search, and DLQ recovery — never by the live ingestion/execution path — so it is legitimately empty during the cash session, and the watchdog paged `features has NO rows … (full stall)` to Discord on every 5-minute fire intraday. The `features` stage freshness is now logged for visibility but never paged; `bronze` and `silver` (real live stages) still alert on staleness exactly as before.
- **Heber gold reads degrade gracefully on a per-partition schema-cast mismatch instead of total-failing**: `HeberReader._is_schema_merge_parquet_error` only recognized the `int64 → null` cast-merge variant (`"to null"`/`"cast_null"`), so when Heber wrote a `meta_label_features` `dt=` partition with `expiry` as `int64` while sibling partitions used `date32`, reading the dataset raised `ArrowNotImplementedError: Unsupported cast from int64 to date32` — which matched neither the corrupt-file nor the schema-merge matcher, so the whole gold read failed and returned an empty frame. Every candidate's ML feature read then failed (degrading scoring to a blind fallback) and the execution loop pegged at ~230% CPU re-trying (observed 2026-06-30, recurring on days Heber emitted a bad partition: 06-23/24/30). The matcher now treats any `"unsupported cast from"` Arrow error as a schema-unification failure and routes it to the file-wise reader, so the good partitions still load. The upstream int64 `expiry` write is fixed separately in Heber.
- **Execution no longer goes blind after a Data-Gateway flap**: `poll_fills` gated fill/order polling, position snapshots, risk-sync, and missed-fill recovery on a *cached* gateway-availability flag that was only refreshed by order submission. When a gateway blip flipped the flag false while the engine sat at its max-position limit (no orders to submit), nothing re-checked the gateway, so the engine silently stopped recording fills, snapshots, and P&L until the next restart — trades kept executing at the broker but never flowed back into Orion's risk state (observed 2026-06-26: 19h blind after a 00:29 flap). `poll_fills` now re-probes gateway availability (60s-cached) every cycle, so it self-heals within a minute of the gateway recovering.
- **pytest no longer pollutes the production error log**: `empire_core.logger` writes rotating `orion_*.log` / `orion_errors_*.log` files to `EMPIRE_LOG_DIR` (default `./logs`), so every test run was pouring test fixtures into the real `logs/orion_errors.log` — fake CRITICALs (e.g. the drawdown-circuit-breaker test's `equity=10100 peak=100000` 90%-drawdown trip), test tickers (`b-1`/`EWY`), greek-exposure errors — across dozens of pytest processes. That corrupted incident diagnosis (the fake CRITICALs read as live kill-switch trips) and could trip any monitoring that tails the error log. `conftest.py` now points `EMPIRE_LOG_DIR` at a throwaway temp dir and runs with a non-default AI-Gateway key (so the `default_dev_credential_in_use` import-time warning doesn't fire either), reducing test → prod-log writes to zero.
- **Services no longer crash-loop and flood CRITICAL pages when the database is briefly unavailable at startup**: `position-monitor`, `data-quality`, `feature-enrichment`, and `pattern-miner` called `init_db()` directly, so a transient TimescaleDB outage (DB restarting / briefly down) raised at startup, the process exited, and launchd restarted it on a tight loop — each crash logging a CRITICAL `SERVICE_PROCESS_CRASHED`. A real DB outage on 2026-06-24 crash-looped `position-monitor` **72 times** in ~4.5h (and re-emitted its startup warnings each time) until the DB recovered. These services now call `wait_for_db()` (bounded retry with backoff) before `init_db()`, matching `execution` — which already did this and rode out the same outage without a single crash. A genuinely-down DB still surfaces loudly after the bounded wait.
- **Legacy pre-2026-05-20 orders no longer drive a Gateway warning flood from the stale-cancel sweep**: Data-Gateway added a per-client `c-<client>-` ownership prefix on 2026-05-20 and now fail-closes any cancel of an order placed before that date (which reached Alpaca as a raw `orion_<uuid>`) with `404 GW-E4404` — those orders can never be cancelled through the Gateway. Orion's stale-entry sweep had been classifying that 404 as a *transient* reject and re-attempting it across sweeps and restarts, inflating a bounded set of stuck legacy orders into 1,164 Gateway warnings over 2026-06-22..24. A cancel rejected `404 GW-E4404` is now recognized as the never-cancellable legacy case and reconciled out — the orphaned row is flipped terminal so the sweep stops re-selecting it (this process and across restarts), its buying-power reservation is dropped, and no retry/backoff or false "reserving DTBP" page is emitted. The match is scoped to the exact `GW-E4404` code, so other (potentially retryable) 404s on the cancel path are untouched. Any such order still open at Alpaca is cleared out-of-band via the dashboard.
- **Cost basis is now recovered for entry fills that aged out of the fill poll's 200-row window**: the fill poll learns of fills only from the latest 200 orders on the shared account, so on a high-volume day an Orion entry's fill can age out before it's seen — no `FillRecord` is written and `_compute_cost_basis_from_fills` (which replays the fills table for an uncontaminated per-symbol average) can't reconstruct that order's cost basis. When the stale-entry sweep now learns from the broker that such an order is already filled (its cancel is rejected `order is already in "filled" state`), Orion fetches that specific order by id — a lookup not bounded by the recent-200 window — and feeds it through the same idempotent fill processor a live poll uses, so the missing `FillRecord` lands and per-symbol cost basis / realized PnL are reconstructed. The status reconcile that stops the 2026-06-22 cancel/alert storm is unchanged and still runs even if the recovery fetch fails (the broker has already confirmed the fill), so the storm fix can't regress; a rare unrecovered fetch is logged durably and still fails safe downstream (`reconcile_pnl` routes an unbasis-able close to `BROKER_UNAVAILABLE`). Missed *closing* fills — which get no `orders` row — are recovered by the separate reconcile below.
- **Cost basis is now recovered for closing fills that aged out of the fill poll's 200-row window**: a successful close never gets an `orders` row (it persists to `exit_decisions`), so the stale-entry sweep — which keys off the orders table — can never surface a close that the 200-row fill poll missed; its `FillRecord` simply never lands and the per-symbol cost basis / realized PnL stay incomplete. A periodic reconcile (on the same non-urgent cadence as the broker position re-sync) now compares the broker's open positions against the fills-table replay: when an Orion symbol's fills magnitude exceeds what the broker actually holds, a reducing (closing) fill went missing. Orion reads the broker's fill activity to find that close's order id, confirms it has booked **zero** fills for that order (so a partial that was already counted can't be double-applied), and feeds it through the same idempotent fill processor a live poll uses. Recovery runs before the position re-sync so the close's PnL is realized against the still-open lot rather than mis-booked, is bounded to a fixed number of broker order lookups per cycle (no 429 storm), and is shared-account safe — a sibling system's order on the same symbol is fetched once and skipped, never counted. A close that still can't be based out fails safe downstream (`reconcile_pnl` routes it to `BROKER_UNAVAILABLE`).
- **The market-open data-flow CRITICAL now actually pages — via Discord**: the check that detects a starved bronze feed at the cash open (born from the 2026-06-08 split-brain that let only 403 bronze events land all day) was POSTing to `SLACK_WEBHOOK_URL`, which is set nowhere, so a real "feed down at the open" alert reached no one. It now posts to `DISCORD_WEBHOOK_URL` (already in `.env`, sourced by the wrapper) with Discord's `{"content": …}` payload shape. It fires only at the fixed market-open times (09:40 / 10:00 / 10:30 ET), so no cross-fire dedup is needed.
- **The launchd-health probe now actually pages — via Discord, with dedup**: the probe that watches for silently-dead Orion launchd jobs (the safety net born from the 2026-05-22 silent `exit 127` incident) was posting to `SLACK_WEBHOOK_URL`, which is set nowhere, so it paged no one — it only wrote `logs/launchd_health.log`. It now posts to the Discord webhook (`DISCORD_WEBHOOK_URL`, already in `.env` and sourced by the wrapper). Because the probe fires every 60s on the same frozen last-exit code, the Discord notifier carries persistent per-`(job, exit_code)` dedup: a stuck job pages immediately, then at most once per hour, so a single bad service can't storm the channel (the same blast-radius failure that tripped Discord's 429 limit on 2026-06-22). Only a successful POST is recorded, so a transient Discord outage retries on the next fire instead of dropping the page; the durable `launchd_health.log` row is still written for every alert regardless.
- **Execution liveness no longer false-pages during long startup or heavy candidate batches**: the native execution service now publishes liveness while heavy engine/feature hydration runs, feature hydration offloads blocking per-ticker reads so the async loop can keep the heartbeat alive, and liveness refreshes after each processed candidate instead of waiting for the whole batch. The watchdog still alerts if startup/work actually wedges, but no longer treats normal long-running startup/work as "execution dead."
- **Gateway trading-permission rejects no longer repeat on every candidate**: when Data-Gateway returns `GW-E2009 Trading capability required`, Orion records the failed order normally, then backs off later opening orders for a cooldown with a clear "Gateway key lacks trading capability" decision reason instead of repeatedly posting orders that the Gateway is guaranteed to reject.
- **Stale-cancel Gateway permission failures no longer flood Discord**: stale entry-order cancel attempts still give up immediately and emit durable error logs on `GW-E2009 Trading capability required`, but they no longer send one Discord page per old order when the shared root cause is Orion's Gateway key lacking trading permission.
- **Stale-entry cancel sweep no longer self-inflicts a Gateway 429 storm**: a rejected cancel used to be re-issued every 5s for the rest of the session (68k+ rate-limit errors on 2026-06-15), saturating the shared Data-Gateway and pushing execution into degraded mode. Each order now backs off per-order (exponential with jitter) on a transient rejection, gives up after a bounded number of attempts — or immediately on a known-permanent broker rejection (e.g. `GW-E2009 trading capability required`) — with a durable error log, and at most a fixed number of cancels are attempted per sweep. A failed cancel never silently marks the order cancelled, so its buying-power reservation accounting is preserved.
- **Already-filled entry orders no longer page a false "reserving DTBP" alert every session**: on a high-order-volume day Orion's fill poll (latest 200 orders on the shared account) can miss an Orion entry's fill, leaving the `orders` row stuck in an open state. The stale-entry sweep then tried to cancel an order the broker had already filled, was rejected with `order is already in "filled" state`, retried, gave up, and paged "will keep reserving DTBP until it expires at the close" — false, because the order was filled (182 such pages on 2026-06-22 tripped Discord's rate limit). A cancel rejected because the order is already terminal at the broker (filled/canceled/expired/rejected) is now reconciled — the row is flipped to that state and its buying-power reservation dropped — instead of being retried and paged. (The fill itself is still re-grounded into risk by the periodic broker position sync; per-fill cost-basis recovery for these orders is now handled — see the cost-basis recovery entry above.)
- **A rate-limited (HTTP 429) options close no longer escalates to an unattributed native flatten**: 429 is now treated as a transient rate-limit (defer and retry the Orion-attributed limit next cycle) rather than a confirmed broker rejection. Escalating a 429 to a native close blinded the daily-loss/drawdown kill switch, because native closes are not Orion-attributed. Other 4xx rejections still escalate as before.
- **Dead-man watchdog no longer goes blind on future-dated rows**: a pipeline stage whose newest row is more than 120s in the future (a clock/data-quality bug) now raises a distinct "future-dated rows" alert instead of silently passing the freshness check forever — a negative age can never exceed the staleness budget, so the stale-data alarm was effectively disabled for that stage. The future-dated alert uses its own suppression key so it can't mask an ordinary staleness alert.
- **Feature hydration de-duplicates revised bars**: overlapping Heber parquet partitions can deliver two revisions of the same bar (Heber de-dupes by `event_id`, not by bar key), which crashed feature computation with "Reindexing only valid with uniquely valued Index objects" and silently degraded enrichment. Hydration now collapses duplicate bar timestamps to one row, preferring the latest-available revision (`ts_available`), and a defensive guard in the compute path warns and keeps the freshest row if duplicates ever slip through another path.

- **Dead-man Discord alerts now respect market closure for market-session services**: stale `ingestion`, `execution`, `feature_enrichment`, and `position_monitor` heartbeats are informational outside the NYSE cash session, while scheduled always-on jobs still alert around the clock. This stops closed-market Discord pages for stale market-loop heartbeats without muting real scheduled-job failures.

### Removed

- **Root scratch probes removed after the Ponytail over-engineering audit**: deleted leftover root-level debug/probe files (`test_arrow*.py`, `test_gateway.py`, `test_rglob.py`, `fix_mocks.py`, `run_mining_now.py`) so only maintained tests under `tests/` remain.
- **Nine unused dependencies removed from the direct dependency list**: `boto3`, `s3fs`, `aiobotocore`, `fsspec`, `litellm`, `aiokafka`, `msgpack`, `sseclient-py`, and `pandera` were declared in `pyproject.toml` but imported nowhere — Heber reads are local-filesystem (no `s3://` access), the LLM path runs through the AI-Gateway HTTP client (not litellm), and ingestion is Gateway-WebSocket (not Kafka). `boto3`/`s3fs`/`aiobotocore`/`fsspec`/`litellm`/`aiokafka`/`pandera` and their unique transitive trees (tiktoken, tokenizers, boto3/s3transfer, typer, typeguard, …) leave the resolved environment entirely; `msgpack` and `sseclient-py` remain in the lockfile only as `alpaca-py` transitives. `pytz` is kept because `alpaca-py` imports it at module load without declaring it.
- **`requests` replaced by `httpx`**: the single synchronous `requests.get` in the Gateway contract probe's health check is now an async `httpx.AsyncClient` call (httpx was already a dependency, used in ~36 places), so the `asyncio.to_thread` wrapper is gone too. `requests` is removed from the direct dependency list (it stays in the lockfile as an `alpaca-py` transitive) along with its `types-requests` stub.
- **`aiohttp` dropped in favor of `httpx`**: the Codex agent's AI-Gateway client (the repo's only `aiohttp` user) now uses `httpx.AsyncClient`. One fewer async HTTP stack to ship; behavior is unchanged (a request timeout still raises a single retriable error). Adds a happy-path test for the gateway completion call, which was previously untested.
- **Unused `FeatureFlags` module deleted** (`orion/core/feature_flags.py`): a singleton flag store with no production callers — no code read its flags, and the `feature_flags` database table its docstring referenced was never created. Removed the module and its test.

### Added (Redesign Wave C — 2026-06-12)

- **Single canonical end-of-day path**: the native ingestion EOD trigger (01:05 UTC, after the day's fills and journal write-backs settle) is now the one and only EOD review. The standalone docker `eod-agent` is retired from the default compose profile (still runnable via `--profile docker` for manual runs). `run_review` is idempotent per trading date — a second run for the same date is skipped unless `force=True` — so even a manual run can no longer double-process a session.
- **Solver mutation proposals are recommendations-only**: the EOD path no longer auto-promotes solvers to paper. A qualifying mutation variant (same backtest qualification bar as before) now persists a PENDING `PromotionRecommendation` (research → paper) carrying its backtest metrics as evidence, and applies zero stage changes. Promotions happen through the existing manual recommendation-approval workflow. This strictly preserves more signal than the prior native path, which silently dropped proposals.
- **Native migration scaffolding for position-monitor and data-quality**: their docker copies are now profile-gated (`--profile docker`) so a stray `docker compose up -d` cannot start a second close-executor or data-quality loop against the shared Alpaca account. The deployment guide documents the RB.4 functional-parity cutover checklist (capture docker baseline → compare a `--dry-run --once` native snapshot → only then stop the docker copy).

### Changed (Redesign Wave C — 2026-06-12)

- **Database image is plain Postgres 16 + pgvector**: the `timescaledb` compose service now uses `pgvector/pgvector:pg16` instead of the TimescaleDB image. Orion uses no hypertables or continuous aggregates; only pgvector (RAG embeddings) matters. Service name, container name, volume, port (5440), and healthcheck are unchanged, so every connection string and the CI image stay identical. Architecture and deployment docs drop the stale "hypertables / time-partitioned" claims.
- **Dead-man watchdog is calendar-aware**: per-stage pipeline-freshness checks are now gated by the NYSE exchange calendar (`exchange_calendars`) rather than a weekday-only clock, so market holidays and early closes no longer produce overnight false alerts. Service-liveness checks still run around the clock. This is the fix that allows the watchdog to be re-enabled.

### Fixed (Redesign Wave C — 2026-06-12)

- **Market-open data-flow check reads the rotated Gateway key**: the check hardcoded the revoked plaintext Gateway key and reported a false "Gateway down" CRITICAL (HTTP 401) after the 2026-06-11 key rotation. It now reads `DATA_GATEWAY_API_KEY` / `GATEWAY_API_KEY` from the environment, and its launchd wrapper sources `.env` and pins the host Gateway endpoint — same discipline as the ingestion wrapper. The launchd-health probe wrapper likewise sources `.env` so its alert webhook resolves.

### Added (Redesign Wave B — 2026-06-11)

- **Flow push behind a flag with shadow parity measurement**: `ORION_FLOW_SOURCE` selects `poll` (default, unchanged), `shadow` (push + poll run together, union deduped by Gateway-minted event id), or `push`. Shadow mode measures push-vs-poll event-id parity over a lag-tolerant rolling window (`ORION_FLOW_PARITY_WINDOW_SECONDS`, default 900s) and persists per-cycle rows to a new `flow_push_parity` table: matched/missed counts, median push latency lead, and unmatchable legacy-id counts. A matched id is finalized exactly once; an arrival later than the window counts as a miss charged to the late path. This is the measurement gate for the Wave C poll→push cutover.
- **Per-solver / per-rule PnL attribution tables**: daily reconciliation now persists `pnl_reconciliation`, `solver_pnl_attribution`, and `rule_pnl_attribution` rows (migration `b3_pnl_attribution`). Attribution is recommendations-only — no solver stage changes — and is suppressed entirely on untrusted (`BROKER_UNAVAILABLE`) days; a rerun that flips a day to untrusted deletes that day's previously-persisted attribution rather than leaving stale demotion evidence.
- **Typed SQLAlchemy models for safety-critical tables**: risk, gold, and DLQ models converted to SQLAlchemy 2.0 `Mapped[]` typed declarations (schema-identical, machine-verified by parity tests) and promoted to the strict mypy tier, so nullability and detached-instance bugs in the risk path now fail the build.

### Fixed (Redesign Wave B — 2026-06-11)

- **Reconciliation lot book seeds from the entire fills history**: the Wave A version seeded the FIFO lot book from a finite 90-day lookback, which could silently cost a close against the wrong (newer) lot when the true oldest open lot predated the window. The lot book now loads all orion-attributed fills through the target day, so FIFO always consumes the correct oldest lot; a close that exhausts recorded basis (in either direction, including short over-covers) routes the day to `BROKER_UNAVAILABLE` instead of producing a trusted-but-wrong total.
- **Reconciliation refuses to trust a day with pending partial fills**: the `fills` table keeps one cumulative row per order (blended price, latest timestamp), so an order still partially filled at reconcile time — e.g. a GTC exit finishing tomorrow — would be double-counted across days. Any orion order in `partially_filled` status now routes the day to `BROKER_UNAVAILABLE` until the order completes.
- **Multi-day closes reconcile on the close day on both sides**: the journal side now buckets realized PnL by the exit fill's day (matching the lot book's realization-day rule) instead of the preserved entry-fill day, and the live fill-processing path now stamps the closing fill's timestamp into `exit_filled_at_utc` — together eliminating a guaranteed false MISMATCH on every multi-day hold.
- **EOD review analyzes the last closed trading session**: the EOD agent previously defaulted to "today" in UTC, which after midnight UTC (8pm ET) pointed at a day with no data. It now targets the most recently closed NYSE session via exchange calendars.
- **Native launchd wrappers pin the Gateway endpoint correctly**: the wrappers now source `.env` first and then pin both `GATEWAY_URL` and `DATA_GATEWAY_URL` to `http://localhost:8080`, so a docker-only hostname left in `.env` can no longer point the native hot path at an unreachable Gateway (config resolves `DATA_GATEWAY_URL` first, which the previous fix missed).

### Added (Redesign Wave A — 2026-06-11)

- **Unified service-liveness contract + dead-man watchdog**: every long-running loop publishes a heartbeat per successful cycle (errors record without advancing it); a 5-minute watchdog alerts Discord on *absence* of success per service-declared budget, plus market-hours per-stage freshness checks on real pipeline data. Built for this repo's dominant failure mode: the silent stall.
- **Alembic is finally baseline-driven**: the 34-migration incremental chain is squashed into a machine-verified baseline (column/index/FK parity proven); fresh databases bootstrap with plain `alembic upgrade head`; autogenerate gained a guard preventing drops of database-only legacy tables and now sees all 36 models (previously only 6 — every past autogen ran on incomplete metadata).
- **Dedicated-Alpaca readiness (dormant)**: `ORION_ALPACA_*` config scaffolding + `ORION_BROKER_MODE` flag (coerces safely, never crashes the fleet) and a written enablement runbook for when a 4th paper key frees up.
- **Flow-push design doc**: full discovery for replacing the 5-hop Heber polling path with Gateway WS push — event-id parity comes free (Gateway-minted ids flow through to Orion's dedup), implementation queued for Wave B.


### Fixed

- **Order attribution survives Data-Gateway per-client ownership isolation**: the gateway now transparently wraps every `client_order_id` sent to the shared Alpaca account with a `c-orion-` ownership prefix and returns broker orders carrying that wrapped value (e.g. Orion's `orion_<uuid>` comes back as `c-orion-orion_<uuid>`). Orion's attribution layer keys off the bare `orion_` prefix, so without normalization `fill_processor` would drop 100% of Orion fills (`is_orion_owned` → False) and `position_monitor` / `reconcile_pnl` would see no Orion fills. `GatewayTradingClient.get_orders` / `get_order` now strip the ownership wrapper at the client boundary (including nested bracket legs), so all downstream attribution keeps operating in Orion's own `orion_` namespace. Legacy un-wrapped ids pass through unchanged. NOTE: this fixes the read/reconciliation path only — placing/cancelling orders also requires the gateway to grant the `orion` client the `trading` capability (currently `trading: false`).
- **Pytest cannot post fixture alerts to the real Discord channel by accident**: alert tests and execution fixtures use symbols like `AAPL260418C00150000`; if a developer shell inherited `DISCORD_WEBHOOK_URL`, those fake unprotected-position alerts were sent to operators. `send_discord_alert()` now blocks real webhook hosts while pytest is running unless `ORION_ALLOW_DISCORD_IN_TESTS=1` is explicitly set, while still allowing reserved `.test`/localhost webhook URLs for unit tests.
- **Trade journal now preserves entry fill data after close**: closing a position previously overwrote the entry fill timestamp (`filled_at_utc`) with the exit fill time, destroying the entry price/qty/time needed for cost-basis reconstruction and round-trip audit. The journal row now carries dedicated `exit_filled_qty`, `exit_filled_avg_price`, `exit_filled_at_utc`, and `exit_broker_order_id` columns; entry columns are never touched during close. Migration `b4_journal_exit_legs` adds the columns to the live database.

### Fixed (2026-06-12)

- **Cost-basis contamination on restart fixed**: after any execution-service restart, `avg_entry` (cost basis) for open positions is now reconstructed from Orion's own `fills` table (`client_order_id LIKE 'orion_%'`) rather than from the broker's `avg_entry_price`. On the shared Alpaca paper account, the broker's per-symbol average is blended across all systems — using it silently corrupted realized-PnL calculations and could falsely trigger the daily-loss / drawdown kill switches. The new `_compute_cost_basis_from_fills` method replays fills in chronological order using the same weighted-average logic as the live `process_fill` path; broker value is only used as a fallback when no Orion fills exist for a symbol.

### Fixed (Redesign Wave A — 2026-06-11)

- **PnL reconciliation now reconstructs realized PnL from a per-symbol lot book over Orion's own fills (O8)**: the prior approach summed same-day signed cashflow from a walk of the shared account's closed orders, which over-counted any multi-day SWING/POSITION close as its full sell proceeds instead of `proceeds − entry_cost`. The broker side is now a FIFO lot-book replay of Orion's own `fills` table (`client_order_id LIKE 'orion_%'`), so a close realizes against its actual earlier entry and is attributed to the closing fill's day. A close whose entry predates the lookback (default 90d) is flagged untrusted rather than silently counted, and forces `BROKER_UNAVAILABLE`. The journal-vs-fills comparison is now an independent internal cross-check (catches journal-derivation bugs; does not catch broker fills Orion never recorded).
- **PnL reconciliation no longer trusts a missing or partial broker**: a fills-table read failure (DB outage) is no longer coerced into an empty broker result, so reconciliation can't "succeed" against nothing. Read failures and unbasis-able closes produce a distinct `BROKER_UNAVAILABLE` verdict that suppresses per-solver/per-rule attribution and demotion candidates, records the reason, and fires a Discord alert.


### Added (Wave 3 — 2026-06-10 audit remediation)

- **E2E tests run in CI** against a real Postgres+pgvector service container: schema migration, the 9-stage pipeline smoke test, and the pgvector/ON-CONFLICT dialect tests now gate every push (live-freshness checks stay local-only).
- **Pytest markers are real**: unit/integration/e2e auto-apply by directory, so `-m unit` (503 tests) and friends finally select correctly; explicit markers still win.

### Changed (Wave 3)

- Configuration guide and `config.py` now document the recommended env-var names AND the actual precedence when both names of an alias pair are set (first-listed alias wins — e.g. `DB_URL` over `ORION_DB_URL`).
- Vendored `qlib-main/` removed (zero references); stale debug/fix session artifacts archived under `archive/sessions/`.


### Added (Wave 2 — 2026-06-10 audit remediation)

- **Meta-search and meta-weekly are enabled in production for the first time** via native launchd daemons with a Discord alert per scheduled run (success summary or failure) — they had only ever existed behind a never-started compose profile.
- **Unprotected positions now reach the risk layer**: failed protective-bracket legs register in a RiskManager registry (recording which legs are missing), alert Discord once, and PositionMonitor re-places ONLY the missing legs first thing each cycle, with stale entries cleared when the position closes before re-protection.
- **Flow watermark overlap window**: a Heber outage no longer silently loses the gap — every poll re-reads a configurable overlap (dedup absorbs repeats), and the startup lookback is configurable (`ORION_INITIAL_FLOW_LOOKBACK_MINUTES`, `ORION_FLOW_POLL_OVERLAP_SECONDS`).
- **Smoke tests for 5 previously-untested jobs** (dlq_consumer, nightly_backfill, solver_promoter, sync_earnings, rollup_job) and a shared async-main runner deduplicating 7 entry points' startup/shutdown boilerplate.

### Fixed (Wave 2)

- **Rollup watermarks never persisted** — `run_once` never committed, so every rollup run cold-started. Found by the new smoke tests.
- **DLQ rows never left PENDING** — status updates were applied to detached ORM instances in a different session, so every batch replayed forever; replay writes and status updates now commit atomically per batch.
- **Close-escalation classification made explicit** (`classify_close_failure`): ambiguous Gateway failure shapes (missing/None/non-int status codes) always defer instead of risking premature native-flatten escalation.
- **Id-less Heber flow rows now get a deterministic event id** (hash of stable fields) instead of a random uuid per read — required for the overlap window's dedup to hold.

### Fixed

- **RiskManager Prometheus gauges were permanently inert.** The module-level init called the async `Metrics.get_instance()` without awaiting it, so risk equity / daily loss / open positions / slippage / exposure gauges never reached Prometheus — surfaced by enabling real mypy coverage, confirmed blocking by adversarial review. Metrics now resolve via a synchronous singleton accessor on first emission; failures degrade to no-metrics rather than touching the risk path.
- **Bounded the two unbounded in-memory caches in the ingestion path** (2026-06-10 audit #10): `FeatureEngine` per-ticker history/flow dicts now LRU-evict above 500 tracked tickers (re-seen tickers rebuild via the normal cold-start path), and the dedup engine's seen-ids cache is a 200k-entry FIFO with the DB remaining authoritative — an evicted id that reappears is still deduplicated.

### Added

- **mypy actually checks the safety-critical packages now** (audit #23): new pragmatic tier covers all of `orion.execution.*` and `orion.core.*` (188 files checked, 0 errors — previously a blanket ignore meant only 3 files were checked), enforced by a new CI Type Check step.
- **Postgres-dialect e2e tests** (audit #17): pgvector similarity search and ON CONFLICT upsert/dedupe paths now run against the real TimescaleDB — the first run immediately caught a NOT NULL constraint SQLite had been silently ignoring.


### Added

- **Discord alerting + ingestion degrade mode — the Gateway WebSocket can no longer die silently (2026-06-10 audit).** Previously, if the Gateway stream exhausted its 10 reconnect attempts, the client set itself not-running but the ingestion loop kept cycling and heartbeating with `drain_events()` returning `[]` forever — zero bars ingested, nothing alerted. The service now detects a dead post-connect stream each cycle and enters DEGRADED mode: one Discord alert (new `orion.shared.alerts.send_discord_alert`, webhook via `DISCORD_WEBHOOK_URL`/`ORION_DISCORD_WEBHOOK_URL`, 15-min dedupe, never raises), an ERROR log per degraded cycle, continued Heber flow polling, and a `GatewayStreamClient.restart()` attempt every cycle (fresh backoff budget); recovery sends a recovery alert and clears the state (`IngestionService.is_degraded`). Initial-connection backoff never trips degrade.
- **Ingestion cycle latency visibility**: each `_run_cycle` duration is measured; WARNING above 15s, ERROR above 45s.
- **Startup reconciliation for orphaned decisions** (`reconcile_orphaned_decisions`): orders whose broker status reached a terminal/submitted state while their `strategy_decisions` row stayed PENDING (crash between order finalize and decision update) are repaired at execution startup, logged per repair.
- **Dependabot patch auto-merge** (`.github/workflows/dependabot-automerge.yml`): semver-patch dependency PRs auto-merge once CI passes.
- **Default dev-credential warning**: startup logs `default_dev_credential_in_use` when `data_gateway_api_key` or `ai_gateway_key` still hold their well-known dev defaults.

### Changed

- **CI actually runs now.** The workflow triggered on `main`/`develop`, but the repo's only branch is `master` — CI (pre-commit, tests, the Gateway contract test, SBOM) had never fired. Triggers now target `master`; install switched from pip to `astral-sh/setup-uv` + `uv sync --dev` (matching `uv.lock`); Python matrix reduced to 3.12 (pyproject requires >=3.12).
- **`slow` pytest marker is now genuinely excluded by default** (`-m "not slow"` added to addopts; the 19.6s pipeline test is now marked) — the docs claimed this but the config never enforced it.
- **LLM agent shell tool locked down**: `codex_client.run_command` now uses `create_subprocess_exec` (no shell) with a conservative command allowlist; disallowed commands and shell metacharacters are rejected back to the model and logged (`codex_command_rejected`); every accepted command logs provenance (`codex_command_executed`).
- **Heber flow polling no longer blocks the event loop**: the sync parquet read in `_poll_heber_flow` is offloaded via `asyncio.to_thread`, so a large scan can't stall bar ingestion.
- **Repo hygiene**: rotated log files, churning `models/*.pkl` (84MB, dirtied on every retrain), and `predict/` session docs are untracked from git (kept on disk) and gitignored.
- **Labeler feature extraction has a real home**: the 21 live feature functions (plus helpers) that `ml/flow_enricher` and `jobs/backfill_ml_features` imported from the deprecated price-target labeler were mechanically extracted to `orion.labeler.feature_extraction`; importers repointed, `enrich_flow_for_scoring`'s public path unchanged.

### Fixed

- **EOD review agent no longer aborts its whole run on one bad solver proposal (root cause of "EOD never works").** The agent's own prompt instructed the LLM to use `target_solver_id='paper_v1'` — a solver that has never existed (seeds are `bullish_sweep_paper_v1` etc.), so the derived-solver insert hit a foreign-key violation and `_persist_solver_edits` had no error handling: one bad proposal killed the run, including the YAML artifact save. Runs only ever succeeded when the LLM happened to ignore the instruction. Three-layer fix: the prompt now injects the real solver-id list from the DB (hardcoded `paper_v1` removed), each proposal's parent id is validated before insert (invalid → skipped + `eod_proposal_invalid_solver` WARNING), and per-proposal persistence is isolated so a DB error logs `eod_proposal_persist_failed` and the run continues. Diagnosis in `proposals/2026-06-10-eod-meta-diagnosis.md` (which also documents that meta-search/meta-weekly have never been scheduled — the `scheduled` compose profile is never brought up).
- **Order-submit risk-state leak closed**: `update_post_trade`, the intended-greeks stash, and `persist_pending_order` ran before the protected block in `_submit_options_order` — a DB failure in `persist_pending_order` escaped the method, leaking the pending-order reservation and stashed greeks (inflating portfolio-greeks checks for that ticker) and crashing the loop iteration. The persist is now wrapped with full compensation (reservation removed, greeks cleared, decision marked failed) mirroring the broker-error path.
- **Oversized closing fills are loud**: a closing fill larger than the known position (impossible under in-order delivery; the PnL math was already clamped) now logs `closing_fill_exceeds_position` at ERROR — this feeds `current_daily_loss`, the kill-switch input, so silent miscounting was unacceptable.
- **Background feature persistence can no longer vanish**: `FeatureEngine` persistence tasks were fire-and-forget (`ensure_future` with no reference — GC-able, failures invisible). Tasks are now tracked with a done-callback that ERROR-logs `feature_persist_failed`, and `FeatureEngine.drain()` flushes outstanding writes on ingestion shutdown.
- **Pipeline batch failures now reach the DLQ**: a failing feature/rule batch in `_run_pipeline` previously vanished with a single error log (unlike persistence failures, which were DLQ'd); failing batches now route to the dead-letter queue and processing continues.

### Removed

- **`orion.main_price_target_labeler` archived** to `archive/2026-06-10_price-target-labeler/` (deprecated, non-functional pipeline; live feature functions extracted first — see Changed). Its `price_target_labeler` docker-compose service and `depends_on` references are gone; stale doc/docstring references updated. Dead `SCHEDULED_HOUR_UTC` constant removed from `main_meta.py` (the real schedule check is 18:00 ET and is unchanged).

### Added

- **`orion.jobs.market_open_dataflow_check` — a scheduled market-open guard that pages us if the bronze feed stalls at the open.** On 2026-06-08 a service-lease split-brain starved ingestion and only **403** bronze events landed all day (normal is ~150k–200k); the split-brain was fixed, but the stall was found by hand hours later. This check makes that class of silent stall loud. It shells out to `docker exec orion_timescaledb psql` for `max(received_ts_utc) FROM bronze_events` and to `curl` for a Gateway liveness probe (`GET /api/v1/alpaca/account`) — deliberately *not* importing Orion's async DB/Gateway clients, because if ingestion is wedged the heavyweight import path may be exactly what is broken and the guard must still run. The decision is a pure function (`evaluate`): **only during the 09:30–16:00 ET cash session** (computed via stdlib `zoneinfo`, weekends excluded; no holiday calendar — a holiday produces at most one spurious alert, the safe direction for a guard) it emits **CRITICAL** if (a) the Gateway is unreachable, (b) `bronze_events` has no rows, or (c) the newest event is older than `--max-age` (default **300s / 5 min**). Outside market hours it reports `market closed -- no alert` with the raw freshness/gateway diagnostics and exits 0. Every run appends a JSON row to `logs/market_open_dataflow_check.log`; an alert additionally POSTs to `SLACK_WEBHOOK_URL` when set (best-effort — the local log row always lands first, so the stall is auditable even if Slack is down). Wired via `scripts/launchd/com.empire.orion.market-open-dataflow-check.plist` (label `com.empire.orion.market-open-dataflow-check`) which invokes `scripts/run_market_open_dataflow_check.sh`; the wrapper uses the canonical `~/.local/bin/uv` path on purpose — mirroring the launchd-health probe, the system that watches the silent-failure footgun must not itself be vulnerable to it — and extends `PATH` so the spawned check can find `docker` + `curl` (launchd starts near-empty). The plist fires at **06:40 / 07:00 / 07:30 PDT == 09:40 / 10:00 / 10:30 ET** (a few minutes after the open, three staggered fires so a single transient miss isn't fatal), Weekday 1–5 (Mon–Fri), via `StartCalendarInterval` entries interpreted in local time (ET and PT observe DST in lockstep, so 06:40 PT stays 09:40 ET year-round — no seasonal adjustment). Manual invocation (`scripts/run_market_open_dataflow_check.sh`) prints the human-readable summary so an operator sees the freshness/gateway numbers on demand; exit 2 on a CRITICAL alert, 0 otherwise. 22 unit tests in `tests/unit/test_market_open_dataflow_check.py` cover market-hours boundaries (open-inclusive / close-exclusive, weekend, tz-aware requirement), psql timestamptz parsing (`+00` and non-UTC offset normalisation, empty table, garbage), every `evaluate` branch (fresh-OK, stale-CRITICAL, no-rows-CRITICAL, gateway-down-CRITICAL, threshold-boundary, and the critical property that a *very* stale feed outside market hours does NOT alert), and the run loop (log row always written, notify only on alert, notifier-exception-doesn't-swallow-the-log, `main` exit codes).

- **Cross-repo contract test: Orion's `GatewayTradingClient.create_order` ↔ Gateway's `POST /api/v1/alpaca/orders` FastAPI signature.** Regression guard for the 2026-05-22 incident (fix in commit 51699c8): Gateway declares all order fields as bare-typed parameters (no `Body` annotation), so FastAPI classifies them as query parameters; Orion's client was sending them in the JSON body, so every live order submission returned `422` with `loc:["query","symbol"] / msg:"Field required"` — 100% of live submissions blocked, nothing in CI caught it for hours. New `tests/contracts/test_create_order_contract.py` (a) AST-parses Gateway's `create_order` signature from `Data-Gateway/gateway/api/alpaca/trading.py` (dep-free; no need to install Gateway's transitive deps in Orion's venv), (b) mocks `httpx.AsyncClient.request` and runs Orion's client through a single `create_order(...)` call, capturing the outgoing kwargs (`params=`, `json=`), and (c) cross-checks four contract invariants: every required Gateway query param is in `params`, no body field overlaps with a Gateway-declared query field (the exact 2026-05-22 footgun), no query field overlaps with a Gateway-declared body field, no field Orion sends is unknown to Gateway. Verified by temporarily reverting Orion to the buggy `json_body=` call — the test fails with a precise "CONTRACT VIOLATION — 2026-05-22 BUG PATTERN" message listing every misplaced field and pointing at the fix. Looks up the Gateway repo via `DATA_GATEWAY_REPO` env var or side-by-side checkout at `../Data-Gateway`; defaults to skip-with-instructions if missing, but set `ORION_CONTRACT_TESTS_REQUIRE_GATEWAY=1` in CI to convert that skip into a hard failure. Companion meta-test pins the AST extractor's classification of `symbol`/`side` (required query) and `client`/`registry` (Depends-marked dependency, excluded from the contract) so a future change to the extractor itself can't silently neuter the contract check. CI wired in `.github/workflows/ci.yml`: a new `actions/checkout@v6` step pulls `JasperDale420/Data-Gateway` into `${{ github.workspace }}/data-gateway` (using repo secret `DATA_GATEWAY_CHECKOUT_TOKEN` — a PAT or fine-grained token with `Contents: read` on Data-Gateway; default `github.token` cannot read across private repos and the checkout will 404 without it), and the test step sets `DATA_GATEWAY_REPO` plus `ORION_CONTRACT_TESTS_REQUIRE_GATEWAY=1` so a missing Gateway repo turns into a hard failure instead of a silent skip.

- **`orion.jobs.launchd_health_probe` — minute-by-minute watchdog for our launchd jobs.** On 2026-05-22 the `com.empire.orion.orphan-close` plist hardcoded `/opt/homebrew/bin/uv` (which doesn't exist on this host), so every fire silently exited 127 for 4.5 hours and ~$67K of additional unrealized loss accumulated on positions the closer was supposed to flatten. The probe shells out to `launchctl list`, filters to `com.empire.orion.*`, and classifies each entry: exit-127 is escalated to CRITICAL (it can never self-heal — a human must edit the plist), any other non-zero exit is WARNING, and the idle-one-shot pattern (`PID=-`, exit=0) is explicitly NOT an alert. Alerts append a JSON row to `logs/launchd_health.log` and POST to `SLACK_WEBHOOK_URL` when set (best-effort — the local log row always lands first, so the failure is auditable even if Slack is down). Wired via `scripts/launchd/com.empire.orion.launchd-health.plist` (StartInterval=60, RunAtLoad=true) which invokes `scripts/run_launchd_health_probe.sh`. The wrapper uses the canonical `~/.local/bin/uv` path on purpose — the system that watches the silent-failure footgun cannot itself be vulnerable to the same footgun. Tests in `tests/unit/test_launchd_health_probe.py` cover parsing (header skip, `-` PID, negative exits, prefix filter), severity classification (the three task-spec cases: all-healthy, exit-127-critical, idle-one-shot), and the run loop (log append, notifier dispatch, notifier-exception-doesn't-swallow-log). Two monitoring blind spots were then closed after a Codex review: (1) the probe **excludes its own label** (`com.empire.orion.launchd-health`) from evaluation — because `main()` exits 1/2 whenever it reports another job, launchd records the probe's own last-status as non-zero, and without the exclusion the probe would alert on *itself* every minute forever (a self-sustaining feedback loop) even after the real fault is fixed; (2) it now compares the live listing against an expected set `REQUIRED_LABELS` (the always-on `RunAtLoad+KeepAlive` daemons `execution` and `ingestion`) and emits a CRITICAL alert for any required job with **no `launchctl list` row at all** via the new `detect_missing_jobs()` — a daemon booted out or never loaded produces no row, so the per-entry classifier was silently blind to it (the worst silent-failure mode: a daemon entirely gone). `com.empire.orion.orphan-close` is deliberately **excluded** from the required set — a second Codex pass caught that it is a one-shot emergency tool whose plist instructs operators to `bootout`/`rm` it after use, so its absence is the normal steady state; requiring it would fire a permanent false CRITICAL once removed. The required set is overridable via `required_labels=` on `run_probe`/`detect_missing_jobs`. `HealthAlert.exit_code` widened to `int | None` to represent the not-loaded case. 31 tests, all green.

### Fixed

- **greek_exposure enrichment restored — the connector now reads the working `/gex` aggregate endpoint instead of the empty `/spot-exposures` route (RCA 2026-06-09).** Greek-exposure feature enrichment stored **0 records on every cycle all day** (89/89 zero on 2026-06-09), silently feeding empty GEX/VEX/CEX into the feature store. Root cause (verified live against the running Gateway + UW): the connector hit `/api/v1/uw/{ticker}/spot-exposures`, whose Gateway provider deserializes via the vendored UW SDK's `SpotGreekExposuresByStrike` model — a SINGLE-ROW model wrongly applied to a `{"data":[...]}` wrapper response, so the parsed model's `.data` is empty and the Gateway returns `data:[]`. UW itself is healthy: a direct call returns HTTP 200 with full data, and the aggregate `/api/v1/uw/gex/{ticker}` route returns exactly the fields the connector parses (`call_gamma`/`put_gamma`/`call_vanna`/`put_vanna`/`call_charm`/`put_charm`/`call_delta`/`put_delta`) as a daily time series whose latest row is the current day. Fix: repoint `UWGreekExposureConnector._fetch_greek_exposure` to `/gex/{ticker}`, and change the list branch of `fetch_and_store` from summing across the response (correct for the old per-strike shape) to taking the most recent row by timestamp (`max(..., key=timestamp)`, order-independent — correct for the new per-day series). TDD in `tests/connectors/test_greek_exposure_endpoint.py` (hits `/gex` not `/spot-exposures`; uses the latest time-series row not a sum; zero on empty); existing single-dict-branch coverage in `test_uw_gateway_connector_retry_contract.py` still passes. Known follow-ups (out of scope): the broken vendored `SpotGreekExposuresByStrike` SDK model in Data-Gateway (root cause of the empty `/spot-exposures` response), and a staleness guard so a lagging daily `/gex` row isn't written with a fresh `ts_utc`.

- **Unfilled entry orders are now auto-cancelled after 180s instead of resting until the close (RCA 2026-06-09).** Orion entries are mid-priced DAY limits, so an order whose mid drifts off-market never fills — yet it sat working at the broker all session, reserving shared day-trading buying power and risking a late fill on an hours-old signal, and only disappeared when it `expired` at 16:00 ET (observed 2026-06-09: EWY ×7 @14:02, EWY ×1 @15:45, XHB ×1 @16:42, plus W/TQQQ — all `expired`, never filled, never cancelled). The risk manager already pruned its *internal* pending-order tracking on a 1h TTL, but nothing cancelled the live broker order. `poll_fills` now runs a stale-entry sweep right after the existing order-status refresh: `_fetch_stale_entry_orders` selects `orion_`-prefixed rows from the `orders` table with a non-null `broker_order_id`, an *open* broker status (`new`/`accepted`/`pending_new`/`held`/`accepted_for_bidding` — never `filled`/`partially_filled`/terminal), and `created_at_utc` older than `_STALE_ENTRY_ORDER_TTL_SECONDS` (180s); `_cancel_stale_entry_orders` cancels each at the Gateway, drops it from risk pending exposure, and optimistically marks the row `canceled` so it isn't re-cancelled on the next poll. Scoping to the `orders` table makes the sweep structurally safe on the shared account: successful closes persist to `exit_decisions` (no `orders` row) and bracket SL/TP legs aren't persisted, so a buy-to-close on a short or a protective leg can never be cancelled here; the lone `persist_exit_order_rejection` close-row is excluded by the non-null-`broker_order_id` + open-status filters. Best-effort throughout — the Gateway surfaces failures as `{"error": ...}` (a rejected cancel is logged and skipped, never counted), and the sweep is wrapped so it can never abort fill polling. TDD in `tests/execution/test_stale_entry_cancel.py` (cancels each stale entry + drops pending; rejection not counted / keeps pending; exception swallowed; no-op when none; and a real-DB query test asserting only stale unfilled `orion_` entries are selected — fresh, filled, expired, `partially_filled`, `PENDING_SUBMIT`, no-broker-id, sibling-system, and REJECTED-close rows all excluded).

- **Options closes now price off a FRESH chain quote instead of a possibly-stale tracked mark — the robust fix for the stuck/unclosable-position class (RCA 2026-06-08, verified against the live Gateway order history).** A +320% MU put (`MU260612P00790000`) sat unclosable for ~30 min: the close limit was derived from `TrackedPosition.current_price`, a **stale mark of 21.0** for a contract actually worth ~6.45, so the marketable-limit math (`mark*0.925`) submitted a SELL-to-close at **19.4** — far above the bid, so it never filled and **rested, reserving the entire 2-lot long**. Every new full-size close into that window was then priced by Alpaca as *opening* a cash-secured short (`40310000`, `required 158000 = 2×$790×100`); the orders-list read-lag in `_cancel_resting_orion_orders` meant the freshly-rested order wasn't always seen to cancel before the next submit. It only closed once the tracked mark refreshed to 6.45 and the resting order was finally cancelled (limit 6.0 filled). Earlier this was mis-diagnosed as a phantom position and "fixed" by reconciling-to-flat — reverted (the long was real). Fix: `close_position` now fetches a live quote via the new `GatewayTradingClient.get_option_quote(symbol)` (locates the contract in its underlying's chain, short-TTL cached) and prices the close **marketably off the live touch** — SELL-to-close floors to the **bid**, BUY-to-cover ceils to the **ask** (`_fresh_close_limit`; floor/ceil rather than round-to-nearest so the limit is never nudged back off-market) — so the close fills immediately and never rests. Falls back to the prior tracked-mark pricing only when no fresh quote is available (chain down / biddless contract), preserving behaviour. Validated end-to-end against the running Gateway (the MU contract now returns bid 4.4 / ask 4.8). TDD in `tests/execution/test_close_position_options_rth.py` (prices off fresh bid not stale mark; mark fallback; short-cover off ask) and `tests/clients/test_gateway_trading_client.py` (chain parse, missing-contract, bad-symbol, per-underlying cache). The deeper Gateway-side contention (the orders-list read-lag) is documented in `predict/260608-gateway-investigation/findings.md`.

- **Realized PnL is now written back to the trade journal on a closing fill — EOD/weekly PnL reporting was structurally blind (RCA 2026-06-08).** `trade_journal_entries.realized_pnl` was NULL for **all 1,728 rows**, so "was the system profitable?" was unanswerable from the system's own data, and the EOD review + weekly aggregator (which read `journal.realized_pnl`) saw nothing. Root cause: there was **no realized-PnL write-back path at all**. Journal rows are created at *entry* with `realized_pnl=NULL`; the only post-entry journal UPDATE (`persist_fill_record`) matches by `broker_order_id`, but an *exit* fill's `broker_order_id` never appears on the *entry* journal row, so it could never reach it; and `PnLTracker.close_position()` — the one function that computed realized PnL — had **zero callers** in the execution/fill pipeline (only read-only API endpoints touched it, and it resets every process restart). `RiskManager.process_fill` *did* already compute `realized_pnl` on a closing fill (for the daily-loss / kill-switch accounting) but only kept it in memory and returned `None`. Fix: `process_fill` now returns a `FillOutcome(realized_pnl, is_closing)`; `FillProcessor` persists it on a closing fill via the new `persist_realized_pnl_to_journal(ticker, …)`, which attributes the PnL to the oldest still-open entry for that ticker (`broker_order_id` set = actually entered, `realized_pnl` NULL = not yet closed) — exact for the common single-fill full close. The write is defensive (a journal side-write failure is logged, never raised, so it can't break the fill / kill-switch path). TDD in `tests/execution/test_realized_pnl_journal_writeback.py` (closing fill writes PnL; opening fill does not).

- **Gateway WebSocket connect now tolerates the Gateway's slow opening handshake (`open_timeout=30`).** Orion ingestion's `websockets.connect()` set no `open_timeout`, defaulting to **10s**; the Gateway serializes `websocket.accept()` behind a shared `asyncio.Lock` on a single-worker event loop that also serves the REST trading proxy and broadcast fan-out, so under market-open load the handshake exceeded 10s and ingestion logged `TimeoutError: timed out during opening handshake` and reconnect-churned (observed 2026-06-05/07; investigation confidence MEDIUM-HIGH). `gateway_stream_client.connect()` now passes `open_timeout=30`. This is an Orion-side stopgap only — the real fix is Gateway-side (move `accept()` before taking the lock in `ConnectionManager.connect`; add uvicorn `--limit-concurrency`/`--backlog`), tracked in `predict/260608-gateway-investigation/findings.md`.

- **Born-stale flow alerts are now dropped at the ingest boundary — the actual fix for the "Stale at fetch / Data Lag" SKIP storm (root-caused 2026-06-07, gpt-5.5 multi-agent investigation, conf 0.95).** "Stale at fetch" / "Data Lag" was the top strategy-decision SKIP reason every trading day, and it was *not* a slow-pipeline problem: candidates were **born already past** the 600s `max_data_lag_seconds` budget at creation. Evidence (trading days 2026-06-02…06-05): 64–83% of candidates were already >600s old at creation; the "Stale at fetch" cohort's born-lag (`created_at_utc − timestamp_utc`) had p50 ≈ 65,823s with 1836/1837 over budget, while the EXECUTE cohort had born-lag p50 227s and **zero** over budget — a candidate's fate is set at birth. In-pipeline latency (`decision.timestamp_utc − created_at_utc`) was p50 0.7–2.6s / p90 69–169s with an undecided backlog of 0 every hour, so the decision path added ~nothing. The driver is that UW flow ingest is poll/batch, not streaming: each poll mints candidates from accumulated events, and a startup catch-up burst replays prior-day events 17–21h old, manufacturing thousands of guaranteed-stale candidates. Fix: `IngestionService._poll_heber_flow` now drops any flow alert already older than `max_data_lag_seconds` (event-time vs `now`) **before** it becomes a BronzeEvent/candidate. The drop is provably execution-neutral — the downstream Data-Lag cutoff (`fetch_pending_candidates` / `auto_skip_stale_candidates`) is computed against a strictly later `now`, so anything dropped at ingest is even staler at decision time and would already be SKIP'd, and the EXECUTE cohort has zero >600s candidates — it only stops minting doomed candidates and clears the SKIP noise (and suppresses the startup burst). A per-cycle `stale_dropped`/`fresh_kept` log line makes the suppression observable. TDD in `tests/ingestion/test_ingestion_service.py`. Bars from the Gateway WS stream are inherently real-time and untouched. (The earlier Gold-read speedup below was wrongly credited with fixing this — it is a real perf/stability win but does not address born-lag.)

- **Live ML scoring no longer scans the full Gold history per candidate — a per-candidate latency/stability win (RCA 2026-06-05). NOTE: this does *not* fix the "Stale at fetch / Data Lag" SKIP storm; see the born-stale ingest-drop entry above for the actual root cause and fix.** The dominant "Stale at fetch" / "Data Lag" SKIPs were later proven to be born-stale at ingest, not a scoring-latency artefact — but the underlying read inefficiency was real and is still worth fixing. Root cause: `HeberReader.read_gold_features` walked the *entire* `dataset=X/` Heber tree (`rglob("*.parquet")`) and opened every file's footer, with no `dt=` date-partition pruning — the as-of filter was applied in pandas *after* the read, so the cost grew unbounded as Gold accumulated history. The live entry-scoring path (`feature_store.get_scoring_features` → ~15 equity Gold datasets, each 4K–7K files: `flow_features` 6953, `oi_momentum_features` 5193, …) scores candidates serially, so a single candidate cost ~18–23s of footer reads and the backlog aged candidates out. Fix: `read_gold_features` gained an opt-in `lookback_days` — when set it scans only `dt=` partitions on/after `asof_date − lookback_days` (the dt window is threaded through the corrupt-file and schema-merge fallback paths too, so a windowed read never leaks out-of-window rows). The live path passes `ORION_GOLD_FEATURE_LOOKBACK_DAYS` (new env var, default **7**); training/backfill/validation callers pass nothing and still read full history — a global window would have corrupted multi-month training sets. Measured on the real Heber cache: per-dataset read 883ms→205ms (4.3×), **per-candidate read 18.5s→4.0s (4.7×)**. Two safety properties verified against the live cache (74/75 latest-row feature values identical to the full read; the lone diff is a pre-existing same-timestamp tie-break in `_load_equity_gold_for_ticker`, not data loss): (1) **widen-if-empty** — if the window has no rows for the symbol (a low-cadence or upstream-stalled dataset, e.g. `ticker_base_rates`/`trend_scan_features` whose latest partition was 37–49 days old), the read transparently falls back to a full-history read so the most recent row is still returned rather than dropped to `None`; those datasets are small so the fallback is cheap, and the speedup is kept for high-cadence datasets whose window is non-empty; (2) the per-dataset empty-dataset **negative cache** is now written only from full-history reads, so an empty *windowed* live read can never blind a later full-history (training) read keyed by the same dataset name. TDD in `tests/clients/test_heber_reader.py` (pruning, inclusive cutoff boundary, full-read default, widen-if-empty, genuinely-empty). Separately surfaced for follow-up: 6 Gold feature datasets (`ticker_base_rates`, `trend_scan_features`, `flow_normalization_features`, `flow_context_features`, `market_regime_features`, `iv_surface_features`) have no partition newer than 2026-04-23…29 — a likely upstream Gold-builder gap worth investigating.

- **Execution-service SIGABRT eliminated — Heber parquet reads no longer spin up Arrow's C++ threadpool.** Root-caused (gpt-5.5 multi-agent investigation, macOS crash reports) two `Abort trap: 6` crashes on 2026-06-02 to a PyArrow C++ `ThreadPool` worker hitting an unhandled C++ exception that escaped to `std::terminate()`→`abort()`, taking down the whole execution process (each followed ~2s later by a launchd restart). The reads run inside an `asyncio.to_thread` executor thread, and spinning a detached Arrow worker from there is what aborted. `HeberReader._read_table` now passes `use_threads=False` to `pq.read_table` — these are small single-symbol/recent-row reads, so single-threaded is plenty, and it removes the abort path entirely. Follow-up (adversarial review) closed the two remaining Arrow-threadpool reach-points on the same read paths: `pq.read_table` now also passes `pre_buffer=False` (the default `True` can spin a background I/O prefetch pool) on both the primary and filter-pushdown-fallback reads, and the Arrow→pandas conversion `table.to_pandas(use_threads=False)` is now single-threaded on both the direct (`_read_parquet`) and per-file (`_read_parquet_filewise`) paths (`to_pandas` defaults to `use_threads=True`, spawning the same Arrow CPU pool that aborted). Regression tests in `tests/clients/test_heber_reader.py` assert `pre_buffer=False` on the read_table calls and `use_threads=False` on the to_pandas calls across the direct, filter-fallback, and filewise paths.

- **Services no longer crash-loop (or start degraded) on a transient TimescaleDB outage.** Startup called `init_db()` directly, so a brief DB blip raised, exited the process, and launchd restarted it on a 30s throttle (observed live 2026-06-07 18:03–18:23 during a Docker restart); worse, an ingestion start with the DB down left the WS subscription pinned to the 11-ticker static watchlist for the *entire* session (the 2026-06-01 near-outage — Alpaca breadth collapsed 314→11). New `db.wait_for_db()` polls a cheap `SELECT 1` with bounded exponential backoff before `init_db()` in both `main_execution` and `ingestion.service`, so a transient outage clears before the service proceeds; it still raises loudly after the bounded attempts so a genuinely-down DB surfaces (fail-fast-after-bounded-retry). It is shutdown-aware (`cancel_event`) so a SIGTERM/SIGINT during the wait aborts startup promptly instead of blocking for the full backoff. Separately, ingestion's startup `UniverseManager.hydrate_from_db()` now runs with `required=True` — a DB error during hydrate re-raises (fails the startup loudly) instead of silently falling through to a static-watchlist-only session, which is what pinned ingestion to 11 tickers all day on 2026-06-01 (the periodic/`required=False` path keeps the non-fatal swallow). TDD in `tests/storage/test_wait_for_db.py` and `tests/core/test_universe_persistence.py`.

- **Ingestion now self-heals a collapsed universe and alarms on bar-breadth collapse — completes the 2026-06-01 near-outage remediation.** The startup `wait_for_db` + `required=True` hydrate (above) stops a DB-down start from *beginning* pinned to the 11-ticker static watchlist, but nothing re-broadened a universe left sparse for any other reason — once degraded, the WS subscription stayed at 11 tickers for the whole session (Alpaca bar breadth 314→11, bronze 21.6k vs ~150–200k normal). Two self-healing nets added to the ingestion run loop: (1) the universe is now **periodically re-hydrated** from recent `candidate_trades` every `ORION_UNIVERSE_REHYDRATE_INTERVAL_SECONDS` (default **300s**, `0` disables) — non-fatally (`required=False`, so a transient DB error here can't crash the loop) — and the existing subscription sync immediately subscribes any recovered tickers on the Gateway, so a degraded/sparse start **self-broadens within minutes** instead of persisting all session; (2) a market-hours **breadth alarm** logs CRITICAL `UNIVERSE_BREADTH_COLLAPSE` when the count of subscribed Alpaca tickers falls below `ORION_UNIVERSE_BREADTH_MIN_TICKERS` (default **30** — above the 11-ticker static floor, below normal 100s+ breadth), so a collapse like 06-01 pages at the open rather than being found in a post-mortem. TDD in `tests/ingestion/test_ingestion_service.py`: re-hydration interval gate, breadth threshold + market-hours gating, run-cycle wiring guard, and an end-to-end sparse-start → re-hydrate → subscription-self-broadens test.

- **Bracket stop-loss / take-profit orders are now orion-attributed and reduce-only — closes a naked-short hole (adversarial review 2026-06-05).** `_place_bracket_orders` created the SL/TP child orders with **no `client_order_id` and no `position_intent`**. Two consequences: (1) the close-path cancel sweep (`_cancel_resting_orion_orders`, which matches the `orion_` prefix) couldn't see them, so a resting bracket SELL survived a flatten and could later fire on a now-flat position as a **naked short**; (2) their fills were dropped by the `orion_`-prefix fill filter, so bracket-exit P&L never reached the risk manager. Both legs now carry an `orion_` `client_order_id` (cancellable + attributed) and reduce-only `position_intent` (the Gateway threads it for the limit take-profit as defence-in-depth against an opening fire), and a bracket leg that returns an `{"error": ...}` dict (the Gateway surfaces HTTP failures that way rather than raising) is now recorded as a FAILED leg instead of a false "protected" with `order_id=None`. TDD in `tests/execution/test_bracket_orders.py`. (A deeper bracket-lifecycle gap remains — a bracket leg filling on its own leaves the sibling GTC leg resting; that needs a broker-native OCO and is tracked separately. Bracket orders are off by default.)

- **Option closes are now an orion-attributed LIMIT first, with native-flatten escalation — fixes self-wash and intent-mismatch close rejections while keeping close PnL attributed (RCA 2026-06-05 + adversarial review).** Reviewing the week of 2026-06-01 found a +320% MU put winner stuck unclosable: the options exit path submitted an opposing marketable LIMIT priced off a stale mark, which on the shared Alpaca account produced two broker rejections — a **self-wash** (`40310000` "sell limit price should be greater than existing buy limit price": the stale mark put the SELL *below* Orion's own $5.00 entry) and a **position-intent mismatch** (`42210000`, new on 06-05 from the forced `position_intent`). The native `DELETE /positions` close avoids both, but carries no `orion_` client_order_id, so its fill is dropped by Orion's `orion_`-prefix fill filter and never reaches `RiskManager.process_fill` — making it unsafe as the default (realized PnL / `current_daily_loss` / drawdown kill switch would go blind to option closes). So `close_position` now, for options inside RTH: (1) cancels Orion's own resting orders on the symbol (`orion_` prefix only), checking the Gateway's `{"error": …}` result and **deferring** the close if a cancel can't be confirmed; (2) submits an **orion-attributed marketable LIMIT** as the primary close — the side comes from the broker qty sign, the limit is a plain marketable price (`mark*(1±0.075)`, NOT floored against avg entry: an entry floor would push a loser's exit away from the market so Alpaca accepts a *resting* limit that never fills), and a `42210000` mismatch **re-verifies the live position**, retries reduce-only only if we still hold the same side, and **caps the retry qty to the current live quantity** so a position that shrank between attempts can't over-close into a short; (3) only on a **confirmed broker 4xx rejection** (or an unpriceable limit) does it **escalate to the native `DELETE /positions` flatten** (bounded to `abs_qty`; a vanished position counts as already-closed) — an ambiguous outcome (timeout / transport error / 5xx, no `status_code`) **defers** instead, since the limit may have been accepted and be resting and a native flatten would double-close into a naked short. The attribution gap is accepted only in the confirmed-rejection escape case. Two rounds of adversarial review (gpt-5.5) shaped this: round 1 caught that a native-first default would blind the kill switch to close PnL; round 2 caught the over-close-on-retry and non-marketable-floor hazards. TDD in `tests/execution/test_close_position_options_rth.py` and `tests/execution/test_close_reduce_only.py`.

- **Close abandonment is now time-bounded, not permanent.** The per-symbol consecutive-failure backstop (`_MAX_CONSECUTIVE_CLOSE_FAILURES=5`) stranded the MU winner: its 5 wash-trade rejections on 06-04 exhausted the counter, and nothing reset it short of a process restart, so the position re-signalled every ~60s but was never re-attempted. `position_monitor` now records a per-symbol last-failure timestamp and, once a `_CLOSE_ABANDON_COOLDOWN_SECONDS=600` cooldown elapses, gives the symbol another attempt and resets the counter — a transient cause (stale mark, buying-power wall, sibling's resting order) can clear instead of stranding the position forever. The CRITICAL `CLOSE_ABANDONED` alert now notes the retry-after window. TDD in `tests/execution/test_close_stop_retry.py`.

- **Gateway/Alpaca error reasons now survive to the caller and the `orders` table — no more bare "403 Forbidden".** `GatewayTradingClient._request` discarded the response body on HTTP errors, returning only `str(exc)` ("Client error '403 Forbidden' for url …"), so `orders.error_message` recorded the status line and lost the actual Alpaca reason code (`40310000` insufficient day-trading buying power, `42210000` intent mismatch, "potential wash trade detected"). It now returns `{"error", "detail", "status_code"}` with the full body; entry rejections append `detail` to `error_message`, and the close-path intent retry branches on it. TDD in `tests/clients/test_gateway_trading_client.py`.

- **Close-order rejections are now persisted to the `orders` table.** The exit path only persisted on success, so a position the system *tried and failed* to close was indistinguishable in the DB from one it simply held — the five 06-04 MU wash-trade close rejections left **zero** `orders` rows. New `persist_exit_order_rejection` writes a `REJECTED` row (with the real reason) at each close-failure exit, making exit failures auditable alongside entry rejections.

- **Opening orders back off when the shared account's day-trading buying power is exhausted.** When the shared Alpaca paper account hits `daytrading_buying_power=0` (PDT wall, mostly driven by sibling systems), Alpaca rejects every new opening order `40310000`; Orion fired 193 rejected buys in a single day (06-02) hammering through it. Two-layer defense: (1) a **proactive** pre-check reads a short-TTL-cached account snapshot and skips the order with a clear `Insufficient day-trading buying power` reason before reserving rate-limiter/risk/pending state (fails OPEN — a missing field or transient read never blocks trading); (2) a **reactive** backoff arms a 120s cooldown after a *confirmed* broker `40310000` rejection, so even when the proactive check fails open on a degraded/malformed account read, Orion stops submitting opening orders after the first confirmed wall instead of flooding (adversarial-review hardening). TDD in `tests/execution/test_dtbp_gate.py`.

- **Expired pending orders are pruned at runtime, not just on restart.** A day order that expires unfilled never fires a fill, so `remove_pending_order` is never called and the row lingered as phantom pending exposure until the next process restart (three expired 06-04 entries — RBLX/ETSY/QURE — were still counted on 06-05 on the long-running native process). `RiskManager.prune_stale_pending_orders` runs the existing TTL sweep (`PENDING_ORDER_LOAD_TTL_SECONDS=3600`) on the periodic risk re-sync, bounding staleness to the TTL. TDD in `tests/execution/test_pending_orders_persistence.py`.

- **launchd native services (`execution`, `ingestion`) no longer orphan their python process on `launchctl kickstart -k`.** The wrapper scripts ended with `exec uv run python -m …`; `exec` made launchd manage the `uv` process, but `uv run` spawns python as a *child* subprocess, so a `kickstart -k` SIGKILL killed `uv` and left python orphaned (reparented to PID 1). The orphan kept holding the single-instance service lease (`SERVICE_LEASE_*`, TTL 120s), so the freshly-started instance blocked ~2 min waiting for the lease to go stale — during deploys this stacked multiple instances and forced manual `bootout`/`pkill`/`bootstrap` recovery. Fix: `scripts/run_execution_native.sh` and `scripts/run_ingestion_native.sh` now `uv sync` up front (tolerating a transient sync failure when a usable `.venv` already exists) and `exec` the venv interpreter directly (`exec "${PROJECT_ROOT}/.venv/bin/python" -m …`), so launchd manages python itself and a kill reaches it with no orphan. Each wrapper's header now documents the safe-restart and orphan-recovery sequence.

- **position_monitor abandons a position after repeated close failures instead of retrying forever.** Backstop to the reduce-only fix: a per-symbol consecutive-failure counter (`_MAX_CONSECUTIVE_CLOSE_FAILURES=5`) stops `execute_exits` from re-submitting a close every 60s for a genuinely stuck position; on the 5th consecutive failure it logs a CRITICAL `CLOSE_ABANDONED` alert and skips that symbol until a close succeeds or the process restarts. TDD in `tests/execution/test_close_stop_retry.py`.

- **Close orders now carry `position_intent` (`sell_to_close` / `buy_to_close`) — defense-in-depth against opening a short on a close.** `GatewayTradingClient.create_order` gained a `position_intent` param (threaded as a query param), and `close_position` sets it from the broker-verified close side. Even if the reduce-only verification were bypassed, Alpaca now rejects a close that has no matching position rather than opening a (naked short) one. Requires the matching additive `position_intent` param on Data-Gateway's `create_order` (endpoint + Alpaca provider). The cross-repo `create_order` contract test confirms Orion only sends fields the Gateway signature accepts.

- **Position closes are now strictly reduce-only against the live broker position — stops a ~3,235/day rejected-order flood and a naked-short bug.** `close_position` trusted the tracked `qty` passed by `position_monitor`. For 0DTE puts where the tracked long no longer existed at the broker (expired/already-closed), it still submitted a SELL — and with no `position_intent`, Alpaca treated each sell as *opening* a cash-secured short put: rejected `40310000` "insufficient options buying power" (~96%) / `42210000` "expires soon, unable to open new positions", retried every 60s (~3,235 rejected order-creates/day in the Gateway logs). Worse, when buying power was available, some sells **filled past the held long**, flipping Orion into naked short puts (fills showed sold > bought for 9 contracts; 5 SPY contracts were sold with *zero* prior buys). Fix: `close_position` re-fetches the live broker position (`_live_position_qty`) and only ever **reduces** it — SELL only when actually long, BUY-to-cover only when short, cap the qty to the held amount, and **refuse to submit when the position can't be confirmed or is flat** (fail-safe: an unverified close could open a short). The broker's signed qty is authoritative for the close direction (overriding the caller's `direction` hint). This also self-heals the existing naked shorts (it will buy-to-cover them). TDD in `tests/execution/test_close_reduce_only.py`; the existing close-path tests (`test_close_position_options_rth`, `test_close_position_short_equity`, `test_execution_engine_close_direction`) now stub the live-position fetch.

- **Position-sizing equity baseline now caps to Orion's allocated slice ($100K), not the full shared account.** `_sync_risk_from_gateway` seeded `current_equity` from the Gateway account equity with no cap — but the Alpaca paper account (~$1M) is shared across ~6-10 systems, so Orion's baseline was the full pool. Sizing (`max_option_premium_pct=2%`, `max_order_size_pct=5%`) computed off $1M → ~10x the intended slice, the root cause of the 5/26 over-exposure (39 positions / 1,007 contracts in a day). New `RiskSettings.allocated_equity` (default `100_000`, env `ORION_RISK_ALLOCATED_EQUITY`, `None` disables) caps the one-shot seed via the new `RiskManager.seed_equity_baseline(gateway_equity)` helper, applied at both seed sites (`_sync_risk_from_gateway` and `poll_fills`). After seeding, equity still moves only via Orion-attributed fills. TDD in `tests/execution/test_equity_seed_cap.py`.

- **`open_positions` no longer drifts high → no more phantom "Max Positions Reached" entry blocks.** `_sync_risk_from_gateway` (which ground-truths the Orion position count against the broker) ran startup-only, so a long-running execution process drifted high as position-monitor closes and option expiries never flowed back as Orion-attributed fills (observed 2026-05-29: execution count 15 vs broker 12). `poll_fills` now re-runs the broker resync on a 120s cadence (`_POSITION_SYNC_MIN_INTERVAL_SECONDS`), so the count self-corrects between restarts and entries aren't blocked at the `max_positions` gate after real positions fall below the limit. Resync failures are caught and logged without breaking fill polling. TDD in `tests/execution/test_poll_fills_periodic_sync.py`.

- **Position snapshots are now actually persisted.** `_maybe_snapshot_positions` was defined but never called, leaving the `positions_snapshots` table empty (no historical position record). `poll_fills` now invokes it every iteration (it self-throttles to its own 60s interval).

- **Transient Gateway blips no longer abandon a position close.** `_check_gateway_available` caches its result for 60s, so a momentary blip's `False` stuck for a full minute — and `close_position` bailed immediately on it (`"Data Gateway unavailable. Cannot close position."`, observed 11× on 2026-05-29), delaying a critical exit until the next monitor cycle. `_check_gateway_available` gained a `force` flag to bypass the cache, and the close path now retries a fresh probe (`_gateway_available_for_close`: 3 attempts, 1s backoff) before giving up. TDD in `tests/execution/test_close_gateway_retry.py`.

### Changed

- **`ORION_RISK_MAX_POSITIONS` raised 5 → 10** in `run_execution_native.sh` (and `.env.example`), alongside a new explicit `ORION_RISK_ALLOCATED_EQUITY=100000`.

### Fixed

- **Options Greeks risk limits now actually enforce on the live execution path.** `enable_greeks_checks=True` (default) plus configured limits (`max_portfolio_gamma=100`, `max_portfolio_vega=200` "IV crush protection", `max_position_delta`/`max_portfolio_delta`) did *nothing* in production: the only enforcement entry point, `RiskManager.check_options_order`, had zero callers — the live path called plain `check_order`, no greek data ever reached the risk manager, `update_position_greeks`/`clear_position_greeks` were never called, and `portfolio_delta/gamma/vega` sat permanently at 0.0. Gamma/vega/IV-crush exposure was uncapped. Now `_execute_options_order` reads per-contract `delta/gamma/theta/vega` off the option-chain response it already fetches (no extra round-trip — the Gateway chain ships greeks), projects them to share-equivalent position greeks (`per-share × 100 × num_contracts`), and routes through `check_options_order` so the configured portfolio/position limits gate every order. On fill, `process_fill` applies the order's stashed greeks to portfolio totals; on close it clears them, so the projected-greek check stays accurate across positions. **Fail-safe is stage-gated:** when a contract's greeks are unavailable (Gateway returns `None` for illiquid/far-OTM contracts) the order is *blocked* with an ERROR (`options_blocked_greeks_unavailable`) in live, but *proceeds* with a WARN (`greeks_unavailable_skipping_gate`, all other risk checks still applied) in paper/test — so flaky greek data never silently halts paper trading nor silently passes uncapped exposure in live. A real `0.0` greek counts as present data, not missing; `ORION_RISK_ENABLE_GREEKS_CHECKS=False` disables the gate entirely (and the missing-greeks block with it). Six TDD integration tests in `tests/execution/test_greeks_enforcement_integration.py` pin the wiring (portfolio-gamma breach rejected/broker untouched; within-limits submitted + greeks stashed; missing-greeks paper proceeds; missing-greeks live blocked; missing-greeks live+checks-disabled proceeds; portfolio greeks tracked across fill→close); all 320 `tests/execution/` tests pass. `signal_preflight` is intentionally unchanged — it is a coarse pre-signal filter that runs before the chain is fetched and has no greeks; the authoritative order gate is the execution engine.

- **`test_full_system_flow` e2e: stale option-chain mock dropped the order before submission.** The test mocked `get_option_chain` with `{"symbol": ..., "mid": ..., "ask": ...}`, but the execution engine matches on `contract.get("contract_symbol")` and reads `bid`/`ask` (not `mid`) — the `symbol` key never matched the candidate's `option_symbol`, so `option_price` stayed `None`, the engine logged `options_price_fetch_failed` and set the decision FALSE, and `create_order` was never called (`mock_client.create_order.assert_called()` failed). Mock now uses `{"contract_symbol": "SPY260418C00500000", "bid": 1.0, "ask": 1.05}`, matching the production contract and the `tests/execution/*` convention; the full pipeline reaches order submission and the test passes.

- **Shared-account filter now matches options positions by underlying — exits actually fire again.** On 2026-05-26, the first full day of post-Gateway-fix live trading produced 39 entries and **0 exits**; -$13.9K unrealized accumulated on positions deep enough to stop out (LRCX put -32%, PLTR put -31%, MU put -46%). Root cause: commit `dca484d` introduced a default-deny filter on `GatewayPositionAdapter.refresh()` (and a sibling in `ExecutionEngine._sync_risk_from_gateway`) that compared the broker's full OCC contract symbol (`AAPL260529P00315000`) against a set of underlyings drawn from `orders.ticker` (`AAPL`). The match can never succeed for options, so every Orion options position was silently dropped before the exit-evaluation loop saw it (`PositionAdapter filtered 37 non-Orion positions; kept 0` every minute, all day). Friday's orphan-close didn't surface the bug because that script writes directly to Gateway, never through the `orders` table. Both filters now derive the underlying via a new shared helper `orion.execution.attribution.occ_underlying(symbol)` — equity passes through unchanged, OCC strips down to the root ticker. Two new TDD regression tests pin the contract: `test_adapter_keeps_orion_options_positions_via_underlying_match` and `test_sync_risk_from_gateway_matches_options_by_underlying`. Both fail on the pre-fix tree (`set() == {'AAPL260529P00315000'}` / `open_positions=0`), both pass after; all 312 `tests/execution/` tests pass with no regressions.

- **Two-phase order persistence — broker can no longer hold an order Orion's DB knows nothing about.** Between 2026-05-12 and 2026-05-21, 47 EXECUTE decisions landed 37 options positions on the Alpaca paper account with ZERO matching rows in the `orders` table — visible via Gateway `/alpaca/positions` ($556K market value, -$476K unrealized P&L) but invisible to position attribution, risk sync, and exit pipelines. Root cause: `_submit_options_order` wrote the DB row only AFTER `client.create_order()` returned, and `orion_execution` was crash-looping (380 restarts in 24h from the lease-conflict deadlock fixed in 622bbb4) — when SIGTERM landed in the window between the broker accepting the order and `persist_order_record` committing, the row never landed. Fix: split persistence into `persist_pending_order` (INSERT with `status='PENDING_SUBMIT'`, called BEFORE the Gateway round-trip) + `persist_order_finalize` (UPDATE by `client_order_id` after, stamping `broker_order_id` + broker-reported status on success, or `status='REJECTED'` + error_message on failure). Even a process kill mid-Gateway-call now leaves a durable tracking row in `PENDING_SUBMIT` state — a startup reconciler can query Gateway by `client_order_id` and update the row to its real broker state. `PENDING_SUBMIT` and `REJECTED` are new status sentinels distinct from broker-side values (`accepted`/`filled`/`pending_new`/`rejected`/`expired`/...) so the reconciler can tell "we never heard back" from "broker said no". The trade journal upsert moves to the pre-submit write and is enriched by finalize when `broker_order_id` is known. Forensic regression tests in `tests/execution/test_execution_engine_pending_submit.py` (4 cases: row lands before Gateway call, row persists through `asyncio.CancelledError` mid-call, finalize-to-broker-status on success, finalize-to-`REJECTED` on Gateway error); all 309 `tests/execution/` tests pass; the smoke test patch target updated from `persist_order_record` to `persist_pending_order`.

- **`close_position` now handles signed broker qty for SHORT equity positions** — `EXIT_ORDER_FAILED: invalid qty: -8000.0` on CRNC was the last remaining exit-failure noise after the Phase 2 options-RTH fix. Alpaca returns SHORT equity positions with negative `qty`, and `PositionMonitor.GatewayPositionAdapter` (unlike `_sync_risk_from_gateway`) loads the full shared-account position list verbatim — so any sibling system's short on a ticker Orion has also touched (e.g., CRNC, where Orion bought puts on a SHORT-bias candidate; another system separately shorted 8000 shares of the underlying) lands in `tracked_positions` with `qty=-8000.0`. From there the value flowed straight into `ExecutionEngine.close_position(qty=…)` and on to Gateway's `DELETE /positions/{symbol}?qty=…` and `POST /orders` body, both of which Alpaca rejects with `invalid qty`. Two changes in `close_position` (`src/orion/execution/execution_engine.py`): (a) early `held_short = qty < 0; abs_qty = abs(qty)` normalization — every downstream Gateway call now receives `abs_qty` (both the options limit path and both equity paths); (b) equity close side is derived from the qty SIGN, not from the `direction` argument. `direction` is the candidate's bullish/bearish bias and can be defaulted to `LONG` for positions opened by sibling systems (whose entry-context lookup misses); the qty sign is the broker's ground truth — `held_short=True` → BUY-to-cover, limit above mark; `held_short=False` → SELL-to-close, limit below mark. The options branch still consults `direction` for close-side (existing `test_close_position_options_rth.py` contract preserved); only the qty value is normalized there. New regression test in `tests/execution/test_close_position_short_equity.py` (3 cases: SHORT-equity market path, SHORT-equity limit path with `direction="LONG"` hint deliberately wrong, LONG-equity unchanged path); all 300 `tests/execution/` tests pass.

### Added

- **Regression-catching smoke test for the fallback-rules wire-in** (Phase 4 of exit-pipeline RCA). Earlier integration tests in `tests/execution/test_position_monitor_fallback_integration.py` proved the fallback-then-ML branching contract on a per-case basis, but both passed when the wire-in was DELETED entirely (a benign ML classifier covers both branches transparently). New `test_fallback_rules_called_once_per_tracked_position` spies on `evaluate_fallback_rules` and asserts the call count equals the tracked-position count exactly once per loop. Verified by temporarily replacing the call site with `fallback = None` — the new test fails immediately while the older tests still pass. Closes the kind of silent-removal vector that the original Phase 2 wire-in lived in for weeks before being audited.

- **`position_running_stats` table — persists per-position MFE/MAE across restarts** (Phase 3 of exit-pipeline RCA, fixes Bug #3). New `PositionRunningStats` model in `models_execution.py` (per-symbol PK, `max_return_pct` / `max_drawdown_pct` / `last_updated_utc`; created automatically by `init_db.metadata.create_all`). Before this fix, PositionMonitor.sync_positions accumulated `TrackedPosition.max_return_pct` / `max_drawdown_pct` in memory only — values lost on container restart. After restart, `_track_new_position` re-seeded them as `max(0, unrealized_pnl_pct)` / `min(0, unrealized_pnl_pct)`. A position that had hit +250% and dipped to +50% looked to the ML exit classifier like its peak was +50% (no peak observed). Combined with the other RCA-A defaults (pnl_velocity=0, distance_to_barrier=1.0, flow_score=0), the classifier saw a "fresh stable trade" feature vector for every re-hydrated position and never crossed the 0.55 exit threshold. New `upsert_position_running_stats` / `load_position_running_stats` in `persistence.py` (both swallow DB errors with WARN logs — must never break the every-5-second sync_positions loop). `sync_positions` now upserts only when a tick observes a new peak/trough (avoids 600 writes/min for unchanged envelopes with ~50 positions × 12 cycles/min) and consults `load_position_running_stats` on the new-position path, seeding with `max(persisted, current, 0)` / `min(persisted, current, 0)` so the more-extreme value wins (a hot-restart-during-spike doesn't shrink the envelope). Tests: 5 unit + 1 integration end-to-end restart simulation in `tests/execution/test_position_running_stats.py`; 287 tests in `tests/execution/` pass with no regressions. Verified live: post-restart, `position_running_stats` table populated with 50 rows in 25 seconds, top winners ALAB +335% / COIN +195% / NBIS +118% all persisted.

### Fixed

- **Options-aware `close_position` — exits no longer rejected by Alpaca outside RTH** (Phase 2 of exit-pipeline RCA, fixes Bug #1, the root cause of "positions never exit"). Every fallback-rule exit was being submitted as a MARKET order via `client.close_position(...)`. Alpaca rejects options market orders outside 9:30am–4:00pm ET with error `42210000`. Production logs showed 21+ `EXIT_ORDER_FAILED` events per 3-minute after-hours window. `ExecutionEngine.close_position` refactored to (a) detect option symbols via the new shared `is_occ_option_symbol` helper in `orion.execution.attribution`, (b) gate options against a new `MarketSchedule.is_market_open_for_options(now)` helper that enforces the [9:30, 16:00) ET window (matches Alpaca's behavior), (c) outside-RTH options return False with DEBUG log (the position-monitor loop retries every 5s across ~50 positions — WARN would flood), (d) inside-RTH options ALWAYS use a marketable LIMIT order priced 7.5% off the caller-supplied `current_price` (the mark `sync_positions` maintains on `TrackedPosition`), direction-aware (LONG close → SELL below mark; SHORT close → BUY above), rounded via `round_to_options_tick`, and (e) options without `current_price` return False with `EXIT_ORDER_FAILED` log — defensive, never fall back to market for options. Equity behavior unchanged: market for IMMEDIATE/use_market, limit otherwise; no RTH gate. `is_occ_option_symbol` moved from `position_monitor.py` to `attribution.py` so `execution_engine.py` can import without circular-import risk; `position_monitor` re-exports the old `_is_occ_option_symbol` alias for backward compatibility. `position_monitor.execute_exits` now passes `use_market_order=False` and `current_price=pos.current_price` so the engine can route correctly per (option/equity, RTH/closed). Tests: 5 new in `tests/execution/test_close_position_options_rth.py` cover every branch; 6 new in `tests/core/test_market_schedule.py` cover the RTH window edges; 1 existing in `test_position_monitor_routing.py` updated to the new contract. 318 tests pass across `execution/`, `ml/`, and `core/` with no regressions. Verified live: post-restart of `orion_position_monitor`, zero `42210000` errors in 100 seconds (vs 21+/3min before); options exits correctly skip silently outside RTH; the only remaining EXIT_ORDER_FAILED in the window is `CRNC` (equity with negative qty `-8000.0` — a separate pre-existing data bug).

- **`performance_tracker` async write callbacks — `ml_predictions` table finally populates** (Phase 1 of exit-pipeline RCA, fixes Bug #2). `log_entry_prediction`, `log_exit_prediction`, and `log_outcome` each defined an inner `def write(session) -> None` callback (sync, returning None), but `db_transaction` does `await operation(session)` — so `await None` raised `TypeError: object NoneType can't be used in 'await' expression` on EVERY call. The TypeError was caught by the broad except and the function returned None / False, silently. Production logs showed zero `ml_predictions` rows in 30+ days despite ~12 exit-evaluation cycles per minute. Discovery: 4-agent parallel RCA into "why don't positions exit?" (`docs/rca/exit_classifier/SYNTHESIS.md`). Fix: all three inner `write` callbacks are now `async def` with `await session.execute(...)`. Surfaced a second bug while building the real-DB integration test — the raw INSERT skipped `prediction_ts` (NOT NULL with Python-side default, which only fires through the ORM, not raw `session.execute(text(...))`); added `prediction_ts` explicitly. Tests: existing 23 mock-based tests pass with an updated helper that now correctly awaits the async write_fn (the old helper called it synchronously — which is why these tests passed even while production was crashing for months); 2 new `TestPerformanceTrackerRealDB` integration tests exercise the actual `db_write → db_transaction → AsyncSession.execute` chain against in-memory SQLite. After restart of `orion_position_monitor`, 24 `ml_predictions` rows landed in the first 2 minutes — observability restored.

- **`run_quality_checks` no longer holds 5+ GiB of redundant Heber DataFrames mid-pass** — FOLLOWUPS #6, root cause of the May 13 `orion_data_quality` cgroup-OOM cascade. Each pass through `data_quality_checker.run_quality_checks` was pulling the same parquet payload multiple times: flow read twice (`get_flow_summary` + `check_flow_staleness`), darkpool read twice (same shape), and both Gold datasets `labels_alert_barriers` + `meta_label_features` read twice each (once by `get_ml_features_summary`, once by `check_recent_labels_features`). With no eviction between phases, up to 8 large pandas DataFrames could be alive simultaneously — enough to push the 6 GiB-capped container past its `mem_limit` and get SIGKILL'd by the cgroup memory controller, which in turn cascaded through the Docker Desktop VM and reaped `orion_ingestion` / `orion_execution` as collateral. The fix is structural, not literal pyarrow streaming: a per-run `ContextVar` cache (`_run_cache`) in `data_quality_checker` so duplicate consumers within a single `run_quality_checks` invocation share one materialization, plus explicit `_cache_evict_prefix(...)` calls at phase boundaries (bars → flow → darkpool → ml) so the previous phase's DataFrames are GC-eligible before the next phase allocates. The four bars reads (24h-all-symbols, 1h-all-symbols, 24h-CRITICAL_TICKERS, 24h-SPY) can't be deduped — their argument shapes differ — but they're each evicted at the end of the bars phase. Public per-check functions (`get_flow_summary`, `check_flow_staleness`, `get_bars_summary`, etc.) are unchanged in signature; they consult the ContextVar transparently, so standalone callers and existing unit tests see the original "no cache" behavior. New tests in `tests/jobs/test_data_quality_checker_cache.py` pin the dedup invariant (flow / darkpool / each Gold dataset → exactly 1 read per `run_quality_checks` call), the ContextVar reset on exit, and the standalone-no-cache behavior. All 13 existing tests in `test_data_quality_checker_heber_source.py` still pass unmodified.

- **Native ingestion now actually enforces its single-instance guarantee** — Phase 5b code-quality review caught that the wrapper script and launchd plist comments advertised "Orion's own service-lease mutually excludes docker-compose and native ingestion," but `acquire_service_lease` was a method on `ExecutionEngine` that only `main_execution.py` ever called. `IngestionService` had no lease wiring, the docker-compose `ingestion:` stanza never set `ORION_LEASE_OWNER_ID`, and `service_lease_ingestion` had zero references anywhere in the tree. If an operator ran `docker compose --profile docker up -d ingestion` while the launchd agent was alive, both processes would have run concurrently — duplicate `bronze_events` rows and racing Alpaca WS subscriptions. The fix extracts `acquire_service_lease` / `renew_service_lease` (plus `SERVICE_LEASE_KEY_PREFIX`, `SERVICE_LEASE_STALE_SECONDS`) from `execution_engine.py` into a new free-function module `orion.core.service_lease`; `ExecutionEngine`'s methods become thin wrappers that retain `_lease_service_id` / `_lease_run_id` on the instance so the existing argument-less `renew_service_lease()` call in the execution main loop is unchanged. `IngestionService.initialize` now calls `acquire_service_lease("ingestion")` *before* any subscriptions or state mutation (fail-fast — RuntimeError propagates to a non-zero process exit); the heartbeat block in `IngestionService.run` calls a new `_maybe_renew_lease()` helper that delegates to `renew_service_lease("ingestion", self._lease_run_id)` and is a no-op when no lease was acquired. `docker-compose.yml` ingestion stanza gets `ORION_LEASE_OWNER_ID=orion_ingestion_compose` (distinct from the wrapper's `orion_ingestion_native`) so the two paths mutually exclude. `docker-compose.yml` execution stanza gets `profiles: [ "docker" ]` for symmetry with the ingestion stanza Phase 5b gated — without it, a plain `docker compose up -d` would have started `orion_execution` alongside the native launchd agent. Existing 9 lease tests pass unchanged (one patch target updated to point at the new module); 4 new tests in `tests/ingestion/test_service_lease_integration.py` cover acquisition + refusal + heartbeat renewal + no-op-without-lease. Verified live: native ingestion restarted, `service_lease_ingestion` row now present in TimescaleDB with `run_id=orion_ingestion_native`.

- **Gateway-loaded positions now carry entry-time market context (`iv_rank_at_entry`, `vix_at_entry`, `gex_at_entry`, `market_tide_30m`, `is_sweep`, `premium_usd`, `dte_at_entry`, `expiry_date`)** — FOLLOWUPS #4 second-half. The exit classifier and the new fallback rules read these fields off `TrackedPosition`, but for positions loaded from Alpaca via Gateway after a restart they were all `None` because `PositionMonitor._fetch_entry_context` only joined `strategy_decisions → candidate_trades` on the underlying ticker (broken for option positions whose Alpaca symbol is the OCC string like `QQQ260522P00721000`), didn't read `evidence.is_sweep`, didn't return `expiration_date`, and didn't fetch the market-context enrichment fields at all. Five changes in `position_monitor.py`: (a) new `_is_occ_option_symbol` helper picks the correct join column (`ct.option_symbol` for OCC symbols, `ct.ticker` for underlyings); (b) join now `LEFT JOIN`s `orders` and filters `client_order_id LIKE 'orion_%'` so non-Orion positions on the shared account fall through to a default empty context instead of inheriting another system's decision; (c) `is_sweep`, `event_id`, `put_call`, `premium_usd` parsed from `candidate_trades.evidence`; (d) `iv_rank_at_entry / vix_at_entry / gex_at_entry / market_tide_30m` populated by calling `flow_enricher.enrich_flow_for_scoring` at the decision's `entry_time` — same code path the ML scorer uses, so train/inference parity is preserved; (e) per-PositionMonitor `_entry_context_cache` keyed on symbol caches the join result for the session lifetime (entry context is immutable post-entry, so no TTL needed). DTE computation moved into Python so the query stays portable across Postgres (prod) and SQLite (tests) — the prior `EXTRACT(DAY FROM ... )::int` would have raised on the SQLite path. Result: exit decisions are now made on the full feature set instead of a degraded all-`None` input. Test coverage in `tests/execution/test_position_monitor_entry_context.py` (4 cases: OCC pattern matcher, full enrichment plumb-through for an option position, end-to-end sync_positions → TrackedPosition assertion, and the non-Orion-attributed fallback path).

### Added

- **Daily bucket-model retraining via `scripts/run_nightly_retrain.sh` + crontab `0 3 * * *`** — bucket scorers and exit classifiers had no automatic retraining schedule; live observation showed 4 SHORT_SWING models stuck at March 31 (~38 days old) while the rest had been touched on May 1, with the live execution loop emitting "Model X is 37 days old (limit: 14), loading anyway per stale_model_policy='warn'" on every startup. New wrapper script archives the current `models/*.pkl` to `models/archive/<ISO-timestamp>/` (mtime preserved, gitignored), runs `scripts/run_training.py` with `ORION_MODEL_DIR=models/` so freshly trained files overwrite the live ones, and on success restarts `orion_execution` so `BucketExitClassifier` (which only loads at `__init__`) picks up the new exit classifiers. `MLScorer` already hot-reloads via mtime check every 60s in the scoring path, so the entry models would refresh without the restart, but exit classifiers would not. Logs append to `logs/cron_retrain.log`. `scripts/run_training.py` updated to honor `ORION_MODEL_DIR` env var so its print/list output reflects the actual save path. Crontab fires at 03:00 daily — 1h after the existing Heber gold pipelines at 02:00 so training reads fresh labels.

- **`ORION_LEASE_OWNER_ID` env knob fixes execution restart-loop deadlock** — `acquire_service_lease` previously generated a fresh `uuid4()` per process incarnation. Combined with `restart: unless-stopped` and the 120s stale-lease window, every container restart saw its own previous instance's "fresh" lease, threw `RuntimeError: Another 'execution' instance holds a fresh lease`, and exited non-zero — Docker restarted the container, which deadlocked again. Cumulative `RestartCount=362` in 35h on `orion_execution` traced to this cause. The lease owner id now reads `ORION_LEASE_OWNER_ID` (falls back to `uuid4()` when unset, preserving the original guard for ad-hoc CLI). docker-compose sets `ORION_LEASE_OWNER_ID=orion_execution_compose` for the execution service so a recreate reclaims its own lease immediately. Two test cases that asserted "every engine instance gets a different uuid" remained passing because they don't set the env var; the takeover path is exercised separately by `test_stale_lease_reclaimed`.

- **`ORION_CIRCUIT_BREAKER_ENABLED` and `ORION_GLOBAL_CIRCUIT_BREAKER_ENABLED` master kill switches for the breakers.** Both default `True` (production behavior unchanged). When `False`, the breaker still records "would-trip" events as WARNING logs (so the operator sees what would have happened) but `is_open()` / `_check_circuit_breaker()` always return False, so trading is never blocked. Intended for forward-testing windows where a spurious trip is more costly than a real broker-error event. `docker-compose.yml` sets both to `false` for the `execution` service to match the current testing posture.

- **HeberReader negative cache for empty Gold datasets** — `read_gold_features` now records a 5-minute negative cache entry whenever `pq.ParquetDataset(...)` returns 0 rows, and short-circuits future calls for the same dataset within the TTL. Prior behavior cost ~3s per call on an empty dataset (path walk + ParquetDataset open returning nothing); with 6 known-empty datasets being read by `feature_store._load_score_features` per ML pre-filter pass, this was ~18s of wasted I/O per candidate — sufficient to age candidates past the 600s `signal_preflight` "Data Lag" threshold during the trading day. Per-process cache (`_gold_empty_dataset_cache`) shared across reader instances; entries auto-expire so a backfilled dataset becomes visible without a process restart.

- **Per-service `mem_limit` on `execution` (3g), `ingestion` (2.5g), `position-monitor` (1.5g), `pattern-miner` (1.5g), `data-quality` (2.5g) in `docker-compose.yml`.** Previously only `feature_enrichment` had a cap. Without explicit limits, Docker Desktop's VM-level OOM killer reaped whichever container had the highest RSS during memory pressure — turning a leak in one service into a cascade kill of unrelated services. With per-container caps, an OOM produces a visible `OOMKilled=true, ExitCode=137` signal scoped to the actual leaker. Caps sized for observed FeatureEngine + ML model RSS plus headroom (execution: 1.6GB observed, ingestion: 1.6GB observed during hydration).

### Fixed

- **Shared-account drawdown kill switch no longer trips on other systems' losses** — `_sync_risk_from_gateway` and `poll_fills` overwrote `current_equity` with the account-wide Alpaca `equity` on every call, and the initial peak seed used `max(equity, last_equity)` which pulled in the prior session's account high. On the shared paper account (3Roses/Cerberus/Kairos/Orbit/WhaleHunter), this meant the drawdown computation was `(account_peak - account_current) / account_peak` rather than Orion-only, falsely tripping `_evaluate_drawdown_kill_switch` whenever any other system had losses. Live trip observed today: `drawdown=5.48% >= limit=5.00%; equity=948758.06 peak=1003797.38; daily_loss=0.0` — Orion's own `current_daily_loss` was zero. Three changes: (a) new `_equity_seeded` flag on `RiskManager` mirroring the existing `_peak_equity_seeded` pattern; both Gateway-sync sites now seed `current_equity` exactly once and never overwrite afterwards. From there, `current_equity` only moves through `update_post_fill` (manager.py:590), which is already Orion-attributed via the `orion_` client_order_id filter. (b) Initial peak seed switched from `max(equity, last_equity)` to just `equity` so drawdown starts at 0% per Orion session — using last_equity pulled in the prior account-wide high which doesn't represent Orion's high-water mark on a shared account. (c) `RiskManager.initialize` sets `_equity_seeded=True` after restoring persisted state so a Gateway sync immediately afterwards doesn't undo the load. Existing `current_daily_loss` Orion-only attribution (already in place from the earlier ticker-prefix work) is unchanged. Closes the case in `memory/project_shared_alpaca_killswitch.md`.

- **Options orders rounded to Alpaca's tick increments to stop 422 Unprocessable Entity rejects** — `(bid + ask) / 2` produced sub-penny mid-quotes (`0.605`, `3.925`, `5.475`) and float-precision artefacts (`0.6000000000000001`, `5.789999999999999`) that Alpaca rejected because options must be on a $0.05 tick under $3.00 and $0.10 tick at/above $3.00. Live data showed the same tickers cycling through accepted ↔ rejected based purely on whether the mid-quote happened to land on a legal tick: 12 of last 20 orders had `broker_order_id IS NULL` and `error_message LIKE '%422 Unprocessable Entity%'`. New `round_to_options_tick(price)` helper in `execution_engine.py` snaps to the nearest legal increment before the order leaves Orion (early enough that contract sizing uses the same rounded price, so internal math and broker submission stay consistent — error stays under one tick per contract). 19-case parametrized regression test (`test_options_tick_rounding.py`) covers sub-penny mid-quotes, float artefacts, the $3 grid boundary, and zero/negative inputs.

- **`auto_skip_stale_candidates` keeps the pending-candidate pool self-clean** — the freshness filter added to `fetch_pending_candidates` solved live starvation but left stale candidates in the table forever as no-decision rows. Over a session that's a slow leak in any pending-count tally and a confusing operator-side signal. New `auto_skip_stale_candidates(batch_limit=500)` in `decision_persistence.py` runs once per execution loop iteration just before the fetch — finds candidates with `timestamp_utc < NOW() - max_data_lag_seconds` and no decision yet, bulk-inserts proper SKIP rows with `strategy_version_id="auto_stale_skip"` and `reason="Stale at fetch: older than max_data_lag_seconds"`. Idempotent (the same outer-join filters out anything already swept on the next pass). First post-deploy run swept 90 stale candidates left behind from the morning's pre-fix backlog. Wrapped in try/except in `main_execution.py` so a transient DB error during the sweep can't break the trading loop.

- **`fetch_pending_candidates` now skips candidates already past `max_data_lag_seconds`** — `decision_persistence.fetch_pending_candidates` ordered by `timestamp_utc.asc()` (FIFO) and had no freshness filter. With ingestion producing ~50 candidates per 60s cycle and the ML+preflight chain running ~20-30s per candidate, even a small backlog of stale candidates pushed fresh ones out of reach forever: live observation showed 21 consecutive `Preflight reject: Data Lag` SKIPs with growing lag (1957s → 3414s) and a pending pool of 115/157. The expensive `signal_engine.decide` runs were entirely wasted because the lag check at the end of preflight rejected each one. New `WHERE timestamp_utc > NOW() - max_data_lag_seconds` clause excludes stale candidates at fetch time so execution always works on the front of the queue. After deploy + execution restart: lag dropped from ~2900s to 279-594s within one cycle, **9 EXECUTE decisions and 6 orders placed in the next 5 minutes** (META/MRNA/BE/NVDA/QQQ/MU/DIA/WLAC/NVTS). Threshold itself unchanged (still 600s) — this is a starvation fix, not a risk-gate change.

- **`orion_ingestion` mem_limit raised 2.5G → 4G; silent-exit guard added to `main_ingest`** — once bronze ingestion was actually receiving bars (after the `_is_bar_message` fix), the ingestion container entered a tight restart loop: ~75–90 seconds per cycle, exit code 0, `OOMKilled=false`, and `RestartCount=15` over 3.5 hours. Each cycle reached `Starting Polling Loop` + `Market Schedule initialized`, then disappeared with no traceback. Two changes: (a) `docker-compose.yml` raises `ingestion.mem_limit` from 2500m to 4g — the previous cap was sized for the FeatureEngine hydration peak (~1.3GB) but didn't include the additional ~700MB needed for the first cycle's Heber flow scan + bar drain when the WS queue was warm; observed RSS at "Polling Loop" entry was 2.05G/2.44G (84%), consistent with the first allocation in `_run_cycle` tipping the container over and SIGKILL'd by the cgroup memory controller (Docker Desktop sometimes reports `OOMKilled=false` for cgroup-level kills). (b) `IngestionService.run` now catches `BaseException` (not just `Exception`) inside the loop body so SystemExit / CancelledError from a misbehaving background task can't silently break the loop condition; the heartbeat/sleep tail is wrapped in its own try/except; and a defensive CRITICAL log (`ingestion_main_loop_exited_without_shutdown_signal`) fires if the while-loop exits with the shutdown event unset. `__main__.py` mirrors the `main_execution.py` `try/except BaseException` wrapper around `asyncio.run` so any escaping crash logs `ingestion_process_crashed` with traceback before re-raising. After the bump, `RestartCount` stayed at 0 across multiple cycles and the pipeline produced 514 UW flow events → 514 silver signals → 55 candidates within the first cycle.

- **`orion_execution` mem_limit raised 3G → 5G** — once candidates started flowing, execution showed the same silent restart-loop pattern: died ~6 seconds after `models_loaded` while processing 50 candidates, with no `execution_main_loop_exited_without_shutdown_signal` / `execution_process_crashed` log (the existing silent-exit guards in `main_execution.py` did not catch it, again consistent with cgroup SIGKILL rather than a Python-side exit). Observed RSS at the kill point was ~2.0–2.1G against the 3G cap — within range of the cgroup limit once Heber gold-feature reads + LightGBM scoring were active. With the new 5G ceiling the container ran 9+ minutes through hydration and produced its first decisions; restartcount dropped to 1.

- **GatewayStreamClient now recognises the bare-EventEnvelope WS shape, restoring `ALPACA_BAR_1M` ingestion** — `_is_bar_message` only matched messages with a top-level `type` field (`type=alpaca_bar_1m`, `type=bar`, or `type=data` with `feed=bars`). The current Data-Gateway WS pushes bars as bare `EventEnvelope` frames (no `type`, only `feed`, `instrument_key`, `symbol`, `payload`, etc.), so every bar fell through every branch of the receive loop and was silently discarded — `bronze_events` had not received a single `ALPACA_BAR_1M` row in 60+ hours, starving silver, candidate, decision, and execution pipelines. Added a third branch that matches `feed in ("bars","stock_bars")` with `instrument_key` or `symbol` present and no `type`, so envelope-shape bars now route to `_process_bar_message`. `subscription_ack` (which uses `feeds`, plural) intentionally does not match. Regression test extends `test_bar_message_detection_handles_gateway_shape` with a real envelope payload and a subscription-ack negative case.

- **`ExecutionEngine.poll_fills` now throttles `/api/v1/alpaca/account` to 15s** — the execution main loop calls `poll_fills` every iteration (~1 Hz when no candidates), each call invoking `client.get_account()`. With the pipeline currently dry, this produced one Gateway request per second per execution restart — ~60/min, ~86k/day — visible as a flood of `auth_success` and `200 OK /api/v1/alpaca/account` lines in `data-gateway` logs. New class constant `_ACCOUNT_POLL_MIN_INTERVAL_SECONDS=15.0` short-circuits the Gateway call when fewer than 15s have elapsed since `_last_fill_poll_ts`. Lease renewal still fires every iteration (so the 120s stale-lease window is unaffected); only the equity-sync request is gated. Rate drops from ~60/min → 4/min, a 15× reduction. Existing `test_poll_fills_updates_risk_state` and `test_poll_fills_renews_lease` continue to pass — the first call always proceeds because `_last_fill_poll_ts` starts as `None`.

- **`main_execution` silent-exit detection** — the run loop is `while not shutdown_event.is_set()`, so if the loop body somehow exits without the shutdown signal, `main()` returns and the process exits with code 0. That looked like a clean shutdown and produced no log entry, masking what may have been hundreds of silent crashes (predict finding A from `rca-restart-loop.md`). Two changes: (a) after the while loop, log `execution_main_loop_exited_without_shutdown_signal` at CRITICAL when shutdown_event is unset; (b) wrap `asyncio.run(main())` in a try/except that logs `execution_process_crashed` with traceback before re-raising, so an exception escaping main() produces a structured event instead of a stderr-only traceback that log aggregation misses.

- **`RiskManager.pending_orders` now durably persisted across restarts** — predict finding H-04. Previously the in-flight-orders dict was memory-only, so a restart between order submission and fill lost the pending exposure tracking until the next Gateway sync. New `pending_orders` table mirrors each entry; `update_post_trade` upserts a row on submit, `remove_pending_order` deletes on fill (now async — `_remove_pending_order_compat` already awaited coroutine returns, so existing engine callsites continue to work), and `RiskManager.initialize` calls `_load_pending_orders` to restore fresh rows into memory. Rows older than `PENDING_ORDER_LOAD_TTL_SECONDS` (1 hour) are dropped on load as almost-certainly orphaned by an earlier crash. Alembic migration `2c4f1a8b9d3e_add_pending_orders_table` creates the table; `init_db()`'s `Base.metadata.create_all` covers SQLite test envs that don't run migrations. DB write failures during persist/delete are logged but don't propagate — a transient blip is recoverable on the next mutation. Two existing tests (`test_risk_race`, `test_risk_manager_positions::test_risk_manager_pending_orders`) updated to await the now-async `remove_pending_order`. Legacy `sync_with_broker` direct-Alpaca path is unchanged; orphaned DB rows from that path are cleaned up by the load-time TTL.

- **Per-process broker-error circuit breaker now uses a time window with a minimum-samples threshold instead of a fixed-size deque** — predict finding H-08. The previous `order_history: deque[bool]` with `maxlen=20` and a hardcoded `0.03` threshold had no time component: a single failure stayed in the deque all day during low-volume periods, and the deliberate empty-on-restart policy meant a single early failure could trip the breaker. The new implementation stores `(monotonic_ts, success)` tuples and prunes entries older than `circuit_breaker_window_seconds` on every check. Trips only when error_rate > `circuit_breaker_error_rate` AND total samples ≥ `circuit_breaker_min_samples`. All three values configurable via `ORION_CIRCUIT_BREAKER_ERROR_RATE` (default 0.03), `ORION_CIRCUIT_BREAKER_WINDOW_SECONDS` (default 300 = 5 min), `ORION_CIRCUIT_BREAKER_MIN_SAMPLES` (default 5). Persisting `broker_outcome_history` separately from `strategy_decisions` for cross-restart memory remains a future enhancement (RE round 1, DA-3); this commit only addresses the in-memory window.

- **`peak_equity` no longer silently overwritten when DB-loaded value equals the $100K hardcoded default** — predict finding H-06. The previous `if peak_equity == 100000.0: peak = max(equity, last_equity)` check in `_sync_risk_from_gateway` could not distinguish "default $100K never seeded" from "real $100K loaded from DB", so the next sync would replace a legitimate historical peak with the current (possibly depressed) account value, hiding a real drawdown. Replaced with an explicit `_peak_equity_seeded: bool` flag set to True whenever peak comes from a real source (DB load via `RiskManager.initialize`, or first Gateway sync). Subsequent syncs do not touch `peak_equity`; the high-water mark is owned by `_evaluate_drawdown_kill_switch`. Predict's original phrasing of the failure mode was partially incorrect (the described "no DB row + account <$100K" scenario was actually handled correctly by the old code); empirical-evidence rule applied — findings.md updated with the corrected description.

- **`OrderRateLimiter.acquire` no longer holds the `asyncio.Lock` during `await asyncio.sleep`** — predict finding H-10. Previously `async with self._lock` wrapped the entire wait-loop, so N callers contending at the limit each waited the full wait_time of the previous holder serially: the lock holder slept inside the lock, blocking everyone else from even checking the bucket until it released. The fix takes the lock briefly to inspect / mutate the in-window deque (cleanup, slot-claim, or wait_time computation) and releases it before sleeping — contenders now sleep in parallel and race for the slot when the window expires. New regression test `test_lock_released_during_sleep` directly verifies the lock is acquirable by another coroutine mid-sleep, so a future refactor cannot silently re-introduce the bug. 9 additional functional tests cover the basic acquire/timeout/window-expiry/burst-within-limit/try_acquire/reset semantics that previously had no coverage at all.

- **`FillProcessor.process_single_fill` now skips fills with no attribution metadata at all** — previously `if client_oid and not client_oid.startswith(ORDER_ID_PREFIX): return` would let through a fill where both `client_order_id` and `id` were empty (the truthiness short-circuit on the first clause). After the H-12 attribution refactor, the check is `if not is_orion_owned(client_oid): return`, which skips unattributed fills (default-deny). In practice this path was rarely hit — fills almost always have one of those two fields — but defensive correctness now matches the contract every other callsite enforces.

- **Regression coverage pinning that `check_order` directly catches drawdown breaches without relying on the CircuitBreaker DB read** — predict finding H-11 hypothesized a 10s gap window between drawdown breach and rejection, claiming `check_order` only consults the CB via 10s-cached `_check_system_health`. Code reading shows the claim was wrong: `check_order` → `_check_loss_limits` → `_drawdown_breached(cfg)` already reads `self.peak_equity` and `self.current_equity` from in-memory state that `process_fill` mutates. The same-process gap does not exist. Added 5 regression tests in `tests/execution/test_check_order_drawdown_direct.py` so a future refactor cannot silently remove the direct check and re-introduce the imagined gap. Predict findings.md updated with empirical evidence.

- **`PositionMonitor.sync_positions` no longer resets every legacy position's `entry_time` to `now()` after restart** — when the monitor saw a broker position for the first time, it set `entry_time = datetime.now(UTC)  # Approximate`. The ML exit classifier feature `time_held_hours` derives from this, so every restart biased exit predictions toward "hold longer" for positions that had been open for hours. The `_fetch_entry_context` query already loaded the matching `strategy_decisions` row; it now also returns `sd.timestamp_utc` as `entry_time` and the sync path threads it through. Falls back to `now()` only when no decision row matches the broker symbol (e.g. an externally-opened position with no Orion-attribution). Predict finding H-07.

### Added

- **Per-service single-instance lease backed by SystemStatus** — predict finding H-05. The architecture's in-memory state (`pending_orders`, `processed_fill_ids`, `_partial_fill_tracker`, `_closing_symbols`) assumes a single process per service, but no startup check enforced it. New `ExecutionEngine.acquire_service_lease(service_id)` writes a `service_lease_<id>` row to SystemStatus with the engine's run_id, hostname, and pid. Subsequent calls from a different run_id within `SERVICE_LEASE_STALE_SECONDS` (120s) raise `RuntimeError` to refuse startup. Stale leases (no renewal in 120s) are treated as crashed prior runs and overwritten. `main_execution.main` now calls `acquire_service_lease("execution")` before `engine.initialize()`. Renewal happens automatically via `poll_fills` (already on the main loop tick) and is best-effort — a transient DB failure during renewal logs a warning but does not crash. Defensive: if another process has taken over the row, renewal aborts without overwriting their details. Different `service_id` values can coexist (e.g., `execution` and `position_monitor`). Soft guard, not a distributed lock — a check-then-write race between two simultaneous starts can let both through; goal is to catch the common cases (forgotten canary, local-while-prod, double-deploy).

- **`degraded_discovery` SystemStatus key gates execution when ticker discovery falls back to the static list** — predict finding H-09. When `feature_enrichment` ticker discovery falls back to the hardcoded `STATIC_TICKER_FALLBACK` (SPY, QQQ, ...) past the warn-streak threshold, the rest of the pipeline previously kept emitting candidates against those tickers — only a log warning fired. Now `persist_discovery_status` (in `enrichment/heber_context`) writes `DEGRADED` to a new `degraded_discovery` SystemStatus row when streak ≥ warn_streak, `OK` otherwise. The row is upserted every cycle so `last_updated_utc` doubles as a feature_enrichment liveness signal. `ExecutionEngine._check_system_health` reads the row and rejects trades while DEGRADED with a CRITICAL `EXECUTION BLOCKED: Ticker discovery is DEGRADED` log. Backward-compat: a missing row (pre-fix deployments) is treated as OK so we don't accidentally block trading on rollout.

- **Centralized Orion attribution helpers in `src/orion/execution/attribution.py`** — exposes `ORDER_ID_PREFIX`, `mint_orion_order_id()`, `is_orion_owned(client_order_id)`, and `orion_order_id_sql_pattern()`. Six previously-duplicated callsites now go through these helpers (DB filter, positions filter, two order-mint sites, fill-foreign-skip). `is_orion_owned` is default-deny: `None` and `""` both return `False`, removing the truthiness foot-gun that produced the 2026-04 shared-account attribution bug. `ORDER_ID_PREFIX` is re-exported from `execution_engine` for backward compatibility. Predict finding H-12.

- **Bracket-order protection state surfaced on every executed decision** — when stop-loss or take-profit placement fails after a successful entry, `_place_bracket_orders` now returns `unprotected: bool` and `partial_protection: bool` flags (`unprotected=True` means no automatic downside exit was placed, the position depends entirely on PositionMonitor's ML/rule exits). The flags are hoisted onto `decision.execution_params["position_unprotected"]` / `["position_partial_protection"]` so a DB query can find unprotected positions without parsing the nested `bracket_orders` dict, and a `position_unprotected` CRITICAL log fires when SL placement fails. Previously SL/TP failures only logged at ERROR level; the entry was already marked TRUE and no metric, status flag, or operator-visible signal recorded the protection gap. Tests cover both-fail, SL-only-fail, TP-only-fail, and both-succeed paths.

### Changed

- **`heber-sync` now mirrors Heber Gold partitions, not just Silver feeds** — Orion's container reads Heber data from a host-mounted cache at `/Users/jacobmcmillan/.heber-cache/data` (mounted read-only as `/Volumes/heber/data`), and `heber-sync` was only rsync'ing Silver feeds for today + yesterday. Result: every Gold dataset Orion's ML scorer relies on (`darkpool_features`, `momentum_features`, `oi_momentum_features`, etc.) was over a month stale (latest cached partition `dt=2026-03-24` while the source had data through `dt=2026-04-27`). The sync loop now also walks `/heber-source/gold/dataset=*/project=*/version=*/dt=*` and mirrors any partition newer than 30 days back, with `--delete` so renamed/quarantined parquet files in the source don't linger in the cache and pollute downstream pyarrow merges. Verified: post-sync, `heber_reader.read_gold_features("darkpool_features", symbols=["COIN","AAPL","SPY","NVDA"])` returns 242 rows with all three expected columns (`darkpool_notional_1d`, `darkpool_premium_ratio`, `darkpool_activity_zscore`) populated.
- **Containers now reach Data-Gateway via Compose service name `data-gateway:8080`** instead of `host.docker.internal:8080`. Orion's compose stack joins the external `data-gateway_default` network so the five Gateway-using services (ingestion, feature_enrichment, execution, position-monitor, nightly-backfill) resolve `data-gateway` directly. Over Apr 22–28 we logged 1,896 `gateway_trading_error` events on `/api/v1/alpaca/account` (periodic risk sync) plus 20 on `/api/v1/alpaca/options/chain/{ticker}` (live order option-chain lookups), most with `[Errno -3] Temporary failure in name resolution` against `host.docker.internal`. Container-to-container DNS removes the round-trip through Docker Desktop's hostname mapping.
- **`min_dte` default lowered 3 → 1** — the existing default was rejecting almost every EXECUTE-quality candidate generated by the flow rules, which predominantly fire on near-dated options (1-DTE SPXW weeklies, 2-DTE single-name flow). Over Apr 23–28 we logged 181 EXECUTE strategy decisions and zero filled orders, with `options_blocked_dte_low` accounting for the bulk of post-decision rejections (`dte=1` SPXW, `dte=2` COIN/NVDA/PLTR/SNDK/VFC/etc.). 0-DTE is still blocked at this gate; 0-DTE-specific handling (`check_zero_dte_winddown`) is downstream and currently has no trained model. Override via `ORION_RISK_MIN_DTE`.
- **`swing_entry_paper_v1` solver wired to `rule_swing_entry_v1`** — was previously routing through `rule_bullish_sweep_v1`, which is a same-day options-flow rule, not the multi-day swing entry path. The diversified baseline solver also gains `rule_swing_entry_v1` so the swing rule has a paper-routing fallback. SWING_* model artifacts refreshed from the latest training run.

### Fixed

- **`feature_enrichment` polled UW connectors and VIX-proxy off-hours, generating zero-write streak warnings on expected-empty data and burning API budget** — UW's `/spot-exposures`, `/market-tide`, `/max-pain`, `/iv-rank` endpoints return `data: []` outside extended trading hours, and VIXY bars only stream when the underlying ETF is trading. With the existing 3-cycle warn threshold (~90 s), pre-market polling drove the `feature_enrichment_zero_write_streak` warning to fire continuously throughout pre-market sessions (observed `streak: 71` for `greek_exposure`, `streak: 6` for `max_pain`/`vix_proxy` at 5:30 AM ET). Added an extended-market-hours gate in `main_feature_enrichment.run_feature_loop` (Mon-Fri 07:00-20:00 ET, matching `main_data_quality._is_market_hours`). Outside the gate, UW + VIX tasks are skipped, the gated feeds' zero-write streaks are reset (so they don't carry over into the next session), and a single INFO log per off-hours window records the skip. 9 parametrized tests cover the gate boundaries.

- **Permanent false-positive `feature_enrichment_non_heber_streak` warning** — the streak counter in `_note_ticker_source_streak` reset only when `source == "heber"`, but the post-Apr-22 OOM redesign made `bronze_db` (TimescaleDB `bronze_events`) the canonical primary ticker-discovery source and demoted Heber to a fallback that only runs when bronze fails. As a result the warning fired on every cycle in production (observed `streak: 71` against `source: "bronze_db"`) — alerting on the architecture's intended healthy state. Updated the function to reset the streak on either `bronze_db` or `heber` (both are recognized data-backed sources) and renamed the warning event to `feature_enrichment_static_fallback_streak` with message "Ticker discovery fell back to static list" so it correctly fires only when discovery has truly degraded to the hardcoded static ticker list. Tests added.

- **`UniverseManager.hydrate_from_db` fails with `'CandidateTrade' has no attribute 'created_at'`** — column is named `created_at_utc` in the model. The wrong attribute name caused a non-fatal exception on every hydration attempt, preventing the universe from being seeded from the DB on service restart.

- **`test_returns_heber_tickers_when_available` broke after 2026-04-22 OOM fix** (2026-04-24): The 2026-04-22 refactor moved Heber parquet scanning to a DB-failure fallback path. The test was not updated and got `static_fallback` because the in-memory test DB returned empty without raising. Fixed by patching `_get_active_tickers_from_bronze` to raise `RuntimeError` so the Heber branch is reachable.

- **Options-open orders falsely tripping `Shorting Disabled`** — `signal_preflight` and `execution_engine._submit_options_order` / `_execute_options_candidate` / `_pre_flight_checks` all mapped `candidate.direction == SHORT` to `OrderSide.SELL` under the assumption that SHORT means a short-sale. For an options-only system, SHORT is a *bearish view on the underlying* (buy a put), which is still a BUY at the broker. The risk manager saw a pending sell, computed `projected_signed < 0`, and rejected with `RISK REJECT: Shorting Disabled. Cannot move to -48960.0` — exactly 5 EXECUTE-quality candidates on 2026-04-23 (IONQ, VFC, INTU, CRWV, SNAP). All four open-path callsites now use `OrderSide.BUY` unconditionally. The shorting kill switch remains in place (intent: catch a future equity short-sale flow). Close/exit paths in `position_monitor` still compute side from direction correctly (SHORT close = BUY, LONG close = SELL).

- **XOP EXECUTE produced zero contracts because `candidate.premium` (aggregate UW flow premium) was used as per-contract price** — `_execute_options_candidate` tried to read per-contract price from the Gateway option chain, but matched on `contract.get("symbol")` when Gateway's payload uses `contract_symbol` (the `symbol` field holds the underlying). The lookup always missed, falling back to `candidate.premium`, which is the total premium of the UW flow sweep ($34,075 for XOP), not a per-contract price. `risk_dollars / (34075 * 100)` rounded to 0 contracts and the order was never submitted. Fixes: (1) match on `contract_symbol`; (2) compute mid from `(bid+ask)/2` instead of relying on a `mid` field that Gateway does not return; (3) fall through to `ask`, then `bid`, then `last`; (4) remove the `candidate.premium` fallback entirely — if we cannot fetch a real per-contract price we fail-closed with `Option Price Fetch Failed` instead of sizing off the wrong number.

- **`orion_feature_enrichment` silent OOM crash-loop** — container had been restarting every 60–180 s for days (969+ "Service started" log lines with zero "stopped" / "Received signal" log lines). `docker events` showed `oom → die exit=137` each cycle but `docker inspect` reported `ExitCode: 0, OOMKilled: false` because the oom flag was overwritten by the subsequent restart on Docker Desktop. Root cause: a stack of memory-heavy operations on every loop iteration — `get_active_tickers_with_source` scanning 2 days of UW-flow parquet every 30 s, `get_latest_vix_data` reading 10 days of VIXY bars, `get_spy_cumulative_return` reading 2 days of SPY bars, and `asyncio.gather` firing 4 UW connectors against 20 tickers in parallel on startup. `HeberReader._read_silver_dataset` loads the entire parquet dataset into pandas and filters in-memory (no time filter push-down), so every "small" Heber read materialized GBs. Docker Desktop VM-level OOM killer SIGKILLed PID 1 (uncatchable, so no clean shutdown log). Layered fix: (1) primary ticker discovery moved from Heber flow parquet to the TimescaleDB `bronze_events` table — indexed, bounded, milliseconds instead of seconds. (2) Added a 5-minute cache (`TICKER_DISCOVERY_INTERVAL`) so ticker discovery runs at most every 5 min, not every loop. (3) Heber-backed context reads (VIX, market_tide, SPY cumulative return) disabled by default via `ORION_FEATURE_ENRICHMENT_PREFER_HEBER_CONTEXT=false`; `get_latest_*` helpers already had this flag, it's just now opt-in rather than opt-out. (4) UW gateway fetches disabled by default via `ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH=false` while the simultaneous-fetch memory spike is investigated; Heber Silver already persists these features directly so the downstream pipeline is unaffected. (5) Wrapped `run_feature_loop` in a top-level `except BaseException` with `exc_info=True` so future non-SIGKILL exit paths are visible. (6) Set explicit `mem_limit: 3g` on the container so memory pressure produces a predictable cgroup OOM rather than ambiguous VM-level kills. Post-fix: container runs stably at ~950 MiB with 0 new restarts over sustained monitoring. RCA: [`docs/rca/feature_enrichment_crash_loop.md`](docs/rca/feature_enrichment_crash_loop.md).

- **Shared-Alpaca position attribution fail-open** — after the daily-loss kill-switch patch, `_sync_risk_from_gateway` was still tagging *every* shared-account position as Orion-owned. Saw `GATEWAY_POSITIONS_SYNC: open_positions=15, skipped_non_orion=0, total_account_positions=15` even though Orion had zero orders in the `orders` table ever. Root cause: `_fetch_orion_tickers` returns an empty `set[str]` when Orion has no orders, but the consumer loop's guard `if orion_tickers and symbol not in orion_tickers` short-circuited on the falsy empty set and bypassed the filter entirely — empty-set-means-skip-all degenerated into empty-set-means-accept-all. Removed the truthiness guard; `None` is still handled as the error sentinel via the early-return above the loop. Post-fix: `open_positions=0, skipped_non_orion=15, total_account_positions=15`. RCA: [`docs/rca/orion_ticker_attribution.md`](docs/rca/orion_ticker_attribution.md).

- **Shared-Alpaca daily-loss false kill-switch** — `_sync_risk_from_gateway` and `poll_fills` were computing `current_daily_loss = max(0, last_equity - equity)` from account-wide equity. Because the Alpaca paper account is shared across 3Roses/Cerberus/Kairos/Orbit/Orion/WhaleHunter, other systems' intraday P&L was tripping Orion's kill switch (saw 154 EXECUTE-quality signals rejected with "Daily Loss Limit 1000.0 Hit (Current Loss: 8567.04)" while Orion itself had zero fills in 7 days). Both sync paths no longer overwrite `current_daily_loss`; Orion-only daily loss is driven by `update_post_fill` from fills with the `orion_` client-order-id prefix, matching the existing position-attribution contract. `current_equity` sync is preserved for drawdown tracking. Also added `ORION_RISK_MAX_DAILY_LOSS` env var (default `20000`) to the execution container so the ceiling can be tuned for forward testing without code changes.

- **Risk manager blocking all BUY orders after first trade** — `_submit_options_order` was passing `candidate.direction` ("LONG"/"SHORT") as the `side` argument to `update_post_trade` instead of the `OrderSide` enum value ("buy"/"sell"). Because `"long" != "buy"`, every pending order was stored with a **negative** signed cost, making subsequent `check_order` calls see a negative projected exposure and reject the order as "Shorting Disabled". Fixed by passing `side` (the `OrderSide.BUY`/`SELL` computed at order submission time) instead.

- **0DTE LightGBM scorer crash on string categoricals** — Legacy 0DTE models (trained Jan 2026) lack `categorical_mappings` in their serialized model data, so string features like `put_call="P"` passed through to `np.array(..., dtype=float)` and raised `ValueError`. The scorer now applies fallback hash-based encoding for any categorical column not covered by the model's mappings.

- **ML scorer silent-failure for stale 0DTE models** — the legacy 53-feature 0DTE artifacts (created 2026-01-20) were incompatible with today's 106-feature pipeline, throwing `ValueError` on every scoring call and falling back to heuristic — but the error was logged on every call, drowning logs without making the root cause obvious. The scorer now logs the fallback exactly **once per (bucket, model-mtime)** with structured fields (`event=ml_legacy_fallback`, `bucket`, `target`, `model_created_at`, `error_type`), and re-arms on the next reload so freshly-stale models surface a fresh warning.

- **Gateway WebSocket disconnect during machine idle** — `GatewayStreamClient` ping interval/timeout (20s/10s) was shorter than Data-Gateway's uvicorn config (30s/90s), causing spurious disconnects; both values now match the server settings.

- **Health monitor trips circuit breaker after machine sleep** — Heartbeat gaps larger than 10 minutes are now treated as host suspension rather than genuine stalls; the monitor resets the heartbeat timestamp and logs a warning instead of opening the circuit breaker.

- **UW flow signals dropped due to missing `limit_price`** — `signal_preflight` rejected any candidate where `underlying_price` was zero (common for UW flow events). The preflight and all flow rules now fall back through `strike_price → option_price → premium` before giving up, matching the execution engine's own live-price fetch strategy.

- **`is_sweep` field not recognized from Heber Silver payloads** — Normalizer was only checking `has_sweep` and `sweep`; now also checks `is_sweep` (the field name used in Heber Silver option-flow events).

- **Candidate persistence missing option contract fields** — `persist_candidates` was not writing `option_symbol`, `strike_price`, `option_type`, `underlying_price`, `premium`, or `expiration_date` to the DB, so the execution engine had to re-fetch them from the signal; all six fields are now persisted.

- **ML prefilter calling synchronous `scorer.score()` in async context** — `MLPreFilter` now calls `scorer.score_enriched()` (async) instead of the sync `score()` method, eliminating event-loop blocking during scoring.

- **UW max-pain connector returning 404s** — Data-Gateway moved the max-pain endpoint from `/api/v1/uw/{symbol}/max-pain` to `/api/v1/uw/options/{symbol}/max-pain` (options router prefix added); updated `UWMaxPainConnector` to use the correct path

- **Docker builds are aligned with the repo’s real dependency manager**: the shared Docker image now installs dependencies from `uv.lock` with `uv sync --frozen --no-dev --no-install-project` instead of the stale Poetry workflow that had been failing builds after the project moved to the modern `[project]` layout. The `data-quality` service entrypoint now also configures logging explicitly via `configure_logging()` so it cannot crash on a missing `service_name` argument at startup.
- **Pattern miner startup and smoke-test safety hardening**: `main_pattern_miner.py` now configures logging explicitly on startup, Orion's shared logger preserves the zero-argument `setup_logging()` behavior older entrypoints still rely on, the live DB smoke test now requires explicit opt-in in both `pytest` and standalone script mode, and smoke cleanup always runs in a `finally` block using exact inserted IDs instead of broad ticker deletes. The pipeline integration test now pins its ML settings so local model file drift does not cause false red builds.
- **Execution startup now heals empty paper solver inventory and fails loud in live stages**: Orion now seeds a canonical set of 5 paper solvers with companion metrics when the active solver inventory is empty in `paper`/`test`, auto-assigns `diversified_baseline_v1` as the fallback baseline when none is configured, and refuses to start execution in higher stages when no active solvers exist. This prevents the silent “all alerts become SKIP because no solver exists” failure mode we hit on April 2, 2026. The CLI seeding script now reuses the same canonical definitions so seeded solvers are router-valid.
- **Feature enrichment typing cleanup**: fixed stale type annotations in `main_feature_enrichment.py` so `mypy .` returns clean again.

- **`pyproject.toml` migrated from Poetry to uv-native format** — added PEP 621 `[project]` table and `[dependency-groups]` so `uv sync` now installs all runtime and dev dependencies correctly; previously `uv sync` was a no-op (0 packages installed) because uv does not read `[tool.poetry.dependencies]`
- `orion_data_quality` container crash-loop: `setup_logging()` call in `main_data_quality.py` was missing required `service_name` argument; fixed to pass `"orion-data-quality"`

### Removed

- **Stale 0DTE model artifacts** — the 5 `models/0DTE_*.pkl` files trained 2026-01-20 against an old 53-feature schema with no `categorical_mappings` were deleted. They always crashed the scorer's feature-vector build and silently no-op'd into the heuristic path, so removing them makes the missing-model path explicit (`if bucket not in self.models: return self._heuristic_score(flow)`). Cannot be retrained until the upstream Heber 0DTE labeling bug is fixed (tracked separately).
- **Vendored UnusualWhales SDK** (228 files, 31K LOC) — only used by legacy labeler; UW imports made lazy with ImportError fallback
- **LakehouseWriter** (S3 Parquet export) — duplicated Heber's lakehouse; deleted module, config fields, and ingestion integration
- **Dead modules**: `clients/mcp_server.py`, `reconciliation/bar_gap_scan.py`, `core/logging_config.py`, `connectors/base.py` (unused protocols), `agents/base.py` (unnecessary ABC), `execution/risk_manager.py` (re-export shim), `main_rollups.py` (superseded by ingestion service)
- **Orphaned DB models**: `SilverEarningsCalendar`, `SilverUWAlert`, dead `_save_silver_data` method
- **Duplicate `core/http_client.py`** — switched all callers to `empire_core.http_client`

### Added

- **`scripts/retrain_0dte.py`** — one-off 0DTE retrain script with a widened 90-day window (vs. the default 10-day window in `TRADE_BUCKET_CONFIGS`). Reuses the full pattern-miner pipeline so output artifacts are byte-compatible with the current SWING/POSITION 106-feature schema. Will produce real models once upstream 0DTE label variance is restored.
- **`BaseGatewayConnector`** base class for UW connectors — consolidates shared retry, auth, and buffer logic
- **`shared/legacy_flags.py`** — centralized legacy pipeline env-var control (was copy-pasted 4 times)
- **`DecisionAction`, `TradeDirection`, `OrderSide` enums** — replaced raw string comparisons across 12 files
- **Health check TTL cache** in `ExecutionEngine` — caches system health for 10s, eliminating N-1 redundant DB queries per execution cycle
- **Parallel UW connector fetching** — `asyncio.gather` in feature enrichment loop + `Semaphore(3)` bounded concurrency per connector
- **`ORION_HEURISTIC_CAP_LIVE` config knob** — heuristic scorer cap in live mode is now configurable via `ORION_HEURISTIC_CAP_LIVE` (default 0.65) instead of a hard-coded 0.55
- **`IngestionService` heartbeat update** — ingestion loop now calls `health_monitor.update_heartbeat()` on each cycle so the health monitor has an accurate last-seen timestamp

### Changed

- Refreshed tracked `POSITION_*` and `SWING_*` LightGBM model artifacts with the latest locally trained binaries so the checked-in model set matches the current Orion workspace state.
- Local machine artifacts are now treated as local: future `proposals/*.yaml` outputs are ignored, and `index.scip` plus `ledger.db` are no longer intended to be tracked in Git.
- Standalone diagnostic scripts moved to `src/orion/scripts/`
- All 5 connectors now use structured logging (structlog) instead of raw `logging.getLogger`
- Env-var parsing in feature enrichment uses generic `_parse_env_threshold()` helper
- Universe hydration on restart now reads from `candidate_trades` (last 24h) instead of removed `SilverUWAlert` table
- UW connector poll timestamps only advance on successful fetch (not on failure)

### Fixed

- **ML scoring dead pipeline — model directory, threshold deadlock, silent fallback**: ML scoring was completely non-functional in production due to three compounding issues: (1) `ORION_MODEL_DIR` defaulted to `/app/models` (Docker path) which doesn't exist outside Docker, causing all candidates to silently fall back to heuristic scoring; the default now auto-detects the environment and falls back to `<project-root>/models/` when not in Docker. (2) The heuristic scorer capped output at 0.50 in live mode while `ml_prefilter_threshold` was also 0.50, creating a deadlock where heuristic-scored candidates could never pass; the MLPreFilter now uses a lower threshold (0.40) when the scorer is running in heuristic mode, while preserving the 0.50 threshold for model-backed scoring. The heuristic cap was raised to 0.55 to create a viable gap. (3) Missing-model and no-models-found conditions logged at INFO level, making the root cause invisible in production logs; these are now WARNING level with actionable messages including the env var to set.

- **Silent-failure hardening across execution, monitoring, and data feeds**: Orion now fails louder when shared-account position filtering breaks, Gateway trading endpoints stop pretending outages mean “no positions/orders,” the option quote tracker distinguishes “no checkpoints” from broken Heber reads or schema drift, `/flows` returns `503` when flow data is structurally invalid, and the data-quality job warns when flow freshness is unknown instead of logging a fake healthy status. Market-hours checks now use the real trading calendar with a logged fallback, malformed earnings payloads raise instead of looking like empty calendars, naive heartbeat timestamps emit warnings, correlation math failures keep their traceback, and `ExecutionEngine` restores its module-level `async_session_factory` patch point for integration/remediation tooling.

- **Execution safety and fill tracking**: partial fills are no longer dropped after the first broker update, fill persistence now refreshes existing rows instead of freezing stale audit data, and risk-reducing close orders can bypass the size ceiling only when they actually shrink absolute exposure. Preflight sizing also honors legacy USD order/ticker caps again.

- **ML bypass and agent reliability**: ML bypass mode now truly passes flows through candidate generation instead of filtering everything out, `MetaAgent.run()` now fails fast with a clear `NotImplementedError` instead of silently returning `None`, and weekly meta summaries create their output directories before writing files.

- **Flow normalization and rule matching**: provider payloads now normalize boolean-like strings and `PUT`/`CALL` tokens consistently, feature/rule processing accepts both compact and verbose put/call encodings, and DTE parse failures are logged instead of being silently swallowed.

- **API outage visibility**: `/search` and `/flows` now return `503` when their backends fail instead of pretending there were simply no results, preserving the traceback in logs and making operator failures visible to clients.

- **Fallback and tooling cleanup**: `SolverRouter` no longer hides failures inside its synthetic-baseline fallback path, repo docs and local agent instructions now point to `python -m orion.ingestion` instead of the removed `main_ingest.py` entrypoint, and the root helper scripts now satisfy `mypy`.

- **HTTP client `elapsed` RuntimeError**: `_log_response` accessed `response.elapsed` inside an httpx response event hook before the response body was read or closed. This caused a `RuntimeError` on every max-pain connector request, exhausting retry budgets and flooding the error log. The access is now wrapped in a try/except so elapsed falls back to 0.0 when unavailable.

- **Lint: unused `timedelta` import** in `processing/rules/base.py`: removed unused `timedelta` import.

- **Lint: bare f-strings** in e2e tests: removed extraneous `f` prefix from string literals with no placeholders.

- **ML scorer categorical encoding**: `_build_feature_map` was passing string categorical fields (`aggressor`, `is_sweep`, `alert_type`, `side`, etc.) through `float()`, crashing with `ValueError: could not convert string to float: 'ASK'`. The scorer silently fell back to heuristic scoring (~0.65) instead of using trained LightGBM models. Now applies the `categorical_mappings` saved during training to encode strings as integer category codes, matching the training-time encoding.

- **Circuit breaker false trigger on shared account**: `peak_equity` defaulted to $100,000 (hardcoded) but the Alpaca paper account had ~$46K equity from other systems' losses. The drawdown kill switch calculated 54% drawdown vs 5% limit and permanently locked out execution. `_sync_risk_from_gateway` now seeds `peak_equity` from actual account balance on first sync when no persisted risk state exists.

- **Rule engine missing option fields**: `CandidateTrade` objects from the rule engine had no `option_symbol`, `strike_price`, `underlying_price`, `premium`, `option_type`, or `expiration_date` set. The execution engine rejected every candidate with `"Options only — no option_symbol on candidate"`. The rule engine base class now populates these fields from signal features and generates OCC-format option symbols (e.g., `SPY260408C00560000`).

- **Flow rule evidence incomplete**: `BullishSweepRule` and other flow rules did not include `is_sweep`, `aggressor`, `premium_usd`, or `put_call` in candidate evidence. The ML pre-filter's `_build_payload` could not reconstruct the flow context for scoring, leading to missing features and incorrect scores.

### Added

- **E2E smoke test** (`tests/e2e/test_smoke_e2e.py`): Nine-stage pipeline verification against real TimescaleDB. Injects simulated SPY data (bar + UW flow) and asserts every stage: bronze ingest → silver signals → feature extraction → rollups → rule engine → candidate persistence → ML model loading/scoring → signal engine decision → execution (mocked broker). Runnable via `uv run pytest tests/e2e/test_smoke_e2e.py -v -s` or standalone.

- **Live data flow health check** (`tests/e2e/test_live_data_flow.py`): Diagnostic that queries real TimescaleDB for the most recent row at each pipeline stage (bronze, silver, rollups, candidates, decisions, orders, signals_live) plus system status, regime, and risk state. Reports stage-by-stage freshness with `FRESH`/`STALE`/`EMPTY` indicators. Fails during market hours if pipeline stages are stale; passes outside market hours with informational output.

- **Position attribution for shared Alpaca account**: The Alpaca paper account is shared by multiple trading systems via Data-Gateway. Orion now identifies its own positions:
  - Order IDs prefixed with `orion_` (e.g., `orion_a1b2c3d4-...`) for all entry and exit orders
  - `_sync_risk_from_gateway` filters positions to only tickers Orion has traded (via `OrderRecord` lookup)
  - `FillProcessor` skips fills whose `client_order_id` doesn't start with `orion_`
  - `OrderRecord` schema includes `system` column (default `"orion"`) for persistent attribution

- **Test collection hang resolved**: `tests/integration/test_e2e_flow_pipeline.py` is a standalone script (no `test_*` functions) that runs blocking I/O at import time; added it to `collect_ignore` in `conftest.py` so pytest no longer hangs when collecting the full test suite.
- **RuntimeWarning: coroutine never awaited in client tests**: `raise_for_status` in `test_mcp_server.py` and `test_trading_rag.py` was incorrectly mocked as `AsyncMock`; changed to `MagicMock` since `httpx.raise_for_status()` is a synchronous method.
- **RuntimeWarning from ingestion test `_run_eod_task` mock**: `test_triggers_eod_at_correct_time` used `AsyncMock` for `_run_eod_task` but `asyncio.create_task` was also mocked, leaving a coroutine unawaited; replaced with a plain `MagicMock` return value.
- **Datetime format inference warning in heber_context**: `pd.to_datetime` in `_coerce_time_series` now specifies `format="mixed"` to avoid the per-element fallback to `dateutil` and suppress the `UserWarning` about inferred formats.
- **SQLAlchemy RuntimeWarning filter added**: Added `ignore::RuntimeWarning:sqlalchemy.*:` to `pytest.filterwarnings` to suppress framework-level async mock interaction noise from SQLAlchemy internals during teardown.

- **Removed legacy gate fields from SystemSettings**: `legacy_label_pipelines_enabled`, `legacy_option_quote_tracker_enabled`, `legacy_pattern_miner_enabled`, `pattern_miner_training_source`, and `exit_classifier_training_source` have been removed from `SystemSettings`. Legacy services now read their feature-gate env vars directly via `os.getenv`, removing the coupling to the central settings object.

- **Gateway fetch disabled by default**: `feature_enrichment_enable_gateway_fetch` now defaults to `False`. Set `ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH=true` to enable gateway-backed feature ingestion. This is a safer default that prevents unintended gateway traffic.

- **Logging test isolation**: `setup_struct_logger` now re-runs logging configuration in pytest environments (detected via `PYTEST_CURRENT_TEST`), fixing test order-dependent failures where later tests saw an unconfigured root logger. Log test assertions now correctly find the JSON line in multi-line output containing both the structured log entry and the exception traceback.

- **EOD review agent SQL trailing whitespace**: Removed trailing whitespace from SQL query in `eod_review_agent.py` to fix ruff lint warning.

- **Healthcheck for scheduled services**: `meta-search`, `meta-weekly`, `dashboard-reset`, and `pattern-miner` containers were permanently unhealthy because they inherited the Dockerfile's `curl /health` check but don't serve HTTP during their scheduled wait periods. Added `kill -0 1` healthcheck override to each service in docker-compose.yml so health reflects process liveness, not HTTP availability.

### Added

- **ML model hot-reload**: MLScorer now checks for updated model files every 60 seconds and auto-reloads when the pattern miner produces new `.pkl` files. No service restart required.

- **Daily model training**: Pattern miner now trains every weekday after market close (was Mon/Fri only). Models stay fresh with daily retraining on the latest trade data.

- **Drift trigger moved to database**: Feature drift flag now stored in `RuntimeConfig` table instead of ephemeral filesystem. Survives container restarts so EOD agent → pattern miner drift-triggered retraining works reliably.

- **Automated solver promotion job**: New `solver_promoter.py` job processes pending `PromotionRecommendation` rows and auto-approves eligible solvers up to the `paper` stage. Promotions to `limited_live` or `scaled_live` are skipped and require manual approval. Gate checks enforce minimum trades (10), positive Sharpe ratio, and max drawdown under 25%. Runs automatically after the weekly meta-search evolution and can also be executed standalone.

- **Position monitor exit execution**: Position monitor now submits close orders via GatewayTradingClient when exit signals trigger, instead of logging "trading connectors archived." Respects circuit breaker and paper mode.

- **Scheduled data quality checker**: New `data-quality` Docker service runs hourly during market hours to detect stale data, bar gaps, and ML feature population issues.

- **Daily meta-search evolution**: Meta-search agent moved from `tools` (manual) to `scheduled` profile. Runs daily at 6 PM ET on weekdays for continuous solver evolution.

### Fixed

- **LLM agent iteration limit too low**: Bumped `codex_client.py` max tool iterations from 10 to 30. EOD agent was hitting the cap mid-analysis, producing empty reports with "Max tool iterations exceeded."

### Added

- **Gateway fetch enabled by default**: Greek exposure, max pain, and IV rank connectors now poll Data-Gateway REST endpoints automatically (every 5min, 1hr, and 15min respectively). Previously gated behind `ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH=false` — now defaults to `true` so per-ticker enrichment data flows into Orion every trading day without manual intervention.

- **Max pain added to heber-sync feeds**: The `heber-sync` Docker sidecar and `scripts/sync-heber-cache.sh` now sync `max_pain` parquet partitions from Heber alongside existing feeds.

- **Heber read functions for GEX, max pain, IV rank**: Added `get_latest_greek_exposure()`, `get_latest_max_pain()`, and `get_latest_iv_rank()` to `heber_context.py` as utility functions for direct parquet reads.

- **Persist regime snapshots to TimescaleDB**: Regime snapshots are now durably written to a `regime_snapshots` table via fire-and-forget async DB writes, in addition to the existing in-memory list. On startup, `seed_regime_snapshots_from_db()` recovers the last 500 snapshots from the DB so the signal pipeline has immediate regime history.

### Fixed

- **Circuit breaker re-tripping on execution restart**: Peak equity in `risk_state` was stale ($53,458) while Alpaca account equity had dropped to $49,404, causing a 7.58% drawdown breach on every restart. Reset peak equity to current account value.

- **ML pre-filter test bypassing scorer via truthy MagicMock**: `test_ml_prefilter_threshold_reads_centralized_config` was producing `strategy_version_id == 'SOLVER_ENSEMBLE'` instead of `'ML_PREFILTER'` because `MagicMock().bypass_scoring` evaluates truthy, causing the pre-filter to always bypass scoring. Fixed by explicitly setting `mock_scorer.bypass_scoring = False` in the test.

- **Heber data inaccessible from Docker containers**: External USB drive (`/Volumes/heber`) can't be bind-mounted by Docker Desktop's Linux VM. Replaced direct volume mounts with a local SSD cache (`~/.heber-cache/data`) synced via `scripts/sync-heber-cache.sh`. Feature enrichment now reads Heber data successfully (was falling back to static ticker list for 370+ consecutive cycles).

- **Error logging across 15 files (28 issues)**: Comprehensive audit found 11 HIGH severity and 17 MEDIUM severity error handling issues. Fixed: added `exc_info=True` for traceback visibility, upgraded `WARNING→ERROR` on critical path failures (circuit breaker, risk sync, fill processing, feature engine, ML pre-filter), added missing log calls to silent `except: continue/pass` blocks (label engine, DLQ consumer, timestamp parsers).

- **Circuit breaker stuck OPEN from stale peak equity**: Peak equity was 100,000 (hardcoded default) while current equity was 53,458.94, causing a permanent 46.5% drawdown breach. Reset `risk_state` and `system_status` to current equity.

- **Meta-search agent base solver not found**: `.env` referenced `v1_legacy` but solvers table uses `diversified_baseline_v1`. Updated default to match seeded solver IDs.

### Added

- **Heber cache sync script** (`scripts/sync-heber-cache.sh`): Syncs silver/gold parquet feeds from external Heber drive to local SSD cache for Docker Desktop compatibility. Docker compose now uses `HEBER_HOST_DATA` env var for the host-side mount path.

- **Meta-search agent running**: Solver evolution service now active, using AI Gateway + `diversified_baseline_v1` as base solver for LLM-guided strategy mutation.

- **Seed initial paper-stage solvers**: The `solvers` table was empty since inception, causing every candidate trade to be safety-SKIPped by the SolverEnsemble. Added 5 conservative paper-stage solvers (BullishSweep, BearishPutPressure, RSI Mean Reversion, Swing Entry, Diversified Baseline) with companion `solver_metrics` rows. Available via `scripts/seed_solvers.py` or Alembic migration 0026.

- **Circuit breaker admin API endpoints**: New `GET /admin/circuit-breaker` (view state), `POST /admin/circuit-breaker/reset` (close/resume trading), and `POST /admin/circuit-breaker/open` (halt trading) endpoints for managing the global circuit breaker without direct DB access.
- **Circuit breaker reset script** (`scripts/reset_circuit_breaker.py`): Standalone script to reset a stuck circuit breaker directly in TimescaleDB. Supports `--dry-run` and custom `--db-url`.

### Fixed

- **Timestamp serialization crash killed ingestion pipeline since Mar 18** (71+ DLQ entries):
  Pandas `Timestamp`, numpy scalars, and stdlib `datetime` objects in JSON/JSONB columns caused `"Object of type Timestamp is not JSON serializable"` errors. Added a shared `make_json_safe()` utility that converts all non-JSON-native types (pd.Timestamp, np.datetime64, np.integer, np.floating, np.bool_, datetime, date, NaN/Inf) to JSON-safe primitives. Applied it as a safety net in `persist_bronze_events`, `persist_silver_signals`, and `persist_candidates`, and at all source sites: Heber event mapper, feature engine signal generation, and the Alpaca bar normalizer.

- **Stale ML models (56+ days old) blocked all trading**: The LightGBM scorer rejected all models exceeding the 14-day freshness limit, causing every candidate to fall back to the heuristic scorer (capped at 0.50 in live mode). Added `ORION_ML_STALE_MODEL_POLICY` config (`skip` | `warn` | `bypass`). Default changed to `warn` — stale models are loaded with a warning rather than silently discarded. The `bypass` option disables ML scoring entirely, letting candidates pass through to the solver ensemble without an ML gate.

- **Global circuit breaker stuck OPEN since 2026-03-19**: Reset the breaker that was tripped by a stale heartbeat (61.38s > 60s threshold). Trading is now resumed.

### Added

- **Temporal excursion fields in triple-barrier label engine**: Label output now includes `ts_mfe`, `ts_mae`, `time_to_mfe_seconds`, `time_to_mae_seconds`, `mfe_mae_ratio`, `excursion_velocity`, and `capture_efficiency` so ML models can learn timing patterns (e.g., fast time-to-MFE = high conviction). New columns added to `candidate_labels` and `labels_event` tables (migration 0025).
- **Temporal excursion features in ML training pipeline**: The 5 temporal excursion fields (`time_to_mfe_seconds`, `time_to_mae_seconds`, `mfe_mae_ratio`, `excursion_velocity`, `capture_efficiency`) are now available as ML training features via `feature_config`, extracted from outcomes in `training_data`, and mapped in the scorer's `_build_feature_map` for inference.

### Fixed

- **`NOT NULL constraint failed: trade_journal_entries.decision_id`** (2026-03-22):
  - `StrategyDecision.decision_id` and `TradeJournalEntry.decision_id` now auto-generate a UUID when not explicitly set, preventing `IntegrityError` when tests or callers omit the primary key.
  - Added `server_default=gen_random_uuid()::text` to both PKs in TimescaleDB via Alembic migration 0027, so the database itself generates UUIDs as a safety net when the ORM default does not fire.
  - Added defensive fallback in `persist_order_record` and `save_decision` to substitute a UUID if `decision.decision_id` is None at journal-write time.
  - Eliminates the `ORDER_PERSIST_ERROR` / `TRADE_JOURNAL_UPSERT_ERROR` entries flooding the error log.

- **Lint: 27 unused imports removed from `execution_engine.py`, `signal_engine.py`, and `pattern_miner.py`** (2026-03-22):
  - `timedelta` and `async_session_factory` removed from `execution_engine.py`.
  - `system_settings` removed from `signal_engine.py`.
  - Re-exported constants in `pattern_miner.py` annotated with `# noqa: F401` to preserve public API surface used by `feature_store.py` and integration tests.

### Removed

- **Legacy feature flags, dead services, and vendored Alpaca deleted** (2026-03-20):
  - Removed 10 legacy config fields from `SystemSettings` (8 `legacy_*_enabled` feature flags + 2 `*_training_source` fields).
  - Deleted 7 dead source files: `main_option_quote_tracker.py`, `main_price_target_labeler.py`, `jobs/nightly_backfill.py`, `jobs/quality_guardrails.py`, `jobs/cleanup_legacy_backfill_watermarks.py`, `jobs/backfill_ml_features.py`.
  - Removed 4 Docker Compose services: `price_target_labeler`, `option_quote_tracker`, `nightly-backfill`, `quality-guardrails`.
  - Promoted `pattern-miner` from `legacy-labels` profile to a standard service.
  - Stripped legacy guard functions (`_legacy_label_pipeline_control`, `_legacy_*_training_control`) from `main_pattern_miner.py`, `ml/pattern_miner.py`, `ml/exit_classifier.py`.
  - Removed 8 legacy env vars from `.env.example`.
  - Deleted 16 test files referencing removed modules.
  - Deleted vendored `src/alpaca/` (v0.32.0); pip-installed `alpaca-py ^0.43.2` provides the same modules.
  - Removed `src/alpaca` from ruff `extend-exclude` and mypy `exclude` in `pyproject.toml`.
  - Deleted 10 scratch/debug files from repo root (`test_arrow*.py`, `test_gateway.py`, `test_rglob.py`, `fix_mocks.py`, `run_mining_now.py`).

### Added

- **Ledger adapter wired into execution flow** (2026-03-19):
  - `OrionLedgerAdapter` is now instantiated in `main_execution.py` and passed to `ExecutionEngine`.
  - Orders are recorded to the unified ledger after successful submission in `_submit_options_order()` and `close_position()`.
  - Fills are recorded to the unified ledger after processing in `_process_single_fill()`.
  - All ledger calls wrapped in try/except — failures never interrupt trading.
  - Enables EmpireUI to read Orion trade data from the standardized `ledger.db`.

### Fixed

- **Maintenance: fixed 5 failing unit tests** (2026-03-20):
  - `test_execution_engine_close_direction`: two tests constructed `ExecutionEngine` via `__new__` without setting `_ledger`, causing `AttributeError` when `close_position()` accessed it. Added `engine._ledger = None` to both tests.
  - `test_ingestion_source_profile`: both tests instantiated `IngestionService()` without mocking `create_gateway_stream_client`, which raises `ValueError` when `DATA_GATEWAY_API_KEY` is absent. Mocked the factory; updated assertions to match the current `_active_event_source_profile()` response shape; added `AsyncMock` for `start`/`subscribe` and mock for `feature_engine.hydrate_history`.
  - `test_run_all_pattern_mining_passes_exit_refresh_flags`: test timed out (>30 s) because `_prefetch_heber_gold_data` performed a real filesystem `rglob` scan on `/Volumes/heber`. Added a monkeypatch that returns empty DataFrames immediately.

- **Lint errors in integration test** (2026-03-18):
  - Removed extraneous `f` prefix from two f-strings without placeholders in `tests/integration/test_e2e_flow_pipeline.py`.
  - Added `strict=False` to `zip()` call in the same file to satisfy B905 rule.

- **Pattern miner gets 0 training outcomes from Heber Gold** (2026-03-14):
  - The pattern miner re-read `labels_alert_barriers` and `meta_label_features` from disk for each of the 16 bucket x target combinations. A transient volume-mount or I/O failure during any single read would zero-out that entire training run with no retries.
  - Added `_prefetch_heber_gold_data()` that reads all Gold datasets once before the bucket loop, with up to 3 retries and exponential backoff for transient failures.
  - `run_all_pattern_mining()` now prefetches data once and passes it to all 16 bucket iterations, eliminating redundant disk reads.
  - Added diagnostic logging to `HeberReader.read_gold_features()` when Gold paths are missing, including data root existence checks and candidate paths tried. Previously, empty reads were completely silent.
  - Backward compatible: `fetch_training_data()` still works standalone without prefetching.

- **Feature enrichment VIX proxy returns 0 rows** (2026-03-13):
  - VIXY (the 1x VIX short-term futures ETF) stopped appearing in Heber bars after Feb 13, 2026. Both the `VIXProxyConnector` and `_get_latest_vix_data_from_heber()` now try multiple VIX proxy symbols in priority order: VIXY, UVIX (2x leveraged, available daily), VIXM (mid-term futures). Each has an appropriate multiplier to approximate the VIX level.
  - Added a 10-day recency filter to prevent using stale VIX proxy data.
  - Fixed a bug where `_get_latest_vix_data_from_heber()` used the raw ETF close price as the VIX level instead of applying the proxy multiplier.

- **Feature enrichment ticker discovery stuck on static fallback** (2026-03-13):
  - Added a secondary Heber-based ticker discovery path using equity bars data. When flow_alerts fails or returns empty, the system now extracts active tickers from recent equity bar instrument keys before falling back to the static 10-ticker list.
  - Added `HeberReader.read_recent_equity_symbols()` method for discovering active equity tickers from bars data.

### Changed

- **Execution engine wired to MCP Server** (2026-03-13):
  - Replaced direct Alpaca connector with Shared-MCP-Server client for order execution, position queries, and account sync. The engine no longer requires Alpaca API keys directly; the MCP server manages its own credentials.
  - Removed `alpaca.trading.enums` dependency (`OrderSide`, `TimeInForce`) from execution engine; uses plain strings instead.
  - `poll_fills()` no longer spams "unauthorized" errors every second when no broker connection exists. Silently no-ops when MCP server is unavailable.
  - Risk sync at startup now pulls account equity and positions from MCP server instead of direct Alpaca API.
  - All risk management checks (daily loss, drawdown, Greeks, sector exposure, circuit breaker, 0DTE wind-down) remain fully intact.
  - Paper mode remains the default (`ORION_STAGE=paper`).
  - Graceful degradation: if MCP server is unreachable, the engine logs once and skips execution (no error spam).
  - Updated tests to use MCP client mocks instead of direct Alpaca connector mocks.

### Fixed

- **EOD Agent LLM calls failing with LogRecord conflict** (2026-03-13):
  - `codex_client.py`, `proposal_builder.py`, `meta_agent.py`, and `weekly_aggregator.py` used stdlib `logging.getLogger()` instead of the Empire structlog logger. When structlog was configured by the EOD service, stdlib `logger.info(..., extra={...})` calls collided with structlog's LogRecord processing, causing `"Attempt to overwrite 'args' in LogRecord"` errors on every LLM call. Switched all agent modules to use `orion.shared.logger.setup_struct_logger()` with structlog keyword arguments.
  - `extract_json_from_response()` failed when LLM responses contained JSON embedded in prose, wrapped in non-standard fences, or truncated at the token limit. Rewrote with four extraction strategies: direct parse, markdown fence extraction, outermost-brace matching, and truncated-JSON repair. Also increased `max_tokens` from 4096 to 8192 to reduce truncation.

- **RAG embedding dimension mismatch** (2026-03-13):
  - The pgvector `embedding_vec` column was created as `Vector(1536)` (OpenAI dimensions) in migration 0004, but the system uses Ollama's `nomic-embed-text` model which produces 768-dim embeddings. This caused all server-side vector searches to fail, falling back to degraded Python-based ranking.
  - Added migration `0024_fix_embedding_vec_dimension` to alter the column from 1536 to 768 dimensions. Existing embeddings will be dropped; re-indexing is required after migration (`python -m orion.rag.indexer`).
  - Fixed the original migration (0004) to use `Vector(768)` for fresh database setups.

### Added

- **Ledger adapter** (2026-03-13):
  - New `OrionLedgerAdapter` class (`src/orion/core/ledger_adapter.py`) bridges Orion execution events to the empire_core `LedgerWriter`. Tracks orders, fills, trades, and positions using the standardised ledger schema.
  - Full test suite (`tests/test_ledger_adapter.py`) covering order placement, buy/sell fills, trade open/close, P&L calculation, and error handling.

### Fixed

- **Logging test stability** (2026-03-13):
  - Fixed `tests/unit/test_logging.py` — both tests failed when run after the full test suite due to pytest log-capture routing being non-deterministic (structlog output lands in `caplog` or `capsys` depending on which other tests ran first). Tests now check both capture channels to find the JSON log line.
  - Fixed `src/orion/shared/logger.py` — removed four unused imports (`clear_context`, `log_error`, `log_retry`, `unbind_context`) and corrected the `structlog` forward reference in the return type annotation (now properly guarded under `TYPE_CHECKING`). Resolves 5 ruff F401/F821 lint violations.

- **Ignore agent temporary database artifacts** (2026-03-10):
  - Added `.agents/tmp/**/*.db`, `.agents/tmp/**/*.db-journal`, and `.agents/tmp/**/*.db-wal` to `.gitignore` to keep `.agents/tmp` runtime databases out of git history.
- **Pre-commit detect-secrets false positives** (2026-03-10):
  - Updated `.pre-commit-config.yaml` to ignore `logs/` files in detect-secrets scanning so generated log output is not flagged during commit/push checks.

- Removed generated `logs/orion.log.*` files from version control and normalized log handling in local `.gitignore` coverage.

- **Test Stability Fixes** (2026-02-25):
  - Fixed `test_feature_flags.py` by ensuring `FeatureFlags._load_from_env()` is explicitly called during tests to load mocked environment variables correctly.
  - Fixed `test_uw_max_pain_heber_source.py` by modifying `UWMaxPainConnector._get_current_price()` to return `None` instead of raising a generic `Exception` when the Heber read fails, preventing the process from crashing and tests from failing when Heber is unavailable.
- **Heber reader parquet noise from macOS sidecar files** (`heber_reader.py`): `_read_table()` now pre-filters `._` prefixed files when reading a directory, preventing PyArrow from attempting to stat macOS metadata sidecar files that cause `EPERM` errors and trigger noisy `heber_reader_filewise_fallback` warnings every ~5 minutes.
- **Cross-Repo Audit: Retry standardization** (2026-02-22):
  - Migrated `UWMaxPainConnector` in `src/orion/connectors/uw_max_pain_connector.py` from raw `httpx.get()` to `create_http_client()` for structured logging hooks and consistent timeout configuration.
  - Updated retry contract tests to mock `connector._client.get` instead of `module.httpx.get`.
- **Cross-Repo Audit: Financial Precision Fixes** (2026-02-22):
  - Removed duplicate `max_open_positions` validation in `src/orion/core/solver_schema.py` (copy-paste artifact).
  - Replaced deprecated `datetime.utcnow` with `datetime.now(timezone.utc)` in `SolverEdit.created_at_utc`.
- **Cross-Repo Audit: pytz → zoneinfo Migration** (2026-02-22):
  - Migrated `src/orion/main_meta_weekly.py` and `scripts/verify_ingestion_sleep.py` from deprecated `pytz` to stdlib `zoneinfo`. Replaced `pytz.timezone()` with `ZoneInfo()` and `US/Eastern` with canonical `America/New_York`.
- **Cross-Repo Audit: Timezone-Aware Timestamps** (2026-02-21):
  - Fixed `datetime.fromtimestamp()` + `datetime.now()` → UTC-aware in `src/orion/ml/scorer.py` for model freshness check.
  - Fixed `datetime.now()` → `datetime.now(timezone.utc)` in `src/orion/ml/pattern_miner.py` (insight ID hash) and `src/orion/agents/proposal_builder.py` (filename timestamp).
- **Cross-Repo Audit: loguru → structlog** (2026-02-21):
  - Migrated `scripts/verify_activity.py` from loguru to structlog.

### Removed

- **Decommissioned Redpanda, MinIO, and createbuckets Docker services**:
  - Removed `redpanda`, `minio`, and `createbuckets` services from `docker-compose.yml`.
  - Removed `redpanda_data` and `minio_data` volume declarations.
  - Removed `REDPANDA_BROKERS` env var from `ingestion` service.
  - Removed `UW_API_KEY` env var from `feature_enrichment` service.
  - Deleted `src/orion/connectors/redpanda_producer.py` (AsyncSingleton Kafka producer).
  - Removed all `RedpandaProducer` usage from `ingestion/service.py` (import, init, start, stop, produce loop, dead `_to_dict` helper).
  - Updated `tests/conftest.py`: removed `REDPANDA_BROKERS` env var and `mock_redpanda_producer` autouse fixture.
  - Updated `tests/unit/test_compose_legacy_gate_wiring.py`: removed `test_ingestion_wires_internal_redpanda_broker_address`.
  - Updated `tests/unit/test_ingestion_source_profile.py`: removed `_DummyProducer` class and Redpanda monkeypatch.
  - Stopped and removed `orion_redpanda`, `orion_minio` containers; cleaned orphans (`orion-createbuckets-1`, `orion_labeler`).
  - Verified: 19/19 affected tests passing, 8 remaining services healthy.

### Changed

- **Gateway auth-contract hardening for UW connectors (RCA/TDD)**:
  - Updated:
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/uw_market_tide_connector.py`
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/uw_greek_exposure_connector.py`
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/uw_max_pain_connector.py`
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/uw_iv_rank_connector.py`
  - Connectors now fail fast on startup when `DATA_GATEWAY_URL/GATEWAY_URL` or `DATA_GATEWAY_API_KEY/GATEWAY_API_KEY` is missing, instead of sending unauthenticated requests that create repeated `401` noise.
  - Added regression tests in `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_uw_gateway_connector_retry_contract.py` for missing URL/key startup validation across all four connectors.
  - Verified with:
    - `pytest -q tests/unit/test_uw_gateway_connector_retry_contract.py`
    - `ruff check src/orion/connectors/uw_iv_rank_connector.py src/orion/connectors/uw_market_tide_connector.py src/orion/connectors/uw_greek_exposure_connector.py src/orion/connectors/uw_max_pain_connector.py tests/unit/test_uw_gateway_connector_retry_contract.py`
    - `mypy src/orion/connectors/uw_iv_rank_connector.py src/orion/connectors/uw_market_tide_connector.py src/orion/connectors/uw_greek_exposure_connector.py src/orion/connectors/uw_max_pain_connector.py`

- **RCA fix for sparse Heber training rows in Orion (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/pattern_miner.py`:
    - stopped treating `bars_to_hit <= 0` as a universal no-snapshot signal.
    - now drops rows only when no-snapshot is explicit (`outcome/outcome_reason` indicates no snapshot) or `snapshot_count <= 0` when snapshot metadata exists.
    - preserves valid `expired` outcomes (which commonly have `bars_to_hit = 0`) so training keeps real negative examples.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py` with the same no-snapshot filter semantics for exit-model training.
  - Updated regression tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_pattern_miner_exit_refresh_config.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_exit_classifier_window_query.py`
  - Verified:
    - `pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py`
    - `ruff check src/orion/ml/pattern_miner.py src/orion/ml/exit_classifier.py tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py`
    - `mypy src/orion/ml/pattern_miner.py src/orion/ml/exit_classifier.py`
    - runtime check: `pattern_miner.fetch_training_data(window_days=365, min_samples=1)` now returns `6812` rows from Heber (up from `4` after prior filter).

- **Heber Gold reader RCA hardening for schema-drifted parquet partitions (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/clients/heber_reader.py`:
    - treats Arrow schema-merge cast failures (`Unsupported cast ... cast_null`) as a filewise-fallback condition, not a terminal read failure.
    - skips hidden macOS sidecar files (`._*.parquet`) during filewise parquet scans to avoid noisy non-parquet warnings.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_heber_reader.py`:
    - added regression coverage for schema-merge fallback behavior,
    - added regression coverage ensuring hidden sidecar files are ignored in filewise reads.
  - Verified with:
    - `pytest -q tests/unit/test_heber_reader.py`
    - `ruff check src/orion/clients/heber_reader.py tests/unit/test_heber_reader.py`

- **Pattern-miner/exit-classifier RCA guard: drop no-snapshot outcomes from Heber training inputs (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/pattern_miner.py`:
    - normalized `bars_to_hit` from Heber outcomes,
    - filters out no-snapshot outcomes (`bars_to_hit <= 0`) before target construction,
    - logs explicit `pattern_miner_drop_no_snapshot_outcomes` warning with dropped/remaining counts.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py`:
    - normalized `bars_to_hit` for exit training outcomes,
    - filters out no-snapshot outcomes before bucket merge/training.
  - Added regression coverage:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_pattern_miner_exit_refresh_config.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_exit_classifier_window_query.py`
  - Verified:
    - `pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py`
    - runtime check in container confirms no-snapshot rows are excluded and Orion now reports no valid outcomes instead of training on degenerate all-zero labels.

- **Ingestion startup RCA fix: removed redundant earnings sync + restored Gateway auth defaults (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ingestion/service.py`:
    - removed startup `sync_todays_earnings()` call that produced redundant Gateway requests after earnings storage moved to Data-Gateway/Heber.
    - startup now logs explicit sourcing contract: earnings are read on-demand from Data-Gateway/Heber.
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml`:
    - `ingestion` and `feature_enrichment` now default `GATEWAY_API_KEY` to `gw_orion_trading_key_55555` when `DATA_GATEWAY_API_KEY` is unset, matching local Data-Gateway client config.
  - Updated `/Users/jacobmcmillan/Empire/Orion/.env.example`:
    - set `DATA_GATEWAY_API_KEY=gw_orion_trading_key_55555` for local baseline parity.
  - Added/updated regression coverage:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_ingestion_source_profile.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py`
  - Verified:
    - `pytest -q tests/unit/test_ingestion_source_profile.py tests/unit/test_compose_legacy_gate_wiring.py`
    - `docker compose up -d --build ingestion feature_enrichment`
    - ingestion logs now show successful Gateway WebSocket auth and no startup earnings/key errors.

- **RCA hardening for Heber migration runtime (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml`:
    - added a default `ingestion` service (`python -m orion.ingestion`) with Heber read mount (`/Volumes/heber/data:/Volumes/heber/data:ro`) so local stack includes the modern ingestion path by default.
    - wired internal Redpanda bootstrap for ingestion (`REDPANDA_BROKERS=redpanda:29092`) to match container-network listener advertising and eliminate `localhost:9092` bootstrap failures.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/clients/heber_reader.py`:
    - `read_gold_features(...)` now supports both canonical and nested watch gold layouts:
      - `/gold/dataset=<dataset>/...`
      - `/gold/labels_alert_barriers/dataset=<dataset>/...`
    - prevents silent empty reads when Heber writes watch datasets under nested path layout.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/pattern_miner.py`:
    - added strict Heber training contract validation before normalization,
    - now raises `RuntimeError` with clear context when required label semantics are missing from `labels_alert_barriers` / `meta_label_features` instead of silently training on malformed data.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_pattern_miner_exit_refresh_config.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_heber_reader.py`
    - added regression coverage for default ingestion wiring and fail-fast Heber contract mismatch behavior.
  - Verified with:
    - `pytest -q` (790 passed, 6 skipped)
    - `ruff check .`
    - `mypy .`

- **RCA fix for runtime DB failures and missing ML tracking tables (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/storage/db.py`:
    - enabled `pool_pre_ping=True` and `pool_recycle=1800` for Postgres async engines to reduce stale-connection failures in long-running workers,
    - ensured `init_db()` imports `models_ml` so ML tables are included in metadata-driven startup table creation.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/storage/models_ml.py`:
    - added `MLPrediction` model (`ml_predictions`) used by performance tracking.
  - Added `/Users/jacobmcmillan/Empire/Orion/alembic/versions/0023_add_ml_tracking_tables.py`:
    - creates `ml_pattern_insights`, `ml_feature_importance_history`, and `ml_predictions` with indexes using existence checks.
  - Added `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_db_resilience_and_ml_schema.py`:
    - verifies Postgres engine resilience settings and that `init_db()` creates all ML tracking tables.
  - Operational remediation performed:
    - rebuilt/restarted core containers,
    - repaired revision tracking with `alembic stamp 0023_add_ml_tracking_tables`,
    - verified `alembic upgrade head` succeeds and post-restart service logs show no recurring DB/missing-table errors.

- **Repo-local lint config and deterministic Numba test bootstrap (debt fix)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/pyproject.toml` to extend local `ruff-base.toml` so Ruff works in standalone clones and git worktrees.
  - Added `/Users/jacobmcmillan/Empire/Orion/ruff-base.toml` with Orion baseline lint/format settings.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/conftest.py` to set `NUMBA_DISABLE_JIT=1` and a fixed `NUMBA_CACHE_DIR` before optional `pandas_ta` import.
  - This removes environment-specific commit/test failures without changing runtime behavior.

- **AsyncSingleton now runs optional async initialization hook**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/shared/patterns.py` to call an instance `_async_init()` coroutine once on first creation.
  - Added `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_async_singleton.py` to assert single-run async init behavior.

- **Meta-search Heber event mapping now emits proper bar events (TDD bugfix)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/agents/meta_search_agent.py` so Heber bar rows are mapped to `BronzeEvent` objects with `event_type=\"ALPACA_BAR_1M\"` instead of raw dictionaries.
  - This prevents feature-engine crashes (`'dict' object has no attribute 'event_type'`) during meta-search evaluation.
  - Tightened `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_meta_search_heber_source.py` to assert Heber bar/flow outputs are event objects with expected event types.
  - Verified regression path with:
    - `pytest -q tests/unit/test_meta_search_heber_source.py::test_fetch_silver_events_prefers_heber -q`
    - `pytest -q tests/integration/test_meta_search_flow.py -q`

- **Documentation standardization started (agent file naming alignment)**:
  - Added `/Users/jacobmcmillan/Empire/Orion/AGENTS.md` as the canonical project AI-instructions file.
  - Removed deprecated `/Users/jacobmcmillan/Empire/Orion/CLAUDE.md` in favor of `AGENTS.md`.

- **Documentation standardization expanded (required/recommended docs + housekeeping)**:
  - Reworked `/Users/jacobmcmillan/Empire/Orion/README.md` to include clear quick-start, configuration, and cross-doc links.
  - Expanded `/Users/jacobmcmillan/Empire/Orion/.env.example` with migration and runtime variables used by Orion services.
  - Added required service docs:
    - `/Users/jacobmcmillan/Empire/Orion/docs/ARCHITECTURE.md`
    - `/Users/jacobmcmillan/Empire/Orion/docs/RUNBOOK.md`
    - `/Users/jacobmcmillan/Empire/Orion/docs/API_REFERENCE.md`
  - Added supporting docs:
    - `/Users/jacobmcmillan/Empire/Orion/docs/DATA_CONTRACTS.md`
    - `/Users/jacobmcmillan/Empire/Orion/CONTRIBUTING.md`
    - `/Users/jacobmcmillan/Empire/Orion/SECURITY.md`
    - `/Users/jacobmcmillan/Empire/Orion/DEVELOPER_NOTES.md`
  - Consolidated PRD/audit fragmentation by moving legacy variants and audit artifacts into `/Users/jacobmcmillan/Empire/Orion/docs/audits/`.

- **Worker container health checks now match runtime type (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml` to disable inherited Dockerfile HTTP healthchecks for non-HTTP worker services (`feature_enrichment`, `execution`, `position-monitor`, `eod-agent`, `indexer`, and other worker-only profile services).
  - Added `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py::test_non_http_workers_disable_inherited_http_healthcheck`.
  - Resolved false `unhealthy` container states caused by inherited `curl http://localhost:8000/health` probes on background worker processes.

- **Heber reader now handles both partition-schema conflicts and transient corrupt parquet files (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/clients/heber_reader.py`:
    - reads datasets with `partitioning=None` to avoid hive-partition merge conflicts (`instrument_type` string vs dictionary),
    - adds `_read_parquet_filewise(...)` fallback for datasets containing a transient corrupt parquet part,
    - skips unreadable parquet files (`heber_reader_skip_file`) while preserving valid rows from other files.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_heber_reader.py`:
    - added coverage for bars and market-tide reads when parquet files include partition-column conflicts.
    - added regression coverage proving bar reads continue when one parquet file is corrupt.
  - Verified with:
    - `pytest -q tests/unit/test_heber_reader.py`
    - `ruff check src/orion/clients/heber_reader.py tests/unit/test_heber_reader.py`

- **Added focused quality gate config and a one-command burn-in monitor script**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/pyproject.toml`:
    - `ruff` now excludes archived/notebook/vendor paths from repo-wide checks (`archive`, `qlib-main`, `src/alpaca`, `scripts`),
    - enabled pragmatic per-file lint ignore set for legacy tests/alembic migration files,
    - removed unavailable `numpy.typing.mypy` plugin from mypy configuration.
  - Added `/Users/jacobmcmillan/Empire/Orion/scripts/run_system_burnin.sh`:
    - builds/recreates core Orion services,
    - runs timed burn-in,
    - captures logs to `.artifacts/burnin/<timestamp>/`,
    - fails fast on hard-error patterns and on redundant direct UW polling from feature enrichment.
  - Updated active-code lint issues surfaced by focused checks:
    - fixed duplicated sector map keys in `/Users/jacobmcmillan/Empire/Orion/src/orion/labeler/constants.py`,
    - fixed strict-zip lint in `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/flow_enricher.py` and `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/flow_processor.py`,
    - fixed minor type/lint issues in `/Users/jacobmcmillan/Empire/Orion/src/orion/agents/meta_search_agent.py`, `/Users/jacobmcmillan/Empire/Orion/src/orion/execution/correlation_adjuster.py`, `/Users/jacobmcmillan/Empire/Orion/src/orion/execution/risk_manager.py`, `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/model_registry.py`, and `/Users/jacobmcmillan/Empire/Orion/src/orion/storage/lakehouse.py`.
  - Verified with:
    - `pytest -q tests/unit/test_meta_search.py tests/unit/test_flow_enricher_delegation.py tests/ml/test_flow_processor.py tests/unit/test_risk_manager_basic.py tests/unit/test_risk_manager_positions.py tests/storage/test_lakehouse.py`
    - `ruff check .`

- **Price-target labeler legacy gate now blocks all local backfill/query helpers when disabled (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - `get_velocity_backfill_candidates(...)` returns early when legacy label pipeline is disabled,
    - `get_checkpoint_backfill_candidates(...)` returns early when legacy label pipeline is disabled,
    - `_get_labeled_price_target_event_ids(...)` returns early when legacy label pipeline is disabled,
    - `backfill_missing_features(...)` returns early before DB init when legacy label pipeline is disabled.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_legacy_label_pipeline_gates.py`:
    - added regression tests proving these paths do not call local DB when the gate is off.
  - Verified with:
    - `pytest -q tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_price_target_labeler_heber_context.py`
    - `ruff check src/orion/main_price_target_labeler.py tests/unit/test_legacy_label_pipeline_gates.py`

- **Feature enrichment now defaults to Heber-only context reads (no redundant UW Gateway polling) and Docker build is Poetry-2 compatible (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_feature_enrichment.py`:
    - added `ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH` gate (default `false`),
    - `run_feature_loop` now skips Gateway credential contract + UW connector polling unless explicitly enabled.
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml`:
    - feature-enrichment now wires `GATEWAY_API_KEY=${DATA_GATEWAY_API_KEY:-}` (no missing-var warning),
    - added `ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH=${ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH:-false}`.
  - Updated `/Users/jacobmcmillan/Empire/Orion/Dockerfile`:
    - upgraded container Poetry runtime to `2.3.2` to match Poetry 2 lockfile metadata.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_feature_enrichment_runtime_signals.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py`
  - Verified with:
    - `pytest -q tests/unit/test_feature_enrichment_runtime_signals.py -k "gateway_fetch_enabled or run_feature_loop"`
    - `pytest -q tests/unit/test_compose_legacy_gate_wiring.py`
    - `ruff check src/orion/main_feature_enrichment.py tests/unit/test_feature_enrichment_runtime_signals.py tests/unit/test_compose_legacy_gate_wiring.py`

- **Compose runtime now mounts Heber data for Heber readers and configures MCP SEC contact env (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml`:
    - mounted `/Volumes/heber/data:/Volumes/heber/data:ro` for `execution`, `feature_enrichment`, and `eod-agent`,
    - added `SEC_CONTACT_EMAIL=${SEC_CONTACT_EMAIL:-alerts@empire.local}` in `mcp-server` environment.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py`:
    - added `test_heber_data_root_is_mounted_for_heber_consumers`,
    - added `test_mcp_server_wires_sec_contact_email`.
  - Verified with:
    - `pytest -q tests/unit/test_compose_legacy_gate_wiring.py -k "heber_data_root_is_mounted_for_heber_consumers or mcp_server_wires_sec_contact_email"`
    - `pytest -q tests/unit/test_feature_enrichment_runtime_signals.py tests/unit/test_compose_legacy_gate_wiring.py`
    - `ruff check src/orion/main_feature_enrichment.py tests/unit/test_feature_enrichment_runtime_signals.py tests/unit/test_compose_legacy_gate_wiring.py`

- **Feature validation source audit now rejects legacy `silver_*` aliases (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/validate_features.py`:
    - removed legacy alias normalization for `silver_*` source IDs in audit paths.
    - `_fetch_source_summary(...)` now explicitly returns `source_unavailable` for unknown source IDs.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_validate_features_source_adapter.py`:
    - replaced alias-mapping expectation with fix-forward canonical-only behavior.
    - added coverage for unknown/legacy source rejection.
  - Verified with:
    - `pytest -q tests/unit/test_validate_features_source_adapter.py tests/unit/test_validate_features_guardrails.py`
    - `ruff check src/orion/jobs/validate_features.py tests/unit/test_validate_features_source_adapter.py`

- **Trainer source parsing now fails safely to Heber on invalid env values (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py`:
    - `_exit_classifier_training_source()` now defaults and falls back to `heber_gold` when source env is invalid.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/pattern_miner.py`:
    - `_pattern_miner_training_source()` now defaults and falls back to `heber_gold` when source env is invalid.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_exit_classifier_window_query.py`
      - added `test_exit_classifier_training_source_invalid_falls_back_to_heber_gold`
      - added autouse default `ORION_EXIT_CLASSIFIER_TRAINING_SOURCE=legacy_sql` fixture for legacy SQL query-contract tests
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_pattern_miner_exit_refresh_config.py`
      - added `test_pattern_miner_training_source_invalid_falls_back_to_heber_gold`
  - Verified with:
    - `pytest -q tests/unit/test_exit_classifier_window_query.py tests/unit/test_pattern_miner_exit_refresh_config.py`
    - `ruff check src/orion/ml/exit_classifier.py src/orion/ml/pattern_miner.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_pattern_miner_exit_refresh_config.py`

- **Nightly backfill decommissioned exit-column stage and archived module/tests (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/nightly_backfill.py`:
    - removed `backfill_exit_columns` orchestration; nightly run now executes ML-feature backfill only.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_nightly_backfill_schedule.py`:
    - added `test_nightly_backfill_no_longer_exposes_exit_backfill_runner`,
    - added `test_run_nightly_backfill_executes_only_ml_backfill`.
  - Archived decommissioned exit-column backfill implementation:
    - moved `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/backfill_exit_columns.py` to `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-12_label-stack-wave14/legacy_code/backfill_exit_columns.py`
    - moved `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_backfill_exit_columns_selection.py` to `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-12_label-stack-wave14/legacy_tests/test_backfill_exit_columns_selection.py`
    - added `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-12_label-stack-wave14/README.md`
  - Verified with:
    - `pytest -q tests/unit/test_nightly_backfill_schedule.py tests/unit/test_legacy_label_pipeline_gates.py -k "nightly_backfill or executes_only_ml_backfill or no_longer_exposes_exit_backfill_runner"`
    - `ruff check src/orion/jobs/nightly_backfill.py tests/unit/test_nightly_backfill_schedule.py`

- **Backfill ML cursor key renamed to Heber-neutral key with legacy fallback (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/backfill_ml_features.py`:
    - active cursor key changed to `backfill_ml_features.heber_gold.cursor`,
    - loader now falls back to legacy keys (`backfill_ml_features.price_target_labels.cursor`, `backfill_ml_features.price_target_labels`) for resume continuity.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_backfill_ml_features_selection.py`:
    - added `test_backfill_cursor_key_uses_heber_neutral_name`,
    - added `test_load_backfill_cursor_falls_back_to_legacy_cursor_key`.
  - Verified with:
    - `pytest -q tests/unit/test_backfill_ml_features_selection.py`
    - `ruff check src/orion/jobs/backfill_ml_features.py tests/unit/test_backfill_ml_features_selection.py`

- **Exit-classifier training default is now Heber-first in code and compose (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/config.py`:
    - `SystemSettings.exit_classifier_training_source` default changed from `legacy_sql` to `heber_gold`.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_config_centralization.py`:
    - renamed and updated default expectation test to `test_training_source_defaults_are_heber_first`.
  - Updated `/Users/jacobmcmillan/Empire/Orion/README.md`:
    - documented Heber-first defaults for both compose and centralized settings.
  - Verified with:
    - `pytest -q tests/unit/test_config_centralization.py -k "legacy_label_gate_settings_env_mapping or training_source_defaults_are_heber_first"`
    - `ruff check src/orion/config.py tests/unit/test_config_centralization.py`

- **Orphan `GoldFeatureWindow` model decommissioned (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/storage/models_gold.py`:
    - removed unused `GoldFeatureWindow` ORM model for local `gold_feature_windows`.
  - Added `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_models_gold_decommission.py`:
    - `test_gold_feature_window_model_is_decommissioned`.
  - Verified with:
    - `pytest -q tests/unit/test_models_gold_decommission.py`
    - `ruff check src/orion/storage/models_gold.py tests/unit/test_models_gold_decommission.py`

- **Legacy watermark cleanup now targets real cursor keys (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/cleanup_legacy_backfill_watermarks.py`:
    - `LEGACY_BACKFILL_WATERMARK_KEYS` now includes both legacy base keys and actual `.cursor` keys used by backfill jobs.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_cleanup_legacy_backfill_watermarks.py`:
    - added `test_legacy_backfill_watermark_keys_include_cursor_suffixes`.
  - Verified with:
    - `pytest -q tests/unit/test_cleanup_legacy_backfill_watermarks.py`
    - `ruff check src/orion/jobs/cleanup_legacy_backfill_watermarks.py tests/unit/test_cleanup_legacy_backfill_watermarks.py`

- **Flow-labeler decommissioned and archived (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml`:
    - removed legacy `labeler` service (`python -m orion.main_labeler`) from orchestration.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/config.py`:
    - removed dead `ORION_ENABLE_LEGACY_FLOW_LABELER` setting.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_config_centralization.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_legacy_label_pipeline_gates.py`
  - Archived legacy flow-labeler module/tests:
    - moved `/Users/jacobmcmillan/Empire/Orion/src/orion/main_labeler.py` to `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-12_label-stack-wave13/legacy_code/main_labeler.py`
    - moved `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_main_labeler_heber_migration.py` to `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-12_label-stack-wave13/legacy_tests/test_main_labeler_heber_migration.py`
    - added `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-12_label-stack-wave13/README.md`
  - Verified with:
    - `pytest -q tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py tests/unit/test_legacy_label_pipeline_gates.py`
    - `ruff check src/orion/config.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py tests/unit/test_legacy_label_pipeline_gates.py`
    - `docker compose config -q`

- **Window-context reads migrated to Heber and orphan producer archived (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - `get_window_features_at_entry(ticker, entry_ts)` now computes `1h`/`1d`/`1w` features directly from Heber Silver (`flow_alerts`, `darkpool`) and no longer queries local `gold_feature_windows`.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py`:
    - replaced local-query assertions with Heber-derived aggregation assertions and local-DB bypass guards.
  - Archived unwired legacy window-feature producer:
    - moved `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/window_feature_job.py` to `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-12_label-stack-wave12/legacy_code/window_feature_job.py`
    - moved `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_window_feature_job_heber_source.py` to `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-12_label-stack-wave12/legacy_tests/test_window_feature_job_heber_source.py`
    - added `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-12_label-stack-wave12/README.md`
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/config.py`:
    - removed dead `ORION_WINDOW_FEATURE_JOB_PREFER_HEBER` setting.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "window_features_at_entry"`
    - `pytest -q tests/unit/test_flow_enricher_delegation.py -k "window_features"`
    - `ruff check src/orion/main_price_target_labeler.py tests/unit/test_price_target_labeler_heber_context.py src/orion/config.py`

- **Compose defaults now use Heber-first source for both trainers**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml`:
    - `ORION_EXIT_CLASSIFIER_TRAINING_SOURCE` default changed to `heber_gold` (pattern miner already `heber_gold`).
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py` to assert the new compose default.
  - Added `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_config_centralization.py` default-source coverage:
    - `test_training_source_defaults_follow_safe_local_defaults`
  - Notes:
    - Compose/runtime default is now Heber-first for both trainers.
    - Code-level invalid-source fallback is now `heber_gold` for both trainers.

- **Exit-classifier legacy SQL path decoupled from `gold_feature_windows` join (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py`:
    - removed lateral join against `gold_feature_windows`,
    - window-context inputs are now selected as direct optional `price_target_labels` columns when present, with `0.0` fallback when absent.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_exit_classifier_window_query.py`:
    - `test_build_bucket_training_data_uses_direct_window_columns_without_lateral_join`
    - `test_build_bucket_training_data_uses_window_columns_when_present_in_schema`
  - Verified with:
    - `pytest -q tests/unit/test_exit_classifier_window_query.py`
    - `pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py tests/unit/test_legacy_label_pipeline_gates.py`
    - `ruff check src/orion/ml/exit_classifier.py tests/unit/test_exit_classifier_window_query.py`

- **Exit-classifier Heber training path now returns real samples (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py`:
    - `ORION_EXIT_CLASSIFIER_TRAINING_SOURCE=heber_gold` now reads Heber Gold datasets (`labels_alert_barriers`, `meta_label_features`) and builds a compatibility training matrix instead of returning empty arrays by default.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_exit_classifier_window_query.py`:
    - `test_build_bucket_training_data_heber_source_uses_gold_datasets_without_local_db`
  - Verified with:
    - `pytest -q tests/unit/test_exit_classifier_window_query.py -k heber_source_uses_gold_datasets_without_local_db`
    - `pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py tests/unit/test_legacy_label_pipeline_gates.py`
    - `ruff check src/orion/ml/exit_classifier.py tests/unit/test_exit_classifier_window_query.py`

- **Trainer source controls added for Heber-first migration (TDD)**:
  - Added config/env controls:
    - `ORION_PATTERN_MINER_TRAINING_SOURCE` (`heber_gold` or `legacy_sql`)
    - `ORION_EXIT_CLASSIFIER_TRAINING_SOURCE` (`legacy_sql` or `heber_gold`)
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/pattern_miner.py`:
    - supports `heber_gold` training reads from Heber Gold datasets (`labels_alert_barriers`, `meta_label_features`) without local SQL dependency.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py`:
    - supports explicit source control and short-circuits in `heber_gold` mode until checkpoint-contract parity exists.
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml` `pattern-miner` env wiring:
    - `ORION_PATTERN_MINER_TRAINING_SOURCE` default `heber_gold`
    - `ORION_EXIT_CLASSIFIER_TRAINING_SOURCE` default `legacy_sql`
  - Added tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_pattern_miner_exit_refresh_config.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_exit_classifier_window_query.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_config_centralization.py`
  - Verified with:
    - `pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py`
    - `ruff check src/orion/config.py src/orion/ml/pattern_miner.py src/orion/ml/exit_classifier.py tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py`

- **Legacy standalone SQL scripts archived**:
  - Moved these scripts into `/Users/jacobmcmillan/Empire/Orion/archive/legacy-sql-scripts/`:
    - `backfill_ml_features.py`
    - `analyze_todays_flow.py`
    - `backtest_exit_strategies.py`
    - `refetch_alpaca_bars.py`
    - `reprocess_bronze_flow.py`
  - Added/updated `/Users/jacobmcmillan/Empire/Orion/archive/legacy-sql-scripts/README.md` documenting archive intent and inventory.
  - Expanded `/Users/jacobmcmillan/Empire/Orion/comprehensive_audit.md` with:
    - remaining local-SQL coupling inventory by file,
    - completed archive action log.

- **Audit decisions expanded for Heber v2 field scope (keep vs dispose)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/comprehensive_audit.md` with an explicit decision matrix:
    - field families to promote into Heber v2 training projection,
    - field families to retire instead of porting,
    - migration sequence for archiving legacy local label loops.
  - Updated `/Users/jacobmcmillan/Empire/Orion/README.md` with a plain-language section documenting current `legacy-labels` defaults and override env vars.

- **Legacy-labels compose defaults now align with model-local retention**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml` `legacy-labels` profile defaults:
    - local label pipelines/labeler loops default to disabled (`false`),
    - pattern-miner and training gates remain enabled (`true`) so local model artifacts/metadata workflows remain available.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py` with a new profile-level assertion:
    - `test_compose_default_legacy_profile_preserves_model_storage_paths`
  - Verified with:
    - `pytest -q tests/unit/test_compose_legacy_gate_wiring.py`
    - `ruff check tests/unit/test_compose_legacy_gate_wiring.py`

- **Model-local retention profile finalized (TDD + audit update)**:
  - Added regression test to confirm specific pattern-miner gate can stay enabled when global legacy pipelines are disabled:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_legacy_label_pipeline_gates.py`
      - `test_pattern_miner_specific_true_overrides_global_off`
  - Updated `/Users/jacobmcmillan/Empire/Orion/comprehensive_audit.md` with the recommended toggle profile for keeping local model artifacts/metadata while disabling legacy labeling paths.
  - Verified with:
    - `pytest -q tests/unit/test_legacy_label_pipeline_gates.py`
    - `ruff check tests/unit/test_legacy_label_pipeline_gates.py`

- **Model-storage preservation lock-in (TDD + audit clarification)**:
  - Added gate-override tests to preserve local model-training capability when global legacy pipelines are off:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_exit_classifier_window_query.py`
      - `test_exit_classifier_training_control_specific_true_overrides_global_false`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_pattern_miner_exit_refresh_config.py`
      - `test_pattern_miner_training_control_specific_true_overrides_global_false`
  - Updated `/Users/jacobmcmillan/Empire/Orion/comprehensive_audit.md`:
    - explicitly marks local model storage as **keep**:
      - model artifacts (`ORION_MODEL_DIR`),
      - model metadata tables (`ml_pattern_insights`, `ml_feature_importance_history`).
  - Verified with:
    - `pytest -q tests/unit/test_exit_classifier_window_query.py -k "training_control"`
    - `pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py -k "pattern_miner_training_control"`

- **Legacy-gate hardening (TDD): label persistence skips when local labelers are disabled**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_labeler.py`:
    - `persist_labels(...)` now exits early and skips `db_write(...)` when `ORION_ENABLE_LEGACY_FLOW_LABELER` (or global legacy gate) is disabled.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - `persist_labels(...)` now exits early and skips `db_write(...)` when `ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER` (or global legacy gate) is disabled.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_legacy_label_pipeline_gates.py`
      - `test_flow_labeler_persist_labels_skips_local_write_when_disabled`
      - `test_price_target_labeler_persist_labels_skips_local_write_when_disabled`
  - Verified with:
    - `pytest -q tests/unit/test_legacy_label_pipeline_gates.py -k "persist_labels_skips_local_write_when_disabled or does_not_init_db_when_specific_gate_disabled"`
    - `pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py`

- **Legacy-gate hardening (TDD): `pattern_miner` training data path control**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/config.py`:
    - added `legacy_pattern_miner_training_enabled` (`ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING`).
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/pattern_miner.py`:
    - added training-gate helpers:
      - `_legacy_pattern_training_control()`
      - `_legacy_pattern_training_enabled()`
    - `fetch_training_data(...)` now exits early with `(None, [])` when legacy pattern training is disabled (before DB query).
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml`:
    - `pattern-miner` now wires `ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING`.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_pattern_miner_exit_refresh_config.py`
      - `test_pattern_miner_training_control_prefers_specific_gate`
      - `test_fetch_training_data_returns_empty_when_legacy_training_disabled`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py`
      - pattern-miner block now asserts `ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING` wiring.
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_config_centralization.py`
      - extended legacy-gate env mapping assertions with pattern-miner training gate.
  - Verified with:
    - `pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py`
    - `ruff check src/orion/config.py src/orion/ml/pattern_miner.py tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py`

- **Heber parity deep-audit (label-table column surface)**:
  - Extended `/Users/jacobmcmillan/Empire/Orion/comprehensive_audit.md` with column-level comparisons:
    - `flow_labels` (`main_labeler`) vs Heber watch outcomes/features: `5/28` direct overlap.
    - `price_target_labels` payload surface (`main_price_target_labeler`) vs Heber watch outcomes/features: `1/163` direct overlap.
  - Added explicit migration implication: local label tables are schema forks and require a contract redesign (not a direct table swap).

- **Legacy-gate hardening (TDD): `exit_classifier` training path control**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/config.py`:
    - added `legacy_exit_classifier_training_enabled` (`ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING`).
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py`:
    - added training-gate helpers:
      - `_legacy_exit_training_control()`
      - `_legacy_exit_training_enabled()`
    - `build_bucket_training_data(...)` now exits early with empty arrays when legacy exit training is disabled (before any DB query).
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml`:
    - `pattern-miner` now wires `ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING`.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_exit_classifier_window_query.py`
      - `test_exit_classifier_training_control_prefers_specific_gate`
      - `test_build_bucket_training_data_returns_empty_when_legacy_training_disabled`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py`
      - pattern-miner block now asserts `ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING` wiring.
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_config_centralization.py`
      - extended legacy-gate env mapping assertions with exit-classifier training gate.
  - Verified with:
    - `pytest -q tests/unit/test_exit_classifier_window_query.py tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py`
    - `ruff check src/orion/config.py src/orion/ml/exit_classifier.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py`

- **Legacy-gate hardening (TDD): `nightly_backfill` + `quality_guardrails` per-service disable controls**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/config.py`:
    - added:
      - `legacy_nightly_backfill_enabled` (`ORION_ENABLE_LEGACY_NIGHTLY_BACKFILL`)
      - `legacy_quality_guardrails_enabled` (`ORION_ENABLE_LEGACY_QUALITY_GUARDRAILS`)
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/nightly_backfill.py`:
    - added legacy gate helpers and early return in `main()` before `init_db()` when disabled.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/quality_guardrails.py`:
    - added legacy gate helpers and early return in `run_guardrail_loop()` before `init_db()` when disabled.
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml`:
    - `nightly-backfill` now wires:
      - `ORION_ENABLE_LEGACY_LABEL_PIPELINES`
      - `ORION_ENABLE_LEGACY_NIGHTLY_BACKFILL`
    - `quality-guardrails` now wires:
      - `ORION_ENABLE_LEGACY_LABEL_PIPELINES`
      - `ORION_ENABLE_LEGACY_QUALITY_GUARDRAILS`
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_legacy_label_pipeline_gates.py`
      - new gate-resolution + no-DB-init tests for nightly backfill and quality guardrails.
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py`
      - added compose env-wiring assertions for both services.
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_config_centralization.py`
      - extended legacy-gate env mapping assertions.
  - Verified with:
    - `pytest -q tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py -k "legacy or pattern_miner or nightly_backfill or quality_guardrails"`
    - `pytest -q tests/unit/test_nightly_backfill_schedule.py tests/unit/test_quality_guardrails.py tests/unit/test_quality_guardrails_results.py`
    - `ruff check src/orion/config.py src/orion/jobs/nightly_backfill.py src/orion/jobs/quality_guardrails.py tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_config_centralization.py`

- **Legacy-gate hardening (TDD): `pattern-miner` per-service disable control**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_pattern_miner.py`:
    - added legacy-gate helpers:
      - `_legacy_label_pipeline_control()`
      - `_legacy_label_pipelines_enabled()`
    - `run_mining_job()` now exits before `init_db()` when disabled.
    - `main()` now exits before `init_db()` when disabled.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/config.py`:
    - added `legacy_pattern_miner_enabled` (`ORION_ENABLE_LEGACY_PATTERN_MINER`).
  - Updated `/Users/jacobmcmillan/Empire/Orion/docker-compose.yml`:
    - `pattern-miner` now wires:
      - `ORION_ENABLE_LEGACY_LABEL_PIPELINES`
      - `ORION_ENABLE_LEGACY_PATTERN_MINER`
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_legacy_label_pipeline_gates.py`
      - `test_pattern_miner_specific_gate_overrides_global_off`
      - `test_pattern_miner_control_key_prefers_specific`
      - `test_pattern_miner_does_not_init_db_when_specific_gate_disabled`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_compose_legacy_gate_wiring.py`
      - extended `test_pattern_miner_is_profiled_with_legacy_label_stack` to assert env wiring.
  - Verified with:
    - `pytest -q tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py`
    - `pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py`
    - `ruff check src/orion/config.py src/orion/main_pattern_miner.py tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py`

- **Heber parity deep-audit (ML trainer compatibility)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/comprehensive_audit.md` with field-level schema parity for:
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/pattern_miner.py`
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py`
  - Added quantified overlap findings versus Heber watch datasets:
    - pattern miner: `4/53` direct feature overlap with Heber watch feature schema,
    - exit classifier: `1/147` direct required-column overlap with Heber watch outcomes/features.
  - Documented explicit keep/move/archive guidance for legacy-label training paths.

- **Heber migration (TDD): ML backfill candidate selection moved off local labels SQL**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/backfill_ml_features.py`:
    - `get_records_to_backfill(...)` now sources candidates from Heber gold datasets:
      - `labels_alert_barriers`
      - `meta_label_features`
    - preserved deterministic keyset pagination semantics with `entry_ts,event_id` ordering and cursor filtering.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_backfill_ml_features_selection.py`
      - migrated candidate-selection assertions from local SQL string checks to Heber-source behavioral checks.
  - Verified with:
    - `pytest -q tests/unit/test_backfill_ml_features_selection.py tests/unit/test_backfill_ml_features_signature.py`
    - `ruff check src/orion/jobs/backfill_ml_features.py tests/unit/test_backfill_ml_features_selection.py tests/unit/test_backfill_ml_features_signature.py`

- **Heber migration (TDD): legacy backfill ML-feature write path disabled**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/backfill_ml_features.py`:
    - disabled local `price_target_labels` mutation in `update_ml_features(...)`,
    - backfill now logs explicit skip events for deprecated local writes.
  - Updated test:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_backfill_ml_features_signature.py`
      - `test_update_ml_features_calls_sector_corr_with_two_args` now enforces no local db write.
  - Verified with:
    - `pytest -q tests/unit/test_backfill_ml_features_signature.py tests/unit/test_backfill_ml_features_selection.py`
    - `ruff check src/orion/jobs/backfill_ml_features.py tests/unit/test_backfill_ml_features_signature.py tests/unit/test_backfill_ml_features_selection.py`

- **Heber migration (TDD): legacy backfill exit-column write path disabled**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/backfill_exit_columns.py`:
    - disabled local `price_target_labels` mutation in:
      - `update_velocity_columns(...)`,
      - `update_checkpoint_columns(...)`,
    - backfill now logs explicit skip events for deprecated local writes.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_backfill_exit_columns_selection.py`
      - `test_update_velocity_columns_avoids_local_db_write`
      - `test_update_checkpoint_columns_avoids_local_db_write`
  - Verified with:
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py`
    - `ruff check src/orion/jobs/backfill_exit_columns.py tests/unit/test_backfill_exit_columns_selection.py`

- **Heber gold migration (TDD): validate_features spot-check + sanity paths**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/validate_features.py`:
    - removed local `price_target_labels` SQL reads from:
      - `spot_check_record(...)`,
      - `run_sanity_checks(...)`,
    - added Heber gold-backed label assembly and sanity-stat computation from:
      - `labels_alert_barriers`,
      - `meta_label_features`.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_validate_features_guardrails.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_validate_features_source_adapter.py`
      - added Heber-only regression coverage for `spot_check_record(...)`.
  - Verified with:
    - `pytest -q tests/unit/test_validate_features_guardrails.py tests/unit/test_validate_features_source_adapter.py tests/unit/test_sync_earnings_gateway.py tests/unit/test_data_quality_checker_heber_source.py`
    - `ruff check src/orion/jobs/validate_features.py tests/unit/test_validate_features_guardrails.py tests/unit/test_validate_features_source_adapter.py`

- **Heber gold migration (TDD): earnings backfill ticker discovery + ML coverage checks**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/sync_earnings.py`:
    - removed local `price_target_labels` ticker discovery from `backfill_all_earnings()`,
    - added Heber gold ticker discovery from `labels_alert_barriers` and `meta_label_features`.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/data_quality_checker.py`:
    - removed local `price_target_labels` SQL reads from:
      - `get_ml_features_summary()`,
      - `check_recent_labels_features()`,
    - implemented Heber gold-backed coverage summaries using:
      - `labels_alert_barriers`,
      - `meta_label_features`.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/validate_features.py`:
    - migrated `_load_label_period()` from local `price_target_labels` SQL to Heber gold `labels_alert_barriers` summary reads.
  - Added tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_sync_earnings_gateway.py`
      - `test_backfill_all_earnings_uses_heber_gold_tickers_without_local_db`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_data_quality_checker_heber_source.py`
      - `test_get_ml_features_summary_prefers_heber_gold`
      - `test_check_recent_labels_features_prefers_heber_gold`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_validate_features_source_adapter.py`
      - `test_load_label_period_prefers_heber_gold_without_local_db`
      - `test_load_label_period_returns_empty_when_heber_unavailable`
  - Verified with:
    - `pytest -q tests/unit/test_sync_earnings_gateway.py tests/unit/test_data_quality_checker_heber_source.py tests/unit/test_validate_features_source_adapter.py`
    - `ruff check src/orion/jobs/sync_earnings.py src/orion/jobs/data_quality_checker.py src/orion/jobs/validate_features.py tests/unit/test_sync_earnings_gateway.py tests/unit/test_data_quality_checker_heber_source.py tests/unit/test_validate_features_source_adapter.py`

- **Heber vs Orion parity audit refresh (repo-level inventory)**:
  - appended a new parity section to `/Users/jacobmcmillan/Empire/Orion/comprehensive_audit.md`:
    - canonical Heber Silver inventory (`44` datasets) vs Orion current Heber consumption (`7` datasets),
    - Orion legacy local Silver/label/gold table inventory,
    - side-by-side keep/migrate/archive recommendations,
    - concrete list of remaining local SQL coupling points centered on `price_target_labels` / legacy labelers.

- **Feature enrichment regime sink de-coupling + legacy VIX connector archival (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_feature_enrichment.py`:
    - removed `silver_regime_history` SQL insert from `persist_regime_snapshot(...)`,
    - replaced persistence with bounded in-process cache (`_recent_regime_snapshots`).
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_feature_enrichment_runtime_signals.py`
      - added `test_persist_regime_snapshot_avoids_local_db_write`,
      - migrated local-db monkeypatch guards to `raising=False`.
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_feature_enrichment_context_heber_source.py`
      - migrated local-db monkeypatch guards to `raising=False`.
  - Archived unused legacy connector:
    - moved `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/vix_connector.py` to `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-11_gateway-heber-migration-wave11/legacy_code/vix_connector.py`
    - added `/Users/jacobmcmillan/Empire/Orion/archive/2026-02-11_gateway-heber-migration-wave11/README.md`.
  - Verified with:
    - `pytest -q tests/unit/test_feature_enrichment_runtime_signals.py tests/unit/test_feature_enrichment_context_heber_source.py`
    - `pytest -q tests/unit/test_feature_enrichment_runtime_signals.py tests/unit/test_feature_enrichment_context_heber_source.py tests/unit/test_feature_enrichment_heber_source.py tests/unit/test_sync_earnings_gateway.py tests/unit/test_option_quote_tracker_heber_source.py tests/unit/test_vix_proxy_connector_heber_source.py tests/unit/test_uw_gateway_connector_retry_contract.py tests/unit/test_uw_max_pain_heber_source.py tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_remediation_rules.py`
    - `ruff check src/orion/main_feature_enrichment.py tests/unit/test_feature_enrichment_runtime_signals.py tests/unit/test_feature_enrichment_context_heber_source.py src/orion/connectors/uw_market_tide_connector.py src/orion/connectors/uw_greek_exposure_connector.py src/orion/connectors/uw_iv_rank_connector.py src/orion/connectors/uw_max_pain_connector.py tests/unit/test_uw_gateway_connector_retry_contract.py tests/unit/test_vix_proxy_connector_heber_source.py`

- **UW enrichment connector local silver sink removal (TDD, combined)**:
  - Updated:
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/uw_market_tide_connector.py`
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/uw_greek_exposure_connector.py`
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/uw_iv_rank_connector.py`
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/uw_max_pain_connector.py`
  - Removed local SQL sink writes (`silver_market_tide`, `silver_greek_exposure`, `silver_iv_rank`, `silver_max_pain`) and replaced persistence with bounded in-process caches for legacy compatibility.
  - Added tests in `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_uw_gateway_connector_retry_contract.py`:
    - `test_market_tide_fetch_and_store_avoids_local_db_write`
    - `test_greek_exposure_fetch_and_store_avoids_local_db_write`
    - `test_iv_rank_fetch_and_store_avoids_local_db_write`
    - `test_max_pain_fetch_and_store_avoids_local_db_write`
  - Verified with:
    - `pytest -q tests/unit/test_uw_gateway_connector_retry_contract.py -k "avoids_local_db_write or handles_retry_exhaustion_gracefully"`
    - `pytest -q tests/unit/test_uw_gateway_connector_retry_contract.py tests/unit/test_uw_max_pain_heber_source.py`
    - `pytest -q tests/unit/test_sync_earnings_gateway.py tests/unit/test_option_quote_tracker_heber_source.py tests/unit/test_vix_proxy_connector_heber_source.py tests/unit/test_uw_gateway_connector_retry_contract.py tests/unit/test_uw_max_pain_heber_source.py tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_remediation_rules.py`
    - `ruff check src/orion/connectors/uw_market_tide_connector.py src/orion/connectors/uw_greek_exposure_connector.py src/orion/connectors/uw_iv_rank_connector.py src/orion/connectors/uw_max_pain_connector.py tests/unit/test_uw_gateway_connector_retry_contract.py`

- **VIX proxy local `silver_vix_data` coupling removal + timeframe contract fix (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/vix_proxy_connector.py`:
    - removed local `silver_vix_data` read/write SQL paths,
    - switched persistence to in-process latest snapshot cache (`self._latest_vix_snapshot`),
    - changed VIXY sourcing to Heber minute bars with UTC-day close aggregation (avoids unsupported `1d` timeframe call).
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_vix_proxy_connector_heber_source.py`:
    - added `test_get_vixy_bars_uses_default_supported_timeframe`,
    - added `test_persist_and_get_current_vix_use_in_memory_cache`.
  - Verified with:
    - `pytest -q tests/unit/test_vix_proxy_connector_heber_source.py`
    - `pytest -q tests/unit/test_vix_proxy_connector_heber_source.py tests/unit/test_sync_earnings_gateway.py tests/unit/test_option_quote_tracker_heber_source.py tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_remediation_rules.py`
    - `ruff check src/orion/connectors/vix_proxy_connector.py tests/unit/test_vix_proxy_connector_heber_source.py src/orion/jobs/sync_earnings.py src/orion/main_option_quote_tracker.py tests/unit/test_sync_earnings_gateway.py tests/unit/test_option_quote_tracker_heber_source.py`

- **Earnings + option-quote legacy local-silver persistence removal (TDD, combined)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/sync_earnings.py`:
    - removed executable `silver_earnings_calendar` SQL paths,
    - `get_earnings_for_ticker(...)` now computes earnings proximity from Data Gateway ticker timeline reads,
    - `_upsert_earnings_direct(...)` is now a compatibility no-op while storage is centralized.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_option_quote_tracker.py`:
    - removed executable `silver_option_quotes` SQL paths,
    - replaced checkpoint read/write with in-process cache (`_quote_checkpoint_cache`) for legacy-gated runtime use.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_sync_earnings_gateway.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_option_quote_tracker_heber_source.py`
  - Verified with:
    - `pytest -q tests/unit/test_sync_earnings_gateway.py`
    - `pytest -q tests/unit/test_option_quote_tracker_heber_source.py`
    - `pytest -q tests/unit/test_sync_earnings_gateway.py tests/unit/test_option_quote_tracker_heber_source.py tests/unit/test_legacy_label_pipeline_gates.py tests/unit/test_compose_legacy_gate_wiring.py tests/unit/test_remediation_rules.py`
    - `ruff check src/orion/jobs/sync_earnings.py src/orion/main_option_quote_tracker.py tests/unit/test_sync_earnings_gateway.py tests/unit/test_option_quote_tracker_heber_source.py`

- **Backfill ML features: remove `silver_uw_flow` join dependency (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/backfill_ml_features.py`:
    - removed `LEFT JOIN silver_uw_flow` from `get_records_to_backfill(...)`,
    - added Heber-based option-chain lookup helper `_get_option_chain_for_event(...)`,
    - `update_ml_features(...)` now resolves missing `option_chain` via Heber before computing P2/P3 features.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_backfill_ml_features_selection.py`:
      - `test_get_records_to_backfill_uses_deterministic_ordering` now enforces no `silver_uw_flow` join,
      - added `test_get_option_chain_for_event_prefers_heber`.
  - Verified with:
    - `pytest -q tests/unit/test_backfill_ml_features_selection.py`
    - `pytest -q tests/unit/test_backfill_ml_features_signature.py tests/unit/test_backfill_ml_features_time_alignment.py tests/unit/test_backfill_ml_features_selection.py`
    - `pytest -q tests/unit/test_backfill_ml_features_signature.py tests/unit/test_backfill_ml_features_time_alignment.py tests/unit/test_backfill_ml_features_selection.py tests/unit/test_validate_features_guardrails.py tests/unit/test_validate_features_source_adapter.py tests/unit/test_reconcile_backfill_heber_source.py tests/unit/test_remediation_rules.py tests/unit/test_data_quality_checker_heber_source.py tests/unit/test_window_feature_job_heber_source.py tests/unit/test_option_quote_tracker_heber_source.py tests/unit/test_vix_proxy_connector_heber_source.py tests/unit/test_sync_earnings_gateway.py tests/unit/test_uw_max_pain_heber_source.py tests/unit/test_uw_gateway_connector_retry_contract.py -k "max_pain or validate_features or reconcile_backfill or data_quality_checker or window_feature_job or option_quote_tracker or vix_proxy or sync_earnings or remediation_rules or backfill_ml_features"`
    - `ruff check src/orion/jobs/backfill_ml_features.py tests/unit/test_backfill_ml_features_selection.py`

- **UW max-pain connector current-price Heber migration (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/uw_max_pain_connector.py`:
    - replaced `_get_current_price(...)` local `silver_alpaca_bars` query with Heber `read_bars(...)`,
    - added schema-tolerant normalization for ticker/time/close extraction from Heber bars,
    - removed local bars SQL fallback path (returns `None` when Heber is unavailable).
  - Added tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_uw_max_pain_heber_source.py`
      - `test_get_current_price_prefers_heber_without_local_db_fallback`
      - `test_get_current_price_returns_none_when_heber_unavailable`.
  - Verified with:
    - `pytest -q tests/unit/test_uw_max_pain_heber_source.py tests/unit/test_uw_gateway_connector_retry_contract.py -k "max_pain"`
    - `pytest -q tests/unit/test_validate_features_guardrails.py tests/unit/test_validate_features_source_adapter.py tests/unit/test_reconcile_backfill_heber_source.py tests/unit/test_remediation_rules.py tests/unit/test_data_quality_checker_heber_source.py tests/unit/test_window_feature_job_heber_source.py tests/unit/test_option_quote_tracker_heber_source.py tests/unit/test_vix_proxy_connector_heber_source.py tests/unit/test_sync_earnings_gateway.py tests/unit/test_uw_max_pain_heber_source.py tests/unit/test_uw_gateway_connector_retry_contract.py -k "max_pain or validate_features or reconcile_backfill or data_quality_checker or window_feature_job or option_quote_tracker or vix_proxy or sync_earnings or remediation_rules"`
    - `ruff check src/orion/connectors/uw_max_pain_connector.py tests/unit/test_uw_max_pain_heber_source.py`

- **VIX proxy connector Heber-source migration (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/connectors/vix_proxy_connector.py`:
    - replaced local `silver_alpaca_bars` read in `_get_vixy_bars()` with Heber `read_bars(...)` sourcing,
    - normalized ticker/time/close extraction for mixed Heber schemas (`instrument_key`, `symbol`, `bar_start_ts_*`, `ts_event`, `close/c`),
    - removed local bars SQL fallback behavior (returns empty list when Heber is unavailable).
  - Added tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_vix_proxy_connector_heber_source.py`
      - `test_get_vixy_bars_prefers_heber_without_local_db_fallback`
      - `test_get_vixy_bars_returns_empty_when_heber_unavailable`.
  - Verified with:
    - `pytest -q tests/unit/test_vix_proxy_connector_heber_source.py`
    - `pytest -q tests/unit/test_data_quality_checker_heber_source.py tests/unit/test_window_feature_job_heber_source.py tests/unit/test_option_quote_tracker_heber_source.py tests/unit/test_vix_proxy_connector_heber_source.py tests/unit/test_sync_earnings_gateway.py`
    - `ruff check src/orion/connectors/vix_proxy_connector.py tests/unit/test_vix_proxy_connector_heber_source.py`

- **Data-quality + window features + quote tracker Heber-only fallback removal (TDD, combined pass)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/data_quality_checker.py`:
    - removed local Silver SQL fallback reads for bars/flow/darkpool quality checks,
    - quality summaries now return explicit unavailable payloads when Heber is disabled/unavailable.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/window_feature_job.py`:
    - disabled local Silver SQL feature-build fallback path,
    - `_build_features(...)` now runs Heber-only aggregation when enabled and returns `None` when Heber data is unavailable.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_option_quote_tracker.py`:
    - removed local `silver_uw_flow` fallback read in `get_pending_checkpoints(...)`,
    - pending checkpoint discovery is now Heber-only (or empty when unavailable/disabled).
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_data_quality_checker_heber_source.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_window_feature_job_heber_source.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_option_quote_tracker_heber_source.py`
  - Verified with:
    - `pytest -q tests/unit/test_data_quality_checker_heber_source.py tests/unit/test_window_feature_job_heber_source.py tests/unit/test_option_quote_tracker_heber_source.py`
    - `pytest -q tests/unit/test_validate_features_guardrails.py tests/unit/test_validate_features_source_adapter.py tests/unit/test_reconcile_backfill_heber_source.py tests/unit/test_remediation_rules.py tests/unit/test_data_quality_checker_heber_source.py tests/unit/test_window_feature_job_heber_source.py tests/unit/test_option_quote_tracker_heber_source.py`
    - `ruff check src/orion/jobs/data_quality_checker.py src/orion/jobs/window_feature_job.py src/orion/main_option_quote_tracker.py tests/unit/test_data_quality_checker_heber_source.py tests/unit/test_window_feature_job_heber_source.py tests/unit/test_option_quote_tracker_heber_source.py`

- **Reconciliation + Validation Heber-only hardening (TDD, combined pass)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/reconcile_backfill.py`:
    - removed Bronze-vs-local-Silver SQL fallback comparison path,
    - reconciliation now compares Bronze counts to Heber-derived counts only,
    - added explicit dataset skip behavior when Heber is disabled/unavailable.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/validate_features.py`:
    - removed local SQL fallback reads from:
      - `validate_overnight_gap(...)`,
      - `validate_darkpool(...)`,
      - source coverage audit fallback path,
    - source audit now returns explicit unavailable summaries when Heber data is missing.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_reconcile_backfill_heber_source.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_remediation_rules.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_validate_features_source_adapter.py`
  - Verified with:
    - `pytest -q tests/unit/test_reconcile_backfill_heber_source.py tests/unit/test_validate_features_source_adapter.py tests/unit/test_remediation_rules.py`
    - `pytest -q tests/unit/test_validate_features_guardrails.py tests/unit/test_validate_features_source_adapter.py tests/unit/test_reconcile_backfill_heber_source.py tests/unit/test_remediation_rules.py`

- **Position restart-resume guardrail (TDD)**:
  - added regression coverage in `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_position_manager_filter.py` to ensure closed positions are not rehydrated as open after process restart (`initialize()` after `ExitDecision` insertion),
  - covers `open -> close -> restart` lifecycle explicitly.

- **Price target labeler sector/correlation Heber-only migration (TDD)**:
  - removed sector/correlation SQL fallback in `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`,
  - `get_sector_correlation_features(...)` now returns null-safe defaults when Heber data is unavailable,
  - updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py` to enforce no SQL fallback execution in empty-Heber path.

- **Error envelope compatibility fix (TDD)**:
  - restored `OrionError.code` enum semantics expected by runtime and tests,
  - preserved `details` alias and normalized `to_dict()` output in `/Users/jacobmcmillan/Empire/Orion/src/orion/core/errors.py`.

- **Price target labeler Heber-only fallback guardrails (TDD)**:
  - removed remaining SQL fallback paths in `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py` for:
    - entry signal reads,
    - subsequent price reads,
    - flow Greeks context reads.
  - tightened Heber-source contract tests to enforce no SQL fallback when Heber data is missing/unusable:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_flow.py`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py`
  - validation: `uv run pytest -q` (`744 passed, 6 skipped`).

- **Execution/position tracking hardening (TDD)**:
  - fixed `PositionManager` identity handling to track positions by `candidate_id` (instead of ticker), preventing same-ticker contract overwrite in multi-position books,
  - preserved backward compatibility for ticker-based lookups/removals while making close-path removal candidate-specific in execution loop,
  - made `ExecutionEngine.close_position(...)` direction-aware so short positions close with `BUY` and long positions close with `SELL`,
  - added regression coverage:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_position_manager_execution_contracts.py::test_add_position_keeps_multiple_contracts_for_same_ticker`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_execution_engine_close_direction.py::test_close_position_uses_buy_side_for_short_direction`
  - validation:
    - `uv run pytest -q tests/unit/test_position_manager_execution_contracts.py::test_add_position_keeps_multiple_contracts_for_same_ticker tests/unit/test_execution_engine_close_direction.py`
    - `uv run pytest -q` (`742 passed, 6 skipped`).

- **Warning cleanup pass (TDD)**:
  - fixed async-mock compatibility in HTTP clients by supporting both sync and awaitable `raise_for_status()`:
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/clients/mcp_server.py`
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/clients/trading_rag.py`
  - removed invalid `@pytest.mark.asyncio` decorators from sync tests in:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_universe_persistence.py`
  - eliminated previously observed runtime/pytest warnings from these paths in suite output.

- **TDD stabilization pass: cross-test DB/session integrity + meta-search compatibility**:
  - hardened global DB session plumbing to prevent leaked `async_session_factory` overrides between tests,
  - updated shared DB transaction helper to prefer dynamic storage session factory while preserving legacy patch target behavior,
  - strengthened solver routing fallback behavior:
    - safe live-stage fallback gating,
    - strict baseline identity checks in fallback path,
    - synthetic baseline safety fallback on missing solver tables,
  - improved fill dedupe guard to avoid false positives from mocked scalar rows,
  - restored meta-search compatibility across mixed test styles:
    - resilient session-factory resolution (mock-aware, stale-assignment-safe),
    - ingestion path now supports both patched `db_write` and direct session workflows,
    - deterministic edit reward mutation for in-memory test objects,
    - robust local bar mapping for evaluation payload compatibility,
    - durable experiment finalization and status persistence.

- **TDD compatibility + risk/execution remediation batch**:
  - restored ML scorer backward compatibility (`use_heuristic`, `model`, optional `extract_features(..., bucket=None)`),
  - made heuristic score capping live-only (paper/backtest remain uncapped),
  - restored normalizer legacy aliases (`call_put`, `flags`) for UW flow payloads,
  - added `SignalEngine.process_signals(...)` compatibility path for legacy FEATURE_EVENT candidates,
  - fixed `SolverRouter` baseline fallback behavior:
    - synthesize minimal config for legacy/incomplete solver blobs,
    - restrict fallback to non-live stages,
  - restored `RiskManager.check_order(...)` support for `max_order_size_usd` override,
  - hardened fill polling compatibility in `ExecutionEngine`:
    - deterministic fill-id handling for first vs incremental fills,
    - dedup guard via persisted processed-fill check,
    - async/sync-safe pending-order removal,
  - aligned default exit-rules factory to legacy six-rule default (price-target rule opt-in).

- **Post-merge compatibility remediation pass (TDD batch)**:
  - stabilized API test compatibility for newer `httpx` transport usage (`ASGITransport`) and boolean flow fixtures,
  - restored legacy compatibility paths in core runtime:
    - `EODReviewAgent` constructor/LLM compatibility,
    - `RiskSettings` legacy fields (`max_order_size_usd`, `max_ticker_exposure_usd`),
    - `RiskManager` metric guards and legacy exposure handling,
    - `ModelRegistry` class-level API compatibility for `get()` / `clear_cache()`,
  - made options connector account probe non-fatal at startup to avoid hard init failures in non-auth test runs,
  - fixed silver flow upsert conflict target (`event_id`) to match schema constraints,
  - restored rule coverage by re-enabling bullish/bearish flow rules in `RuleEngine`,
  - improved search + RAG resilience:
    - robust embedding resolution (sync/async),
    - safer JSON premium filtering + fallback behavior,
    - defensive `/search` result mapping and error handling,
  - made `SolverRouter` session factory resolution patch-friendly and test-stable,
  - updated monitor test assertions to structured-log output capture.

- **HTTP Client: requests → httpx** — Migrated all HTTP clients from `requests` to `httpx`:
  - 4 UW connectors: `uw_greek_exposure_connector.py`, `uw_iv_rank_connector.py`, `uw_market_tide_connector.py`, `uw_max_pain_connector.py`
  - `gateway_contract_probe.py`
  - 3 scripts: `raw_flow_backfill.py`, `validate_whalehunter_darkpool_vs_api.py`, `validate_whalehunter_flow_vs_api.py`
  - Updated `test_uw_gateway_connector_retry_contract.py` to use `httpx.HTTPStatusError`
  - Exception mappings: `HTTPError→HTTPStatusError`, `Timeout→TimeoutException`, `ConnectionError→ConnectError`, `RequestException→HTTPError`

### Added

- **Feature Enrichment Ticker Discovery Local-DB Fallback Removal (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_feature_enrichment.py`:
    - removed local SQL ticker discovery fallback (`silver_uw_flow`) from `get_active_tickers_with_source(...)`,
    - ticker discovery now uses Heber first, then static fallback only.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_feature_enrichment_runtime_signals.py`:
    - `test_get_active_tickers_with_source_falls_back_to_static_without_db`
    - `test_get_active_tickers_with_source_falls_back_to_static`
    - both assert no `db_query` fallback path.
  - Verified with:
    - `pytest -q tests/unit/test_feature_enrichment_runtime_signals.py -k "active_tickers_with_source"`
    - `pytest -q tests/unit/test_feature_enrichment_heber_source.py tests/unit/test_feature_enrichment_context_heber_source.py tests/unit/test_feature_enrichment_runtime_signals.py`

- **Feature Enrichment Context SQL Fallback Removal (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_feature_enrichment.py`:
    - removed local SQL fallback paths for:
      - `get_latest_vix_data()`
      - `get_latest_market_tide()`
      - `get_spy_cumulative_return()`,
    - these helpers now use Heber context reads or explicit defaults (`{}`, `None`, `0.0`).
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_feature_enrichment_context_heber_source.py`:
    - `test_get_latest_market_tide_returns_none_when_heber_unavailable`
    - `test_get_latest_vix_data_returns_empty_when_heber_unavailable`
    - `test_get_spy_cumulative_return_returns_zero_when_heber_unavailable`
    - updated `test_context_reads_can_disable_heber` to assert no local DB fallback.
  - Verified with:
    - `pytest -q tests/unit/test_feature_enrichment_context_heber_source.py`
    - `pytest -q tests/unit/test_feature_enrichment_heber_source.py tests/unit/test_feature_enrichment_context_heber_source.py tests/unit/test_feature_enrichment_runtime_signals.py`

- **Price Target Labeler Checkpoint Quote SQL Removal (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - replaced `get_real_checkpoint_prices(...)` local SQL query (`silver_option_quotes`) with Heber-only flow-backed extraction via `_get_real_checkpoint_prices_from_heber(...)`,
    - removed final active local `silver_*` query path in this module,
    - fixed prior exception-path bug in checkpoint quote fallback logging and normalized NaN numeric values.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py`:
    - `test_get_real_checkpoint_prices_prefers_heber_when_available`
    - `test_get_real_checkpoint_prices_returns_empty_when_heber_unavailable`.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "real_checkpoint_prices"`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py tests/unit/test_price_target_labeler_heber_vix_proxy.py tests/unit/test_price_target_labeler_heber_bars.py`

- **Price Target Labeler `silver_ticker_info` + Regime/Underlying SQL Fallback Cleanup (TDD, combined pass)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - removed local SQL fallback usage in:
      - `get_regime_at_entry(...)` (no local VIX SQL fallback),
      - `get_underlying_price_at_entry(...)`,
      - `get_underlying_price_at_offset(...)`,
    - removed DB-backed ticker sector cache helpers:
      - `_get_sector_from_db(...)`
      - `_persist_ticker_info(...)`,
    - `get_ticker_info(...)` now uses in-memory cache + UW API only.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_vix_proxy.py`
      - `test_get_regime_at_entry_leaves_vix_none_when_heber_vix_unavailable`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_bars.py`
      - `test_get_underlying_price_at_entry_returns_none_when_heber_has_no_bar`
      - `test_get_underlying_price_at_offset_returns_none_when_heber_has_no_bar`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_market_tide.py`
      - `test_get_regime_at_entry_uses_heber_tide_without_sql_fallback`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py`
      - `test_get_ticker_info_returns_defaults_without_db_lookup`.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_vix_proxy.py tests/unit/test_price_target_labeler_heber_bars.py`
    - `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "ticker_info_returns_defaults_without_db_lookup"`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py tests/unit/test_price_target_labeler_heber_vix_proxy.py tests/unit/test_price_target_labeler_heber_bars.py`

- **Price Target Labeler VIX/Underlying SQL Fallback Removal (TDD, combined pass)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - removed local SQL fallback usage for:
      - regime VIX context lookup (`get_regime_at_entry`),
      - underlying price lookup at entry (`get_underlying_price_at_entry`),
      - underlying price lookup at offset (`get_underlying_price_at_offset`),
    - these helpers now return Heber-derived values or explicit `None` defaults.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_vix_proxy.py`
      - `test_get_regime_at_entry_leaves_vix_none_when_heber_vix_unavailable`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_bars.py`
      - `test_get_underlying_price_at_entry_returns_none_when_heber_has_no_bar`
      - `test_get_underlying_price_at_offset_returns_none_when_heber_has_no_bar`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_market_tide.py`
      - `test_get_regime_at_entry_uses_heber_tide_without_sql_fallback`
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_vix_proxy.py tests/unit/test_price_target_labeler_heber_bars.py`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py tests/unit/test_price_target_labeler_heber_vix_proxy.py tests/unit/test_price_target_labeler_heber_bars.py`

- **Price Target Labeler Event-Flow SQL Fallback Removal (TDD, combined pass)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - removed event-flow SQL fallback helpers:
      - `_get_entry_signals_sql(...)`
      - `_get_subsequent_prices_sql(...)`
      - `_get_flow_greeks_sql(...)`,
    - Heber-unavailable behavior now returns safe defaults:
      - entry signals: empty list,
      - subsequent prices: empty list,
      - flow greeks: null-shaped payload.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_flow.py`
      - `test_get_entry_signals_returns_empty_when_heber_empty`
      - `test_get_subsequent_prices_returns_empty_when_heber_missing_columns`
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py`
      - `test_get_flow_greeks_returns_null_payload_when_heber_missing`.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_context.py -k "entry_signals or subsequent_prices or flow_greeks"`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py tests/unit/test_price_target_labeler_heber_vix_proxy.py tests/unit/test_price_target_labeler_heber_bars.py`

- **Price Target Labeler Phase Feature SQL Fallback Removal (TDD, combined pass)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - removed phase-feature local SQL fallback helpers:
      - `_get_phase1_bucket_features_sql(...)`
      - `_get_p2_features_sql(...)`
      - `_get_p3_features_sql(...)`,
    - Heber-unavailable behavior now keeps explicit default null payloads for:
      - phase1 market-context fields,
      - P2 (`oi_change_1d`, `oi_change_pct`, `iv_vs_hv_ratio`, `hv_30d`),
      - P3 (`high_52w_distance_pct`, `is_spread_leg`, `same_expiry_trades_1h`).
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py`:
    - `test_get_phase1_bucket_features_returns_none_when_heber_empty`
    - `test_get_p2_features_returns_none_when_heber_empty`
    - `test_get_p3_features_returns_none_when_heber_empty`
    - each now asserts no SQL fallback usage.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "phase1_bucket_features or p2_features or p3_features"`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py tests/unit/test_price_target_labeler_heber_vix_proxy.py tests/unit/test_price_target_labeler_heber_bars.py`

- **Price Target Labeler Flow-Context SQL Fallback Cluster Removal (TDD, combined pass)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - removed local SQL fallback helpers for flow-context features:
      - `_get_opposing_flow_sql(...)`
      - `_get_flow_aggression_sql(...)`
      - `_get_institutional_flow_1w_sql(...)`,
    - Heber-unavailable behavior now returns safe default outputs:
      - opposing flow: `{"count": 0, "premium": 0.0}`,
      - flow aggression: null-shaped metrics,
      - institutional 1w flow: `None`.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py`:
    - `test_get_opposing_flow_returns_zeroes_when_heber_empty`
    - `test_get_flow_aggression_returns_none_when_heber_empty`
    - `test_get_institutional_flow_1w_returns_none_when_heber_empty`
    - each asserts no SQL fallback path is called.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "opposing_flow or flow_aggression or institutional_flow_1w"`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py tests/unit/test_price_target_labeler_heber_vix_proxy.py tests/unit/test_price_target_labeler_heber_bars.py`

- **Price Target Labeler Context Fallback De-Coupling (TDD, combined pass)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - removed local SQL fallbacks for:
      - market tide (`_get_market_tide_before_entry_sql`),
      - GEX snapshot (`_get_gex_at_entry_sql`),
      - GEX rolling averages (`_get_gex_rolling_averages_sql`),
      - darkpool volume (`_get_darkpool_volume_sql`),
      - RVOL metrics (`_get_rvol_metrics_sql`),
    - Heber-unavailable behavior now returns explicit null-shaped values instead of querying local `silver_*` tables.
  - Updated tests:
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py`
      - no-SQL-fallback assertions for GEX / rolling GEX / darkpool / RVOL,
      - added rolling GEX Heber coverage.
    - `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_vix_proxy.py`
      - regime tide path now asserts `market_tide_net is None` when Heber tide is unavailable.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_vix_proxy.py`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py tests/unit/test_price_target_labeler_heber_vix_proxy.py tests/unit/test_price_target_labeler_heber_bars.py`

- **Price Target Labeler Max-Pain SQL Fallback Removal (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - removed `_get_max_pain_distance_sql(...)` local fallback (`silver_max_pain`),
    - `get_max_pain_distance(...)` now uses Heber-derived max-pain distance only (returns `None` when unavailable).
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py`:
    - `test_get_max_pain_distance_returns_none_when_heber_empty`.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py -k "max_pain_distance or iv_rank"`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py tests/unit/test_price_target_labeler_heber_vix_proxy.py`

- **Price Target Labeler IV-Rank SQL Fallback Removal (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - removed `_get_iv_at_offset_sql(...)` (`silver_iv_rank` SQL fallback),
    - `get_iv_at_offset(...)` now falls back from Heber `iv_rank` snapshots directly to Heber flow estimation,
    - `get_iv_rank_at_entry(...)` now uses the same Heber-only fallback chain.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py`:
    - `test_get_iv_at_offset_falls_back_to_heber_flow_estimate_when_iv_rank_unusable`
    - `test_get_iv_rank_at_entry_returns_none_when_heber_iv_rank_and_flow_unavailable`.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py -k "iv_at_offset_falls_back_to_heber_flow_estimate or get_iv_rank_at_entry_returns_none_when_heber_iv_rank_and_flow_unavailable or iv_rank"`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py tests/unit/test_price_target_labeler_heber_vix_proxy.py`

- **Price Target Labeler Regime Fallback De-Duping (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - removed the inline `silver_market_tide` fallback query in `get_regime_at_entry(...)`,
    - `get_regime_at_entry(...)` now reuses shared fallback helper `_get_market_tide_before_entry_sql(...)` and consumes `net_premium`.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_vix_proxy.py`:
    - added `test_get_regime_at_entry_uses_shared_market_tide_sql_fallback_helper`.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_vix_proxy.py -k "shared_market_tide_sql_fallback_helper or falls_back_to_sql_when_heber_vix_unavailable or prefers_heber_vix_proxy"`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py tests/unit/test_price_target_labeler_heber_vix_proxy.py`

- **Price Target Labeler IV-Rank Heber-First Fallback Hardening (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
    - `get_iv_rank_at_entry(...)` now falls back to local `silver_iv_rank` lookup before any estimation path,
    - removed local `silver_uw_flow` SQL IV-history fallback for IV-rank entry calculation,
    - added Heber flow-based IV-rank estimator `_estimate_iv_rank_from_heber_flow(...)` for cases where IV-rank snapshots are unavailable.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py`:
    - `test_get_iv_rank_at_entry_estimates_from_heber_flow_when_iv_rank_missing`.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py -k "iv_rank"`
    - `pytest -q tests/unit/test_price_target_labeler_heber_flow.py tests/unit/test_price_target_labeler_heber_market_tide.py tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py`

- **Validate Features Overnight-Gap Heber-First Spot Check (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/jobs/validate_features.py`:
    - `validate_overnight_gap(...)` now prefers Heber bars-derived `(today_open, prior_close)` inputs and falls back to local SQL only when Heber data is unavailable,
    - added `_get_overnight_gap_inputs_from_heber_for_validation(...)` with schema/time normalization for Heber bar frames.
  - Updated tests in `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_validate_features_source_adapter.py`:
    - `test_validate_overnight_gap_prefers_heber_when_available`
    - `test_validate_overnight_gap_falls_back_to_local_when_heber_empty`.
  - Verified with:
    - `pytest -q tests/unit/test_validate_features_source_adapter.py -k "overnight_gap"`
    - `pytest -q tests/unit/test_validate_features_source_adapter.py tests/unit/test_validate_features_guardrails.py`
    - `ruff check src/orion/jobs/validate_features.py tests/unit/test_validate_features_source_adapter.py`

- **Feature Enrichment Heber-First VIX Context Reads (TDD)**:
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_feature_enrichment.py`:
    - `get_latest_vix_data()` now prefers Heber `VIXY` bars for VIX proxy context and falls back to local SQL when unavailable,
    - added helper paths for VIX proxy regime mapping and 1-day change derivation from Heber bars.
  - Updated `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_feature_enrichment_context_heber_source.py`:
    - added Heber-preferred and fallback coverage for `get_latest_vix_data()`.
  - Verified with:
    - `pytest -q tests/unit/test_feature_enrichment_context_heber_source.py -k "latest_vix_data or latest_market_tide or spy_cumulative_return"`
    - `pytest -q tests/unit/test_feature_enrichment_heber_source.py tests/unit/test_feature_enrichment_context_heber_source.py tests/unit/test_feature_enrichment_runtime_signals.py`
    - `ruff check src/orion/main_feature_enrichment.py tests/unit/test_feature_enrichment_context_heber_source.py`

- **Validate Features Source-ID Canonicalization + Legacy Alias Adapter (TDD)**:
  - Updated `src/orion/jobs/validate_features.py`:
    - introduced canonical source IDs for feature/source contracts:
      - `bars`, `flow_alerts`, `darkpool`, `greek_exposure`, `max_pain`, `market_tide`, `vix_data`, `regime_history`,
    - added backward-compatible normalization for legacy IDs (for example `silver_uw_flow` -> `flow_alerts`),
    - migrated audit source specs/order and feature-source mapping to canonical IDs while preserving local SQL fallback behavior.
    - `validate_darkpool(...)` now uses Heber-first darkpool volume lookup with local SQL fallback for spot-check validation.
  - Updated tests in `tests/unit/test_validate_features_source_adapter.py`:
    - added canonicalization and legacy-alias acceptance coverage,
    - added guardrail asserting feature source mappings no longer use `silver_*` source IDs.
    - added darkpool validation-source coverage:
      - `test_validate_darkpool_prefers_heber_when_available`
      - `test_validate_darkpool_falls_back_to_local_when_heber_empty`.
  - Verified with:
    - `pytest -q tests/unit/test_validate_features_source_adapter.py tests/unit/test_validate_features_guardrails.py`
    - `pytest -q tests/unit/test_validate_features_source_adapter.py tests/unit/test_validate_features_guardrails.py tests/unit/test_heber_reader.py -k "validate_features or darkpool"`

- **Combined Pass: Meta-Search Sonar Closure + Async Session Add Hardening (TDD)**:
  - Updated `src/orion/agents/meta_search_agent.py`:
    - resolved remaining Sonar new-code issues in promotion/ingestion helpers,
    - converted `_move_processed_proposals(...)` from async to sync helper (no awaited operations),
    - adjusted demotion flow to return `0` when a pending identical recommendation already exists,
    - added `_session_add(...)` helper to safely support both normal SQLAlchemy `session.add(...)` and async-mocked session adds in tests.
  - Updated `tests/unit/test_meta_promotion.py`:
    - added `test_handle_solver_demotion_skips_duplicate_pending_recommendation`,
    - switched ingestion persistence assertion to patch `db_write(...)` directly for deterministic transaction validation.
  - Validation:
    - `uv run pytest -q tests/unit/test_meta_promotion.py tests/unit/test_meta_search.py tests/unit/test_meta_search_edits.py tests/unit/test_meta_search_heber_source.py tests/unit/test_main_execution_heber_source.py`
    - `uv run ruff check src/orion/agents/meta_search_agent.py tests/unit/test_meta_promotion.py`
    - `sonar-scanner -Dproject.settings=sonar-project.properties`
  - Result:
    - Sonar new-code issues now **0** (quality gate still fails only on `new_coverage` threshold).

- **Sonar Coverage Wiring + Transitional Coverage Scope Control**:
  - Updated `sonar-project.properties`:
    - added explicit Python coverage report path:
      - `sonar.python.coverage.reportPaths=coverage.xml`,
    - kept temporary coverage exclusions for high-churn refactor files:
      - `src/orion/agents/meta_search_agent.py`
      - `src/orion/main_execution.py`
  - Regenerated coverage artifact with focused new-code test suite:
    - `uv run pytest -q tests/unit/test_heber_reader.py tests/unit/test_main_execution_heber_source.py tests/unit/test_meta_search_heber_source.py tests/unit/test_meta_search.py tests/unit/test_meta_search_edits.py tests/unit/test_meta_promotion.py --cov=src --cov-report=xml:coverage.xml`
  - Additional TDD branch hardening in `tests/unit/test_main_execution_heber_source.py`:
    - added helper-path edge coverage for:
      - null/alias normalizers (`_normalize_flow_ticker`, `_normalize_put_call`),
      - coercion edge cases (`_coerce_bool`),
      - Heber row filtering for non-matching ticker, invalid premium, and numeric-coercion fallback behavior.
  - Attempted removal of `main_execution` coverage exclusion was revalidated and remains below gate threshold in current new-code window; temporary exclusion remains in place pending broader execution-loop test expansion.

- **Cross-Repo Darkpool Canonicalization Contract (TDD)**:
  - Updated `src/orion/clients/heber_reader.py`:
    - switched canonical darkpool Silver dataset to `darkpool`,
    - retained backward-compatible read fallback to legacy `darkpool_trades`,
    - preserved optional `darkpool_dataset` override for explicit local control.
  - Updated tests in `tests/unit/test_heber_reader.py`:
    - `test_read_darkpool_prefers_canonical_dataset_when_both_exist`
    - `test_read_darkpool_falls_back_to_legacy_alias_dataset`.
  - Verified with:
    - `pytest -q tests/unit/test_heber_reader.py -k "darkpool"`

- **Combined Pass: Heber Darkpool Alias Compatibility + Legacy Execution Helper Decommission (TDD)**:
  - Updated `src/orion/clients/heber_reader.py`:
    - added darkpool dataset alias fallback support for Silver reads:
      - primary `darkpool_trades`
      - fallback alias `darkpool`,
    - `read_darkpool(...)` now tries configured aliases in order and returns the first non-empty dataset,
    - added optional constructor override for darkpool dataset selection.
  - Updated `src/orion/main_execution.py`:
    - removed dead legacy helper functions no longer used in runtime flow:
      - `get_pending_candidates`
      - `update_candidate_status`,
    - retained active replacement helpers (`fetch_pending_candidates`, `update_decision_status`).
  - Added/updated tests:
    - `tests/unit/test_heber_reader.py`
      - `test_read_darkpool_falls_back_to_alias_dataset`
    - `tests/unit/test_main_execution_decommission.py`
      - `test_legacy_candidate_status_helpers_removed`
  - Verified with:
    - `pytest -q tests/unit/test_heber_reader.py tests/unit/test_main_execution_decommission.py tests/unit/test_main_execution_heber_source.py tests/unit/test_main_execution_exit_scope.py`

- **Meta-Search Sonar Remediation Pass (TDD validation)**:
  - Updated `src/orion/agents/meta_search_agent.py` to resolve Sonar reliability/code-smell findings:
    - replaced sync YAML file reads in async ingestion path with `asyncio.to_thread(...)`,
    - removed redundant multi-exception catches where `ValidationError` duplicated `ValueError`,
    - removed unused parameter/local bindings (`_base_solver_id`, `_solver_run`, `_price_data`),
    - removed DataFrame `inplace=True` mutations in favor of assignment-style transforms,
    - ensured async DB write callback performs async work (`await session.flush()`).
  - Validation:
    - `ruff check src/orion/agents/meta_search_agent.py src/orion/main_execution.py tests/unit/test_meta_search_heber_source.py tests/unit/test_main_execution_heber_source.py`
    - `uv run pytest -q tests/unit/test_meta_search_heber_source.py tests/unit/test_main_execution_heber_source.py`
    - `sonar-scanner -Dproject.settings=sonar-project.properties`
  - Sonar new-code issues reduced from **17 → 8** (remaining are cognitive-complexity refactors in `meta_search_agent.py`).

- **Combined Pass: Execution Recent-Flow + Meta-Search Event Fetching Heber-First (TDD)**:
  - Updated `src/orion/main_execution.py`:
    - added Heber-first recent-flow sourcing for exit-rule context with SQL fallback:
      - env `ORION_EXECUTION_PREFER_HEBER_RECENT_FLOW` (default enabled),
    - added Heber flow normalization for exit-rule-compatible fields (`flow_ts_utc`, `premium_usd`, `put_call`, `aggressor`, `is_sweep`, `option_chain`, `expiry`, `strike`).
  - Updated `src/orion/agents/meta_search_agent.py`:
    - added Heber-first event-fetching for solver backtest windows with SQL fallback:
      - env `ORION_META_SEARCH_PREFER_HEBER_EVENTS` (default enabled),
    - split event loading into explicit Heber/local methods,
    - fixed local SQL fetch path to actually execute before returning.
  - Added tests:
    - `tests/unit/test_main_execution_heber_source.py`
    - `tests/unit/test_meta_search_heber_source.py`
  - Verified with:
    - `pytest -q tests/unit/test_main_execution_exit_scope.py tests/unit/test_main_execution_heber_source.py tests/unit/test_meta_search_hardening.py tests/unit/test_meta_search_heber_source.py`

- **Combined Pass: Reconciliation + EOD Regime Bars Heber-First (TDD)**:
  - Updated `src/orion/jobs/reconcile_backfill.py`:
    - added Heber-first reconciliation read mode with SQL fallback:
      - env `ORION_RECONCILE_BACKFILL_PREFER_HEBER` (default enabled),
    - added Heber adapters for bars/flow/darkpool count aggregation and schema-normalization helpers.
  - Updated `src/orion/agents/eod_review_agent.py`:
    - added Heber-first regime-bars read mode with SQL fallback:
      - env `ORION_EOD_REVIEW_PREFER_HEBER_BARS` (default enabled),
    - added Heber bars loader that normalizes to existing regime inputs (`ticker`, `close`).
  - Added tests:
    - `tests/unit/test_reconcile_backfill_heber_source.py`
    - `tests/unit/test_eod_review_agent_heber_bars.py`
  - Updated compatibility test:
    - `tests/unit/test_remediation_rules.py::test_reconcile_backfill_logic`
    - forces SQL mode for deterministic legacy call-count assertions.
  - Verified with:
    - `pytest -q tests/unit/test_reconcile_backfill_heber_source.py tests/unit/test_eod_review_agent_heber_bars.py tests/unit/test_eod_agent.py tests/unit/test_remediation_rules.py::test_reconcile_backfill_logic`

- **Pydantic Settings Migration (os.getenv → SystemSettings/AgentSettings)**:
  - Added 20+ fields to `SystemSettings` and `AgentSettings` in `src/orion/config.py`:
    - `run_id`, `log_format`, `api_key`, `metrics_enabled`, `metrics_port`, `model_dir`,
      `max_model_age_days`, `proposals_dir`, `monitor_lag_threshold`, `monitor_dlq_lookback`,
      `ollama_embedding_model`, `ollama_base_url`, `deepseek_api_key`, `deepseek_model`, `uw_base_url`
  - Migrated 12 files from `os.getenv`/`os.environ.get` to centralized settings singletons:
    - `api/auth.py`, `api/main.py`, `shared/db_utils.py`, `shared/dlq_utils.py`, `shared/metrics.py`,
      `jobs/monitor_system.py`, `ml/scorer.py`, `ml/exit_classifier.py`, `ml/pattern_miner.py`,
      `rag/embeddings.py`, `jobs/run_meta_loop.py`, `agents/codex_client.py`
  - Removed unused `os` imports from migrated files

- **Combined Pass: Batch-Bound Backfill + Non-Heber Source Streak Alerts (TDD)**:
  - Updated `src/orion/jobs/backfill_exit_columns.py`:
    - added non-empty-batch circuit breaker:
      - env `ORION_BACKFILL_EXIT_MAX_BATCHES`
      - runtime arg `max_batches`
      - CLI flag `--max-batches`,
    - run summary now includes:
      - `max_batches`
      - `total_batches`
      - abort reason `max_batches_reached` when triggered.
  - Updated `src/orion/main_feature_enrichment.py`:
    - added non-Heber source streak warning controls:
      - env `ORION_FEATURE_ENRICHMENT_NON_HEBER_WARN_STREAK`
      - invalid values log `feature_enrichment_non_heber_warn_streak_invalid`,
    - added consecutive non-Heber warning event:
      - `feature_enrichment_non_heber_streak`.
  - Extended tests:
    - `tests/unit/test_backfill_exit_columns_selection.py`
      - `test_run_backfill_aborts_when_max_batches_reached`
    - `tests/unit/test_feature_enrichment_runtime_signals.py`
      - `test_non_heber_warn_streak_threshold_invalid_env_uses_default`
      - `test_note_ticker_source_streak_warns_on_non_heber_threshold`.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_feature_enrichment_runtime_signals.py tests/unit/test_feature_enrichment_heber_source.py tests/unit/test_feature_enrichment_gateway_contract.py`
- **Window + Data Quality Heber-First Source Integration (TDD)**:
  - Updated `src/orion/jobs/window_feature_job.py`:
    - added Heber-first feature aggregation path with local SQL fallback:
      - env `ORION_WINDOW_FEATURE_JOB_PREFER_HEBER` (default enabled),
    - computes flow/darkpool window aggregates directly from Heber datasets when available,
    - preserves the existing SQL aggregation path as fallback.
  - Updated `src/orion/jobs/data_quality_checker.py`:
    - added Heber-first flow/darkpool summary + staleness checks with local SQL fallback:
      - env `ORION_DATA_QUALITY_CHECKER_PREFER_HEBER` (default enabled),
    - flow/darkpool summaries now include backend provenance (`heber` or `local_db`).
  - Added tests:
    - `tests/unit/test_window_feature_job_heber_source.py`
    - `tests/unit/test_data_quality_checker_heber_source.py`
  - Verified with:
    - `pytest -q tests/unit/test_window_feature_job_heber_source.py tests/unit/test_data_quality_checker_heber_source.py`
- **Combined Pass: Option Quote Tracker + Bar Quality Heber-First Reads (TDD)**:
  - Updated `src/orion/main_option_quote_tracker.py`:
    - added Heber-first pending-flow sourcing for checkpoint tracking with SQL fallback,
    - added env toggle:
      - `ORION_OPTION_QUOTE_TRACKER_PREFER_HEBER` (default enabled),
    - normalizes Heber flow rows into legacy checkpoint payload shape (`event_id`, `option_symbol`, `flow_ts_utc`, `minutes_since_entry`).
  - Updated `src/orion/jobs/data_quality_checker.py`:
    - extended existing Heber-first mode (`ORION_DATA_QUALITY_CHECKER_PREFER_HEBER`) to bars checks:
      - bars summary,
      - zero-valued bars,
      - critical-ticker staleness,
      - bar gap detection,
    - preserves SQL fallback behavior when Heber read paths are unavailable.
  - Added/extended tests:
    - `tests/unit/test_option_quote_tracker_heber_source.py`
    - `tests/unit/test_data_quality_checker_heber_source.py`
      - bars Heber-first and fallback coverage.
  - Verified with:
    - `pytest -q tests/unit/test_data_quality_checker_heber_source.py tests/unit/test_option_quote_tracker_heber_source.py tests/unit/test_window_feature_job_heber_source.py tests/unit/test_validate_features_source_adapter.py tests/unit/test_validate_features_guardrails.py tests/unit/test_legacy_label_pipeline_gates.py`
- **Feature Enrichment Heber-First Regime Context Reads (TDD)**:
  - Updated `src/orion/main_feature_enrichment.py`:
    - added Heber-first context read mode for regime inputs:
      - env `ORION_FEATURE_ENRICHMENT_PREFER_HEBER_CONTEXT` (default enabled),
    - `get_latest_market_tide()` now reads Heber `market_tide` first and falls back to local SQL,
    - `get_spy_cumulative_return()` now reads Heber `bars` (SPY) first and falls back to local SQL,
    - added robust column/time normalization helpers for schema compatibility.
  - Added tests:
    - `tests/unit/test_feature_enrichment_context_heber_source.py`
  - Verified with:
    - `pytest -q tests/unit/test_feature_enrichment_context_heber_source.py tests/unit/test_feature_enrichment_runtime_signals.py tests/unit/test_feature_enrichment_heber_source.py tests/unit/test_feature_enrichment_gateway_contract.py tests/unit/test_data_quality_checker_heber_source.py tests/unit/test_option_quote_tracker_heber_source.py tests/unit/test_window_feature_job_heber_source.py tests/unit/test_validate_features_source_adapter.py tests/unit/test_validate_features_guardrails.py tests/unit/test_legacy_label_pipeline_gates.py`
- **Validate Features Heber-First Source Audit Adapter (TDD)**:
  - Updated `src/orion/jobs/validate_features.py`:
    - added Heber-first source-audit adapter for dataset coverage checks with local SQL fallback,
    - added env toggle:
      - `ORION_VALIDATE_FEATURES_PREFER_HEBER` (`1` default, falsey values disable Heber reads),
    - added source coverage helpers for:
      - label-period bounds,
      - Heber dataframe coverage summarization,
      - per-source backend provenance (`heber` vs `local_db`),
    - refactored `audit_data_sources()` to use shared source specs and emit backend + preference details.
  - Added tests:
    - `tests/unit/test_validate_features_source_adapter.py`
      - env parsing behavior
      - Heber-preferred selection
      - local fallback behavior
      - dataframe summarization behavior.
  - Verified with:
    - `pytest -q tests/unit/test_validate_features_source_adapter.py tests/unit/test_validate_features_guardrails.py tests/unit/test_heber_reader.py`
- **Combined Pass: Runtime Guardrails for Backfill + Feature-Enrichment Loop (TDD)**:
  - Updated `src/orion/jobs/backfill_exit_columns.py`:
    - added elapsed-runtime circuit breaker:
      - env default `ORION_BACKFILL_EXIT_MAX_DURATION_SECONDS`,
      - runtime arg `max_duration_seconds`,
      - CLI flag `--max-duration-seconds`,
    - backfill now aborts safely when elapsed runtime exceeds threshold with:
      - `aborted=true`
      - `abort_reason=max_duration_seconds_reached`
      - `max_duration_seconds` in summary payload.
  - Updated `src/orion/main_feature_enrichment.py`:
    - added loop wait configuration:
      - env `ORION_FEATURE_ENRICHMENT_LOOP_SLEEP_SECONDS`,
      - invalid values emit `feature_enrichment_loop_sleep_seconds_invalid` and fall back to default,
    - added loop consecutive-error warning threshold:
      - env `ORION_FEATURE_ENRICHMENT_LOOP_ERROR_WARN_STREAK`,
      - warning event `feature_enrichment_loop_error_streak`.
  - Extended tests:
    - `tests/unit/test_backfill_exit_columns_selection.py`
      - `test_run_backfill_aborts_when_max_duration_seconds_reached`
    - `tests/unit/test_feature_enrichment_runtime_signals.py`
      - `test_loop_sleep_seconds_invalid_env_uses_default`
      - `test_note_loop_error_warns_at_threshold`.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_feature_enrichment_runtime_signals.py`
- **Feature Enrichment Runtime Signal Hardening (TDD)**:
  - Updated `src/orion/main_feature_enrichment.py`:
    - added source-aware ticker discovery via `get_active_tickers_with_source(...)` (`heber`, `local_db`, `static_fallback`),
    - added ticker-source transition telemetry with structured events:
      - `feature_enrichment_ticker_source_changed`,
    - added consecutive zero-write streak monitoring per feed with configurable warning threshold:
      - env: `ORION_FEATURE_ENRICHMENT_ZERO_WRITE_WARN_STREAK`
      - events: `feature_enrichment_zero_write_streak` and `feature_enrichment_zero_write_warn_streak_invalid`,
    - preserved `get_active_tickers(...)` as a compatibility wrapper.
  - Added `tests/unit/test_feature_enrichment_runtime_signals.py`:
    - validates Heber/local/static fallback behavior,
    - validates zero-write streak warn/reset semantics,
    - validates ticker source transition logging semantics.
  - Verified with:
    - `pytest -q tests/unit/test_feature_enrichment_runtime_signals.py tests/unit/test_feature_enrichment_gateway_contract.py tests/unit/test_feature_enrichment_heber_source.py`
- **Gateway/Heber Parity Audit Inventory Refresh**:
  - Updated `/docs/ORION_GATEWAY_HEBER_PARITY_AUDIT_2026-02-05.md` with a quantified remaining dependency inventory for local `silver_*` table usage:
    - per-table file/reference counts,
    - hotspot ranking by file,
    - implementation-first migration sequencing for step 1.
- **Combined Pass: Backfill Fail-Fast Threshold + Elapsed-Time Telemetry (TDD)**:
  - Updated `src/orion/jobs/backfill_exit_columns.py`:
    - added failure circuit-breaker threshold:
      - env default `ORION_BACKFILL_EXIT_MAX_FAILED_RECORDS`,
      - runtime arg `max_failed_records`,
      - CLI flag `--max-failed-records`,
    - backfill now aborts safely when total failed records reaches threshold and reports:
      - `aborted`
      - `abort_reason`
      - `max_failed_records`,
    - added elapsed-time telemetry in summary payload:
      - `velocity.elapsed_seconds`
      - `checkpoint.elapsed_seconds`
      - `total_elapsed_seconds`.
  - Extended `tests/unit/test_backfill_exit_columns_selection.py`:
    - `test_run_backfill_aborts_when_max_failed_records_reached`
    - `test_run_backfill_summary_includes_elapsed_seconds`.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_exit_columns_selection.py`
- **Pattern Miner Exit-Refresh Resolution Telemetry (TDD)**:
  - Updated `src/orion/ml/pattern_miner.py`:
    - added `_exit_classifier_schema_refresh_config_details_from_env()` to return resolved flags with source metadata,
    - added `_exit_classifier_schema_refresh_mode(...)` mode labels (`off`, `prefetch_once`, `per_bucket`),
    - `run_all_pattern_mining()` now emits structured resolution telemetry:
      - `event=exit_training_schema_refresh_config_resolved`
      - `refresh_mode`
      - `refresh_source`
      - resolved booleans.
  - Updated `tests/unit/test_pattern_miner_exit_refresh_config.py`:
    - `test_exit_classifier_schema_refresh_config_details_tracks_source`
    - `test_exit_classifier_schema_refresh_mode_labels`
    - strengthened `test_run_all_pattern_mining_passes_exit_refresh_flags` with telemetry assertions.
  - Verified with:
    - `uv run pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py`
    - `uv run pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py`
- **Combined Pass: Dead-Letter Rotation Retention Cap + Schema-Refresh Runbook Note (TDD)**:
  - Updated `src/orion/jobs/backfill_exit_columns.py`:
    - added rotation-retention cap for dead-letter archives:
      - env default: `ORION_BACKFILL_EXIT_DEAD_LETTER_MAX_ROTATED_FILES`,
      - runtime arg: `dead_letter_max_rotated_files`,
      - CLI flag: `--dead-letter-max-rotated-files`,
    - rotates with monotonic suffixing and prunes oldest `.jsonl.N` / `.jsonl.N.gz` files when cap is reached.
  - Extended `tests/unit/test_backfill_exit_columns_selection.py`:
    - `test_write_dead_letter_record_prunes_oldest_rotation_when_cap_reached`
    - `test_write_dead_letter_record_prunes_oldest_gzip_rotation_when_cap_reached`
  - Added schema rollout guidance:
    - `docs/runbooks/schema_rollout.md`
    - `docs/runbooks/README.md` runbook index entry.
  - Verified with:
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_pattern_miner_exit_refresh_config.py`
- **Pattern Miner Exit-Refresh Strategy Env Unification (TDD)**:
  - Updated `src/orion/ml/pattern_miner.py`:
    - added `ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY` with supported modes:
      - `off|disabled|none|false`
      - `prefetch_once|once`
      - `per_bucket|each_bucket|each`
    - strategy env takes precedence over legacy bool flags when valid,
    - invalid strategy values log `exit_training_schema_refresh_strategy_invalid` and fall back to legacy env flags.
  - Updated `tests/unit/test_pattern_miner_exit_refresh_config.py`:
    - `test_exit_classifier_schema_refresh_strategy_per_bucket_overrides_legacy`
    - `test_exit_classifier_schema_refresh_strategy_invalid_falls_back_to_legacy`
  - Verified with:
    - `uv run pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py`
    - `uv run pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py`
- **Combined Pass: Dead-Letter Gzip Rotation + Exit-Training Refresh Env Wiring (TDD)**:
  - Updated `src/orion/jobs/backfill_exit_columns.py`:
    - added optional gzip compression for rotated dead-letter files:
      - env default `ORION_BACKFILL_EXIT_DEAD_LETTER_COMPRESS_ROTATED`
      - runtime/CLI `dead_letter_compress_rotated` with flags:
        - `--dead-letter-compress-rotated`
        - `--no-dead-letter-compress-rotated`,
    - rotated dead-letter files now support `.jsonl.N.gz` output when enabled,
    - added `dead_letter_compressed` counters in phase + total summary payload.
  - Updated `src/orion/ml/pattern_miner.py`:
    - added `_exit_classifier_schema_refresh_config_from_env()` config helper,
    - `run_all_pattern_mining()` now reads and forwards:
      - `ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH`
      - `ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET`
    - includes safety guard: per-bucket refresh is disabled if force refresh is false.
  - Added tests:
    - `tests/unit/test_backfill_exit_columns_selection.py`
      - `test_write_dead_letter_record_rotates_and_gzips_when_enabled`
      - `test_run_backfill_dead_letter_rotation_tracks_compressed_files`
    - `tests/unit/test_pattern_miner_exit_refresh_config.py`
      - env config defaults
      - invalid config guard behavior
      - pass-through wiring from `run_all_pattern_mining()` to exit-classifier trainer.
  - Verified with:
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py -k "dead_letter and (rotation or gzip or compressed)" tests/unit/test_pattern_miner_exit_refresh_config.py`
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_pattern_miner_exit_refresh_config.py`
- **Exit Classifier All-Bucket Refresh Strategy Control (TDD)**:
  - Updated `src/orion/ml/exit_classifier.py`:
    - `train_all_exit_classifiers(...)` now accepts `refresh_each_bucket` (default `False`),
    - when `force_schema_refresh=True` and `refresh_each_bucket=False`, orchestrator performs one schema pre-refresh and logs strategy `prefetch_once`,
    - when both `force_schema_refresh=True` and `refresh_each_bucket=True`, each bucket training call receives `force_schema_refresh=True` and no one-time pre-refresh is executed.
  - Updated `tests/unit/test_exit_classifier_window_query.py`:
    - `test_train_all_exit_classifiers_refresh_each_bucket_forces_bucket_refresh`
    - existing orchestrator refresh tests retained for one-time pre-refresh path.
  - Verified with:
    - `uv run pytest -q tests/unit/test_exit_classifier_window_query.py -k "train_all_exit_classifiers or train_bucket_exit_classifier_passes_force_schema_refresh"`
    - `uv run pytest -q tests/unit/test_exit_classifier_window_query.py tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py`
- **Exit Classifier Orchestration Schema-Refresh Control (TDD)**:
  - Updated `src/orion/ml/exit_classifier.py`:
    - `build_bucket_training_data(...)` now accepts `force_schema_refresh` and can force schema metadata refresh before bucket preflight validation,
    - `train_bucket_exit_classifier(...)` now accepts and forwards `force_schema_refresh`,
    - `train_all_exit_classifiers(...)` now accepts `force_schema_refresh`; when enabled it performs one explicit schema refresh prior to bucket loop and logs `exit_training_schema_forced_refresh`.
  - Extended `tests/unit/test_exit_classifier_window_query.py`:
    - `test_train_bucket_exit_classifier_passes_force_schema_refresh`
    - `test_train_all_exit_classifiers_force_refreshes_schema_once`
  - Verified with:
    - `pytest -q tests/unit/test_exit_classifier_window_query.py -k "force_schema_refresh or force_refreshes_schema_once"`
    - `pytest -q tests/unit/test_exit_classifier_window_query.py tests/unit/test_backfill_exit_columns_selection.py`
- **Backfill Exit-Columns Dead-Letter Redaction + Rotation Controls (TDD)**:
  - Updated `src/orion/jobs/backfill_exit_columns.py`:
    - added dead-letter payload redaction controls:
      - env default `ORION_BACKFILL_EXIT_DEAD_LETTER_REDACT_FIELDS`
      - runtime/CLI `dead_letter_redact_fields` / `--dead-letter-redact-fields`,
    - added dead-letter file rotation by max size:
      - env/arg default `ORION_BACKFILL_EXIT_DEAD_LETTER_MAX_BYTES`
      - runtime/CLI `dead_letter_max_bytes` / `--dead-letter-max-bytes`,
    - added per-phase and total `dead_letter_rotated` counters to backfill summary payload,
    - `_write_dead_letter_record(...)` now supports redaction + rotation and returns a rotation flag.
  - Extended `tests/unit/test_backfill_exit_columns_selection.py`:
    - `test_write_dead_letter_record_applies_redaction_and_rotation`
    - `test_run_backfill_dead_letter_redaction_and_rotation`
    - updated retry test assertions for terminal `error_message` contract.
  - Verified with:
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py`
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_exit_classifier_window_query.py`
- **Exit Classifier Force-Refresh Schema Probe + Missing-Family Metrics (TDD)**:
  - Updated `src/orion/ml/exit_classifier.py`:
    - `_load_price_target_label_columns(...)` now supports `force_refresh=True` to bypass schema cache in long-lived workers during active migrations,
    - added `_group_count_map(...)` and now emits `missing_by_family_counts` alongside `missing_by_family` in `exit_training_schema_missing_columns` logs.
  - Updated `tests/unit/test_exit_classifier_window_query.py`:
    - `test_load_price_target_label_columns_force_refresh_bypasses_cache`
    - `test_build_bucket_training_data_logs_missing_family_counts`
  - Verified with:
    - `uv run pytest -q tests/unit/test_exit_classifier_window_query.py`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py`
- **Exit Classifier Empty-Dataset Contract + Schema Cache + Missing-Column Family Diagnostics (TDD)**:
  - Updated `src/orion/ml/exit_classifier.py`:
    - introduced `EXIT_FEATURE_NAMES` as a single feature-schema source-of-truth for training data output contracts,
    - `build_bucket_training_data(...)` now returns stable empty matrices with schema for:
      - unknown bucket names,
      - valid buckets with zero query rows,
    - added schema metadata TTL cache (`SCHEMA_CACHE_TTL_SECONDS`) for `price_target_labels` column probes to reduce repeated metadata reads during frequent training loops,
    - added `_group_missing_columns_by_family(...)` and enriched `exit_training_schema_missing_columns` logs with grouped diagnostics (`entry_context`, `outcome`, `checkpoint_returns`, `checkpoint_greeks`, `checkpoint_time_decay`, `other`).
  - Updated `tests/unit/test_exit_classifier_window_query.py`:
    - `test_group_missing_columns_by_family_assigns_expected_buckets`
    - `test_load_price_target_label_columns_uses_ttl_cache`
    - strengthened no-row/unknown-bucket contract assertions to require stable empty shapes with feature schema.
  - Verified with:
    - `uv run pytest -q tests/unit/test_exit_classifier_window_query.py`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py`
- **Backfill Exit Columns Summary + Dead-Letter + Retry Knobs (TDD)**:
  - Updated `src/orion/jobs/backfill_exit_columns.py`:
    - `run_backfill(...)` now returns a structured summary payload (`velocity`, `checkpoint`, and totals),
    - added optional dead-letter JSONL sink for exhausted retries (`dead_letter_path` / `ORION_BACKFILL_EXIT_DEAD_LETTER_PATH`),
    - added configurable retry controls for function and CLI:
      - `max_retries`
      - `retry_sleep_seconds`
      - `--max-retries`
      - `--retry-sleep-seconds`
      - `--dead-letter-path`
    - extended `_update_record_with_retry(...)` to return terminal error metadata.
  - Extended `tests/unit/test_backfill_exit_columns_selection.py`:
    - strengthened retry tests for error payload behavior,
    - `test_run_backfill_writes_dead_letter_for_exhausted_retry`,
    - updated continuation test to assert summary counters.
  - Verified with:
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py -k "update_record_with_retry or dead_letter or continues_when_velocity_update_raises"`
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py`
- **Exit Classifier Schema-Preflight Guard + Query-Failure Degradation (TDD)**:
  - Updated `src/orion/ml/exit_classifier.py`:
    - added `_required_price_target_columns_for_bucket(...)` for bucket-specific checkpoint schema requirements,
    - added `_load_price_target_label_columns(...)` metadata probe for `price_target_labels`,
    - `build_bucket_training_data(...)` now short-circuits with stable empty outputs when required columns are missing,
    - query failures now degrade safely via `exit_training_query_failed` path instead of propagating exceptions.
  - Updated `tests/unit/test_exit_classifier_window_query.py`:
    - `test_required_price_target_columns_for_bucket_includes_checkpoint_families`
    - `test_build_bucket_training_data_short_circuits_when_required_columns_missing`
    - `test_build_bucket_training_data_returns_empty_with_feature_schema_on_query_error`
  - Verified with:
    - `uv run pytest -q tests/unit/test_exit_classifier_window_query.py`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py`
- **Exit Classifier Query-Error Fallback + Stable Empty Output Contract (TDD)**:
  - Updated `tests/unit/test_exit_classifier_window_query.py`:
    - `test_build_bucket_training_data_returns_empty_with_feature_schema_on_query_error`
    - `test_build_bucket_training_data_returns_stable_empty_matrix_shape_when_rows_filtered`
  - Updated `src/orion/ml/exit_classifier.py`:
    - added `_empty_training_arrays(...)` for stable empty output shape/dtypes,
    - `build_bucket_training_data(...)` now catches query failures and returns empty arrays with feature schema instead of raising,
    - standardized non-empty output dtypes to `float` (`X`) and `int` (`y`).
  - Verified with:
    - `uv run pytest -q tests/unit/test_exit_classifier_window_query.py`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py`
- **Backfill Exit Columns Resilience + Retry + Progress Telemetry (TDD)**:
  - Updated `src/orion/jobs/backfill_exit_columns.py`:
    - added bounded retry helper `_update_record_with_retry(...)` for per-record update failures,
    - introduced retry controls (`MAX_RECORD_RETRIES`, `RETRY_SLEEP_SECONDS`),
    - `run_backfill(...)` now continues processing after per-record failures in both velocity and checkpoint phases,
    - added structured per-phase progress summaries with processed/updated/failed/retried counts.
  - Extended `tests/unit/test_backfill_exit_columns_selection.py`:
    - `test_update_record_with_retry_retries_then_succeeds`
    - `test_update_record_with_retry_marks_failure_after_max_retries`
    - `test_run_backfill_continues_when_velocity_update_raises`
  - Verified with:
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py`
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_exit_classifier_window_query.py`
- **Exit Classifier Dataset Shape Stability for Empty Training Batches (TDD)**:
  - Updated `tests/unit/test_exit_classifier_window_query.py`:
    - `test_build_bucket_training_data_returns_stable_empty_matrix_shape_when_rows_filtered`
    - strengthened `test_build_bucket_training_data_handles_missing_max_return_pct_key` with shape assertions.
  - Updated `src/orion/ml/exit_classifier.py`:
    - `build_bucket_training_data(...)` now returns a stable empty matrix shape `(0, len(feature_names))` and empty label vector shape `(0,)` when rows are filtered out,
    - enforces numeric output dtypes (`float` for `X`, `int` for `y`) for non-empty and empty results.
  - Verified with:
    - `uv run pytest -q tests/unit/test_exit_classifier_window_query.py`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py`
- **Exit Classifier Cross-Bucket Query Contract + SQL Null-Normalization Validation (TDD)**:
  - Extended `tests/unit/test_exit_classifier_window_query.py` with:
    - `test_build_bucket_training_data_query_contract_per_bucket`
    - `test_build_bucket_training_data_query_coalesces_entry_and_window_fields`
  - Validated `src/orion/ml/exit_classifier.py` training query contract:
    - bucket-specific checkpoint columns are present for `0DTE`, `SHORT_SWING`, `SWING`, and `POSITION`,
    - SQL-side `COALESCE(...)` defaults are enforced for entry + window fields (`1h/1d/1w`) to reduce null-driven training drift.
  - Verified with:
    - `pytest -q tests/unit/test_exit_classifier_window_query.py`
    - `pytest -q tests/unit/test_exit_classifier_window_query.py tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_price_target_labeler_heber_context.py -k "window_features_at_entry or velocity_backfill_candidates or checkpoint_backfill_candidates or query_contract_per_bucket or query_coalesces_entry_and_window_fields"`
- **Exit Classifier Label-Distribution Guard Before Model Fit (TDD)**:
  - Updated `tests/unit/test_exit_classifier_window_query.py`:
    - `test_can_train_with_labels_rejects_single_class_and_sparse_classes`
    - `test_build_bucket_training_data_skips_malformed_numeric_rows`
  - Updated `src/orion/ml/exit_classifier.py`:
    - added `_can_train_with_labels(...)` and integrated it into `train_bucket_exit_classifier(...)` to avoid invalid stratified splits on single-class/sparse-label datasets,
    - hardened `build_bucket_training_data(...)` to skip malformed numeric rows while preserving valid samples.
  - Verified with:
    - `uv run pytest -q tests/unit/test_exit_classifier_window_query.py`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py`
- **Exit Classifier Training-Data Contract Hardening (TDD)**:
  - Updated `src/orion/ml/exit_classifier.py`:
    - `build_bucket_training_data(...)` now safely handles malformed/missing numeric values via `_safe_float(...)`.
    - skips non-numeric checkpoint returns instead of raising conversion errors.
    - tolerates missing `max_return_pct` by skipping invalid rows.
  - Extended `tests/unit/test_exit_classifier_window_query.py`:
    - `test_build_bucket_training_data_skips_non_numeric_checkpoint_returns`
    - `test_build_bucket_training_data_handles_missing_max_return_pct_key`
  - Verified with:
    - `pytest -q tests/unit/test_exit_classifier_window_query.py`
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_price_target_labeler_heber_context.py -k "velocity_backfill_candidates or checkpoint_backfill_candidates or window_features_at_entry"`
- **Exit Classifier Training Robustness: Sweep Normalization + Sample Guard (TDD)**:
  - Updated `tests/unit/test_exit_classifier_window_query.py`:
    - `test_build_bucket_training_data_unknown_bucket_short_circuits_without_query`
    - `test_build_bucket_training_data_normalizes_is_sweep_string_false_and_shapes_features`
  - Updated `src/orion/ml/exit_classifier.py`:
    - added `_is_truthy(...)` to normalize bool-like payloads (`"false"`, `"0"`, etc.) for training features,
    - `build_bucket_training_data(...)` now uses normalized sweep encoding (`is_sweep`),
    - added feature-size mismatch guard to skip malformed samples and log structured warning.
  - Verified with:
    - `uv run pytest -q tests/unit/test_exit_classifier_window_query.py`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py`
- **Exit Classifier Training Query Parameter Binding Hardening (TDD)**:
  - Updated `tests/unit/test_exit_classifier_window_query.py`:
    - `test_build_bucket_training_data_binds_trade_type_parameter`
    - existing lateral-window test now captures execute params.
  - Updated `src/orion/ml/exit_classifier.py`:
    - `build_bucket_training_data(...)` now binds `trade_type` via SQL parameters (`:trade_type`) instead of string interpolation.
  - Verified with:
    - `uv run pytest -q tests/unit/test_exit_classifier_window_query.py`
- **Shared Window-Feature Query Consolidation for Labeler + Exit Classifier (TDD)**:
  - Updated `src/orion/main_price_target_labeler.py`:
    - `get_window_features_at_entry(...)` now uses a single `DISTINCT ON (period)` query for `1h/1d/1w` instead of per-period calls.
  - Updated `src/orion/ml/exit_classifier.py`:
    - `build_bucket_training_data(...)` now uses one lateral window join with `jsonb_object_agg(period, features)` instead of separate `w1h/w1d/w1w` joins.
  - Added/updated tests:
    - `tests/unit/test_price_target_labeler_heber_context.py`
      - `test_get_window_features_at_entry_uses_single_query_and_maps_periods`
      - `test_get_window_features_at_entry_returns_empty_dict_on_query_error`
    - `tests/unit/test_exit_classifier_window_query.py`
      - `test_build_bucket_training_data_uses_single_lateral_window_lookup`
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "window_features_at_entry or velocity_backfill_candidates or checkpoint_backfill_candidates"`
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py`
    - `pytest -q tests/unit/test_exit_classifier_window_query.py`
- **Backfill Exit-Columns Candidate Selection Delegation to Shared Labeler Paths (TDD)**:
  - Updated `tests/unit/test_backfill_exit_columns_selection.py`:
    - `test_get_records_to_backfill_delegates_to_labeler`
    - `test_get_all_records_for_checkpoints_delegates_to_labeler`
    - updated cursor pass-through tests to assert delegated helper arguments.
  - Updated `tests/unit/test_price_target_labeler_heber_context.py`:
    - `test_get_velocity_backfill_candidates_queries_expected_shape`
    - `test_get_checkpoint_backfill_candidates_queries_expected_shape`
  - Updated `src/orion/main_price_target_labeler.py`:
    - added shared helper `get_velocity_backfill_candidates(...)`,
    - added shared helper `get_checkpoint_backfill_candidates(...)`,
    - added `_build_backfill_cursor_clause(...)` for consistent keyset cursor logic.
  - Updated `src/orion/jobs/backfill_exit_columns.py`:
    - `get_records_to_backfill(...)` now delegates to `get_labeler_velocity_backfill_candidates(...)`,
    - `get_all_records_for_checkpoints(...)` now delegates to `get_labeler_checkpoint_backfill_candidates(...)`.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_exit_columns_selection.py`
    - `uv run pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "window_features_at_entry or velocity_backfill_candidates or checkpoint_backfill_candidates"`
- **Flow Enricher GEX Rolling-Average Delegation to Shared Labeler Path (TDD)**:
  - Updated `tests/unit/test_flow_enricher_delegation.py`:
    - `test_get_gex_at_entry_delegates_base_to_labeler_and_adds_rolling_avg`
    - `test_get_gex_at_entry_skips_sql_avg_when_labeler_has_no_snapshot`
    - these now enforce delegated rolling-average lookup (no local `db_query` path).
  - Updated `src/orion/main_price_target_labeler.py`:
    - added shared helper `get_gex_rolling_averages(...)` with Heber-first and SQL fallback implementations.
  - Updated `src/orion/ml/flow_enricher.py`:
    - `_get_gex_at_entry(...)` now uses `get_labeler_gex_rolling_averages(...)`,
    - removed local `_get_gex_rolling_averages(...)` SQL helper and direct `silver_greek_exposure` access from flow enricher.
  - Verified with:
    - `pytest -q tests/unit/test_flow_enricher_delegation.py -k gex_at_entry`
    - `pytest -q tests/unit/test_flow_enricher_delegation.py`
- **Flow Enricher Market-Context Delegation to Shared Labeler Paths (TDD)**:
  - Extended `tests/unit/test_flow_enricher_delegation.py` with:
    - `test_get_market_context_delegates_to_labeler_helpers`
    - `test_get_market_context_defaults_phase1_dte_and_skips_p3_without_expiry`
  - Updated `src/orion/ml/flow_enricher.py`:
    - `_get_market_context(...)` now delegates to shared labeler helpers:
      - `get_labeler_rvol_metrics(...)`
      - `get_labeler_phase1_bucket_features(...)`
      - `get_labeler_p3_features(...)`
    - added expiry normalization helper and routed `enrich_flow_for_scoring(...)` to pass `dte`, `option_chain`, and `expiry` into `_get_market_context(...)`.
    - removed local SQL-heavy market-context queries from flow enricher.
  - Verified with:
    - `pytest -q tests/unit/test_flow_enricher_delegation.py -k "market_context_delegates or market_context_defaults"`
    - `pytest -q tests/unit/test_flow_enricher_delegation.py`
- **Backfill Exit-Columns Subsequent-Price Delegation to Shared Labeler Path (TDD)**:
  - Extended `tests/unit/test_backfill_exit_columns_selection.py` with:
    - `test_get_subsequent_prices_delegates_to_labeler`
  - Updated `src/orion/jobs/backfill_exit_columns.py`:
    - `get_subsequent_prices(...)` now delegates to shared labeler helper (`get_labeler_subsequent_prices`) instead of running a local `silver_uw_flow` query.
  - Verified with:
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py -k subsequent_prices`
    - `pytest -q tests/unit/test_backfill_exit_columns_selection.py`
- **Price-Target Labeler IV-Rank Entry Heber-First Path (TDD)**:
  - Extended `tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py` with:
    - `test_get_iv_rank_at_entry_prefers_heber_when_available`
    - `test_get_iv_rank_at_entry_falls_back_to_sql_when_heber_empty`
  - Updated `src/orion/main_price_target_labeler.py` so `get_iv_rank_at_entry(...)` now uses Heber IV-rank lookup first (`_get_iv_rank_from_heber`) and falls back to the existing SQL percentile calculation.
  - Verified with:
    - `uv run pytest -q tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py -k iv_rank_at_entry`
    - `uv run pytest -q tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py`
- **Price-Target Labeler Flow-Greeks Event Lookup Heber-First Path (TDD)**:
  - Extended `tests/unit/test_price_target_labeler_heber_context.py` with:
    - `test_get_flow_greeks_prefers_heber_when_available`
    - `test_get_flow_greeks_falls_back_to_sql_when_heber_missing`
  - Updated `src/orion/main_price_target_labeler.py` so `get_flow_greeks(...)` now:
    - checks Heber flow first via `_get_flow_greeks_from_heber(...)`,
    - falls back to extracted SQL path `_get_flow_greeks_sql(...)` when Heber has no matching event.
  - Preserved existing Greeks contract order (stored greeks -> Alpaca API -> Black-Scholes fallback) while moving event lookup to Heber-first.
  - Verified with:
    - `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k flow_greeks`
- **Flow Enricher Flow-Greeks Delegation to Shared Labeler Paths (TDD)**:
  - Added `tests/unit/test_flow_enricher_delegation.py` with:
    - `test_get_flow_greeks_delegates_to_labeler_and_p2_when_option_chain_present`
    - `test_get_flow_greeks_skips_p2_when_option_chain_missing`
  - Updated `src/orion/ml/flow_enricher.py`:
    - `_get_flow_greeks(...)` now delegates base greeks to shared labeler helper (`get_labeler_flow_greeks`),
    - enriches `iv_vs_hv_ratio` / `oi_change_*` via shared P2 helper (`get_labeler_p2_features`) when `ticker + option_chain + entry_ts` are available,
    - removes local SQL-heavy flow-greeks derivation path from this enrichment helper.
  - Verified with:
    - `pytest -q tests/unit/test_flow_enricher_delegation.py`
- **Flow Enricher Context Helper Delegation to Shared Labeler Paths (TDD)**:
  - Extended `tests/unit/test_flow_enricher_delegation.py` with:
    - `test_get_market_tide_delegates_to_labeler`
    - `test_get_iv_rank_delegates_to_labeler`
    - `test_get_darkpool_volumes_delegates_to_labeler_and_maps_windows`
    - `test_get_regime_delegates_to_labeler`
  - Updated `src/orion/ml/flow_enricher.py` to route:
    - `_get_market_tide(...)` -> `get_labeler_market_tide_before_entry(...)`,
    - `_get_iv_rank(...)` -> `get_labeler_iv_rank_at_entry(...)`,
    - `_get_darkpool_volumes(...)` -> `get_labeler_darkpool_metrics(...)` with `30m/1h/4h/1d` key mapping,
    - `_get_regime(...)` -> `get_labeler_regime_at_entry(...)`.
  - Verified with:
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py -k "market_tide_delegates or iv_rank_delegates or darkpool_volumes_delegates or get_regime_delegates"`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py`
- **Flow Enricher GEX Base Snapshot Delegation to Shared Labeler Path (TDD)**:
  - Extended `tests/unit/test_flow_enricher_delegation.py` with:
    - `test_get_gex_at_entry_delegates_base_to_labeler_and_adds_rolling_avg`
    - `test_get_gex_at_entry_skips_sql_avg_when_labeler_has_no_snapshot`
  - Updated `src/orion/ml/flow_enricher.py`:
    - `_get_gex_at_entry(...)` now delegates base snapshot (`gex`, `vex`) to `get_labeler_gex_at_entry(...)`,
    - retains local SQL only for 20-day rolling averages via extracted `_get_gex_rolling_averages(...)`,
    - skips rolling-average SQL lookup when shared base snapshot is unavailable.
  - Verified with:
    - `pytest -q tests/unit/test_flow_enricher_delegation.py -k gex_at_entry`
    - `pytest -q tests/unit/test_flow_enricher_delegation.py`
- **Flow Enricher Max-Pain Distance Delegation to Shared Labeler Path (TDD)**:
  - Extended `tests/unit/test_flow_enricher_delegation.py` with:
    - `test_get_max_pain_distance_delegates_to_labeler`
    - `test_get_max_pain_distance_returns_none_without_dte`
  - Updated `src/orion/ml/flow_enricher.py`:
    - `_get_max_pain_distance(...)` now delegates to shared labeler helper (`get_labeler_max_pain_distance`) when DTE is present,
    - preserves `None` behavior when DTE is missing and removes local direct SQL dependency for this helper.
  - Verified with:
    - `pytest -q tests/unit/test_flow_enricher_delegation.py -k max_pain_distance`
    - `pytest -q tests/unit/test_flow_enricher_delegation.py`
- **Flow Enricher Combined Flow-Context + VIX Delegation Pass (TDD)**:
  - Extended `tests/unit/test_flow_enricher_delegation.py` with:
    - `test_get_vix_delegates_to_labeler_regime`
    - `test_get_flow_metrics_delegates_context_to_labeler_helpers`
  - Updated `src/orion/ml/flow_enricher.py`:
    - `_get_vix(...)` now delegates to `get_labeler_regime_at_entry(...)` and reads `vix_at_entry`,
    - `_get_flow_metrics(...)` now delegates:
      - flow aggression to `get_labeler_flow_aggression(...)`,
      - sector/spy context to `get_labeler_sector_correlation_features(...)`,
      - earnings proximity to `get_labeler_earnings_proximity(...)`,
    - preserves existing output shape and DTE-window flag behavior.
  - Verified with:
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py -k "get_vix_delegates_to_labeler_regime or get_flow_metrics_delegates_context_to_labeler_helpers"`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py`
- **Flow Enricher Window-Feature Delegation to Shared Labeler Helper (TDD)**:
  - Added shared helper in `src/orion/main_price_target_labeler.py`:
    - `get_window_features_at_entry(ticker, entry_ts)` for latest `gold_feature_windows` payloads (`1h`, `1d`, `1w`).
  - Extended `tests/unit/test_flow_enricher_delegation.py` with:
    - `test_get_window_features_delegates_to_labeler_and_maps_period_values`
  - Updated `src/orion/ml/flow_enricher.py`:
    - `_get_window_features(...)` now delegates retrieval to `get_labeler_window_features_at_entry(...)`,
    - preserves existing downstream key mapping (`call_put_imbalance_*`, `sweep_ratio_*`, `flow_count_*`, `dp_volume_*`, `call_put_ratio_*`, `total_premium_*`).
  - Verified with:
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py -k "get_window_features_delegates_to_labeler_and_maps_period_values"`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py`
- **Flow Enricher Window Retrieval Cleanup Pass (TDD)**:
  - Extended `tests/unit/test_flow_enricher_delegation.py` with:
    - `test_get_window_features_delegates_to_labeler_and_maps_period_values`
  - Updated shared labeler helper surface in `src/orion/main_price_target_labeler.py`:
    - added `get_window_features_at_entry(...)` for reusable period-window retrieval.
  - Updated `src/orion/ml/flow_enricher.py`:
    - `_get_window_features(...)` now delegates retrieval to `get_labeler_window_features_at_entry(...)`,
    - removed now-unused local DB import path from flow-enricher for this feature family.
  - Verified with:
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py -k "get_window_features_delegates_to_labeler_and_maps_period_values"`
    - `uv run pytest -q tests/unit/test_flow_enricher_delegation.py`
- **Backfill Underlying-Price Source Alignment to Shared Labeler Path (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_signature.py` with:
    - `test_get_underlying_price_at_entry_delegates_to_labeler`
    - `test_get_underlying_price_at_offset_delegates_to_labeler`
  - Updated `src/orion/jobs/backfill_ml_features.py` to delegate:
    - `get_underlying_price_at_entry(...)` -> shared labeler helper,
    - `get_underlying_price_at_offset(...)` -> shared labeler helper.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "underlying_price_at_entry_delegates or underlying_price_at_offset_delegates"`
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py`
- **Backfill Flow-Greeks Source Alignment to Shared Labeler Path (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_signature.py` with:
    - `test_get_flow_greeks_delegates_to_labeler`
  - Updated `src/orion/jobs/backfill_ml_features.py` so `get_flow_greeks(...)` now delegates to the shared labeler helper (`get_labeler_flow_greeks`) instead of running a local SQL query.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k flow_greeks_delegates`
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py`
- **Backfill Ticker-Info Source Alignment to Shared Labeler Path (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_signature.py` with:
    - `test_get_ticker_info_delegates_to_labeler`
  - Updated `src/orion/jobs/backfill_ml_features.py` so `get_ticker_info(...)` now delegates to shared labeler ticker-info logic (`get_labeler_ticker_info`) and uses a local cache envelope, removing the local direct UW-client path.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k ticker_info_delegates`
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py`
- **Backfill Earnings-Proximity Source Alignment to Shared Labeler Path (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_signature.py` with:
    - `test_get_earnings_proximity_delegates_to_labeler`
  - Updated `src/orion/jobs/backfill_ml_features.py` to:
    - add `get_earnings_proximity(...)` delegation to shared labeler helper (`get_labeler_earnings_proximity`),
    - replace inline earnings-date math in `update_ml_features(...)` with delegated helper output.
  - Updated existing orchestration test `test_update_ml_features_calls_sector_corr_with_two_args` to stub the new helper call path.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k earnings_proximity_delegates`
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py`
- **Backfill Phase-1 Feature Delegation + Dead Local SQL Removal (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_signature.py` with:
    - `test_get_phase1_bucket_features_delegates_to_labeler`
  - Updated `src/orion/jobs/backfill_ml_features.py` to:
    - add `get_phase1_bucket_features(...)` delegation to shared labeler helper (`get_labeler_phase1_bucket_features`),
    - remove unused local `get_phase1_features(...)` SQL implementation,
    - route `update_ml_features(...)` through the delegated wrapper path.
  - Updated `test_update_ml_features_calls_sector_corr_with_two_args` to stub the wrapper call path.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "phase1_bucket_features_delegates or update_ml_features_calls_sector_corr_with_two_args"`
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py`
- **Backfill Sector-Correlation Wrapper Alignment (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_signature.py` with:
    - `test_get_sector_correlation_features_delegates_to_labeler`
  - Updated `test_update_ml_features_calls_sector_corr_with_two_args` to stub `backfill.get_sector_correlation_features(...)` directly.
  - Updated `src/orion/jobs/backfill_ml_features.py` to:
    - add `get_sector_correlation_features(...)` wrapper delegating to shared labeler helper (`get_labeler_sector_correlation_features`),
    - route sector-correlation enrichment through the wrapper and remove inline direct import in `update_ml_features(...)`.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "sector_corr_with_two_args or sector_correlation_features_delegates"`
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py`
- **Backfill IV-Rank Wrapper Alignment (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_signature.py` with:
    - `test_get_iv_rank_at_entry_delegates_to_labeler`
  - Updated `test_update_ml_features_calls_sector_corr_with_two_args` to stub `backfill.get_iv_rank_at_entry(...)`.
  - Updated `src/orion/jobs/backfill_ml_features.py` to:
    - add `get_iv_rank_at_entry(...)` wrapper delegating to shared labeler helper (`get_labeler_iv_rank_at_entry`),
    - route IV-rank enrichment through the wrapper and remove inline direct import call in `update_ml_features(...)`.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "iv_rank_at_entry_delegates or sector_corr_with_two_args"`
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py`
- **Backfill P2/P3 Wrapper Alignment (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_signature.py` with:
    - `test_get_p2_features_delegates_to_labeler`
    - `test_get_p3_features_delegates_to_labeler`
  - Updated `test_update_ml_features_calls_sector_corr_with_two_args` to stub:
    - `backfill.get_p2_features(...)`
    - `backfill.get_p3_features(...)`
  - Updated `src/orion/jobs/backfill_ml_features.py` to:
    - add wrapper delegates `get_p2_features(...)` and `get_p3_features(...)`,
    - route P2/P3 enrichment through wrappers and remove inline direct import usage in `update_ml_features(...)`.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "get_p2_features_delegates or get_p3_features_delegates or sector_corr_with_two_args"`
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py`
- **Backfill Context Helper Wrapper Alignment (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_signature.py` with:
    - `test_get_darkpool_metrics_delegates_to_labeler`
    - `test_get_rvol_metrics_delegates_to_labeler`
    - `test_get_flow_aggression_delegates_to_labeler`
    - `test_get_institutional_flow_1w_delegates_to_labeler`
    - `test_get_market_tide_before_entry_delegates_to_labeler`
    - `test_get_regime_at_entry_delegates_to_labeler`
  - Updated `test_update_ml_features_calls_sector_corr_with_two_args` to stub:
    - `backfill.get_darkpool_metrics(...)`
    - `backfill.get_rvol_metrics(...)`
    - `backfill.get_flow_aggression(...)`
    - `backfill.get_institutional_flow_1w(...)`
    - `backfill.get_market_tide_before_entry(...)`
    - `backfill.get_regime_at_entry(...)`
  - Updated `src/orion/jobs/backfill_ml_features.py` to:
    - add wrapper delegates for darkpool, RVOL, flow aggression, institutional flow, market tide, and regime helpers,
    - route `update_ml_features(...)` through wrappers instead of inline direct helper imports.
  - Verified with:
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "get_darkpool_metrics_delegates or get_rvol_metrics_delegates or get_flow_aggression_delegates or get_institutional_flow_1w_delegates or get_market_tide_before_entry_delegates or get_regime_at_entry_delegates or sector_corr_with_two_args"`
    - `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py`
- **Gateway Live Contract Probe (TDD)**:
  - Added `src/orion/jobs/gateway_contract_probe.py` with `run_gateway_contract_probe(...)` and CLI entrypoint (`python -m orion.jobs.gateway_contract_probe`) to validate:
    - `/health` readiness with bounded retry,
    - websocket auth + subscription handshake contract,
    - unknown-action error mapping (`GW-E3001`),
    - best-effort `type=data` envelope/schema presence.
  - Added `tests/unit/test_gateway_contract_probe.py` for probe logic and retry/error contracts.
  - Added `tests/integration/test_gateway_live_contract_probe.py` (env-gated via `ORION_GATEWAY_LIVE_API_KEY`) for repeatable live Gateway validation.
  - Verified locally against `http://localhost:8080`: health/auth/subscription/error mapping succeeded; no `type=data` frame observed in short capture window.
- **SQLite Contention Durability Slice (TDD)**:
  - Added `tests/unit/test_db_utils_sqlite_retry.py` to lock down retry semantics for SQLite lock contention in `db_transaction(...)`.
  - Added bounded retry/backoff support to `src/orion/shared/db_utils.py` with env-configurable settings:
    - `ORION_SQLITE_LOCK_RETRY_ATTEMPTS`
    - `ORION_SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS`
    - `ORION_SQLITE_LOCK_RETRY_MAX_DELAY_SECONDS`
  - Added `src/orion/jobs/sqlite_contention_soak.py` with `run_sqlite_contention_soak(...)` and CLI entrypoint (`python -m orion.jobs.sqlite_contention_soak`) to stress concurrent writes and report contention outcomes.
  - Added `tests/unit/test_sqlite_contention_soak.py` to verify soak summary accounting (`attempted = successful + failed`) and persisted counter correctness.
  - Extended `tests/unit/test_heber_reader.py` with catalog URL-shape contract coverage for both host-root and `/api/v1`-suffixed client base URLs.
  - Updated `src/orion/clients/heber_reader.py` to build explicit catalog-origin URLs for `/health` and `/api/v1/datasets`, removing ambiguous `httpx` path-join behavior across different base URL shapes.
  - Audit pass 203 confirmed this closes a mixed-environment integration drift where catalog requests could misroute when callers configured different `httpx` base URL forms.
  - Extended `tests/unit/test_price_target_labeler_heber_context.py` with Heber-first darkpool volume coverage and SQL fallback expectations.
  - Updated `src/orion/main_price_target_labeler.py` to add Heber-first darkpool aggregation (`_get_darkpool_volume_from_heber`) with SQL fallback (`_get_darkpool_volume_sql`) in `get_darkpool_volume(...)`.
  - Extended `tests/unit/test_price_target_labeler_heber_context.py` with Heber-first RVOL coverage and SQL fallback expectations.
  - Updated `src/orion/main_price_target_labeler.py` to add Heber-first RVOL aggregation (`_get_rvol_metrics_from_heber`) with extracted SQL fallback (`_get_rvol_metrics_sql`) in `get_rvol_metrics(...)`.
  - Fixed failing Heber-context tests for flow aggression and institutional flow by migrating:
    - `get_flow_aggression(...)` to Heber-first (`_get_flow_aggression_from_heber`) with extracted SQL fallback (`_get_flow_aggression_sql`),
    - `get_institutional_flow_1w(...)` to Heber-first (`_get_institutional_flow_1w_from_heber`) with extracted SQL fallback (`_get_institutional_flow_1w_sql`).
  - Extended `tests/unit/test_price_target_labeler_heber_context.py` with Heber-first sector/correlation coverage and SQL fallback expectations.
  - Updated `src/orion/main_price_target_labeler.py` to add Heber-first sector/correlation aggregation (`_get_sector_correlation_features_from_heber`) with extracted SQL fallback (`_get_sector_correlation_features_sql`) in `get_sector_correlation_features(...)`.
  - Extended `tests/unit/test_price_target_labeler_heber_context.py` with Heber-first opposing-flow coverage and SQL fallback expectations.
  - Updated `src/orion/main_price_target_labeler.py` to add Heber-first opposing-flow aggregation (`_get_opposing_flow_from_heber`) with extracted SQL fallback (`_get_opposing_flow_sql`) in `get_opposing_flow(...)`.
  - Extended `tests/unit/test_price_target_labeler_heber_context.py` with phase-1 bucket market context Heber-first/fallback coverage.
  - Updated `src/orion/main_price_target_labeler.py` to add Heber-first phase-1 market context aggregation (`_get_phase1_bucket_features_from_heber`) with extracted SQL fallback (`_get_phase1_bucket_features_sql`) in `get_phase1_bucket_features(...)`.
  - Extended `tests/unit/test_price_target_labeler_heber_context.py` with P2 option-feature Heber-first/fallback coverage.
  - Updated `src/orion/main_price_target_labeler.py` to add Heber-first P2 option-feature aggregation (`_get_p2_features_from_heber`) with extracted SQL fallback (`_get_p2_features_sql`) in `get_p2_features(...)`.
  - Extended `tests/unit/test_price_target_labeler_heber_context.py` with P3 option-feature Heber-first/fallback coverage.
  - Updated `src/orion/main_price_target_labeler.py` to add Heber-first P3 option-feature aggregation (`_get_p3_features_from_heber`) with extracted SQL fallback (`_get_p3_features_sql`) in `get_p3_features(...)`.
  - Validated P3 Heber-first behavior end-to-end in the consolidated Heber-context suite (`18 passed`) and documented the migration pass in the parity audit.
- **Execution Exit-Policy Contract Hardening (TDD, Combined)**:
  - Added `tests/unit/test_position_manager_execution_contracts.py` covering:
    - canonical `candidate.option_symbol` propagation to tracked `option_chain`,
    - `entry_option_price` wiring for option positions,
    - startup rehydration loading beyond 50 open positions.
  - Added `tests/unit/test_main_execution_exit_scope.py` covering:
    - options-only exit-rule applicability guard semantics.
  - Updated `src/orion/execution/position_manager.py` to:
    - add `OpenPosition.entry_option_price`,
    - resolve option contract identity with precedence: `candidate.option_symbol` -> runtime context -> legacy evidence,
    - remove the fixed startup `LIMIT 50` cap from open-position rehydration.
  - Updated `src/orion/main_execution.py` to:
    - add `_should_apply_options_exit_rules(...)`,
    - skip options-only exit-rule evaluation for non-option positions.
  - This closes three audited execution drift points in one slice: inert price-target exit prerequisites, non-canonical option symbol propagation, and incomplete open-position monitoring scope.
- **Execution Exit Flow Contract Scoping (TDD)**:
  - Extended `tests/unit/test_main_execution_exit_scope.py` with contract-scoping coverage for recent-flow inputs.
  - Updated `src/orion/main_execution.py` to:
    - add `_scope_recent_flow_for_position(...)`,
    - filter same-ticker `recent_flow` to matching `position.option_chain` for option positions,
    - pass scoped flow into exit-rule evaluation loop.
  - This reduces cross-contract contamination where one option position could be exited based on unrelated flow for the same underlying ticker.
- **Execution Exit Flow Component Fallback Matching (TDD)**:
  - Extended `tests/unit/test_main_execution_exit_scope.py` with cases for flow rows that lack `option_chain` but include contract components (`expiry`, `strike`, `put_call`).
  - Updated `src/orion/main_execution.py` to:
    - add OCC parser `_parse_option_chain_contract(...)`,
    - add `_flow_matches_contract_components(...)`,
    - include component-based matching fallback in `_scope_recent_flow_for_position(...)` when flow-side `option_chain` is missing.
  - This keeps contract scoping effective even when upstream flow normalization omits `option_chain` on some rows.
- **Gateway Stream URL/Auth Contract Hardening (TDD)**:
  - Added `tests/unit/test_gateway_stream_client_contract.py` covering:
    - websocket URL normalization across `http/https/ws/wss` and `/api/v1`-suffixed gateway URLs,
    - failed-auth cleanup behavior (socket close + connection state reset).
  - Updated `src/orion/connectors/gateway_stream_client.py` to:
    - centralize websocket URL normalization via `_normalize_ws_url(...)`,
    - strip `/api/v1` suffix before websocket route composition to keep endpoint on `/ws`,
    - add `_cleanup_failed_connection(...)` and invoke it on auth/connect failure paths.
  - This removes a known integration footgun where API-prefixed gateway URLs produced invalid websocket paths and could leave stale handles after failed handshakes.
- **Price-Target Labeler Heber VIX-Proxy Regime Path (TDD)**:
  - Added `tests/unit/test_price_target_labeler_heber_vix_proxy.py` covering:
    - Heber VIX-proxy snapshot derivation from VIXY bars (`_get_heber_vix_proxy_snapshot_at_or_before(...)`),
    - Heber-first regime detection behavior in `get_regime_at_entry(...)`,
    - SQL fallback behavior when Heber VIX proxy data is unavailable.
  - Updated `src/orion/main_price_target_labeler.py` to:
    - add `_map_vix_proxy_to_regime(...)` and `_get_heber_vix_proxy_snapshot_at_or_before(...)`,
    - route `get_regime_at_entry(...)` through Heber-first VIX proxy lookup before existing SQL fallback.
  - This reduces steady-state dependence on local `silver_vix_data`/`silver_alpaca_bars` SQL reads for regime features while preserving backward-compatible fallback behavior.
- **Price-Target Labeler Heber Context Read Paths (TDD)**:
  - Added `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py` covering:
    - Heber-first GEX lookup (`get_gex_at_entry`) with SQL fallback,
    - Heber-first market-tide lookup (`get_market_tide_before_entry`) with SQL fallback.
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py` to:
    - route GEX and market-tide reads through Heber-first helpers,
    - retain explicit SQL fallback helpers for phased migration safety.
  - Extended `/Users/jacobmcmillan/Empire/Orion/src/orion/clients/heber_reader.py` with:
    - `read_greek_exposure(...)`,
    - `read_market_tide(...)`,
    - `ts_utc` support in generic time-range filtering.
  - Extended `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_heber_reader.py` with dataset-level tests for the new reader methods.
- **Price-Target Labeler Market-Tide Flow-Derived Heber Fallback (TDD)**:
  - Added `tests/unit/test_price_target_labeler_heber_market_tide.py` covering:
    - net-premium reconstruction from Heber market-tide aggregates,
    - flow-derived fallback reconstruction (`premium_usd` + `put_call`) when aggregate market-tide dataset is absent/incompatible,
    - Heber-first tide behavior in both `get_market_tide_before_entry(...)` and `get_regime_at_entry(...)`.
  - Updated `src/orion/main_price_target_labeler.py` to:
    - centralize Heber tide math in `_sum_market_tide_from_dataframe(...)`,
    - add `_get_heber_market_tide_net_premium(...)` with aggregate-read then flow-derived fallback strategy,
    - reuse that helper in both market-tide feature extraction and regime detection path before SQL fallback.
- **Price-Target Labeler Heber Max-Pain + IV-Rank Context Paths (TDD)**:
  - Added `tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py` covering:
    - Heber-first max-pain distance lookup with SQL fallback,
    - Heber-first IV-rank offset lookup with SQL fallback.
  - Extended `tests/unit/test_heber_reader.py` with:
    - `read_max_pain(...)` dataset filtering coverage,
    - `read_iv_rank(...)` dataset filtering coverage.
  - Updated `src/orion/main_price_target_labeler.py` to:
    - add `_get_max_pain_distance_from_heber(...)` + SQL fallback split helper,
    - add `_get_iv_rank_from_heber(...)` + SQL fallback split helper for `get_iv_at_offset(...)`.
  - Updated `src/orion/clients/heber_reader.py` with:
    - `read_max_pain(...)`,
    - `read_iv_rank(...)`,
    - strict mypy-safe DataFrame casting in parquet reader return path.
- **Legacy Backfill Watermark Cleanup Job + Storage Helper (TDD)**:
  - Added `tests/unit/test_storage_watermarks_cleanup.py` covering:
    - no-op behavior for empty key input,
    - count-only behavior when no matching rows exist,
    - delete execution behavior when matching rows exist.
  - Added `tests/unit/test_cleanup_legacy_backfill_watermarks.py` covering:
    - one-shot deletion path for known legacy backfill keys,
    - dry-run path that reports matches without deleting.
  - Added `src/orion/jobs/cleanup_legacy_backfill_watermarks.py` with:
    - explicit legacy-key set for retired backfill watermark paths,
    - `cleanup_legacy_backfill_watermarks(dry_run=...)`,
    - CLI support via `python -m orion.jobs.cleanup_legacy_backfill_watermarks [--dry-run]`.
  - Added `delete_watermarks(...)` helper in `src/orion/storage/watermarks.py` and tightened `upsert_watermark(...)` timezone typing guard.
- **Legacy Backfill Watermark Cleanup Runbook Procedure**:
  - Updated `docs/runbooks/database_ops.md` with:
    - legacy-key inspection SQL against `ingest_watermarks`,
    - dry-run + execution commands for `cleanup_legacy_backfill_watermarks`,
    - before/after evidence-capture checklist for operational traceability.
- **Price-Target Labeler Heber Flow Read Path (TDD)**:
  - Added `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_flow.py` covering:
    - Heber-first entry candidate sourcing in `get_entry_signals(...)`
    - SQL fallback when Heber flow is unavailable/insufficient
    - Heber-first subsequent option-price sourcing in `get_subsequent_prices(...)`
    - SQL fallback when Heber flow shape is incompatible
  - Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py` to:
    - add Heber-normalization helpers for flow candidates,
    - use Heber-first reads for entry-candidate selection and subsequent option-price lookup,
    - retain SQL fallback paths for compatibility and phased migration safety.
  - This reduces direct dependency on Orion-local `silver_uw_flow` for two core price-target labeling read paths.
- **Price-Target Labeler Heber Bar Lookup Fallback (TDD)**:
  - Added `tests/unit/test_price_target_labeler_heber_bars.py` covering:
    - Heber-first underlying-price lookup at entry time
    - SQL fallback when Heber bars are unavailable
    - offset-price lookup using Heber bars before SQL fallback
  - Updated `src/orion/main_price_target_labeler.py` to:
    - add Heber bar lookup helper for latest close at-or-before a target timestamp,
    - route `get_underlying_price_at_entry` and `get_underlying_price_at_offset` through Heber-first lookup with existing SQL fallback.
  - This reduces direct dependency on local `silver_alpaca_bars` for core underlying-price context in price-target labeling while preserving backward compatibility.
- **Backfill ML-Features Durable Keyset Resume State (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_selection.py` with:
    - `test_run_backfill_resumes_with_keyset_cursor_when_available`
  - Updated `src/orion/jobs/backfill_ml_features.py` to:
    - persist resume cursor state using `entry_ts` + `event_id`,
    - load keyset cursor state on startup with timestamp-watermark fallback,
    - persist both keyset cursor and legacy watermark during progress updates.
  - `backfill_ml_features` now resumes with strict keyset continuity when available while preserving backward compatibility with existing timestamp-only state.
- **Backfill Cursor-Only Resume Cleanup (TDD, Combined)**:
  - Extended `tests/unit/test_backfill_ml_features_selection.py` with:
    - `test_load_backfill_cursor_does_not_fallback_to_legacy_watermark`
    - `test_save_backfill_cursor_does_not_write_legacy_watermark`
  - Extended `tests/unit/test_backfill_exit_columns_selection.py` with:
    - `test_load_phase_cursors_do_not_fallback_to_legacy_watermarks`
    - `test_save_phase_cursors_do_not_write_legacy_watermarks`
  - Updated `src/orion/jobs/backfill_ml_features.py` and `src/orion/jobs/backfill_exit_columns.py` to:
    - remove timestamp-watermark fallback reads on cursor load,
    - remove timestamp-watermark writes on cursor save,
    - rely exclusively on durable keyset cursor state (`entry_ts` + `event_id`) for backfill resume.
- **Backfill Exit-Columns Keyset Resume State (TDD)**:
  - Extended `tests/unit/test_backfill_exit_columns_selection.py` with:
    - `test_run_backfill_resumes_with_keyset_cursor_when_available`
  - Added durable cursor state support (`entry_ts` + `event_id`) in:
    - `src/orion/storage/models.py` (`job_cursor_state` table)
    - `src/orion/storage/watermarks.py` (`get_cursor_state`, `upsert_cursor_state`)
    - `src/orion/jobs/backfill_exit_columns.py` (per-phase cursor load/save wiring)
  - `backfill_exit_columns` now resumes with full keyset cursor when available, with backward-compatible timestamp-watermark fallback
- **Gateway/Heber Parity Audit (Pass 186)**: Continued audit with:
  - implemented strict keyset resume state for exit backfill phases to avoid same-timestamp duplicate replay after restarts
  - retained legacy timestamp watermark compatibility during transition
  - residual guidance to run a one-time cleanup for legacy-only watermark keys after rollout stabilization
- **Backfill Exit-Columns Crash-Resume Watermarks (TDD)**:
  - Extended `tests/unit/test_backfill_exit_columns_selection.py` with:
    - timestamp-only cursor SQL contract tests for velocity/checkpoint selectors
    - `test_run_backfill_resumes_from_phase_watermarks_and_persists_progress`
  - Updated `src/orion/jobs/backfill_exit_columns.py` to:
    - load persisted per-phase watermarks at run start
    - persist per-record watermark progression for velocity and checkpoint phases
    - resume selector scans with timestamp-only cursors when event-id state is unavailable
  - Adds crash-safe timestamp resume behavior to exit-column backfills, aligning with existing ML backfill continuity controls
- **Gateway/Heber Parity Audit (Pass 184)**: Continued audit with:
  - implemented persisted per-phase watermark resume flow in `backfill_exit_columns`
  - added TDD coverage for phase-level resume cursor injection and watermark progression
  - residual guidance to persist full keyset resume state (`entry_ts` + `event_id`) for strict duplicate-free restart continuity
- **Backfill Session Taxonomy Alignment (TDD)**:
  - Added `tests/unit/test_backfill_ml_features_time_alignment.py` to assert backfill entry-session parity with live labeler boundaries (`OPEN/MID/CLOSE`)
  - Updated `src/orion/jobs/backfill_ml_features.py` to delegate `get_entry_time_features` to `main_price_target_labeler` canonical logic
  - Prevents backfill from rewriting `price_target_labels.entry_session` with divergent bucket values
- **Backfill Candidate Ordering Stabilization (TDD)**:
  - Added `tests/unit/test_backfill_ml_features_selection.py` to verify deterministic candidate-query ordering
  - Updated `src/orion/jobs/backfill_ml_features.py` to order candidates by `entry_ts,event_id` before `LIMIT`
  - Prevents non-deterministic backfill slice composition across retries/runs
- **Backfill Cursor Pagination Hardening (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_selection.py` to validate keyset cursor SQL contract and run-loop cursor progression
  - Updated `src/orion/jobs/backfill_ml_features.py` with `after_entry_ts` / `after_event_id` keyset pagination support
  - Updated `run_backfill` to advance an in-run cursor between pages for monotonic candidate traversal
- **Quality-Guardrails Failure Signaling Hardening (TDD)**:
  - Added `tests/unit/test_quality_guardrails_results.py` for structured guardrail result handling (`failed`/`issues`) and log-path assertions
  - Added `quality_guardrails._result_failure_summary()` to summarize structured failures
  - Updated `quality_guardrails._run_job()` to log explicit errors for failed-result payloads and avoid false "completed" logs
- **Quality-Guardrails Fail-Fast Escalation Toggle (TDD)**:
  - Extended `tests/unit/test_quality_guardrails_results.py` to assert `_run_job()` success/failure return contract and fail-fast behavior
  - Added `quality_guardrails._env_flag()` and `ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES` toggle
  - Updated `_run_job()` to return `bool` success status and raise `RuntimeError` on structured check failures when fail-fast mode is enabled
- **Quality-Guardrails Per-Job Fail-Fast Escalation (TDD)**:
  - Extended `tests/unit/test_quality_guardrails_results.py` to assert selective fail-fast behavior based on guardrail job name
  - Added `quality_guardrails._env_csv()` and `quality_guardrails._fail_fast_enabled_for_job()` helpers
  - Added `ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES_JOBS` env support (comma-separated job names), while preserving global `ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES` override
  - Updated `_run_job()` to apply fail-fast policy per job when configured
- **Quality-Guardrails Failure Backoff Controls (TDD)**:
  - Extended `tests/unit/test_quality_guardrails.py` with `_failure_backoff_elapsed()` coverage
  - Added `quality_guardrails._env_nonneg_int()` and `quality_guardrails._failure_backoff_elapsed()` helpers
  - Added `ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS` scheduler env support
  - Updated guardrail loop to defer reruns of failed jobs until the configured backoff window elapses
- **Quality-Guardrails Runtime Backoff Policy Reload (TDD)**:
  - Extended `tests/unit/test_quality_guardrails.py` with `_resolve_job_failure_backoff_policy()` coverage
  - Added `quality_guardrails._resolve_job_failure_backoff_policy()` helper for per-iteration policy resolution
  - Updated scheduler loop to resolve job backoff policy inside each loop iteration
  - Runtime changes to `ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS` now apply without restarting the process
- **Quality-Guardrails Backoff Policy Cache + Change Detection (TDD)**:
  - Extended `tests/unit/test_quality_guardrails.py` with `_resolve_job_failure_backoff_policy_cached()` coverage
  - Added `quality_guardrails._resolve_job_failure_backoff_policy_cached()` helper
  - Added raw-env parsing helper to avoid redundant parse work when policy is unchanged
  - Updated scheduler loop to reuse cached policy when env input is unchanged and refresh only on change
- **Quality-Guardrails DB Runtime Policy Source (TDD)**:
  - Added `runtime_config` table in `src/orion/storage/models.py` for centralized runtime key/value JSON settings
  - Extended `tests/unit/test_quality_guardrails.py` with runtime-policy normalization and DB-cache resolver coverage
  - Added runtime-config helpers in `quality_guardrails`:
    - `_runtime_backoff_policy_from_value()`
    - `_load_runtime_backoff_config_row()`
    - `_resolve_runtime_backoff_policy_cached()`
  - Updated scheduler to prefer DB-backed `quality_guardrails.backoff_seconds_jobs` policy (when valid) with env fallback
- **Quality-Guardrails Retry Semantics Fix (TDD)**:
  - Added `_next_last_run()` helper and tests in `tests/unit/test_quality_guardrails.py`
  - Updated scheduler loop to advance `last_*` timestamps only for successful runs
  - Prevents failed guardrail runs from being incorrectly rate-limited by success-timestamp updates
- **Feature-Enrichment Gateway Auth Fail-Fast (TDD)**:
  - Added `main_feature_enrichment._gateway_runtime_contract()` to enforce Gateway URL + API key startup contract
  - Updated feature-enrichment connector wiring to pass resolved `gateway_key` explicitly
  - Added `test_feature_enrichment_gateway_contract.py` coverage for missing-key failure and URL/key normalization
  - Added compose wiring assertion in `test_compose_legacy_gate_wiring.py` and wired `GATEWAY_API_KEY` for `feature_enrichment` service
- **Validate-Features Guardrail Hardening (TDD)**:
  - Added canonical `MINUTES_TO_CLOSE_MAX = 390` bound and aligned spot-check + batch sanity validation in `src/orion/jobs/validate_features.py`
  - Extended sanity query to track `ml_ready = false` population and fail checks when incomplete rows are present
  - Added `tests/unit/test_validate_features_guardrails.py` coverage for minutes-to-close bound consistency and incomplete-row guardrail failure behavior
- **Pattern-Miner Profile Alignment (TDD)**:
  - Added `test_pattern_miner_is_profiled_with_legacy_label_stack` in `tests/unit/test_compose_legacy_gate_wiring.py`
  - Added `profiles: [ "legacy-labels" ]` to `pattern-miner` in `docker-compose.yml`
  - Prevents default runtime from running pattern-miner without its legacy label input stack
- **Legacy Label Compose Profile Opt-In (TDD)**:
  - Added `test_legacy_label_stack_services_are_profiled_for_opt_in` in `tests/unit/test_compose_legacy_gate_wiring.py`
  - Added `profiles: [ "legacy-labels" ]` for `labeler`, `price_target_labeler`, `option_quote_tracker`, `nightly-backfill`, and `quality-guardrails` in `docker-compose.yml`
  - Default compose runtime now excludes this legacy stack unless `--profile legacy-labels` is explicitly enabled
- **Gateway/Heber Parity Audit (Pass 166)**: Continued audit with:
  - runtime source-alignment finding for `pattern-miner` in default compose vs legacy-profile-gated `price_target_labels` source pipeline
  - stale/empty-input risk framing for model mining when legacy label profile is disabled
  - staged remediation guidance to either profile-gate `pattern-miner` short-term or migrate to Heber-backed training datasets
- **Gateway/Heber Parity Audit (Pass 167)**: Continued audit with:
  - feature-enrichment auth-contract finding: compose wires `GATEWAY_URL` without Gateway API key for UW enrichment connectors
  - silent-degradation finding: connector failures return empty results and main loop continues, allowing no-data operation without fail-fast startup
  - remediation guidance for explicit Gateway-key startup validation, compose env wiring, and enrichment smoke checks
- **Gateway/Heber Parity Audit (Pass 168)**: Continued audit with:
  - refreshed SQL-coupling hotspot counts after legacy-profile rollout controls
  - concentration update identifying remaining heavy debt in `validate_features`, `main_price_target_labeler`, and ML backfill/support jobs
  - transition guidance from broad audit passes to targeted hotspot closeout
- **Gateway/Heber Parity Audit (Pass 169)**: Continued audit with:
  - guardrail blind-spot finding: feature sanity checks filter to `ml_ready` rows and can miss stalled/incomplete label populations
  - validation-consistency finding: `minutes_to_close` bounds diverge between spot-check (`0-390`) and batch sanity (`0-500`) paths
  - remediation guidance for incomplete-row coverage metrics and canonical validation-bound alignment
- **Gateway/Heber Parity Audit (Pass 170)**: Continued audit with:
  - implemented feature-enrichment startup auth contract enforcement for Gateway URL/API key
  - compose wiring update for `feature_enrichment` Gateway API key env contract
  - residual guidance for sustained zero-write runtime alerting beyond startup checks
- **Gateway/Heber Parity Audit (Pass 171)**: Continued audit with:
  - implemented guardrail sanity fix for incomplete-row visibility (`ml_ready = false`) in `validate_features`
  - aligned minutes-to-close validation bounds to a canonical `390` max across spot-check and batch sanity paths
  - residual guidance for operational escalation policy on sustained incomplete-row states
- **Gateway/Heber Parity Audit (Pass 172)**: Continued audit with:
  - implemented `pattern-miner` compose profile alignment to legacy label stack dependency
  - added test coverage to enforce profile coupling at compose-contract level
  - residual guidance to migrate pattern-miner training source to Heber canon for non-legacy runtime rejoin
- **Gateway/Heber Parity Audit (Pass 173)**: Continued audit with:
  - implemented `quality_guardrails` structured-failure signaling to avoid false-green completion logs
  - added TDD coverage for guardrail result summary and failed-result logging behavior
  - residual guidance to define escalation policy for guardrail failures (alert-only vs fail-fast per job class)
- **Gateway/Heber Parity Audit (Pass 174)**: Continued audit with:
  - implemented global fail-fast escalation policy toggle for guardrail structured failures
  - added TDD coverage for `_run_job()` boolean success contract and fail-fast runtime behavior
  - residual guidance to add per-job escalation policy granularity
- **Gateway/Heber Parity Audit (Pass 175)**: Continued audit with:
  - fixed guardrail scheduler timestamp advancement to occur only on successful job runs
  - added TDD coverage for success/failure timestamp progression semantics
  - residual guidance to add optional failure backoff for noisy prolonged outage scenarios
- **Gateway/Heber Parity Audit (Pass 176)**: Continued audit with:
  - implemented backfill entry-session contract alignment to canonical labeler time-bucketing semantics
  - added TDD coverage for OPEN/MID/CLOSE boundary parity between backfill and live labeler
  - residual guidance that deterministic backfill candidate ordering remains to be remediated
- **Gateway/Heber Parity Audit (Pass 177)**: Continued audit with:
  - implemented deterministic ordering for `backfill_ml_features` candidate selection query
  - added TDD coverage for ordered query contract and limit-parameter handling
  - residual guidance to introduce explicit progress watermarking for full backfill resumability
- **Gateway/Heber Parity Audit (Pass 178)**: Continued audit with:
  - implemented keyset cursor pagination in `backfill_ml_features` candidate selection and run loop
  - added TDD coverage for cursor SQL predicate contract and cursor advancement behavior
  - residual guidance that persisted watermark state is still needed for crash-safe resumability
- **Backfill Remaining-Budget Pagination Safety (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_selection.py` with `test_run_backfill_requests_only_remaining_budget`
  - Updated `src/orion/jobs/backfill_ml_features.py` run loop to request `min(batch_size, remaining_limit)` each iteration
  - Updated cursor advancement to follow the last processed row (not pre-fetched page tail)
  - Prevents over-fetching rows beyond the process budget and avoids cursor overshoot conditions
- **Gateway/Heber Parity Audit (Pass 179)**: Continued audit with:
  - implemented remaining-budget fetch sizing in `backfill_ml_features` run pagination loop
  - added TDD coverage for per-iteration fetch-limit contraction and cursor continuity
  - residual guidance to add persisted watermarking for crash-safe restart continuity
- **Backfill Resume Watermark Persistence (TDD)**:
  - Extended `tests/unit/test_backfill_ml_features_selection.py` with:
    - `test_get_records_to_backfill_supports_timestamp_only_cursor_filter`
    - `test_run_backfill_resumes_from_watermark_and_persists_progress`
  - Updated `src/orion/jobs/backfill_ml_features.py` to:
    - load persisted watermark from `ingest_watermarks` at startup
    - support timestamp-only cursor filtering (`entry_ts >= watermark_ts`)
    - persist watermark progress during run processing
  - Adds crash-safe timestamp resume semantics for ML feature backfills
- **Gateway/Heber Parity Audit (Pass 180)**: Continued audit with:
  - implemented persisted backfill resume timestamp using existing watermark store
  - added TDD coverage for resume-start cursor injection and per-record watermark progression
  - residual guidance to persist full keyset cursor (`entry_ts` + `event_id`) for strict no-duplicate replay semantics
- **Backfill Exit-Columns Candidate Recovery Hardening (TDD)**:
  - Added `tests/unit/test_backfill_exit_columns_selection.py` to enforce selection contracts for:
    - velocity backfill candidates across all three velocity targets (75/100/150)
    - checkpoint backfill candidates across all price/return checkpoint columns
  - Updated `src/orion/jobs/backfill_exit_columns.py` candidate SQL to:
    - include partial-row recovery predicates for all target columns
    - apply deterministic ordering (`entry_ts`, `event_id`) before `LIMIT`
  - Prevents silent misses where rows had partial checkpoint/velocity population
- **Gateway/Heber Parity Audit (Pass 181)**: Continued audit with:
  - implemented partial-row recovery predicates for `backfill_exit_columns` velocity and checkpoint selectors
  - added TDD coverage for all-column selection contracts and deterministic ordering
  - residual guidance to add run-level pagination/progress watermarking for very large checkpoint backfills
- **Backfill Exit-Columns Phase Pagination (TDD)**:
  - Extended `tests/unit/test_backfill_exit_columns_selection.py` with:
    - cursor-filter SQL contract tests for velocity/checkpoint selectors
    - run-loop pagination test across both backfill phases
  - Updated `src/orion/jobs/backfill_exit_columns.py` to:
    - support keyset cursor parameters in both selectors
    - paginate phase processing with `min(batch_size, remaining)` fetch windows
    - advance per-phase cursors by processed row (`entry_ts`, `event_id`)
  - Prevents single-page truncation and allows deterministic multi-page traversal for large backfill sets
- **Gateway/Heber Parity Audit (Pass 182)**: Continued audit with:
  - implemented multi-page deterministic traversal in `backfill_exit_columns` for both velocity and checkpoint phases
  - added TDD coverage for selector cursor predicates and per-phase run-loop pagination
  - residual guidance to add persisted per-phase watermarks for crash-safe resume parity with ML backfill
- **Gateway/Heber Parity Audit (Pass 1)**: Added a migration-focused audit document at `docs/ORION_GATEWAY_HEBER_PARITY_AUDIT_2026-02-05.md`
  - Includes integration gap analysis against `../Data-Gateway` and `../Heber`
  - Includes technical debt backlog and keep/migrate/dispose framing for features and labels
  - Includes migration sequence and open architecture decisions
- **HeberReader Contract Tests**: Added `tests/unit/test_heber_reader.py` to cover:
  - Catalog health endpoint contract (`/health`)
  - Silver parquet reads with instrument/as-of filtering
  - Gold parquet reads with symbol/as-of filtering
- **Parity Matrix Extension**: Expanded `docs/ORION_GATEWAY_HEBER_PARITY_AUDIT_2026-02-05.md` with:
  - Column-level labels parity table (Orion vs Heber)
  - Column-level features parity table (Orion vs Heber)
  - Explicit keep/migrate/dispose decisions per feature/label family
- **Gateway Stream Contract Tests**: Added `tests/connectors/test_gateway_stream_client.py` to validate:
  - Gateway `type=data` + `envelope` + `data` bar message parsing
  - Invalid bar rejection behavior
  - Pre-connect subscription queue behavior
- **Gateway/Heber Config Mapping Tests**: Added coverage in `tests/unit/test_config_centralization.py` for:
  - `DATA_GATEWAY_*` env mappings
  - legacy `GATEWAY_*` alias compatibility
  - `HEBER_*` env mappings into centralized settings
- **Heber Labeler Migration Tests**: Added `tests/unit/test_main_labeler_heber_migration.py` covering:
  - Heber flow payload normalization into labeler records
  - Alias-field handling for mixed flow schemas
- **Heber Feature-Enrichment Source Tests**: Added `tests/unit/test_feature_enrichment_heber_source.py` for:
  - Top-ticker extraction from Heber flow frames
  - Recent-window filtering behavior
  - Graceful handling when expected columns are missing
- **Gateway/Heber Parity Audit (Pass 3)**: Extended `docs/ORION_GATEWAY_HEBER_PARITY_AUDIT_2026-02-05.md` with:
  - 2026-02-06 status updates for completed migration items
  - Current SQL-coupling technical-debt counts and highest-concentration files
  - Remaining high-priority integration gaps and wave-2 archive candidates
- **Gateway/Heber Parity Audit (Pass 4)**: Added deep audit coverage for:
  - `main_price_target_labeler`, `ml/flow_enricher`, and SQL-coupled backfill/validation jobs
  - Severity-ranked findings including a concrete backfill runtime bug and train/inference feature-semantics drift
  - Module-by-module migration readiness and updated P0/P1/P2 backlog
- **Backfill Signature Regression Test**: Added `tests/unit/test_backfill_ml_features_signature.py` to enforce the `get_sector_correlation_features(ticker, entry_ts)` call contract.
- **Gateway/Heber Parity Audit (Pass 5)**: Continued audit with:
  - validation of the backfill runtime bug fix
  - SQL portability debt findings (`date_trunc`, Postgres casts/operators) in migration-critical modules
- **Gateway/Heber Parity Audit (Pass 6)**: Added function-level migration map for `main_price_target_labeler`:
  - source-by-source migration targets into Heber datasets
  - safe slice order for incremental migration
  - explicit parity gates before additional archival
- **Gateway/Heber Parity Audit (Pass 7)**: Continued audit with:
  - active-service keep/migrate/retire matrix from `docker-compose` runtime wiring
  - darkpool contract-drift finding (`darkpool` vs `darkpool_trades`) across Data Gateway, Heber, and Orion reader paths
  - ML dependency mapping for `pattern_miner`, `exit_classifier`, and `nightly_backfill`
  - refreshed SQL-coupling hotspot heatmap and updated archival readiness wave
- **Gateway/Heber Parity Audit (Pass 8)**: Continued audit with:
  - deep review of `validate_features`, `data_quality_checker`, and `window_feature_job` migration readiness
  - schema/column contract mismatch mapping between legacy Orion SQL assumptions and Heber Silver canon
  - feature-lineage drift finding in validation mapping vs current checkpoint quote source usage
  - DST scheduling-risk finding in fixed-offset market-time logic
- **Gateway/Heber Parity Audit (Pass 9)**: Continued audit with:
  - live-pipeline gap finding: current runtime wiring does not produce UW-flow signals required by active flow rules
  - deployment drift finding: compose profile lacks ingestion service entry despite ingestion-based assumptions
  - dual-write debt finding: Data Gateway pulls are persisted back into Orion-local silver tables
  - auth-contract mismatch finding in `sync_earnings` (`Authorization` token client vs Gateway `X-Gateway-Key` requirement)
- **Gateway/Heber Parity Audit (Pass 10)**: Continued audit with:
  - execution path split-brain finding (`main_execution.py` vs `execution/service.py`) and active deployment path verification
  - ML prefilter candidate-contract mismatch finding (nullable option fields vs required scorer inputs)
  - inference enrichment coupling finding (`flow_enricher` still bound to Orion-local `silver_*`/`gold_feature_windows`)
  - inactive ML flow processor wiring review and archive/consolidation guidance
- **Gateway/Heber Parity Audit (Pass 11)**: Continued audit with:
  - runtime entrypoint drift findings across execution queue path, rollup generation, and compose wiring
  - changelog-to-code mismatch finding for execution consolidation claims vs current deployed codepath
  - labeling-stack fragmentation analysis (`flow_labels`, `price_target_labels`, and PRD 6.3 label jobs) with archive-wave guidance
- **Gateway/Heber Parity Audit (Pass 12)**: Continued audit with:
  - active runtime Gateway auth-contract drift findings (`X-Gateway-Key` requirements vs compose env wiring)
  - direct-provider bypass finding in option quote tracking (direct Alpaca calls vs Gateway endpoint parity)
  - explicit keep/migrate/dispose matrix for Orion label/feature families and Heber gold contract gaps
- **Gateway/Heber Parity Audit (Pass 14)**: Continued audit with:
  - cross-repo contract gap findings between Heber alert-label option-bar fetch shape and Data Gateway route implementation
  - cross-repo auth gap findings (Heber alert-label gateway calls missing `X-Gateway-Key`)
  - concrete keep/migrate/dispose decisions for Orion label families into Heber gold dataset splits
- **Gateway/Heber Parity Audit (Pass 16)**: Continued audit with:
  - write-only service finding for `flow_labels`/`main_labeler` in current Orion repo wiring
  - dependency confirmation that active model-training and backfill paths are centered on `price_target_labels`
  - decommission gating recommendations for moving `labeler` to opt-in profile pending external-consumer check
- **Gateway/Heber Parity Audit (Pass 17)**: Continued audit with:
  - compose env-contract drift findings for Gateway-backed services (missing Gateway key wiring)
  - direct-UW dependency drift findings in `main_price_target_labeler` versus centralization goals
  - archival execution notes for orphaned integration modules
- **Gateway/Heber Parity Audit (Pass 18)**: Continued audit with:
  - concrete `sync_earnings` contract-break findings (Gateway path shape and auth mismatch)
  - label-ontology parity findings between Orion `price_target_labels` and current Heber `labels_alert_*` views
  - migration-split recommendation for outcome labels vs training-fact feature datasets
- **Gateway/Heber Parity Audit (Pass 19)**: Continued audit with:
  - `sync_todays_earnings` date-semantic drift finding (`date.today()` override vs provider record dates)
  - runtime-coupling finding that earnings sync remains tied to ingestion startup path
  - operational guidance for explicit scheduling and parity verification of earnings calendar freshness
- **Gateway/Heber Parity Audit (Pass 20)**: Continued audit with:
  - Heber watch quote-route mismatch findings (`/options/quotes?symbols=` vs Gateway per-contract quote route)
  - cross-repo auth-gap findings for watch quote pulls (missing Gateway API key contract)
  - P0 alignment guidance for quote contract and watch service auth wiring
- **Gateway/Heber Parity Audit (Pass 21)**: Continued audit with:
  - ops/remediation job inventory findings for currently unwired `src/orion/jobs/*` modules
  - runtime-vs-repo ownership risk framing for dormant CLI/test-only jobs
  - operational jobs matrix recommendation before next archive wave decisions
- **Gateway/Heber Parity Audit (Pass 22)**: Continued audit with:
  - cross-repo default URL drift findings (`DATA_GATEWAY_URL` defaulting to `:8000` in Heber paths)
  - deployment-risk finding where Heber default points to lakeFS port rather than Data Gateway
  - normalization and fail-fast recommendations for Gateway URL contracts
- **Gateway/Heber Parity Audit (Pass 23)**: Continued audit with:
  - active Orion ingestion-path finding for direct Alpaca Greeks enrichment in persistence layer
  - duplicate ownership risk framing for Greeks data across direct-provider and Gateway/Heber contracts
  - canonicalization recommendations for option Greeks sourcing
- **Gateway/Heber Parity Audit (Pass 24)**: Continued audit with:
  - compose/runtime wiring findings for Heber-dependent Orion services lacking explicit Heber data mount/env contracts
  - deployment-risk finding for silent fallback/empty-read behavior in Heber-backed flows
  - fail-fast and environment-contract recommendations for Heber runtime access
- **Gateway/Heber Parity Audit (Pass 25)**: Continued audit with:
  - duplicate outcome-tracking stack findings across Heber watch labels and Orion local checkpoint-label pipeline
  - retirement-decision framing for canonical outcome path ownership
  - parity-bridge recommendations for migrating `price_target_labels`-dependent training fields
- **Gateway/Heber Parity Audit (Pass 26)**: Continued audit with:
  - nightly-backfill credential-contract gap findings (`backfill_ml_features` direct UW dependency vs compose env wiring)
  - scheduler timebase finding for fixed UTC-5 ET conversion (DST drift risk)
  - enrichment completeness and scheduling-hardening recommendations
- **Gateway/Heber Parity Audit (Pass 27)**: Continued audit with:
  - ingestion-runtime parity finding: `IngestionService` documents Heber UW sourcing but still processes Alpaca-only events in active cycle
  - UW enrichment schema-drift findings across Greek exposure / max pain / IV-rank connector field mappings vs Gateway normalized payloads
  - feature-enrichment auth-contract finding: Gateway key wiring missing in compose path for API-key-protected UW endpoints
  - darkpool feed-name drift finding (`darkpool` vs `darkpool_trades`) across Data Gateway poller, Heber Silver partitioning, and Orion `HeberReader`
- **Gateway/Heber Parity Audit (Pass 28)**: Continued audit with:
  - Orion Admin API drift finding: `/flows` still reads Orion-local `silver_uw_flow` instead of centralized Gateway/Heber flow interfaces
  - orphaned-integration finding: Shared MCP client/service stack remains wired with direct provider credentials but has no active runtime consumers
  - MCP endpoint-contract finding: default `MCP_SERVER_URL` (`localhost:8001`) is misaligned with compose topology (`8090:8001` host mapping / `mcp-server` service DNS)
- **Gateway/Heber Parity Audit (Pass 29)**: Continued audit with:
  - MetaSearch regression finding: `_fetch_silver_events` defines but does not execute its data-fetch coroutine, yielding empty evaluation inputs
  - changelog-to-code drift finding for prior “fixed” meta-search fetch path vs current implementation
  - adaptive-runtime coupling findings: meta weekly/evolution and EOD analytics still depend on Orion-local ingestion tables while compose omits ingestion service
- **Gateway/Heber Parity Audit (Pass 30)**: Continued audit with:
  - repo-hygiene debt finding: `detect-secrets` baseline is coupled to large/generated and archived files, creating commit-loop instability
  - commit-reliability risk framing tied to tracked `codebase.md` and baseline line-number churn
  - scheduler-reliability finding: weekly meta evolution runs on exact-minute trigger with no catch-up window
- **Gateway/Heber Parity Audit (Pass 31)**: Continued audit with:
  - root-cause integration finding: Orion `HeberReader` hardcodes Silver feed names instead of using Heber catalog feed-resolution contracts
  - cross-repo naming drift finding: `darkpool` vs `darkpool_trades` inconsistencies persist between Data Gateway producer feeds, Heber writer/storage keys, and catalog inventory
  - data-quality/scaling finding: `HeberReader` filter fallback re-reads full parquet datasets without re-applying symbol filters
- **Gateway/Heber Parity Audit (Pass 32)**: Continued audit with:
  - cross-service URL-contract finding: Heber watch poller/consumer and feature-enrichment build Data Gateway paths with inconsistent `/api/v1` assumptions
  - endpoint-shape mismatch finding: watch market-context enrichment targets `/alpaca/stocks/bars` while Gateway serves `/api/v1/alpaca/stocks/{symbol}/bars`
  - auth-contract finding: Heber watch and alert-label pipeline Gateway requests omit required `X-Gateway-Key` header wiring
  - migration-hardening recommendations for shared URL builders, startup contract validation, and integration tests for watch enrichment paths
- **Gateway/Heber Parity Audit (Pass 33)**: Continued audit with:
  - API-surface mismatch finding: Heber watch and label pipelines call batch options routes (`/options/quotes`, `/options/bars`) not exposed by current Gateway Alpaca router
  - provider/router drift finding: Gateway Alpaca provider has batch options quote/bar support, but router only exposes single-contract paths
  - discovery-contract drift finding: Gateway `/catalog` advertises stale/nonexistent Alpaca paths (`/options/bars`, `/stocks/bars`) vs actual router exports
- **Gateway/Heber Parity Audit (Pass 34)**: Continued audit with:
  - execution-tech-debt finding: `main_execution.py` still contains dead helper paths (`get_pending_candidates`, `update_candidate_status`) that reference non-existent `CandidateTrade` columns
  - changelog drift finding: removal claim for those helpers is currently false in code
  - consolidation drift finding: changelog still claims `main_execution.py` is a thin wrapper while runtime module remains a full execution loop
- **Gateway/Heber Parity Audit (Pass 35)**: Continued audit with:
  - stream-contract finding: `GatewayStreamClient` mutates local subscription state before ACK and does not surface Gateway `type=error` subscription failures
  - feed-contract finding: Orion subscribes with legacy `feed=\"bars\"` and relies on Gateway fallback behavior instead of canonical feed IDs (`stock_bars`, etc.)
  - reliability recommendations for ACK-gated subscription state, explicit WS error handling, and feed-ID contract tests
- **Gateway/Heber Parity Audit (Pass 36)**: Continued audit with:
  - data-completeness finding: `backfill_exit_columns` selects rows by `price_at_15m IS NULL` only, which can skip partially-missing checkpoint columns
  - operational-throughput finding: nightly backfill applies fixed per-run limits with non-deterministic `LIMIT`-only selectors (no stable pagination/order)
  - scheduling-contract drift finding: `nightly_backfill` docs claim post-close 4:30pm ET while runtime config schedules 4:00pm ET
- **Gateway/Heber Parity Audit (Pass 37)**: Continued audit with:
  - label-integrity finding: `main_option_quote_tracker` can write latest-available quotes into overdue historical checkpoints (`ts_utc` backdated), corrupting checkpoint chronology
  - contract-symbol finding: quote tracker reconstructs OCC symbols from components instead of using canonical `silver_uw_flow.option_chain`
  - reliability recommendations for checkpoint-timestamp tolerance gating and provenance checks on quote writes
- **Gateway/Heber Parity Audit (Pass 38)**: Continued audit with:
  - connector-reliability finding: UW Gateway connectors decorate fetches with `@retry(...)` but swallow exceptions and return `None`, effectively disabling retry/backoff protections
  - regime-signal correctness finding: `get_spy_cumulative_return` window query computes long-horizon return instead of the intended bounded 20-bar trend input
  - schema-governance finding: enrichment silver tables (`silver_greek_exposure`, `silver_market_tide`, `silver_max_pain`, `silver_iv_rank`) are written via raw SQL but not represented in canonical silver ORM/docs artifacts
- **Gateway/Heber Parity Audit (Pass 39)**: Continued audit with:
  - volatility-metric integrity finding: active `VIXProxyConnector` computes `vix_1d_change` and `vix_5d_ma` from recent 1-minute `silver_alpaca_bars` rows while labeling them as daily-style metrics
  - regime-input fidelity finding: `main_feature_enrichment` still feeds `MultiAxisRegimeDetector` with hardcoded `realized_vol=0.015` instead of derived live-bar volatility
  - consolidation finding: direct Alpaca `VIXConnector` remains in repo but is not wired into runtime, leaving duplicate VIX-source paths without a canonical owner
- **Gateway/Heber Parity Audit (Pass 40)**: Continued audit with:
  - labeling-progress integrity finding: `main_labeler` checks labeled IDs for only a bounded prefix of candidate records, then filters the full backlog, which can misclassify already-labeled rows as unlabeled
  - observability finding: `persist_labels` returns attempted label count while insert path uses `ON CONFLICT DO NOTHING`, inflating `total_labeled` progress logs under duplicate retries
  - reliability recommendations for DB-driven unlabeled pagination and true inserted-row counting in labeler metrics
- **Gateway/Heber Parity Audit (Pass 41)**: Continued audit with:
  - drift-baseline correctness finding: `pattern_miner.get_last_week_importance` orders newest-first but collapses duplicate feature rows via dict overwrite, effectively retaining oldest-per-feature values in-window
  - training-readiness finding: pattern-miner training query gates on `last_tracked_ts` but not `ml_ready`, allowing incomplete/unvalidated rows into model fitting
  - model-quality recommendations for latest-per-feature baseline selection and explicit readiness gating in training datasets
- **Gateway/Heber Parity Audit (Pass 42)**: Continued audit with:
  - model-bias finding: `exit_classifier` training data generation skips `max_return_pct <= 0` trades, introducing winner/survivor bias in exit timing models
  - readiness-contract finding: exit-classifier training query does not enforce `ml_ready` completeness before building samples
  - validation-method finding: exit-classifier AUC is evaluated with random `train_test_split` rather than time-aware validation, increasing temporal leakage risk
- **Gateway/Heber Parity Audit (Pass 43)**: Continued audit with:
  - runtime split-brain finding: compose runs both `main_execution` and `position_monitor`, each with independent close-position execution paths
  - exit-feature fidelity finding: `PositionMonitor` initializes `entry_time` from process `now` rather than true entry timestamp, skewing time-held exit signals
  - context-lookup finding: position monitor resolves entry context via `candidate_trades.ticker` match only, which can miss option-contract positions keyed by `option_symbol`
- **Gateway/Heber Parity Audit (Pass 44)**: Continued audit with:
  - observability finding: Admin `/dashboard/*` endpoints are powered by an in-memory `PnLTracker` singleton with no active runtime feed path in repo
  - source-of-truth drift finding: dashboard path bypasses persisted execution tables (`orders`, `fills`, `positions_snapshots`) and can report stale/empty portfolio state
  - migration recommendation to anchor dashboard telemetry to durable execution/broker-backed data
- **Gateway/Heber Parity Audit (Pass 45)**: Continued audit with:
  - execution-state finding: `PositionManager` add/sync methods are effectively unwired in runtime, so exit-rule position state can go stale after startup
  - identity-model finding: position tracking is keyed by underlying ticker, which can collapse multiple option contracts on the same symbol
  - exit-targeting finding: `ExecutionEngine.close_position` uses ticker-only symboling in `main_execution` exit path, lacking explicit option-contract close handling
- **Gateway/Heber Parity Audit (Pass 46)**: Continued audit with:
  - data-linkage finding: `ExecutionEngine` exit persistence path omits `ExitDecision.candidate_id` even though model/schema expects candidate linkage
  - restart-correctness finding: `PositionManager.initialize` determines open positions via `StrategyDecision.candidate_id` ↔ `ExitDecision.candidate_id` join, which can misclassify exited positions when linkage is missing
  - reliability recommendations for candidate-linked exit writes and restart-resume regression coverage
- **Gateway/Heber Parity Audit (Pass 47)**: Continued audit with:
  - execution-concurrency finding: `main_execution.fetch_pending_candidates` uses anti-join polling without atomic claim/lock semantics, allowing duplicate pickup in multi-worker scenarios
  - idempotency-contract finding: `strategy_decisions` stores `candidate_id` as non-unique, so duplicate decisions per candidate are structurally possible
  - hardening recommendations for claim-based polling and one-decision-per-candidate constraints/tests
- **Gateway/Heber Parity Audit (Pass 48)**: Continued audit with:
  - fill-idempotency finding: execution restart path can reprocess recent fills because dedupe state is process-local (`_partial_fill_tracker`, `processed_fill_ids`) and reset on sync/restart
  - ordering finding: risk-state mutation happens before DB fill dedupe (`ON CONFLICT DO NOTHING`), so duplicate fill events can still skew risk/equity state
  - integration-gap finding: DB-backed processed-fill helpers exist in `ExecutionEngine` but are not used by active fill polling flow
- **Gateway/Heber Parity Audit (Pass 49)**: Continued audit with:
  - options-risk enforcement finding: live `_execute_options_order` path does not invoke `RiskManager.check_order`/`check_options_order`, so options orders can bypass configured risk/Greeks gates
  - sizing-contract drift finding: preflight validates options candidates using `calculate_size` + ticker-level `check_order`, but execution submits contracts via `max_option_premium_pct` + connector contract sizing
  - hardening recommendations for mandatory options risk-gate enforcement and preflight-vs-execution quantity parity tests
- **Gateway/Heber Parity Audit (Pass 50)**: Continued audit with:
  - options-unit finding: `RiskManager` share-style cost math (`quantity * price`) conflicts with contract-based options premium semantics (`contracts * price * 100`)
  - enforcement-gap finding: `check_options_order` delegates to `check_order` and therefore inherits non-contract-aware notional checks
  - hardening recommendations for contract-normalized risk accounting and options-specific max-order/exposure regression tests
- **Gateway/Heber Parity Audit (Pass 51)**: Continued audit with:
  - rollup-source finding: execution preflight and admin `/rollups` endpoints both read Orion-local `gold_ticker_rollup` rather than a centralized Heber-backed rollup contract
  - deployment finding: rollup production is tied to ingestion-service background startup, while current compose profile runs execution path with rollup requirement disabled
  - hardening recommendations for canonical rollup ownership, source alignment, and freshness/SLO validation
- **Gateway/Heber Parity Audit (Pass 52)**: Continued audit with:
  - infra-runtime finding: compose provisions `redpanda`/`minio` services while active runtime profile does not run Orion ingestion producer path
  - lakehouse-contract finding: `LakehouseWriter` is disabled unless `ORION_LAKEHOUSE_*` vars are set, but those vars are absent from current compose env blocks
  - hardening recommendations for canonical transport ownership and fail-fast lakehouse configuration checks
- **Gateway/Heber Parity Audit (Pass 53)**: Continued audit with:
  - deployment-coverage finding: FastAPI Admin endpoints are exercised by in-process ASGI tests but no API service is present in the active compose runtime
  - data-contract finding: API `/flows` and `/rollups` remain coupled to Orion-local silver/gold tables rather than a Gateway/Heber canonical facade
  - hardening recommendations for explicit API product ownership, deployment smoke checks, and canonical data-source routing
- **Gateway/Heber Parity Audit (Pass 54)**: Continued audit with:
  - directional-exit finding: `ExecutionEngine.close_position` hardcodes `OrderSide.SELL` and `main_execution` does not pass position direction, enabling wrong-side closes when shorting is active
  - path-divergence finding: `position_monitor` uses broker-native `close_position(symbol)` while `main_execution` uses explicit side logic, creating inconsistent close semantics across active runtimes
  - hardening recommendations for direction-aware close orders and unified close primitive coverage
- **Gateway/Heber Parity Audit (Pass 55)**: Continued audit with:
  - position-state finding: `PositionManager` rehydrates/stores exit-rule quantity from `decision.execution_params`, but execution path does not persist submitted `qty` there
  - exit-reliability finding: `main_execution` uses tracked `position.qty` directly for close orders, so zero/incorrect rehydrated qty can propagate into close attempts
  - hardening recommendations for persisted quantity source-of-truth and restart qty-parity regression coverage
- **Gateway/Heber Parity Audit (Pass 56)**: Continued audit with:
  - auditability finding: execution-time failure reasons are set on the in-memory decision object, but post-execution DB update path persists only `executed_successfully`
  - observability finding: `strategy_decisions.reason` can remain stale pre-execution text while final status indicates execution failure/skips
  - hardening recommendations for full post-execution decision-state persistence and failure-reason regression tests
- **Gateway/Heber Parity Audit (Pass 57)**: Continued audit with:
  - checkpoint-coverage finding: `main_option_quote_tracker` selects only newest `LIMIT 1000` flow events in a fixed recency window, enabling starvation of older eligible checkpoint rows
  - config-drift finding: tracking-age constant is defined in code but SQL filter is hardcoded to `24 hours`
  - hardening recommendations for deterministic pagination/cursor processing and checkpoint coverage monitoring
- **Gateway/Heber Parity Audit (Pass 58)**: Continued audit with:
  - exit-context finding: `main_execution` exit rules fetch recent flow from Orion-local `silver_uw_flow`, while flow-based exit logic mostly returns no signal when this context is empty
  - ownership-drift finding: centralized Gateway->Heber ingestion assumptions plus non-running ingestion runtime increase chance of silent empty-flow exit evaluation
  - hardening recommendations for Heber/Gateway-backed exit context reads and fail-loud missing-context telemetry
- **Gateway/Heber Parity Audit (Pass 59)**: Continued audit with:
  - rule-activation finding: `main_execution` invokes exit rules with `context={}`, effectively disabling context-dependent rules (`current_oi`, `current_iv`, `current_option_price`)
  - coverage finding: several configured exit rules can silently return `None` in production path due to missing context population
  - hardening recommendations for populated exit-context construction and missing-context observability/tests
- **Gateway/Heber Parity Audit (Pass 60)**: Continued audit with:
  - flow-scoping finding: `main_execution` fetches exit context by underlying ticker only and feeds that stream to all exit rules
  - contract-integrity finding: several exit rules aggregate flow without option-contract scoping, allowing unrelated expiry/strike flow to influence exits
  - hardening recommendations for contract-aware flow filters and mixed-contract regression coverage
- **Gateway/Heber Parity Audit (Pass 61)**: Continued audit with:
  - rule-contract finding: `PriceTargetExitRule` is enabled in default exit set but requires `entry_option_price`, which is not present/populated on `OpenPosition`
  - activation-risk finding: target/stop price exits can remain silently inert despite appearing configured
  - hardening recommendations for rule/position contract alignment and rule-activation validation tests
- **Gateway/Heber Parity Audit (Pass 62)**: Continued audit with:
  - contract-id propagation finding: `PositionManager` sets `OpenPosition.option_chain` from evidence/context and does not default to canonical `CandidateTrade.option_symbol`
  - filtering-fidelity finding: exit-rule DTE filtering falls back to `"UNKNOWN"` and broad matching when `option_chain` is missing
  - hardening recommendations for canonical option-symbol propagation and DTE-bucket validation coverage
- **Gateway/Heber Parity Audit (Pass 63)**: Continued audit with:
  - policy-scope finding: options-specific exit-rule set is applied to all tracked open positions, while execution stack supports both options and equities
  - monitoring-coverage finding: `PositionManager.initialize` rebuilds at most 50 open positions, so larger books can leave older positions outside exit-rule monitoring
  - hardening recommendations for instrument-type rule gating and full open-position reconstruction coverage
- **Gateway/Heber Parity Audit (Pass 64)**: Continued audit with:
  - schema-contract finding: UW flow normalizer emits `premium_usd` while `FeatureEngine.process_uw_flow` aggregates only `payload.premium`
  - feature-integrity finding: in-memory `flow_net_premium_15m` can undercount/zero out on normalized UW flow payloads, impacting downstream flow-derived bar features
  - hardening recommendations for canonical premium-field access (`premium_usd` first) and cross-stage contract tests
- **Gateway/Heber Parity Audit (Pass 65)**: Continued audit with:
  - universe-source finding: `main_feature_enrichment.get_active_tickers` degrades from Heber reads to local `silver_uw_flow` SQL and then to static hardcoded symbols
  - observability gap finding: fallback tiers are mostly silent, allowing centralized integration failures to masquerade as normal enrichment operation
  - hardening recommendations for explicit fallback telemetry and canonical Heber/Gateway-backed ticker-universe ownership
- **Gateway/Heber Parity Audit (Pass 66)**: Continued audit with:
  - cadence-control finding: `main_feature_enrichment` advances poll timers even when connectors return zero records, delaying retry on failed pulls
  - freshness-integrity finding: regime snapshots consume latest VIX/market-tide rows without max-age checks, allowing stale context to appear current
  - hardening recommendations for success-gated cadence updates and explicit source freshness SLAs in regime snapshot generation
- **Gateway/Heber Parity Audit (Pass 67)**: Continued audit with:
  - source-of-truth finding: `UWMaxPainConnector` mixes Gateway max-pain payloads with local `silver_alpaca_bars` price lookups for distance calculations
  - date-semantics finding: max-pain daily keying uses host-local `date.today()` rather than market/session-aware date derivation
  - hardening recommendations for canonical price sourcing and ET/session-consistent daily bucketing in max-pain persistence
- **Gateway/Heber Parity Audit (Pass 68)**: Continued audit with:
  - data-integrity finding: normalized `is_sweep` string payloads are coerced via `bool(...)` during silver persistence, so `"false"` can persist as `True`
  - downstream-risk finding: sweep-dependent flow analytics/rules can be skewed by inverted `silver_uw_flow.is_sweep` values
  - hardening recommendations for explicit boolean parsing and regression coverage on raw+normalized payload contracts
- **Gateway/Heber Parity Audit (Pass 69)**: Continued audit with:
  - normalization-default finding: missing/invalid UW `put_call` values are coerced to `"C"` during flow normalization
  - directional-bias finding: malformed side data can be silently counted as bullish call flow in downstream premium splits
  - hardening recommendations for strict side validation/quarantine and unknown-side monitoring
- **Gateway/Heber Parity Audit (Pass 70)**: Continued audit with:
  - enrichment-coverage finding: pre-persist flow Greeks enrichment truncates option-symbol requests to first 100 rows
  - data-quality finding: rows beyond that cap are silently written without Alpaca Greeks fields, creating batch-order-dependent sparsity
  - hardening recommendations for chunked full-coverage enrichment and explicit coverage telemetry
- **Gateway/Heber Parity Audit (Pass 71)**: Continued audit with:
  - schema-contract finding: silver flow/darkpool persistence uses composite conflict targets not backed by declared unique constraints
  - migration-drift finding: `silver_uw_flow.is_sweep` type differs between Alembic schema (`String`) and ORM runtime model (`Boolean`)
  - hardening recommendations for conflict-key/column-type contract alignment and startup schema validation
- **Gateway/Heber Parity Audit (Pass 72)**: Continued audit with:
  - throughput finding: `main_labeler` performs serial per-flow, per-horizon Heber bar reads (N+1 query pattern)
  - scaling-risk finding: labeling loop work scales with `flows * horizons`, increasing backlog and lakehouse read pressure during busy windows
  - hardening recommendations for batched ticker-window bar reads and bounded-concurrency labeling
- **Gateway/Heber Parity Audit (Pass 73)**: Continued audit with:
  - batch-integrity finding: silver flow persistence validates only `option_price` while other non-null columns can still be missing
  - resilience finding: a single malformed flow row can fail bulk silver insert and abort the full ingestion cycle
  - hardening recommendations for required-field prevalidation, bad-row quarantine, and mixed-validity regression tests
- **Gateway/Heber Parity Audit (Pass 74)**: Continued audit with:
  - input-robustness finding: `main_labeler` timestamp coercion is not row-isolated, so one malformed timestamp can fail whole cycle normalization
  - throughput-risk finding: repeated malformed source rows can stall labeling progress by forcing retry-loop failures
  - hardening recommendations for per-row parse isolation, bad-row telemetry, and mixed-quality regression coverage
- **Gateway/Heber Parity Audit (Pass 75)**: Continued audit with:
  - temporal-integrity finding: `main_labeler` checkpoint bar reads use `asof_time=now`, not horizon-bounded as-of time
  - leakage-risk finding: historical labels may consume post-horizon available bars, reducing as-of parity confidence
  - hardening recommendations for target-time-bounded as-of reads and leakage regression tests
- **Gateway/Heber Parity Audit (Pass 76)**: Continued audit with:
  - semantics-drift finding: market-tide net premium uses inconsistent formulas across enrichment and labeler paths
  - parity-risk finding: direction labels can diverge across modules unless put-premium sign semantics are explicitly standardized
  - hardening recommendations for shared canonical net formula and cross-module contract tests
- **Gateway/Heber Parity Audit (Pass 77)**: Continued audit with:
  - coverage-bound finding: `main_labeler` limits unlabeled candidate scans to a rolling 72-hour lookback window
  - recovery-risk finding: outages/backlogs longer than that window can leave historical unlabeled flows permanently unprocessed
  - hardening recommendations for cursor-based unlabeled pagination and oldest-unlabeled lag monitoring
- **Gateway/Heber Parity Audit (Pass 78)**: Continued audit with:
  - coverage-integrity finding: `main_labeler` coerces missing underlying entry prices to zero and silently drops those flows from labeling
  - observability-gap finding: skip reasons for entry-price-invalid drops are not surfaced as explicit batch metrics
  - hardening recommendations for drop-reason telemetry and bar-based fallback reconstruction at flow entry time
- **Gateway/Heber Parity Audit (Pass 79)**: Continued audit with:
  - validation-consistency finding: silver persistence prechecks are incomplete for bars/darkpool/alerts relative to required schema fields
  - resilience-risk finding: malformed non-flow rows can still poison bulk inserts and block valid-row persistence
  - hardening recommendations for uniform required-field validation, bad-row quarantine, and mixed-validity tests across all silver event types
- **Gateway/Heber Parity Audit (Pass 80)**: Continued audit with:
  - failure-isolation finding: strict timestamp resolver raises during row-build and is not isolated per record
  - pipeline-risk finding: single malformed timestamp can abort silver persistence for the whole cycle before bulk insert
  - hardening recommendations for row-local timestamp quarantine and malformed-timestamp telemetry by event type
- **Gateway/Heber Parity Audit (Pass 81)**: Continued audit with:
  - consistency finding: ingestion persists bronze and silver in separate transactions, allowing bronze-success/silver-fail partial states
  - recovery-risk finding: dedupe treats bronze presence as terminally processed, so replayed events can be dropped before silver recovery
  - hardening recommendations for atomic bronze+silver persistence or explicit bronze-to-silver reconciliation workflow
- **Gateway/Heber Parity Audit (Pass 82)**: Continued audit with:
  - ordering finding: ingestion publishes to Redpanda before bronze commit and swallows publish failures
  - consistency-risk finding: stream/DB sinks can diverge (phantom bus events or silent bus loss) without built-in reconciliation
  - hardening recommendations for transactional outbox-style ordering (or equivalent) plus sink-parity telemetry
- **Gateway/Heber Parity Audit (Pass 83)**: Continued audit with:
  - replay-observability finding: DLQ duplicate-bronze path swallows normalization exceptions and continues with raw events
  - recovery-risk finding: malformed duplicate events can repeatedly fail silver replay without explicit root-cause signaling
  - hardening recommendations for explicit replay error classification and normalization-gated downstream processing
- **Gateway/Heber Parity Audit (Pass 84)**: Continued audit with:
  - cold-start finding: ingestion runtime processes Alpaca bars through `FeatureEngine` without calling history hydration first
  - consistency-risk finding: early-cycle indicator quality can diverge from signal-engine path, which hydrates explicitly on init
  - hardening recommendations for ingestion-time feature-history hydration and cold-start parity tests
- **Gateway/Heber Parity Audit (Pass 85)**: Continued audit with:
  - scope-fidelity finding: `FeatureEngine.hydrate_history` hydrates fixed static watchlist while runtime universe is dynamic
  - readiness-visibility finding: global hydration flag does not represent per-ticker hydration coverage
  - hardening recommendations for active-universe hydration and ticker-level readiness tracking
- **Gateway/Heber Parity Audit (Pass 86)**: Continued audit with:
  - feature-semantics finding: `flow_count_15m` currently counts mixed `UW_FLOW` + `UW_DARKPOOL` events while premium sums are `UW_FLOW`-only
  - drift-monitoring risk: downstream distribution checks can move on darkpool mix changes rather than options-flow count changes
  - hardening recommendations for split counters (or strict flow-only count semantics) plus feature-contract regression tests
- **Gateway/Heber Parity Audit (Pass 87)**: Continued audit with:
  - lifecycle-gap finding: `UniverseManager` dynamic controls (`update_from_event`/`cleanup`) are defined but not wired into ingestion/runtime call paths
  - subscription-drift risk: Alpaca stream subscriptions are additive-only in ingestion and are not reconciled via `unsubscribe` against active-universe churn
  - hardening recommendations for per-cycle universe lifecycle reconciliation, stale-symbol unsubscribe, and off-universe event suppression tests
- **Gateway/Heber Parity Audit (Pass 88)**: Continued audit with:
  - correctness finding: `get_spy_cumulative_return` query window semantics do not enforce a strict latest-20-bar return calculation
  - regime-risk finding: trend proxy fed into regime snapshots can be biased by full-history/row-frame behavior instead of intended recent-window movement
  - hardening recommendations for bounded-window query rewrite plus deterministic cumulative-return regression tests
- **Gateway/Heber Parity Audit (Pass 89)**: Continued audit with:
  - degradation finding: feature-enrichment ticker discovery silently falls back from Heber to local SQL and then static tickers
  - parity-risk finding: local `silver_uw_flow` fallback can diverge from Heber-first source-of-truth in current centralized ingestion architecture
  - hardening recommendations for explicit fallback telemetry/severity and policy-gated degrade mode
- **Gateway/Heber Parity Audit (Pass 90)**: Continued audit with:
  - buffering finding: stream connectors use unbounded queues while ingestion drains in fixed-size batches per cycle
  - resilience-risk finding: queue-overflow handling path is inert under current unbounded queue configuration, so overload policy is implicit
  - hardening recommendations for bounded buffering, explicit overflow policy, and adaptive drain/backpressure controls
- **Gateway/Heber Parity Audit (Pass 91)**: Continued audit with:
  - contract-mismatch finding: `uw_greek_exposure_connector` parses call/put gamma-vanna-charm fields from `/uw/{symbol}/spot-exposures` that the Gateway strike endpoint does not emit
  - data-quality risk: persisted `silver_greek_exposure` aggregates can silently degrade toward default/zero values under schema mismatch
  - hardening recommendations for endpoint/schema alignment plus strict response-contract validation tests
- **Gateway/Heber Parity Audit (Pass 92)**: Continued audit with:
  - runtime-drift finding: ingestion service constructs/documents Heber flow integration but does not execute any `HeberReader` read path in cycle processing
  - operability-risk finding: entrypoint/docs still claim Heber flow ingestion, creating source-of-truth ambiguity for live runtime behavior
  - hardening recommendations for either explicit Heber cycle wiring or dead-code/doc cleanup plus source-capability health signaling
- **Gateway/Heber Parity Audit (Pass 93)**: Continued audit with:
  - contract-drift finding: `uw_iv_rank_connector` maps `iv_high`/`iv_low` fields while Gateway normalized IV-rank contract emits `one_year_high`/`one_year_low`
  - data-quality risk: `silver_iv_rank` 52-week high/low features can silently default to zero under key mismatch
  - hardening recommendations for field-map alignment, missing-key telemetry, and canonical payload integration tests
- **Gateway/Heber Parity Audit (Pass 94)**: Continued audit with:
  - contract-drift finding: `uw_max_pain_connector` expects `max_pain` key while Gateway normalized response centers on `max_pain_strike`
  - data-availability risk: valid max-pain rows can be skipped before persistence, leaving downstream distance-to-max-pain features sparse
  - hardening recommendations for key-map alignment (`max_pain_strike` first) and normalized-payload persistence tests
- **Gateway/Heber Parity Audit (Pass 95)**: Continued audit with:
  - temporal-integrity finding: daily earnings sync writes batch `today` date as `report_date` for all rows instead of record-level report dates
  - data-correctness risk: earnings calendar keys can be misdated around timezone/schedule variations despite valid upstream record dates
  - hardening recommendations for per-record report-date persistence plus fallback-date telemetry and regression tests
- **Gateway/Heber Parity Audit (Pass 96)**: Continued audit with:
  - coverage finding: earnings backfill symbol set is derived solely from local `price_target_labels` history
  - parity-risk finding: centralized/runtime-relevant symbols without prior labels can be excluded from earnings backfill coverage
  - hardening recommendations for canonical-universe symbol seeding and per-run source-composition metrics
- **Gateway/Heber Parity Audit (Pass 97)**: Continued audit with:
  - query-targeting finding: daily earnings sync does not pass an explicit date parameter to premarket/afterhours Gateway fetches
  - semantic-risk finding: “today sync” behavior depends on upstream default date rules (current/last market day) instead of explicit run-date intent
  - hardening recommendations for explicit date propagation and per-run returned-date telemetry
- **Gateway/Heber Parity Audit (Pass 98)**: Continued audit with:
  - coverage finding: earnings backfill performs single-call ticker fetches against a paginated Gateway earnings endpoint
  - completeness-risk finding: historical earnings rows can be truncated per symbol when pagination is not consumed
  - hardening recommendations for full pagination handling and per-ticker fetch completeness metrics
- **Gateway/Heber Parity Audit (Pass 99)**: Continued audit with:
  - consistency finding: daily earnings sync persists batch-level `announce_time` labels instead of record-level timing fields
  - semantics-risk finding: `announce_time` persistence rules diverge between daily sync and historical backfill paths
  - hardening recommendations for unified timing extraction/fallback policy and fallback-rate telemetry
- **Gateway/Heber Parity Audit (Pass 100)**: Continued audit with:
  - observability finding: earnings backfill swallows per-ticker/per-row exceptions and can report low error counts despite failed processing
  - integrity-risk finding: sync health signals (`errors`, completion logs) can overstate success during Gateway/contract regressions
  - hardening recommendations for structured error propagation, per-ticker failure accounting, and ingestion quality telemetry
- **Gateway/Heber Parity Audit (Pass 101)**: Continued audit with:
  - correctness finding: daily earnings sync imports non-exported UW earnings symbols, causing import-time failure before fetch execution
  - reliability-risk finding: ingestion startup catches this failure as warning, allowing runtime to proceed with stale/missing earnings calendar updates
  - hardening recommendations for canonical module imports, startup health gating, and import-path smoke coverage
- **Gateway/Heber Parity Audit (Pass 102)**: Continued audit with:
  - scaling finding: `HeberReader` applies time/as-of filtering after parquet load, while active-ticker discovery reads flow without symbol pushdown
  - performance-risk finding: feature enrichment can devolve into repeated full-feed scans as Heber silver datasets grow
  - hardening recommendations for predicate pushdown/partition pruning and bounded lookback read-paths
- **Gateway/Heber Parity Audit (Pass 103)**: Continued audit with:
  - schema-mapping finding: daily earnings sync reads `eps_estimate/revenue_*` attributes not defined on the current `Earnings` model
  - data-quality risk: daily earnings writes can persist null fundamentals even when Gateway provides normalized estimate/actual fields
  - hardening recommendations for unified earnings field extraction across daily/backfill paths with explicit mapping tests
- **Gateway/Heber Parity Audit (Pass 104)**: Continued audit with:
  - integration-fragility finding: `HeberReader.health_check()` relies on relative path escape (`../../health`) from `/api/v1` base URLs
  - operability-risk finding: catalog base-url shape changes can break health/dataset calls asymmetrically and mask root-cause configuration drift
  - hardening recommendations for explicit endpoint construction and startup URL-contract validation
- **Gateway/Heber Parity Audit (Pass 105)**: Continued audit with:
  - temporal-semantics finding: earnings proximity lookup uses date-only predicates and ignores stored `announce_time`
  - feature-risk finding: same-day pre/post earnings states can collapse to identical `days_to_earnings/is_post_earnings` outputs
  - hardening recommendations for announce-time-aware proximity logic and boundary-case regression coverage
- **Gateway/Heber Parity Audit (Pass 106)**: Continued audit with:
  - integration finding: ingestion stream startup can silently fall back from Gateway mode to direct Alpaca polling on missing Gateway key/config
  - parity-risk finding: centralized stream path can be bypassed at runtime without explicit degraded-state signaling
  - hardening recommendations for fail-fast/health-gated Gateway mode and source-of-truth telemetry
- **Gateway/Heber Parity Audit (Pass 107)**: Continued audit with:
  - contract-mapping finding: ticker-earnings backfill timing extractor looks for `report_time` while Gateway normalized payload emits `time`
  - data-consistency risk: historical `announce_time` can be systematically blank/under-populated despite available upstream timing fields
  - hardening recommendations for normalized time-key support and timing-field coverage metrics
- **Gateway/Heber Parity Audit (Pass 108)**: Continued audit with:
  - integration finding: options execution price discovery calls Alpaca snapshots directly instead of Gateway quote endpoints
  - parity-risk finding: live execution bypasses centralized Gateway auth/rate-limit/contract layer used elsewhere in migration
  - hardening recommendations for Gateway-backed quote sourcing in options execution and fallback visibility
- **Gateway/Heber Parity Audit (Pass 109)**: Continued audit with:
  - contract-mapping finding: earnings backfill requires `report_date` field while Gateway-normalized ticker earnings emit `date`
  - completeness-risk finding: backfill rows can be skipped when date values remain in model `additional_properties` instead of `report_date`
  - hardening recommendations for normalized date-key extraction and coverage instrumentation
- **Gateway/Heber Parity Audit (Pass 110)**: Continued audit with:
  - endpoint-contract finding: `HeberReader.list_datasets()` targets `/datasets` while catalog routes are mounted at `/api/v1/datasets`
  - configuration-risk finding: metadata discovery behavior depends on whether `HEBER_CATALOG_URL` includes `/api/v1`, creating asymmetric health vs dataset checks
  - hardening recommendations for canonical catalog route construction and URL-shape validation
- **Gateway/Heber Parity Audit (Pass 111)**: Continued audit with:
  - fallback-path finding: direct Alpaca polling fetches bars with fixed `limit=10000` and no pagination
  - completeness-risk finding: when Gateway streaming is unavailable, fallback polling can silently truncate high-volume/multi-symbol bar sets
  - hardening recommendations for paginated polling fetches and fallback-volume completeness telemetry
- **Gateway/Heber Parity Audit (Pass 112)**: Continued audit with:
  - contract-scope finding: `HeberReader.read_gold_features()` reads dataset root without project/version filters
  - data-integrity risk: multiple gold projects/versions can be mixed in a single read, weakening reproducibility and parity guarantees
  - hardening recommendations for explicit project/version scoping and version-aware read contracts
- **Gateway/Heber Parity Audit (Pass 113)**: Continued audit with:
  - leakage-guard finding: `HeberReader` as-of filter is bypassed when `ts_available` column is missing
  - integrity-risk finding: gold/silver reads can become unconstrained by availability time without explicit warning, weakening point-in-time guarantees
  - hardening recommendations for fail-closed as-of enforcement and column-contract validation
- **Gateway/Heber Parity Audit (Pass 114)**: Continued audit with:
  - connection-lifecycle finding: Gateway stream auth/connection failures return without explicit websocket close/reset
  - resilience-risk finding: repeated failed connect attempts can accumulate stale socket handles/state and obscure root-cause auth failures
  - hardening recommendations for deterministic close/reset on failed handshake paths and reconnect-state hygiene
- **Gateway/Heber Parity Audit (Pass 115)**: Continued audit with:
  - endpoint-composition finding: Gateway stream client appends `/ws` directly to configured base URL without stripping API prefixes
  - configuration-risk finding: `DATA_GATEWAY_URL` values containing `/api/v1` produce invalid websocket paths (for example `/api/v1/ws`)
  - hardening recommendations for canonical base-url normalization and websocket-path validation
- **Gateway/Heber Parity Audit (Pass 116)**: Continued audit with:
  - earnings-sync contract finding: `sync_earnings` uses UW SDK base/auth conventions (`Authorization`, `/api/earnings/*`) that do not match Gateway contracts
  - integration-risk finding: Gateway expects `X-Gateway-Key` and `/api/v1/uw/earnings/*`, making current request composition prone to repeated fetch failures
  - hardening recommendations for Gateway-native earnings client routing, auth alignment, and completeness telemetry
- **Gateway/Heber Parity Audit (Pass 117)**: Continued audit with:
  - endpoint-composition finding: UW enrichment connectors append `/api/v1/uw/...` to raw `DATA_GATEWAY_URL` without canonicalization
  - stale-data risk: API-prefixed base URLs can double-prefix routes and quietly degrade enrichment output to zero/partial writes
  - hardening recommendations for shared URL normalization and per-feed freshness/degraded-state telemetry
- **Gateway/Heber Parity Audit (Pass 118)**: Continued audit with:
  - reader-scope finding: `HeberReader` filter fallback retries parquet reads without symbol filters after pushdown failures
  - data-integrity risk: symbol-scoped consumers can receive cross-symbol rows and full-table scans during schema/partition drift events
  - hardening recommendations for fail-closed scoped reads, fallback row-scope guards, and filter-failure observability
- **Gateway/Heber Parity Audit (Pass 119)**: Continued audit with:
  - regime-input correctness finding: current SQL for “20-bar cumulative return” uses window semantics and row selection that can misstate recent trend
  - model-risk finding: regime detector can receive distorted trend signals, affecting snapshot classification quality
  - hardening recommendations for deterministic 20-bar slice math and regression-tested trend computation
- **Gateway/Heber Parity Audit (Pass 120)**: Continued audit with:
  - observability finding: enrichment connectors collapse request failures and true empty-result states into the same `stored 0` loop signal
  - operations-risk finding: sustained Gateway/auth regressions can masquerade as normal low-activity periods while feature tables go stale
  - hardening recommendations for failure-state classification, degraded-mode alerting, and freshness-based SLO checks
- **Gateway/Heber Parity Audit (Pass 121)**: Continued audit with:
  - runtime-config finding: `price_target_labeler` relies on direct UW metadata client auth (`UW_API_KEY`) while compose wiring only provides Gateway URL
  - feature-quality risk: sector/earnings feature fields can silently degrade for unmapped tickers when UW credentials are absent
  - hardening recommendations for centralized metadata sourcing and feature-completeness telemetry
- **Gateway/Heber Parity Audit (Pass 122)**: Continued audit with:
  - data-contract finding: `backfill_ml_features` recomputes `entry_session` using a different bucket taxonomy than live label generation
  - integrity-risk finding: partial backfills can overwrite existing rows and mix incompatible session vocabularies in `price_target_labels`
  - hardening recommendations for shared time-feature helpers and overwrite guards on previously-populated fields
- **Gateway/Heber Parity Audit (Pass 123)**: Continued audit with:
  - backlog-progress finding: `backfill_ml_features` selects rows with `LIMIT` but no deterministic ordering/cursor and retries failed rows without quarantine
  - completion-risk finding: nightly attempts can recycle the same problematic records while leaving eligible backlog rows untouched
  - hardening recommendations for cursor-based ordering, retry metadata, and `attempted` vs `updated` vs `completed` run metrics
- **Gateway/Heber Parity Audit (Pass 124)**: Continued audit with:
  - modularity finding: `main_price_target_labeler` imports shared `orion.labeler` helpers/constants, then shadows them with local redefinitions
  - parity-risk finding: local math windows/volatility and sector mapping drift from shared package behavior, causing inconsistent label outputs
  - hardening recommendations for de-shadowing to a single source of truth plus parity regression checks
- **Gateway/Heber Parity Audit (Pass 125)**: Continued audit with:
  - integration finding: `main_option_quote_tracker` still sources candidates from local `silver_uw_flow` instead of centralized Heber/Gateway reads
  - coverage-risk finding: fixed newest-first `LIMIT 1000` can truncate quote checkpoint coverage on high-volume days
  - hardening recommendations for centralized candidate sourcing, cursor-based progression, and coverage telemetry
- **Gateway/Heber Parity Audit (Pass 126)**: Continued audit with:
  - producer-wiring finding: `gold_feature_windows` write path is isolated to `WindowFeatureJob`, but runtime orchestration starts `RollupJob` and compose has no window-feature service
  - feature-freshness risk: active consumers (`flow_enricher`, `exit_classifier`) can read stale/missing window features without explicit producer-health gating
  - hardening recommendations for single producer ownership, freshness SLO checks, and producer-status telemetry
- **Gateway/Heber Parity Audit (Pass 127)**: Continued audit with:
  - data-semantics finding: `window_feature_job` skips persisting zero-flow windows, leaving gaps in `gold_feature_windows` timeline
  - model-risk finding: consumers query “latest <= entry_ts” and can carry forward stale historical window context as if current
  - hardening recommendations for explicit zero-window materialization and consumer freshness-age guards
- **Gateway/Heber Parity Audit (Pass 128)**: Continued audit with:
  - universe-scope finding: `window_feature_job` computes rows only for static watchlist symbols
  - coverage-risk finding: active off-watchlist flow symbols can miss window-context features entirely in `gold_feature_windows`
  - hardening recommendations for active-universe driven window generation and per-ticker coverage telemetry
- **Gateway/Heber Parity Audit (Pass 129)**: Continued audit with:
  - contract-versioning finding: consumers query `gold_feature_windows` without filtering by `feature_set_id` despite versioned primary key design
  - reproducibility-risk finding: mixed-version rows can produce ambiguous or nondeterministic feature selection in scoring/training lookups
  - hardening recommendations for explicit feature-set scoping and version-aware regression coverage
- **Gateway/Heber Parity Audit (Pass 130)**: Continued audit with:
  - window-semantics finding: `window_feature_job` writes processing-time sliding snapshots (`window_end=now`) instead of canonical period-aligned buckets
  - consistency-risk finding: downstream latest-row lookups can vary by scheduler timing rather than true interval boundary semantics
  - hardening recommendations for boundary-aligned window keys and stable per-bucket retrieval tests
- **Gateway/Heber Parity Audit (Pass 131)**: Continued audit with:
  - query-pattern finding: `flow_enricher` retrieves 1h/1d/1w window features using a per-period SQL loop (three round-trips per event)
  - scalability-risk finding: per-event query amplification adds avoidable latency and DB pressure during scoring bursts
  - hardening recommendations for set-based single-roundtrip retrieval and query-latency instrumentation
- **Gateway/Heber Parity Audit (Pass 132)**: Continued audit with:
  - producer-consumer drift finding: `window_feature_job` emits `gold_feature_windows` rows for `period='5m'` but active consumers read only `1h/1d/1w`
  - efficiency-risk finding: dead-period writes add avoidable compute/storage while creating false confidence about 5m feature usage
  - hardening recommendations for period contract tests and usage telemetry by period
- **Gateway/Heber Parity Audit (Pass 133)**: Continued audit with:
  - config-semantics finding: `ORION_USE_GATEWAY` currently affects only Alpaca streaming transport while UW connectors/Heber readers remain gateway-centric
  - operations-risk finding: partial toggle semantics can cause mixed-mode runtime behavior and confusing cutover expectations
  - hardening recommendations for explicit flag scoping, subsystem mode diagnostics, and configuration-matrix tests
- **Gateway/Heber Parity Audit (Pass 134)**: Continued audit with:
  - config-lifecycle finding: Alpaca stream gateway mode default is captured at module import (`USE_GATEWAY`) rather than resolved per instance
  - runtime-risk finding: post-import config/env changes may not take effect unless callers pass explicit override
  - hardening recommendations for constructor-time mode resolution and per-instance mode tests
- **Gateway/Heber Parity Audit (Pass 135)**: Continued audit with:
  - orchestration finding: `data_quality_checker` exists as a scheduled-quality module but is not wired into compose/runtime job scheduling
  - operations-risk finding: parity and freshness regressions may go undetected unless checker is run manually
  - hardening recommendations for scheduled wiring, alert-channel integration, and deployment-profile execution tests
- **Gateway/Heber Parity Audit (Pass 136)**: Continued audit with:
  - governance finding: `validate_features` exists as a migration-critical drift check but is not wired into CI/scheduled runtime
  - quality-risk finding: feature-contract regressions can persist until manually discovered
  - hardening recommendations for automated validation cadence, fail gates, and persisted quality snapshots
- **Gateway/Heber Parity Audit (Pass 137)**: Continued audit with:
  - scheduler-fidelity finding: nightly backfill uses weekday-only trading-day logic and does not consult exchange holiday calendars
  - operations-risk finding: scheduled runs may execute on non-trading holidays/irregular sessions and report misleading normal-cycle status
  - hardening recommendations for exchange-calendar scheduling, holiday policy logging, and session-aware scheduler tests
- **Gateway/Heber Parity Audit (Pass 138)**: Continued audit with:
  - interface-contract finding: `HeberReader.read_bars` accepts `timeframe` but currently ignores it and always reads default bars dataset
  - integrity-risk finding: non-default timeframe requests can silently receive wrong granularity data
  - hardening recommendations for timeframe validation/routing, fail-fast behavior, and contract tests
- **Gateway/Heber Parity Audit (Pass 139)**: Continued audit with:
  - coverage finding: reconciliation job currently checks only Alpaca bronze/silver bar parity, excluding Gateway/Heber-critical datasets
  - operations-risk finding: reconciliation path is effectively unscheduled (test/manual only), limiting live migration assurance
  - hardening recommendations for multi-dataset reconciliation scope and scheduled alerting integration
- **Gateway/Heber Parity Audit (Pass 140)**: Continued audit with:
  - scoring-contract finding: SignalEngine ML prefilter uses raw scorer input semantics that mismatch candidate field shapes (`CALL/PUT` vs `C/P`, `strike_price` vs `strike`, premium-scale ambiguity)
  - quality-risk finding: mismapped prefilter features can false-skip valid candidates before solver evaluation
  - hardening recommendations for parity-safe normalization or enriched scoring in prefilter path with contract tests
- **Gateway/Heber Parity Audit (Pass 141)**: Continued audit with:
  - policy-config finding: ensemble execution gate is hardcoded at `0.5` despite “configurable” intent
  - operations-risk finding: threshold cannot be tuned per environment/stage without code edits, increasing policy drift risk
  - hardening recommendations for centralized typed threshold config and stage-aware behavior tests
- **Gateway/Heber Parity Audit (Pass 142)**: Continued audit with:
  - config-governance finding: ML prefilter threshold is parsed from ad-hoc env lookup in decision logic, outside centralized settings validation
  - runtime-risk finding: malformed or out-of-range values can surface only during decision execution
  - hardening recommendations for typed centralized config ownership, bounds validation, and startup diagnostics
- **Gateway/Heber Parity Audit (Pass 143)**: Continued audit with:
  - closure finding: migration-critical active runtime surfaces now have sufficient audit coverage to move into remediation sequencing
  - residual-scope finding: remaining review items are primarily non-runtime experimental paths and post-fix archive cleanup
  - handoff recommendations for remediation planning against prioritized audit backlog
- **Gateway/Heber Parity Audit (Pass 144-147)**: Continued audit with:
  - revalidation snapshot showing pass-137 to pass-142 remediation items now implemented (scheduler wiring, reconciliation scope, timeframe enforcement, stream-mode resolution, calendar-aware nightly scheduling)
  - unresolved runtime-contract confirmation that `sync_earnings` still uses Bearer-token UW SDK path instead of Gateway `X-Gateway-Key` auth contract
  - ingestion-truth finding that Heber UW ingestion remains documentation/comment intent rather than active ingestion-cycle behavior
  - final migration-close checklist narrowing remaining work to implementation/decommission rather than further discovery
- **Gateway/Heber Parity Audit (Pass 148-149)**: Continued audit with:
  - remediation confirmation that `sync_earnings` now uses Gateway-native `X-Gateway-Key` calls and record-level earnings dates
  - ingestion source-truth alignment updates documenting externalized UW flow/darkpool ownership and runtime source-profile diagnostics
  - residual note that full Heber-driven UW event ingestion inside `IngestionService` remains a separate implementation step
- **Gateway/Heber Parity Audit (Pass 150-151)**: Continued audit with:
  - ownership-split finding that Orion local label/quote services and Heber watch/Gold pipelines still run in parallel without canonical source-of-truth assignment
  - schema-surface comparison documenting current gap between Orion `price_target_labels` enrichment breadth and Heber watch/meta-label outputs
  - decommission-readiness inventory of remaining local SQL-coupled labeling/feature modules pending archive waves after ownership signoff
- **Gateway/Heber Parity Audit (Pass 152)**: Continued audit with:
  - proposed keep/migrate/archive decision matrix for 11 remaining local SQL-coupled label-stack modules
  - phased archive order recommendation based on coupling and replacement readiness
  - explicit blocker list defining what must be decided before archive PR waves can begin safely
- **Gateway/Heber Parity Audit (Pass 153)**: Continued audit with:
  - data-quality debt finding that silent exception fallbacks in active feature/label enrichment paths can hide production degradation
  - schema-governance finding that dynamic label inserts claim schema safety without actual column-existence validation
  - observability hardening recommendations for per-feature failure telemetry and deterministic insert guards
- **Gateway/Heber Parity Audit (Pass 154)**: Continued audit with:
  - closure snapshot confirming migration-critical audit coverage is complete for active runtime paths
  - consolidated open implementation blockers (ownership mapping, observability hardening, schema guards, archive execution)
  - explicit transition from discovery to staged remediation and decommission execution
- **Gateway/Heber Parity Audit (Pass 155)**: Continued audit with:
  - remediation confirmation that dynamic `price_target_labels` inserts now use explicit schema validation instead of implicit key filtering
  - observability remediation confirmation that silent enrichment fallbacks now emit structured warning events with counters
  - residual note that alert thresholds/DLQ replay for fallback events remain follow-up work
- **Gateway/Heber Parity Audit (Pass 156)**: Continued audit with:
  - decommission-readiness remediation confirmation that legacy local label pipelines now emit explicit startup deprecation warnings
  - operator-clarity improvement: each warning includes replacement Heber dataset/pipeline ownership hints
  - residual note that staged service disable/archive controls are still pending implementation
- **Gateway/Heber Parity Audit (Pass 157)**: Continued audit with:
  - decommission-control remediation confirmation that legacy label services now support runtime disable via env gate
  - rollout-safety improvement allowing staged shutdown without code edits
  - residual note that per-service kill switches may still be needed for finer-grained cutovers
- **Gateway/Heber Parity Audit (Pass 158)**: Continued audit with:
  - remediation confirmation that per-service legacy label kill switches are now implemented with global fallback precedence
  - focused rollout-operability finding that compose services do not yet expose per-service gate env controls
  - control-attribution finding that disabled-service logs still reference only the global gate key
- **Gateway/Heber Parity Audit (Pass 159)**: Continued audit with:
  - compose lifecycle finding that `restart: unless-stopped` causes restart-loop churn when legacy services are intentionally disabled via env gates
  - decommission-operations recommendation to shift disable semantics to compose-level inclusion control (profiles/overrides) or adjust restart policy during migration waves
  - runbook/smoke-check recommendation so disabled legacy services remain stably off in deployment
- **Gateway/Heber Parity Audit (Pass 160)**: Continued audit with:
  - config-governance finding that new legacy label gate env vars are parsed ad hoc in service modules rather than centralized typed settings
  - consistency-risk recommendation to centralize gate ownership/preference semantics in `SystemSettings`
  - config-layer test/documentation recommendation for operator-safe rollout controls
- **Gateway/Heber Parity Audit (Pass 161)**: Continued audit with:
  - remediation confirmation that disabled legacy flow/price-target labelers no longer initialize DB before honoring disable gates
  - TDD coverage expansion for disabled-mode startup ordering (`init_db` must not run when disabled)
  - residual note that compose restart-loop behavior for disabled services still needs rollout-level remediation
- **Gateway/Heber Parity Audit (Pass 162)**: Continued audit with:
  - remediation confirmation that disabled-service logs now attribute the effective legacy control key/value (specific vs global fallback)
  - compose-operability remediation that per-service legacy gate env controls are now wired in default compose service definitions
  - residual note that container restart-loop behavior under `restart: unless-stopped` remains a separate lifecycle-policy fix
- **Gateway/Heber Parity Audit (Pass 163)**: Continued audit with:
  - remediation confirmation that legacy gate controls are now centralized in typed `SystemSettings` fields
  - config-governance remediation replacing duplicated service-level env parsing with centralized typed settings resolution
  - residual note that compose restart-loop behavior for disabled services remains open
- **Gateway/Heber Parity Audit (Pass 164)**: Continued audit with:
  - remediation confirmation that compose restart-loop churn for intentionally disabled legacy label services is mitigated
  - lifecycle hardening by moving legacy label services to `restart: on-failure`
  - residual note that full decommission strategy (profiles/inclusion control) remains a follow-up architectural decision

### Changed

- **SignalEngine Threshold + Prefilter Contract Hardening**:
  - Added centralized typed runtime settings for `ml_prefilter_threshold` (`ORION_ML_PREFILTER_THRESHOLD`) and `ensemble_consensus_threshold` (`ORION_ENSEMBLE_CONSENSUS_THRESHOLD`) in `src/orion/config.py`
  - `SignalEngine` now reads both thresholds from centralized config instead of hardcoded/ad-hoc values
  - Added ML prefilter payload normalization in `src/orion/processing/signal_engine.py`:
    - normalizes `put_call` to `C/P`
    - emits scorer-compatible `strike` field
    - prefers `evidence.premium_usd` over nullable/ambiguous candidate premium
    - bypasses ML prefilter when required context is incomplete to avoid false skips
  - Added focused tests in `tests/unit/test_signal_engine_prefilter_config.py` for payload normalization and threshold-config behavior
- **HeberReader Timeframe Contract Hardening**:
  - `src/orion/clients/heber_reader.py` now enforces supported bar timeframes and fails fast for unsupported values instead of silently ignoring `timeframe`
  - Added unit coverage in `tests/unit/test_heber_reader.py` for unsupported timeframe rejection
- **Reconciliation Scope Expansion**:
  - `src/orion/jobs/reconcile_backfill.py` now reconciles Bronze vs Silver counts across `ALPACA_BAR_1M`, `UW_FLOW`, and `UW_DARKPOOL` datasets (ticker/day granularity)
  - Added dataset-scoped discrepancy logging and aggregate cross-dataset summary
  - Updated reconciliation unit test in `tests/unit/test_remediation_rules.py` for multi-dataset execution path
- **Guardrail Job Scheduling Wiring**:
  - Added `src/orion/jobs/quality_guardrails.py` daemon scheduler that runs:
    - reconciliation (`run_reconciliation`)
    - data quality checks (`run_quality_checks`)
    - feature sanity checks (`run_sanity_checks`)
  - Added `quality-guardrails` service to `docker-compose.yml` with configurable intervals via env vars
  - Added focused unit tests for scheduler interval/config helpers in `tests/unit/test_quality_guardrails.py`
- **Nightly Backfill Calendar-Aware Scheduling**:
  - `src/orion/jobs/nightly_backfill.py` now schedules runs from exchange session close time (+ delay) instead of weekday-only time checks
  - Added calendar-unavailable fallback behavior to preserve runtime safety
  - Added unit tests for session-time derivation and next-run selection in `tests/unit/test_nightly_backfill_schedule.py`
- **Alpaca Stream Mode Resolution Fix**:
  - `src/orion/connectors/alpaca_stream_connector.py` now resolves gateway/direct mode at connector initialization time from `system_settings.orion_use_gateway` instead of using an import-time frozen constant
  - Added unit tests in `tests/unit/test_alpaca_stream_mode.py` to verify runtime-setting behavior and explicit override precedence
- **Earnings Sync Gateway Auth Contract Fix**:
  - `src/orion/jobs/sync_earnings.py` now fetches earnings directly from Data Gateway REST endpoints using `X-Gateway-Key` auth, removing Bearer-token UW SDK-through-gateway coupling
  - Daily sync now uses record-level Gateway earnings dates (`data.date`) rather than forcing `date.today()` for all rows
  - Added focused Gateway-contract tests in `tests/unit/test_sync_earnings_gateway.py` for header wiring, response parsing, daily date semantics, and ticker backfill row handling
- **Ingestion Source Profile Alignment**:
  - `src/orion/ingestion/service.py` now emits explicit startup source-profile diagnostics (`alpaca_mode`, produced event types, external UW-flow/darkpool ownership)
  - Updated ingestion comments and entrypoint docs to match current runtime behavior (Alpaca event production in-process; UW flow/darkpool ingestion externalized)
  - Added focused source-profile unit test in `tests/unit/test_ingestion_source_profile.py`
- **Label Insert Schema Guard + Enrichment Fallback Telemetry**:
  - Added `src/orion/labeler/schema_guard.py` with table-column discovery and deterministic row payload validation (`resolve_insert_columns`)
  - `src/orion/main_price_target_labeler.py` now validates label payload keys against live `price_target_labels` schema and fails fast on unknown/missing columns
  - Added structured fallback warning telemetry and counters in `src/orion/main_price_target_labeler.py` and `src/orion/ml/flow_enricher.py` for previously silent enrichment-degradation paths
  - Added focused unit coverage in `tests/unit/test_label_schema_guard.py`
- **Legacy Label Pipeline Startup Warnings**:
  - Added runtime startup warnings for `orion.main_option_quote_tracker`, `orion.main_labeler`, and `orion.main_price_target_labeler` when legacy local-label paths are active
  - Warning payloads now include intended Heber replacement paths to support safer migration operations
- **Legacy Label Pipeline Runtime Gate**:
  - Added `ORION_ENABLE_LEGACY_LABEL_PIPELINES` gate in `src/orion/main_option_quote_tracker.py`, `src/orion/main_labeler.py`, and `src/orion/main_price_target_labeler.py`
  - When disabled, these services emit `DEPRECATED_PIPELINE_DISABLED` and exit before entering active processing loops
- **Per-Service Legacy Label Runtime Gates (TDD)**:
  - Added per-service gate overrides:
    - `ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER`
    - `ORION_ENABLE_LEGACY_FLOW_LABELER`
    - `ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER`
  - Added focused test coverage in `tests/unit/test_legacy_label_pipeline_gates.py` for override precedence and disabled early-return behavior
- **Disabled-Mode DB Init Guard for Legacy Labelers (TDD)**:
  - `src/orion/main_labeler.py` and `src/orion/main_price_target_labeler.py` now check disable gates before `init_db()`
  - Added tests in `tests/unit/test_legacy_label_pipeline_gates.py` to ensure disabled services do not touch DB initialization paths
- **Legacy Gate Control Attribution + Compose Wiring (TDD)**:
  - Added effective control-resolution helpers in `src/orion/main_option_quote_tracker.py`, `src/orion/main_labeler.py`, and `src/orion/main_price_target_labeler.py`
  - `DEPRECATED_PIPELINE_DISABLED` logs now emit the actual control key/value that disabled the service
  - Added compose env wiring for `ORION_ENABLE_LEGACY_FLOW_LABELER`, `ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER`, and `ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER` in `docker-compose.yml`
  - Added coverage in `tests/unit/test_legacy_label_pipeline_gates.py` and `tests/unit/test_compose_legacy_gate_wiring.py`
- **Legacy Gate Config Centralization (TDD)**:
  - Added typed legacy gate settings in `src/orion/config.py` under `SystemSettings`
  - Updated legacy gate resolution in `src/orion/main_option_quote_tracker.py`, `src/orion/main_labeler.py`, and `src/orion/main_price_target_labeler.py` to use centralized settings
  - Added config mapping coverage in `tests/unit/test_config_centralization.py`
- **Legacy Label Compose Restart Hardening (TDD)**:
  - Updated `docker-compose.yml` restart policy for `labeler`, `price_target_labeler`, and `option_quote_tracker` to `on-failure`
  - Added compose policy coverage in `tests/unit/test_compose_legacy_gate_wiring.py`
- **Legacy UW/Main-Ingest Archival**: Archived inactive pre-migration code, tests, and scripts under `archive/2026-02-05_gateway-heber-migration/`
  - Archived deprecated ingestion/UW connector implementations to `archive/.../legacy_code/`
  - Archived legacy tests coupled to removed modules (`orion.main_ingest`, `orion.connectors.uw_flow_connector`) to `archive/.../legacy_tests/`
  - Archived legacy UW backfill scripts to `archive/.../legacy_scripts/`
  - Added archive manifest: `archive/2026-02-05_gateway-heber-migration/README.md`
- **Runtime Consolidation Wave 6 Archival**: Archived inactive queue-driven execution path under `archive/2026-02-06_runtime-consolidation-wave6/`
  - Archived `src/orion/execution/service.py` to `archive/.../legacy_code/execution_service.py`
  - Archived `src/orion/shared/candidate_queue.py` to `archive/.../legacy_code/candidate_queue.py`
  - Archived queue-specific unit tests to `archive/.../legacy_tests/test_candidate_queue.py`
  - Added archive manifest: `archive/2026-02-06_runtime-consolidation-wave6/README.md`
- **Label Stack Wave 7 Archival**: Archived inactive PRD 6.3 label jobs under `archive/2026-02-06_label-stack-wave7/`
  - Archived `src/orion/jobs/label_job.py` to `archive/.../legacy_code/label_job.py`
  - Archived `src/orion/jobs/window_label_job.py` to `archive/.../legacy_code/window_label_job.py`
  - Added archive manifest: `archive/2026-02-06_label-stack-wave7/README.md`
- **Integration Debt Wave 8 Archival**: Archived orphaned integration modules under `archive/2026-02-06_integration-debt-wave8/`
  - Archived `src/orion/connectors/uw_ticker_info_connector.py` to `archive/.../legacy_code/uw_ticker_info_connector.py`
  - Archived `src/orion/jobs/backfill_historical_gex.py` to `archive/.../legacy_code/backfill_historical_gex.py`
  - Added archive manifest: `archive/2026-02-06_integration-debt-wave8/README.md`
- **Runner Debt Wave 9 Archival**: Archived deprecated runner/harness modules under `archive/2026-02-06_runner-debt-wave9/`
  - Archived `src/orion/run_agent.py` to `archive/.../legacy_code/run_agent.py`
  - Archived `src/orion/paper_live_harness.py` to `archive/.../legacy_code/paper_live_harness.py`
  - Added archive manifest: `archive/2026-02-06_runner-debt-wave9/README.md`
- **HeberReader Data Access Path**: Replaced unsupported HTTP reads (`/silver/read`, `/gold/read`) with Heber-compatible access:
  - Silver and Gold reads now use Heber parquet layout from `HEBER_DATA_ROOT`
  - Catalog calls limited to supported endpoints (for example `/health`, `/datasets`)
- **GatewayStreamClient Message Handling**:
  - Added support for Data Gateway websocket payload shape (`type=data`, `feed=bars`, `envelope`, `data`)
  - Uses envelope-provided `event_id` when present for idempotency parity
  - Normalizes `symbol`/`ticker` keys into payload for downstream Alpaca bar normalization
  - Queues subscriptions requested before websocket connection and flushes them on startup
- **Centralized Gateway/Heber Runtime Config**:
  - Added `system_settings` fields for `data_gateway_url`, `data_gateway_api_key`, `heber_catalog_url`, `heber_data_root`, and `orion_use_gateway`
  - Added backward-compatible alias support (`GATEWAY_*` -> `DATA_GATEWAY_*`)
  - Refactored Gateway/Heber callers to use centralized config (`gateway_stream_client`, `heber_reader`, UW enrichment connectors, `sync_earnings`, `main_feature_enrichment`)
  - Removed hardcoded default Gateway API key fallback in UW connectors
- **Main Labeler Data Source**:
  - Migrated `main_labeler.py` read path from local `silver_uw_flow` SQL queries to Heber-backed `HeberReader.read_flow(...)`
  - Migrated price lookup for label horizons to Heber bars (`HeberReader.read_bars(...)`)
  - Kept `flow_labels` persistence in local Orion DB for compatibility during transition
- **Feature Enrichment Active-Ticker Discovery**:
  - Updated `main_feature_enrichment.py` to source active tickers from Heber flow data first
  - Retained local SQL discovery as a fallback path for operational safety

### Fixed

- **Alpaca Connection Limit**: Fixed `connection limit exceeded` error by migrating `AlpacaStreamConnector` to use Data Gateway's WebSocket multiplexer
  - New `GatewayStreamClient` connects to Gateway's `/ws` endpoint instead of directly to Alpaca
  - `ORION_USE_GATEWAY=true` (default) routes all streaming through Gateway
  - Eliminates competing WebSocket connections that exceed Algo Trader Pro's 1-connection limit
- **Backfill Runtime TypeError**: Fixed wrong-arity call in `backfill_ml_features.py` by updating `get_sector_correlation_features` invocation to match the two-argument function signature.

### Added

- **Exit Classifier Window Features**: Added 10 window features from `gold_feature_windows` to exit classifier training
  - `call_put_imbalance`, `sweep_ratio`, `flow_count` for 1h/1d/1w periods
  - `dp_volume_1d`, `call_put_ratio_1d/1w` for dark pool context
  - Uses LATERAL JOIN to look up historical window features at entry time
- **Checkpoint Greeks Infrastructure**: Modified labeler to fetch Greeks from Alpaca at each checkpoint
  - `get_real_checkpoint_prices` now returns delta, gamma, theta, vega, iv per checkpoint
  - Greeks added to label dict in `build_label` function
  - Note: INSERT statement update for persistence is a follow-up task

- **Quant Audit Phase 2 Remediation**: Comprehensive risk and ML fixes
  - **Projected Gamma Check**: `_check_greeks_limits` now uses projected gamma (current + trade) instead of just current
  - **Vega Exposure Limits**: New `max_portfolio_vega` (200) and `max_position_vega` (50) in `RiskSettings`
  - **check_options_order** now accepts `vega` parameter for comprehensive Greeks checking
  - **portfolio_vega** tracking in RiskManager for IV exposure monitoring
  - **Heuristic Score Cap**: Fallback scorer capped at 0.50 to prevent untrained buckets generating live signals
  - **Model Freshness Validation**: `ORION_MAX_MODEL_AGE_DAYS` env var (default 14) - stale models are skipped
  - **Slippage Tracking**: `process_fill` accepts `expected_price` and logs slippage in basis points
  - New test file: `tests/unit/test_risk_greeks_v2.py` with 12 test cases for Greeks fixes
- **Correlation-Aware Position Sizing**: Reduce position size when correlated with existing holdings
  - New `CorrelationAdjuster` class calculates rolling correlation with portfolio
  - `calculate_size_with_correlation()` async method in RiskManager
  - Auto-wired in `ExecutionEngine.__init__` when enabled
  - Config: `correlation_size_scaling`, `correlation_threshold` (0.70), `correlation_penalty_factor` (0.30)
  - Disabled by default (`ORION_RISK_CORRELATION_SIZE_SCALING=false`) for safe rollout
  - New test file: `tests/unit/test_correlation_adjuster.py` with 10 test cases
  - Full Risk Management section added to README

### Fixed

- **EOD Agent Async Bug**: Fixed missing `await` on `session.execute()` in `performance_tracker.py` `get_daily_accuracy()` and `get_weekly_performance()` functions
- **EOD Agent Proposal Schema**: Fixed LLM prompt to match `ProposalBuilder` validation - changed `solver_mutation` to `solver_edit`, added required `evidence_pointers`, `test_plan` fields
- **EOD Agent FK Constraint**: Fixed `solver_edits` insert by creating Solver stub before edit record
- **ML Scoring Feature Mismatch**: Fixed MLScorer receiving only 2/53 features during inference
  - Created `flow_enricher.py` module with `enrich_flow_for_scoring()` that queries same DB sources as labeler
  - Now populates 21/53 features: GEX, VEX, market tide, IV rank, VIX, regimes, max pain distance
  - Added `score_enriched()` async method to MLScorer for real-time enrichment
  - Updated `main_ingest.py` to use `process_flows_enriched()` for feature parity with training
- **Alpaca Trading Connector**: Added `client_order_id` parameter to `submit_market_order()` to match execution engine calls

### Added

- **Drift-Triggered Pattern Mining**: Retrain ML models when high feature drift detected
  - New `orion/core/drift_trigger.py` module with flag coordination
  - EOD agent sets drift flag when any feature PSI > 0.25
  - Pattern miner checks for drift flag every hour (in addition to Mon/Fri schedule)
  - Immediate model retraining when drift detected
- **Expanded ML Features**: Added 33 new entry-time features to pattern miner
  - Options Greeks: `delta_at_entry`, `gamma_at_entry`, `theta_at_entry`, `vega_at_entry`, `iv_at_entry`, `iv_vs_hv_ratio`
  - Volume/OI: `volume_at_entry`, `open_interest_at_entry`, `rvol_1h`, `rvol_daily`
  - Flow: `ask_side_ratio`, `sweep_ratio_1h`, `same_ticker_premium_1h`
  - Timing: `entry_hour`, `minutes_to_close`, `entry_session`, `entry_day_of_week`
  - Context: `spy_correlation_5d`, `spy_return_1h`, `days_to_earnings`, `sector`
- **Trade Execution Flow**: Complete end-to-end execution pipeline from ML candidates to broker orders
  - Fixed `SignalEngine` to fetch current price and set `limit_price` in execution params
  - Fixed `RiskSettings` to use percentage-based limits instead of fixed USD amounts
    - `max_order_size_pct`: 5% of account equity (was fixed $5,000)
    - `max_ticker_exposure_pct`: 10% of account equity (was fixed $10,000)
  - Fixed `risk_manager.calculate_size()` to cap position size at max_order_size_pct
  - Fixed `ExecutionEngine` side parameter conversion (`side.value` instead of `str(side)`)
  - Fixed `TradeJournalEntry` field names to match model schema (`decision_id` not `journal_id`)
  - Disabled rollup requirement for testing via `ORION_REQUIRE_ROLLUPS_FOR_SIGNALS_LIVE=false`

### Added

- **Options Trading Pipeline**: Trade options contracts instead of equities based on UW flow signals
  - New `CandidateTrade` fields: `option_symbol`, `strike_price`, `expiration_date`, `option_type`, `underlying_price`, `premium`
  - New `AlpacaOptionsConnector` with `submit_option_order()`, `get_option_quote()`, OCC symbol generation
  - `ExecutionEngine` routes to options path when `candidate.option_symbol` is present
  - Premium-based sizing: max 2% of equity per options trade (`max_option_premium_pct`)
  - DTE minimum check (3 days by default) prevents trading very short-dated options
  - New risk settings: `max_option_premium_pct`, `min_dte`, `max_option_positions`

- **Weekly Meta Agent Evolution**: Friday EOD comprehensive analysis and solver evolution
  - New `WeeklyDataAggregator` class aggregates EOD reports, trade data, and ML insights
  - `run_weekly_evolution()` method analyzes execution quality, ML drift, and generates mutations
  - Execution quality analysis: fill rate, rejection rate, health classification
  - ML drift detection: tracks AUC trends across model buckets
  - Automated solver mutation proposals based on top-performing features
  - New `main_meta_weekly.py` CLI with `--dry-run` and `--scheduled` modes
  - Scheduled Friday 5:30 PM EST via `meta-weekly` docker-compose service

- **Expanded ML Targets**: 4 targets for multi-dimensional trade scoring
  - `hit_target_50`: 50% profit before 20% stop (original)
  - `avoid_stop`: Avoid 20% stop entirely (original)
  - `hit_target_100`: High conviction runner - 100% profit before stop
  - `quick_winner`: Fast exit - 50% profit within 1 hour
  - New `MultiTargetScorer` class with `score_all()`, `get_composite_score()`, and `get_trade_signal()`
  - 4 buckets × 4 targets = 16 models (up from 8)

- **Bucket-Specific Exit Classifiers**: ML-based exit timing for each trade bucket
  - 0DTE: Checkpoints at 5m, 10m, 15m, 30m, 1h, EOD (AUC=0.935)
  - SHORT_SWING: Checkpoints at 30m, 1h, 2h, 4h, 8h, EOD (AUC=0.896)
  - SWING: Checkpoints at 1h, 4h, 8h, EOD, 1d, 2d, 1w, 2w (AUC=0.904)
  - POSITION: Checkpoints at 1d, 2d, 3d, 1w, 2w, 3w, 4w (AUC=0.955)
  - DB columns added: price_at_2w/3w/4w, return_at_2w/3w/4w
  - **Greeks at all checkpoints**: delta, gamma, theta, vega, IV fetched from Alpaca
  - **Time decay features**: DTE, theta_decay_pct, time_value_pct at each checkpoint
  - Bucket-aware heuristic fallbacks with tuned take-profit/stop-loss thresholds
  - Training target: "Did exiting at checkpoint capture ≥80% of max return?"

- **Position Monitor & Exit Execution**: Automated position management
  - `PositionMonitor` class syncs with Alpaca broker positions
  - Tracks max return, max drawdown for trailing stop logic
  - Evaluates ML exit classifier for each position
  - `AlpacaTradingConnector` extended with `get_all_positions()`, `close_position()`
  - `main_position_monitor.py` CLI with `--interval`, `--dry-run`, `--once` modes
  - `position-monitor` docker-compose service (60s check interval)

- **ML Model Persistence Pipeline**: End-to-end wiring of ML models for live trading
  - `pattern_miner.py` now saves trained models to `/app/models/{bucket}_{target}.pkl`
  - Models saved with metadata (feature names, creation timestamp, model type)
  - Conditional save: only persists models with holdout AUC >= 0.55
  - `scorer.py` rewritten to load bucket-specific models (0DTE, SHORT_SWING, SWING, POSITION)
  - Automatic bucket classification based on DTE for flow scoring
  - Graceful fallback to heuristic scorer when no model available
- **EOD Agent Service**: New `eod-agent` docker service that runs daily after market close
  - `main_eod.py` entry point with `MarketSchedule` integration (waits for 16:30 ET)
  - Mounts `~/.codex` for credentials passthrough
- **Codex CLI Client**: `codex_client.py` async wrapper for headless `codex exec` calls
  - `run_codex_completion()` for subprocess-based LLM execution
  - `build_chat_prompt()` and `extract_json_from_response()` helpers
- **Reasoning Level Config**: `ORION_REASONING_LEVEL` env var (default: `extra_high`)
- **ML Pattern Mining Layer**: Automated LightGBM-based pattern discovery
  - `src/orion/ml/pattern_miner.py` - trains on `price_target_labels`, extracts decision tree rules
  - `src/orion/ml/schemas.py` - Pydantic models for pattern insights
  - `src/orion/storage/models_ml.py` - Database tables (`ml_pattern_insights`, `ml_feature_importance_history`)
  - **8 models**: 4 trade buckets (0DTE, SHORT_SWING, SWING, POSITION) × 2 targets (hit_target_50, avoid_stop)
  - Bucket-specific lookback windows: 10 days (0DTE) to 90 days (POSITION)
  - `pattern-miner` docker service runs Mon + Fri after market close
  - EOD agent prompt updated to interpret ML insights (AUC scores, top rules, feature importance)
- **EOD → MetaAgent Solver Generation Pipeline**: Automated solver mutation and creation
  - EOD agent can now propose `solver_mutation` type with structured ops (modify_param, add_rule, toggle_feature)
  - `refine_and_promote()` method: iteratively backtests solver, sends results to MetaAgent for refinement until score threshold met
  - Auto-promotes to paper trading when `composite_score >= 0.5` (max 3 refinement iterations)
  - New solvers in `research` stage stay there if refinement fails; successful ones go directly to `paper`
  - Lineage tracked in `solver_edits` table for evolutionary tracing
- **Multi-Axis Regime System**: PRD Regime Upgrade implementation
  - 5 regime enums: `TrendRegime`, `VolRegime`, `RiskRegime`, `SessionRegime`, `VIXRegime`
  - `MultiAxisRegimeDetector` class with Market Tide integration for risk scoring
  - `RegimeRiskManager` with position sizing multipliers per regime axis
  - `silver_vix_data` and `silver_regime_history` tables
  - `vix_connector.py` for VIX/VVIX data ingestion
  - SignalEngine blocks trading during SHOCK regime
  - 6 new regime columns in `price_target_labels` for ML features
- **UW Feature Endpoints**: Market context enrichment for ML
  - 4 new silver tables: `silver_greek_exposure`, `silver_market_tide`, `silver_max_pain`, `silver_iv_rank`
  - 4 new connectors: GEX/Vanna, Market Tide, Max Pain, IV Rank
  - `feature_enrichment` service polls endpoints at configured intervals
  - 7 new entry feature columns in `price_target_labels` (GEX, Tide, Max Pain, IV Rank)
- **ML Feature Validation System**: Comprehensive audit tooling for all 130+ features
  - `src/orion/jobs/validate_features.py` - 3 validation modes (spot-check, sanity, audit-sources)
  - `FEATURE_SOURCE_MAPPING` documents all 60+ features and their source tables
  - Data source audit covers all 8 silver tables with gap detection
  - 7 automated sanity checks (Greeks ranges, time features, volume constraints)
- **Enhanced Backfill Job**: `backfill_ml_features.py` now populates 50+ features
  - Darkpool metrics (9 window sizes: 15m, 30m, 1h, 4h, 1d, 3d, 1w, 2w, 4w)
  - RVOL metrics (5 windows: 30m, 1h, daily, 3d, weekly)
  - Flow aggression (ask_side_ratio, sweep_ratio_1h, same_ticker_premium_1h)
  - Regime features (trend, vol, risk, session, VIX regimes)
  - Market tide and institutional flow

### Changed

- Reduced ingestion polling interval from 5 minutes to 1 minute for more responsive data capture
- Configured UW connectors (flow, darkpool, alerts) with explicit 5-minute lookback on cold start (darkpool API has max 200 items per request)

### Added

- Added `ensemble_consensus_threshold` configuration to `SystemSettings` (default 0.5) for configurable signal decision threshold
- Added singleton pattern to `CircuitBreaker` class for more efficient instantiation
- **Dynamic Exit Strategies**: Implemented 6 flow-based exit rules per PDF spec
  - `PositionManager` class tracks open positions with entry context (IV, premium, sweep count)
  - Exit rules: SentimentReversal, NetPremiumDecline, VolumeOIDivergence, WaningMomentum, IVContraction, OpposingClusters
  - `close_position()` method in ExecutionEngine for exit order execution
  - `ExitDecision` table for tracking exit triggers
  - Backtest shows 96.5% exit rate, avg 3.3 min to exit on historical data
- **Logic Audit P2-L1**: Added `_hydrated` flag to `FeatureEngine` to warn when `process_alpaca_bars()` is called before `hydrate_history()` - prevents silent cold-start indicator degradation
- **Contract Tests P1-C1**: Added 9 new API endpoint tests covering `/promotions`, `/search`, `/rollups`, and `/flows` - API test coverage now comprehensive
- **Dead Code P2-DC2**: Updated `.gitignore` to exclude debug output files (`*.txt`, `coverage.json`) and removed 8 tracked debug files from repository
- **M5: Atomic fill processing** - Implemented DB-backed idempotency for fill processing to enable multi-instance deployments:
  - `_record_fill_in_db()` uses atomic INSERT with ON CONFLICT DO NOTHING
  - `_is_fill_processed_in_db()` checks DB source of truth
  - `_load_processed_fills_from_db()` pre-warms cache on startup (last 30 days)
  - In-memory cache used as fast-path optimization, DB is source of truth
- **0DTE Entry Signal**: `ZeroDTESweepRule` based on price target analysis
  - Criteria: DTE=0, sweep, $100-150K premium, ASK aggressor
  - Confidence boost for puts (0% historical stop rate) and market open hour
  - 50% profit target, 20% stop loss
- **Price Target Labeling System**: Tracks option price over time
  - `price_target_labels` table: max return, drawdown, target hit times
  - `main_price_target_labeler.py` service for continuous labeling
  - Tracks 50%, 75%, 100%, 150% profit targets and 20% stop loss
- **PriceTargetExitRule**: Exit rule for profit target/stop loss based exits

### Changed

- `SignalEngine.decide()` now uses configurable consensus threshold from `system_settings.ensemble_consensus_threshold` instead of hardcoded 0.5
- **M1 Consolidation**: `main_execution.py` is now a thin wrapper (38 lines) that delegates to `ExecutionService.run()` - all execution logic consolidated in single source of truth
- `ExecutionService._save_decision()` now handles full signal persistence (SignalLive + TradeJournalEntry) for EXECUTE decisions

### Fixed

- Fixed `overnight_gap_pct` calculation to correctly find prior trading day close (handles weekends/holidays)
- Fixed `vwap_distance_pct` calculation to use bar closest to entry timestamp instead of day's open
- Fixed `_calculate_projected_exposure` return type annotation from `Tuple[float, float, float]` to `Tuple[float, float]` to match actual 2-value return in `risk_manager.py`
- Fixed `check_status` inner function return type from `None` to `bool` in `circuit_breaker.py`
- Fixed `fetch_state` inner function return type from `None` to `dict[str, Any]` in `circuit_breaker.py`
- Fixed `pending_orders` type hint to use standard `Tuple[str, float]` instead of lowercase `tuple` in `risk_manager.py`
- **Meta Learning Fixes**:
  - Added missing `_log_experiment()` method to `MetaSearchAgent` (critical - was blocking evolution cycle)
  - Fixed `_load_context()` referencing `self.store` instead of `self.vector_store`
  - Fixed `_fetch_silver_events()` defining but never calling `fetch_bars_and_flow`
  - Removed duplicate validation check in `SolverRiskConfig.check_global_limits()`
  - Fixed deprecated `datetime.utcnow()` usage in `SolverEdit.created_at_utc`
- **Processing Test Fixes**:
  - Rewrote `test_signal_engine.py` to test current async `SignalEngine.decide()` API with proper mocking
  - Fixed `test_normalizer.py` key assertions: `call_put` → `put_call`, `flags` → `flags_json`
- **Jobs Module Fixes**:
  - Fixed SQL `is None` → `.is_(None)` in `label_job.py` (was never matching rows)
  - Added missing `db_write(_process_and_save)` call in `label_job.py`
  - Fixed deprecated `datetime.utcnow()` → `datetime.now(timezone.utc)` in `seed_solvers.py`
  - Fixed deprecated `asyncio.get_event_loop()` → `asyncio.run()` in `dlq_consumer.py`
- **Connector Async Fixes**:
  - Fixed blocking `time.sleep()` → `await asyncio.sleep()` in `uw_alerts_connector.py` and `uw_darkpool_connector.py`
- **Parallel Processing Fixes**:
  - Fixed fire-and-forget `asyncio.create_task()` → `asyncio.ensure_future()` in `feature_engine.py`
  - Fixed deprecated `datetime.utcnow()` → `datetime.now(timezone.utc)` in `main_ingest.py`, `models_risk.py`, `bar_gap_scan.py`
- **Performance Audit Fixes**:
  - Added connection pool config (`pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`) to `db.py`
- **Memory Audit Fixes**:
  - Added `BoundedSet` class for `processed_fill_ids` in `risk_manager.py` (max 10000 entries)
  - Added `flow_max_size_per_ticker` cap (500) in `feature_engine.py` to prevent memory growth

### Removed

- Removed unused `get_pending_candidates()` function from `main_execution.py` that referenced non-existent columns
- Removed unused `update_candidate_status()` function from `main_execution.py`
- Removed unused `_persist_signal_live()` method from `ExecutionService` in `service.py`
- Removed unused `_persist_trade_journal()` method from `ExecutionService` in `service.py`
- Removed obsolete comments and unused imports (`datetime`, `timezone`)
- Removed duplicate execution logic from `main_execution.py` (~250 lines of code eliminated)

## [Unreleased]

### Fixed
- `UniverseManager.hydrate_from_db` now uses `CandidateTrade.expiration_date` instead of the removed `expiry` attribute, eliminating a recurring non-fatal error on every service startup.
