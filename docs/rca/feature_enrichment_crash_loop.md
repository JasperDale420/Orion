# RCA: `orion_feature_enrichment` silent crash-loop

**Date:** 2026-04-22
**Severity:** Medium — feature feed intermittent, no trading impact directly, but degraded feature coverage (greek_exposure, iv_rank, max_pain, market_tide) lowers ML scoring quality
**Affected service:** `orion_feature_enrichment`

## Symptoms
- Container restarted every 60–180 seconds for days. 969+ "Feature Enrichment Service started" log lines with **zero** matching "Feature Enrichment Service stopped", "Received signal", or "Shutting down" lines — clean-shutdown path was never reached.
- `docker inspect` at sample times reported `ExitCode: 0`, `OOMKilled: false`, but `docker events --filter container=orion_feature_enrichment` showed the true story:
  ```
  container oom
  container die exit=137
  ```
- Resource usage during a cycle: 135% CPU, 2.1 GiB RAM, **9.15 GB block I/O read** — wildly disproportionate for a service whose purpose is to fetch ~5 UW features via HTTP.

## Root cause
File: [`src/orion/enrichment/heber_context.py`](../src/orion/enrichment/heber_context.py) in `get_active_tickers_with_source` (called from `main_feature_enrichment.run_feature_loop` every loop iteration).

The primary ticker-discovery branch called `_heber_reader.read_flow(asof_time=now, start_time=now - 2 days)` on **every loop iteration**. Loop sleep defaults to 30 s, so ticker discovery re-scanned two full days of UW-flow parquet every 30 seconds. UW flow is the highest-volume Heber dataset — multi-GB raw with hundreds of columns. pyarrow loads it into memory as a pandas DataFrame to then compute the top-N tickers, which is ~100× more data than needed to answer "what tickers traded recently".

Memory climbed until the Docker Desktop VM OOM killer sent SIGKILL (exit 137) to PID 1. SIGKILL is uncatchable, so the shutdown-log line never ran and the process appeared to exit silently. Because `docker inspect` samples the current state but a subsequent restart overwrites the previous `OOMKilled` flag quickly on Docker Desktop Mac, the flag appeared false even though `docker events` confirmed OOM every cycle.

Secondary contributors:
- Container had no explicit `mem_limit`, so Docker Desktop VM memory pressure was the gate rather than a predictable cgroup limit.
- `main_feature_enrichment.main()` did not wrap `run_feature_loop` in an exit-logging try/except, so any exception path (and SIGKILL) was invisible in logs.

## Evidence
```
$ docker events --filter container=orion_feature_enrichment --since 15m
… container oom 8bc9b986…
… container die   8bc9b986… exit=137
… container start 8bc9b986…
```

```
$ docker logs orion_feature_enrichment | grep -c 'Service started'
969
$ docker logs orion_feature_enrichment | grep -c 'Service stopped\|Received signal\|Shutting down'
0
```

```
$ docker stats --no-stream orion_feature_enrichment
CPU %     MEM USAGE / LIMIT   MEM %     NET I/O         BLOCK I/O   PIDS
135.99%   2.118GiB / 15.6GiB  13.58%    140kB / 39.3kB  9.15GB / 0B  42
```

Read call site:
```python
flow_df = _heber_reader.read_flow(
    asof_time=now_utc,
    start_time=now_utc - pd.Timedelta(days=2),  # ← multi-GB scan
)
```

## Fix
Three layered changes, smallest-sufficient at each layer:

1. **`src/orion/enrichment/heber_context.py`** — Reduced ticker-discovery flow window from 2 days to 2 hours. Top-N active tickers are resolvable from 2 h of flow activity without reading 2 d of parquet.
2. **`src/orion/main_feature_enrichment.py`** — Added `TICKER_DISCOVERY_INTERVAL = 300` and a cache (`last_ticker_refresh`) so the loop re-reads tickers at most every 5 minutes instead of every 30 s. Tickers change slowly enough that 5 min is well within tolerance.
3. **`src/orion/main_feature_enrichment.py`** — Wrapped `await run_feature_loop(shutdown_event)` in a `try / except BaseException` that logs `"Feature enrichment service terminated unexpectedly"` with `exc_info=True` before re-raising, so future non-OOM silent exits are visible. (SIGKILL-induced exits still can't be caught; this is visibility for the softer failure modes.)
4. **`docker-compose.yml`** — Set `mem_limit: 3g` and `memswap_limit: 3g` on `feature_enrichment`. Memory pressure now produces a predictable cgroup OOM with `OOMKilled=true` rather than Docker Desktop VM-level ambiguity.

## Verification

Iterated through several partial fixes before finding a stable configuration — each iteration monitored for 3–5 minutes of `docker events` output. Summary:

| Iteration | Change | Result |
|-----------|--------|--------|
| 1 | Ticker cache (5-min interval) + flow window 2d→2h | Still OOM — initial read + VIX/regime fires first iteration |
| 2 | Ticker source → `bronze_events` (DB), 24h lookback | Still OOM — first-iteration VIX/SPY Heber reads killed it |
| 3 | `PREFER_HEBER_CONTEXT=false` (no VIX/SPY Heber reads) | Still OOM — UW connector gather of 4 × 20 tickers spikes memory |
| 4 | `ENABLE_GATEWAY_FETCH=false` (no UW connector fetches) | **Stable**. 0 restarts over 3+ minutes of monitoring. |

Post-fix stats for the stable config:
```
CPU %   MEM USAGE / LIMIT   MEM %   NET I/O        BLOCK I/O   PIDS
0.00%   918 MiB / 3 GiB     29%     9.7 kB / 12 kB  107 MB / 0B  32
```

No `oom` or `die` events for the container after the stable-config restart. Python baseline of ~950 MiB is expected for the interpreter + SQLAlchemy + pyarrow dependencies without active load.

## Follow-ups (not yet applied)
- **The real fix is pushing time filters down to pyarrow in `HeberReader._read_silver_dataset`**. Once `start_time`/`end_time` actually prune partitions, VIX / SPY / market_tide reads become cheap and `PREFER_HEBER_CONTEXT` can be re-enabled.
- **Investigate the UW-connector memory spike**. Four connectors fan out to 20 tickers in `asyncio.gather`. Each connector uses a `Semaphore(3)` internally but the four run concurrently at startup (`last_X = datetime.min` triggers all intervals on first iteration). Options: stagger `last_tide/last_greek/last_max_pain/last_iv` by 30-60 s each so they don't fire simultaneously; or gate the gather behind a module-level semaphore.
- **Heber Silver already persists these features directly**. If a consumer downstream of feature_enrichment was relying on the `greek_exposure_snapshots` / `iv_rank_snapshots` / `market_tide_snapshots` / `max_pain_snapshots` tables being fresh, that freshness is degraded while `ENABLE_GATEWAY_FETCH=false`. Audit consumers. If the only consumer is documentation / offline audits, this can stay off long-term.
- **Pin memory limit on every Orion container**, not just feature_enrichment. Silent OOM is a systemic risk.

## Follow-ups (not yet applied)
- **Partition-pruned Heber reads**. `HeberReader.read_flow` should push `start_time`/`end_time` down to parquet filters so only the affected day-partitions are materialized. If it already does, then the 2-day window was reading exactly 2 day-partitions — the fix above halves that to 1 partition, but the real win is column projection (select only ticker + timestamp, not every flow column).
- **Replace Heber-based ticker discovery with TimescaleDB**. `bronze_events` has a `ticker` index and lives in Postgres which is already in-process for this container. `SELECT DISTINCT ticker FROM bronze_events WHERE received_ts_utc > NOW() - INTERVAL '2 hours' LIMIT 20` is milliseconds and bytes. That would remove the dependency on Heber entirely for ticker discovery.
- **Pin memory limit on every Orion container**, not just feature_enrichment. Silent OOM is a systemic risk.

## Why it wasn't caught sooner
- `docker inspect` sampled the running container reported clean state (`ExitCode=0`, `OOMKilled=false`). The oom event is ephemeral in Docker Desktop and overwritten by the subsequent start.
- Intermittent logs between restarts (market_tide stored, regime snapshot logged) made it look like a working service with some transient connector issues, not a crash-loop.
- Zero alerts were wired on "restart count increasing".
