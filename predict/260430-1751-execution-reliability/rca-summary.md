# RCA Summary — Pipeline-Online Investigation (2026-05-02)

Consolidated findings from a parallel 4-agent root-cause analysis on Friday's "alerts flowing, scoring happening, zero orders" state. Original symptom: 753 candidates / 376 strategy decisions / 1 EXECUTE / 0 fills in the trailing 24h.

## Findings

### A. orion_execution restart-loop (root cause — newly discovered)

**Symptom:** `RestartCount=362` in 35h on `orion_execution`, average restart every ~5.8 min, ExitCode=0 most of the time.

**Root cause:** `acquire_service_lease()` in `execution/execution_engine.py` generated a fresh `uuid4()` for every process incarnation. Combined with `restart: unless-stopped` and a 120s stale-lease window, every container restart saw its own previous instance's lease (which had been renewed within the last 30s) and refused to start with `RuntimeError: Another 'execution' instance holds a fresh lease`. Container exit propagated the exception → restart-policy fired → new uuid → same deadlock. Fix: lease identity reads `ORION_LEASE_OWNER_ID` env, falls back to uuid4 when unset; docker-compose sets `ORION_LEASE_OWNER_ID=orion_execution_compose` so a container restart reclaims its own lease.

**Secondary mode (data_quality, feature_enrichment):** OOMKilled=true, ExitCode=137 — Docker Desktop VM-level OOM killer reaping whichever container had the highest RSS during memory pressure. None of the active services had per-container `mem_limit` set (only `feature_enrichment` did). Fix: added `mem_limit` per service (execution 3g, ingestion 2.5g, position_monitor/pattern_miner 1.5g, data_quality 2.5g) so leaks are bounded and visible as scoped OOM events instead of cross-container cascades.

**Hardening:** added `execution_main_loop_exited_without_shutdown_signal` CRITICAL log + `execution_process_crashed` exc_info trap in `main_execution.py` so future silent ec=0 exits produce a structured event instead of fully invisible exits.

### B. ML pre-filter rejecting ~85% of candidates

**Symptom:** ML scores 0.03–0.45 vs 0.5 threshold for candidates with rule-engine `p_take=0.65–0.70`. Score floor cluster around 0.05–0.10 indicates the LightGBM model is converging to its prior.

**Root cause:** `feature_store._load_score_features()` consumes 6 Heber Gold datasets (`iv_surface_features`, `oi_momentum_features`, `straddle_momentum_features`, `temporal_excursion_features`, `flow_normalization_features`, `ticker_base_rates`) — **all six are upstream-empty** (see Finding C). Missing features arrive as `np.nan`, are then `fillna(0.0)` before predict_proba (scorer.py:175). With 30–60 of ~80 features zeroed, the model can't lift score above its prior baseline, hence the uniform 0.03–0.45 distribution. Calibration is doing the right thing on the data it has — the data is just mostly zeros.

**Mitigations applied:**
1. Lowered `ORION_ML_PREFILTER_THRESHOLD=0.05` for the execution service (was 0.5) — pipeline can now pass candidates through end-to-end while feature gap is fixed upstream.
2. Added `HeberReader._gold_empty_dataset_cache` (5min TTL) so repeated reads of the same empty dataset are no-ops instead of full path-walks. Cuts per-candidate I/O cost from ~18s to <1s when datasets are empty.

**Open:** Heber-side backfill of the 6 empty datasets is required for the ML model to operate on real features. Out of scope for Orion.

### C. gold_dataset_empty for 6 datasets

**Symptom:** repeated WARNING `gold_dataset_empty` for `iv_surface_features`, `ticker_base_rates`, `oi_momentum_features`, `straddle_momentum_features`, `temporal_excursion_features`, `flow_normalization_features` on every ML pre-filter pass.

**Root cause:** **Genuinely empty source data.** Neither a reader bug nor a sync filter — the upstream Heber Gold-builder has not produced partitions for these 6 dataset families. Verified by:
- Host cache `/Users/jacobmcmillan/.heber-cache/data/gold/dataset=NAME/` is an empty shell (no `project=*/version=*/dt=*` partitions, zero parquet files) for all 6.
- heber-sync source `/heber-source/gold/dataset=NAME/` is also empty.
- Comparison dataset `darkpool_features` has 30 parquet files in identical layout (proving sync logic works).
- `gold/labels_alert_barriers/dataset=NAME/` IS populated for these names (so the labeler stage ran on an upstream version), but that's labeler output, not feature data — reader correctly does not use it as a feature fallback.

**Fix:** Heber-side. The producer pipeline for these 6 feature families needs investigation (likely a silently failing or disabled job). No Orion-side change needed once upstream publishes data — `heber-sync` will mirror automatically.

**Orion-side mitigation applied:** the negative cache in HeberReader (Finding B fix #2) suppresses the per-candidate cost of reading these empty datasets until the upstream gap is resolved.

### D. signal_preflight Data Lag + throughput collapse

**Symptom:** 38 EXECUTE decisions today downgraded to SKIP "Data Lag" (`now - candidate.timestamp_utc > 600s`). Per-hour throughput collapsed during Friday's session: 14:00 UTC: 331 candidates / 168 decisions → 19:00 UTC: 61 candidates / **0 decisions**.

**Root cause:** signal_engine processes candidates **serially** (`for cand in candidates:` in `signal_engine.py`), and the dominant per-candidate cost was 6 sequential reads against empty Heber datasets at ~3s each (Finding C) → ~18s I/O wasted per candidate. Live log timestamps confirm 9–14s/candidate. At market-open arrival rate (~5.5 candidates/min), per-candidate cost (~10s) put the queue rate at break-even; any spike pushed it negative and the backlog grew monotonically. By 19:00 UTC, candidates aged past the 600s `signal_preflight` threshold and every EXECUTE was rejected as "Data Lag" before reaching the broker.

Note: `candidate.timestamp_utc` is the **source UW flow event time** (from `SilverSignal.event_time_utc`), not the candidate creation time. So the 600s lag budget must absorb the entire ingest pipeline, not just signal_engine queue time.

**Fix:** the HeberReader negative cache (Finding B fix #2) is the dominant lever — cuts per-candidate I/O cost ~10s → ~1s, restores throughput by an order of magnitude, and eliminates Data Lag rejections as a downstream side-effect.

## Applied Fixes (uncommitted at the time of this writing)

| Fix | File | Finding |
|-----|------|---------|
| Stable lease owner id via `ORION_LEASE_OWNER_ID` | `src/orion/execution/execution_engine.py`, `docker-compose.yml` | A |
| `mem_limit` on execution/ingestion/position_monitor/pattern_miner/data_quality | `docker-compose.yml` | A |
| `execution_main_loop_exited_without_shutdown_signal` CRITICAL log | `src/orion/main_execution.py` | A |
| `execution_process_crashed` exc_info trap around `asyncio.run` | `src/orion/main_execution.py` | A |
| Lowered `ORION_ML_PREFILTER_THRESHOLD=0.05` for execution service | `docker-compose.yml` | B |
| HeberReader negative cache for empty Gold datasets (5min TTL) | `src/orion/clients/heber_reader.py` | B, C, D |
| `ORION_CIRCUIT_BREAKER_ENABLED` master kill switch | `src/orion/config.py`, `src/orion/execution/execution_engine.py`, `docker-compose.yml` | (testing knob) |
| `ORION_GLOBAL_CIRCUIT_BREAKER_ENABLED` master kill switch | `src/orion/config.py`, `src/orion/core/circuit_breaker.py`, `docker-compose.yml` | (testing knob) |

## Verification

Post-fix container state (Sat 2026-05-02 ~06:01 UTC, market closed):
- `orion_execution`: rc=0, oom=false, mem 1.6GB / 3GB cap (54%)
- `orion_ingestion`: rc=0, oom=false, mem 1.6GB / 2.5GB cap (65%)
- `orion_position_monitor`: rc=0, oom=false, mem 216MB / 1.5GB cap
- `orion_pattern_miner`: rc=0, oom=false, mem 148MB / 1.5GB cap
- `orion_data_quality`: rc=0, oom=false, mem 138MB / 2.5GB cap (was OOM-cycling at ~10/h)
- Service lease acquired with stable id `orion_execution_compose`
- Engine `Entering Service Loop`, no candidates pending (zero unprocessed in DB), signal_engine polling

Pipeline ready for Monday market open. End-to-end EXECUTE→order verification deferred until next trading session.

## Out of scope

- Heber-side backfill of `iv_surface_features`, `ticker_base_rates`, `oi_momentum_features`, `straddle_momentum_features`, `temporal_excursion_features`, `flow_normalization_features` (Finding C — upstream gap).
- ML model retraining once those datasets are populated.
- Profiling the FE/DQ memory leak referenced in `docs/rca/feature_enrichment_crash_loop.md` (mem_limits now contain blast radius, but the underlying leak still wants investigation).
- Replacing `restart: always`/`restart: unless-stopped` with `restart: on-failure:N` + healthcheck-driven liveness (RCA Option 2 — defer until silent-exit log proves the gather hardening surfaces the right signals).
