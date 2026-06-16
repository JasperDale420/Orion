# RCA — Orion not trading May 13, 2026 (multi-container restart loop)

**Author:** debugging session 2026-05-13 13:30 PT
**Branch evidence collected on:** `claude/vibrant-newton-13e376` (worktree)
**Status:** Root cause identified, remediation proposed but **not applied** — awaiting operator confirmation.

---

## Symptom

- May 13: `0` orion EXECUTE decisions and `0` orders during the full 6.5-hour session, despite 255 SKIP decisions getting written.
- Bronze data flow stopped at **06:13 PT on May 13 (17 min before market open)** and never resumed.
- All 251 of 255 SKIPs today had `reason = "Stale at fetch: older than max_data_lag_seconds"` — i.e. the auto-skip sweeper from the May 8 commit was working as designed, but nothing was producing fresh decisions for it to skip past.
- `orion_ingestion` `RestartCount = 1202` since the May 8 recreate; `orion_execution` `RestartCount = 264`; both currently restarting every 60-120 seconds.
- Every container restart reaches `Hydrating FeatureEngine history for 11 tickers...` and dies before `FeatureEngine hydration complete.`
- Docker reports `ExitCode: 0` and `OOMKilled: false` on both — which is the deceptive signal that masked this for 5 days.

## Root cause

**`orion_data_quality` is in a tight self-OOM loop. The pages it accumulates between cgroup OOMs push the Linux VM over its 16 GiB ceiling, triggering global OOM kills that target whichever process has the highest RSS — which is almost always `orion_ingestion` or `orion_execution`. Those services aren't broken; they're collateral damage.**

The Docker-VM kernel log makes this unambiguous (dmesg, kernel uptime 569,437-569,618 = the last few minutes):

```
oom-kill:constraint=CONSTRAINT_MEMCG,
  oom_memcg=/docker/914822215d811ee811e78ac6f3790d4bf4613dd549cb3a93a6ef404a04ee690d
  task=python, pid=…, uid=999
Memory cgroup out of memory: Killed process … total-vm:5230792kB, anon-rss:2549436kB
memory: usage 2560000kB, limit 2560000kB, failcnt 23967
```

Cgroup `914822…` = `orion_data_quality`. `failcnt=23967` means the cgroup memory-limit check has fired 23,967 times. Its `mem_limit: 2500m` is being hit and killed on every cycle. `restartcount=7424` since May 8 confirms it.

Interleaved with the MEMCG kills are `CONSTRAINT_NONE / global_oom` events:

```
oom-kill:constraint=CONSTRAINT_NONE, …, global_oom,
  task_memcg=/docker/6a9530c99bdd012c28ef0891646861af07ce2b5de28872ba7f7bafb689d2c3a7,
  task=python
Out of memory: Killed process … total-vm:4858292kB, anon-rss:2814792kB
```

Cgroup `6a9530c9…` = `orion_ingestion`. `constraint=CONSTRAINT_NONE` means the kernel ran out of memory *system-wide* (not because that container hit its own cap). At the moment of kill, ingestion's RSS was 2.65-2.81 GiB — comfortably under its 4 GiB cap. Likewise `edfd2ad0…` = `orion_execution` was killed via `global_oom` while at 2.5-2.8 GiB RSS, well under its 5 GiB cap. **They were not OOM-evaluated against their own caps; they were the victim selected by the global OOM killer when the VM ran out of pages.**

Live RSS sampling captured the kill events directly:

```
13:29:29 orion_execution  2.576GiB / 5GiB
13:29:34 orion_execution  122.5MiB        ← killed, restarted
13:30:18 orion_ingestion  2.421GiB / 4GiB
13:30:24 orion_ingestion  108.7MiB        ← killed, restarted
```

Both services died at <60% of their own caps. Cgroup OOM cannot do that; only VM-global OOM can. The `OOMKilled=false` flag in `docker inspect` is misleading because that flag is only set for *cgroup-level* OOMs — global OOMs (the VM kernel's last-resort kill) don't populate it.

The `ExitCode: 0` is the same misleading artefact, surfaced by the `runc` execution path when the cgroup it was managing got externally signal-killed during initialization. Python never ran a finally clause, never logged a CRITICAL exit, never set a shutdown event. The `main_ingest` silent-exit guard I added on May 8 is intact and would fire on a Python-side crash — but it cannot fire when the kernel SIGKILLs the process from outside.

## Why `orion_data_quality` is the trigger

`scripts/main_data_quality.py` is supposed to run `run_quality_checks()` *hourly during market hours*. The wrapper is a single while-loop with `await asyncio.sleep(3600)` — so one cycle per hour at steady state.

The actual behaviour is one cycle every 20-50 seconds:

```
20:27:51  Data quality checker started in scheduled mode (hourly during market hours).
20:28:10  Data quality checker started in scheduled mode (hourly during market hours).
20:28:33  Data quality checker started in scheduled mode (hourly during market hours).
20:29:04  …
20:29:52  …
20:30:30  …
```

That's the **startup banner** firing each time. So the process never gets to its sleep — it gets cgroup-killed mid-`run_quality_checks()`, container restarts via `restart: unless-stopped`, prints the banner, runs the checks again, dies again.

`run_quality_checks()` is a sequential pipeline of Heber gold reads — bars/flow/darkpool/features summary, zero-valued bar check, staleness check, gap check, etc. Each goes through `_read_heber_bars_24h`, `_read_heber_flow_24h`, etc., materializing pandas DataFrames via `pyarrow.ParquetDataset(...).to_pandas()`. The DataFrames are held in `results` dict for the full pipeline. With Heber gold partitions growing daily and the 2.5 GiB cap unchanged since launch, peak RSS now exceeds the cap.

**Verdict on the original choice of `mem_limit: 2500m` for `data_quality`:** it was sized for the Heber dataset that existed in March/April. The data has grown.

## Why this got past the May 8 fix

On May 8 the system was visibly broken in a different way — ingestion + execution were in their *own* tight cgroup-OOM loops (ingestion at the 2.5G cap, execution at the 3G cap). My fixes that day:
- bumped ingestion to 4 GiB and execution to 5 GiB,
- added a Python-side silent-exit guard,
- shipped the bare-EventEnvelope WS fix that unblocked bronze,
- fixed the shared-account drawdown false-trip and tick-rounding on options orders.

After those, the pipeline was demonstrably trading (9 EXECUTEs, 8 orders accepted by Alpaca in the 5 minutes I verified) and `restartcount` was 0/1 for both services.

What I did **not** look at on May 8 was `orion_data_quality`. Its `mem_limit: 2500m` was untouched, and it was already restarting every 1-2 hours (I saw "Up 2 seconds (health: starting)" once and moved on without digging). Over the following 5 days:
- The shared Alpaca account took a $104k drawdown (other systems trading, normal),
- The Alpaca account position count grew from 19 to 102 (other systems again),
- Heber gold partitions grew with each daily run of `Heber/scripts/run_gold_pipelines.sh` (the 02:00 cron),
- `orion_data_quality`'s RSS during a run crossed the 2.5 GiB cap, and it started cgroup-OOM-looping continuously,
- The rate of cgroup OOM events climbed (one every ~20-30 seconds), pushing the VM's working set high enough that global-OOM started firing on whichever container had the highest RSS at the moment — that's ingestion or execution.

The cascade: data_quality cgroup-OOM-loop → VM page accounting churn → global-OOM kills → ingestion/execution restart-loop → no candidate processing → all decisions go SKIP via auto-skip sweeper → 0 orders.

## Why the silent-exit guard didn't catch this

The May 8 guard was specifically designed for the *previous* failure mode: Python's `main()` returning normally without `shutdown_event` set. It catches:
- BaseException escaping `asyncio.run(main())` → logs `ingestion_process_crashed`, re-raises (exit non-zero).
- While loop exiting with `shutdown_event` unset → logs `ingestion_main_loop_exited_without_shutdown_signal`.

It cannot catch:
- SIGKILL from the kernel OOM killer (no chance for Python to run code).
- The container runtime reporting `ExitCode: 0` for an externally killed container (a Docker Desktop quirk on macOS).

This is the right design — a Python-side guard for a Python-side failure mode. The new failure mode is below Python's awareness, and needs OS-level signal (dmesg, cgroup memory.events) or RSS monitoring to catch.

## Evidence catalog

All evidence is reproducible from the current state of the system.

1. **Container restart counts** (`docker inspect`):
   - `orion_ingestion` restartcount=1202, `OOMKilled=false`, `ExitCode=0`
   - `orion_execution` restartcount=264, `OOMKilled=false`, `ExitCode=0`
   - `orion_data_quality` restartcount=7424, `OOMKilled=false`, `ExitCode=0`

2. **Decision distribution today** (TimescaleDB):
   - 251 SKIPs with `reason = "Stale at fetch: older than max_data_lag_seconds"`
   - 3 SKIPs with `reason = "Preflight reject: Data Lag"`
   - 1 SKIP with `reason = "ML pre-filter: score 0.03 below threshold (0.05)"`
   - 0 EXECUTE decisions
   - 0 `orion_` orders

3. **Bronze gap today**:
   - Last ALPACA_BAR_1M `received_ts_utc = 2026-05-13 13:13:00Z` (06:13 PT, 17 min before open).
   - No bronze rows during the 6.5-hour session.

4. **Live ingestion log signature** (every cycle, never reaches "FeatureEngine hydration complete."):
   ```
   GatewayStreamClient created; market data sourced from Data-Gateway
   Initializing Ingestion Service...
   Universe hydrated from candidate_trades: 149 tickers
   Hydrating FeatureEngine history for 11 tickers...
   [no further output before next container start]
   ```

5. **`Shutdown signal received` count in ingestion logs across all 1202 restarts: 0.**
   **`ingestion_main_loop_exited_without_shutdown_signal` / `ingestion_process_crashed` count: 0.**
   The guard didn't fire because Python didn't exit; the kernel killed the process.

6. **Docker-VM dmesg** — multiple cgroup-MEMCG OOMs on `914822…` (data_quality) interleaved with global-OOMs on `6a9530c…` (ingestion) and `edfd2ad…` (execution). `failcnt=23967` on the data_quality cgroup.

7. **Live RSS samples** — ingestion and execution killed at 2.4-2.8 GiB RSS (cgroup cap 4 GiB / 5 GiB respectively). Cgroup OOM impossible at those numbers; only VM-global OOM can do that.

8. **`data_quality` startup banner cadence** — one every 20-50 seconds vs the designed `asyncio.sleep(3600)` (one per hour). Confirms it's never reaching its own sleep loop.

## Hypothesis verification path (not yet executed)

The cheapest causal test is to **stop `orion_data_quality` and observe whether ingestion+execution stabilize**. Predictions if the hypothesis is correct:

- Within 1-2 minutes of `docker stop orion_data_quality`, the VM's `MemFree` should rise materially (currently 3.7 GiB on a 16 GiB system; expect it to climb past 6 GiB once the page-churn stops).
- Ingestion will complete `FeatureEngine hydration complete.` for the first time in 5 days and reach `Starting Polling Loop. Interval: 60s` without dying.
- Execution will likewise complete hydration and start processing pending candidates.
- The cgroup-MEMCG OOM events in dmesg will stop entirely (since data_quality is the only container actively hitting its cap).
- New global-OOM events should stop too, but the kernel does keep memory pressure for a while; first ~5 minutes after stop are when you'd still see one or two.

If those predictions hold, the hypothesis is confirmed. If ingestion still dies after stopping data_quality, there's a second contributor and we go back to Phase 1.

## Remediation

### Immediate (P0 — restore trading)

1. **Stop `orion_data_quality`** (`docker compose stop data_quality` or `docker stop orion_data_quality`). This breaks the OOM loop instantly. Quality checks are advisory and run hourly by design — losing them temporarily is non-trivial-but-acceptable.
2. **Watch ingestion + execution finish one full hydration cycle.** Expected within 90-120 seconds.
3. **Verify** new bronze rows landing post-restart; expect first decision in 1-2 minutes after that.

### Short-term (P1 — make `data_quality` work again, within today)

Two options, in priority:

**A) Bump `mem_limit` for data_quality (cheapest, narrow):** raise `2500m` → `5g` to match what we did for execution. Removes the cgroup OOM. **Caveat:** this only buys time — the underlying memory usage of `run_quality_checks` will continue to grow with the Heber dataset. Pick this if we want trading back tonight and a structural fix tomorrow.

**B) Rewrite `run_quality_checks` to release DataFrames as it goes:** currently `results = {}` holds every read for the full pipeline. Refactor so each check returns its summary stats *only* and frees the underlying DataFrame before the next check runs. Most checks are aggregations — a 24h flow DataFrame can become a 5-row summary frame trivially. **This is the right fix.** ETA half a day of careful work plus tests.

I'd ship **A + change the comment in docker-compose** to note that the new limit is a band-aid for the leak in B, then do B in a separate session with proper testing.

### Medium-term (P2 — catch this class of failure earlier)

1. **VM-level OOM observability**: `docker inspect` doesn't surface global-OOM kills. Either:
   - Add a sidecar that polls dmesg every 30s and reports new `oom-kill:constraint=` events to Prometheus / a logfile, OR
   - Wrap each service's docker-compose entry with a healthcheck that fails when `MemAvailable` on the VM drops below 2 GiB.
2. **Drift alarm on `restartcount`**: if any orion container's restartcount climbs more than N in a 24h window, log CRITICAL. Currently the only way to notice is to manually run `docker inspect`.
3. **Heber gold size guard**: add a startup-time check in `data_quality_checker` that estimates dataset size before reading and refuses to load >X GB at once. Or stream via `pyarrow.dataset.Scanner` with batch iteration instead of full `.to_pandas()`.

### Long-term (P3 — what makes this kind of regression less likely)

The Docker-Desktop-on-macOS quirk where global-OOM kills surface as `ExitCode: 0 / OOMKilled: false` is a known footgun and it ate 5 days of trading because nobody saw the signal. The structural fix is to **stop relying on the Docker-reported exit status as the source of truth for container health**, and instead monitor:
- Heartbeat freshness in `system_status` table (already exists — `global_health` was at "HEALTHY" today despite ingestion being dead for 6 hours, so the heartbeat logic also needs work).
- Pipeline-stage freshness (last bronze row, last decision, last order) with alert thresholds during market hours.

## What I did NOT do

- I have **not** stopped data_quality or applied any of the remediations. The user's request was "do an RCA on all of this" — I built the evidence chain and confirmed the mechanism without touching live state.
- I have **not** modified `docker-compose.yml` since the May 8 commit. The compose file currently has data_quality at `mem_limit: 2500m`.
- I have **not** restarted any service in the course of this investigation.
- The 4 SHORT_SWING entry models, POSITION_avoid_stop AUC=0.5 issue, and the cron retrain itself are **separate, pre-existing** issues called out in earlier session notes. They are not the cause of this incident.

## Decision request to operator

Pick one:

1. **"Stop data_quality and bring trading back now."** I run `docker stop orion_data_quality`, watch hydration complete, and confirm new bronze + decisions flow. ~5 minutes wall time.
2. **"Stop data_quality and apply the mem_limit bump (option A above) in the same change."** Same as #1 plus an edit to docker-compose. Locks in the band-aid so the next operator-initiated `docker compose up` doesn't re-hit the loop.
3. **"Show me option B before deciding."** I write up the `run_quality_checks` streaming refactor as a separate change for review, but apply #1 in the meantime to stop the bleeding.

Until one of these lands, the trading pipeline will continue to crash-loop and no orders will be placed.

---

## Addendum — what was applied (operator approved option 2 + sonarqube stop + heber-health-monitor stop)

### Actions taken at 2026-05-13 13:40-15:16 PT

1. **`docker-compose.yml`** — `data_quality.mem_limit` raised `2500m` → `5g` (and matching `memswap_limit`). Comment in the file now flags this as a band-aid pending the `run_quality_checks` streaming refactor.
2. **Stopped `orion_data_quality`** to break the OOM loop, then `docker compose up -d --no-deps --force-recreate data-quality` so the new limit takes effect.
3. **Stopped `sonarqube`** (1.55 GiB RSS, code-quality tool, not on the trading path). Freed VM headroom.
4. **Stopped `heber-health-monitor`** (695 MiB RSS, Prometheus-style metrics sidecar for Heber's own pipeline health, not on Orion's data path). Freed additional VM headroom.

### Verified outcomes

| metric | before | after stop+bump | after sonarqube stop | after heber-health stop |
|---|---|---|---|---|
| ingestion restarts/3min | ~9 | ~3 | 1 | 0 |
| execution restarts/3min | ~3 | ~3 | 1 | 0 |
| data_quality restarts/3min | ~150 | ~3 | ~3 | ~4 (its own cap, contained) |
| VM MemFree | 1.66 GiB | 1.66 GiB | 2.43 GiB | 4.51 GiB → 3.46 GiB (rebalancing) |

After the heber-health-monitor stop, the trading containers finally completed hydration:
- `ingestion`: reached `Starting Polling Loop` and then `Market closed. Sleeping until 2026-05-14 13:30:00 UTC` (overnight sleep, ~15h until tomorrow's market open).
- `execution`: reached `Engines Initialized. Entering Service Loop` and is polling for candidates.

Final restartcounts at 22:16 UTC: ingestion 1238, execution 297, data_quality 67. Trading-container counts have **not moved in the past 5 minutes**. Data_quality continues to cycle at ~3-4/min on its own 5G cap — visible but no longer causing collateral damage.

### Reversibility

- `docker start sonarqube` restores sonarqube (1.5 GiB cost). Do this when you next need code-quality review.
- `docker start heber-health-monitor` restores Heber's Prometheus metrics (700 MiB cost). Do this when you want Heber pipeline metrics again — it's a metrics-only sidecar, not on the data path.
- The docker-compose change is a code edit and persists across `docker compose up` cycles. To revert: `git revert` the commit and `docker compose up -d --no-deps --force-recreate data-quality`.

### Still owed (NOT applied today)

- **Option B from the original RCA:** refactor `run_quality_checks()` so it doesn't hold every Heber DataFrame in a single `results` dict for the full pipeline. This is the structural fix. The 5G band-aid will hold for now but the leak will continue to grow as Heber gold partitions accumulate; eventually the band-aid will need to grow again. Half-day of careful work, no urgency tonight.
- **Drift alarm on `restartcount`** — operator-facing visibility into runaway restart loops. Currently the only way to notice is to `docker inspect` by hand.
- **VM-level OOM observability** — `docker inspect` doesn't surface `CONSTRAINT_NONE / global_oom` kills. A sidecar that watches dmesg and alerts would have saved this incident from hiding for 5 days.

### What to watch tomorrow

At 06:30 PT (13:30 UTC), market opens. Expected sequence:
1. Ingestion wakes from overnight sleep, starts pulling ALPACA_BAR_1M from Gateway WS and UW_FLOW from Heber.
2. Bronze rows appear within seconds; silver/candidates within the first minute.
3. Execution starts processing candidates (60-second cycle).
4. First EXECUTE decision and order within ~5 min of open.

Diagnostic command set for tomorrow morning:
```bash
docker inspect orion_ingestion orion_execution --format '{{.Name}} restartcount:{{.RestartCount}}'
docker exec orion_timescaledb psql -U orion -d orion_db -c "
  SELECT 'bronze 5m' s, COUNT(*) FILTER (WHERE received_ts_utc > NOW() - INTERVAL '5 minutes') n FROM bronze_events
  UNION ALL SELECT 'decisions 5m', COUNT(*) FILTER (WHERE timestamp_utc > NOW() - INTERVAL '5 minutes') FROM strategy_decisions
  UNION ALL SELECT 'orion orders today', COUNT(*) FILTER (WHERE created_at_utc::date = CURRENT_DATE) FROM orders WHERE client_order_id LIKE 'orion_%';"
```

If restartcounts on ingestion/execution have climbed materially overnight, the band-aid wasn't enough and option B is now urgent.
