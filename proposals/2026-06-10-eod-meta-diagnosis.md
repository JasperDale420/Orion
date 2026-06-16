# EOD Review & Meta-Search Diagnosis — 2026-06-10

Read-only investigation. No source files modified. Author: automated diagnosis pass.

## TL;DR
- **EOD agent is NOT "never working" — it runs daily in Docker (`orion_eod_agent`, `run_id=docker_persistent_eod`) and reaches the LLM successfully.** It crashes at the **DB persistence step** with a `ForeignKeyViolationError` whenever the LLM follows the prompt's own instruction to emit `target_solver_id='paper_v1'`, because **no solver with `solver_id='paper_v1'` is seeded** (only `*_paper_v1` variants are). This is the #1 root cause and is fully reproduced in the live logs.
- **Meta-search has NO production scheduler at all.** `meta-search` and `meta-weekly` Docker services exist but sit behind the `scheduled` Compose profile that is **never brought up** (no `meta` containers exist, no launchd plist, no cron). Every meta-search log line in `logs/` is from pytest, not production. So "the meta runs never worked" = they were never actually scheduled.
- Secondary: `main_meta.py` has a constant/logic mismatch in its self-scheduler, several persistence paths are fragile, and EOD persistence is not exception-isolated so one bad proposal nukes the whole run (report + all YAML artifacts).

---

## (a) Current wiring diagram — what is scheduled where

### Native (launchd) — actually loaded (`launchctl list`)
```
com.empire.orion.ingestion      -> scripts/run_ingestion_native.sh -> python -m orion.ingestion   (RUNNING, PID 34597)
com.empire.orion.execution      -> python -m orion.main_execution                                 (RUNNING)
com.empire.orion.launchd-health
com.empire.orion.market-open-dataflow-check
com.empire.orion.orphan-close
com.empire.ai-gateway           -> cli-proxy LISTENING on :8002 (PID 30402)  [LLM endpoint, healthy]
```
There is **NO launchd plist for eod / meta** in `scripts/launchd/` (only execution, ingestion, launchd-health, market-open-dataflow-check, orphan-close).

The native ingestion service contains an **in-process EOD trigger**:
`src/orion/ingestion/service.py:573 _check_eod_trigger()` fires `EODReviewAgent().run_review()` once/day when `now_utc.hour == 1 and minute >= 5` (01:05 UTC). Errors are swallowed (`service.py:584 _run_eod_task` try/except logs only). **But** the native wrapper points DB at `localhost:5440` and gateway at `localhost:8080` (`run_ingestion_native.sh:56-58`), a different stack than the Docker EOD agent uses.

### Docker Compose
Running containers right now (`docker ps`): `orion_eod_agent`, `orion_timescaledb`, `orion_pattern_miner`, `orion_position_monitor`, `orion_feature_enrichment`, `orion_data_quality`, `orion_indexer`, `orion_heber_sync`. **No `orion_meta_search` / `orion_meta_weekly` containers exist.**

| Service | Compose def | Profile | Command | Status |
|---|---|---|---|---|
| `eod-agent` | docker-compose.yml:288 | **none (default)** | `python -m orion.main_eod` | **RUNNING** (Up 21h, healthy) — crashes each cycle at DB persist |
| `meta-search` | docker-compose.yml:317 | `scheduled` | `python -m orion.main_meta --base-solver … --scheduled` | **NOT RUNNING** (profile not up) |
| `meta-weekly` | docker-compose.yml:382 | `scheduled` | `python -m orion.main_meta_weekly --scheduled` | **NOT RUNNING** (profile not up) |

`eod-agent` env (docker-compose.yml:300-305): `DB_URL=postgresql+asyncpg://orion:orion_password@timescaledb:5432/orion_db`, `ORION_RUN_ID=docker_persistent_eod`, `ORION_AI_GATEWAY_URL=http://host.docker.internal:8002/v1` → reaches the native ai-gateway (confirmed listening on :8002). The LLM path **works**.

### Net effect
- EOD: scheduled twice (Docker `eod-agent` AND native ingestion 01:05 UTC trigger) but against two different stacks; the Docker one runs and the LLM succeeds, then it dies at persistence.
- Meta (daily + weekly): **effectively unscheduled in production.** Only manual CLI (`-m orion.main_meta …`) or the post-EOD `solver_mutation_processor` path would ever invoke it.

---

## (b) Evidence collected

### EOD live failure (today, 2026-06-10) — `logs/orion.log`, `run_id=docker_persistent_eod`
LLM tools executed fine (`run_command`, `write_file` "Tool completed"), report written, then:
```
"event": "eod_review_failed", "date": "2026-06-10", level ERROR
asyncpg.exceptions.ForeignKeyViolationError: insert or update on table "solvers"
  violates foreign key constraint "solvers_parent_solver_id_fkey"
DETAIL:  Key (parent_solver_id)=(paper_v1) is not present in table "solvers".
[SQL: INSERT INTO solvers (... parent_solver_id ...) VALUES (...)]
[parameters: ('eod_95281e27d473f42d', 'eod_derived', ..., 'paper_v1', 'llm_eod_agent', ...)]
  File ".../main_eod.py", line 133, in _run_eod_review  -> agent.run_review(today)
  File ".../agents/eod_review_agent.py", line 119, in run_review -> _persist_solver_edits
  File ".../agents/eod_review_agent.py", line 192, in _persist_solver_edits -> db_write(save_edits)
```
The exception propagates up through `main_eod.py:52` (`EODService.run`) → logged as `eod_service_error` → 60s backoff → loop. So the agent re-attempts and re-fails. orion.log shows 2 EOD failure events vs 1 "EOD Review Complete" in the current window.

### Root-cause code path
- `src/orion/agents/eod_review_agent.py:854` (LLM prompt example) and `:884` ("Use target_solver_id='paper_v1' to mutate the active paper solver") instruct the model to emit `paper_v1`.
- `eod_review_agent.py:168-178`: builds `Solver(... parent_solver_id=str(base_id) ...)` where `base_id = p.get("target_solver_id")` (line 153) = `'paper_v1'`.
- `src/orion/storage/models_solvers.py:26`: `parent_solver_id ... ForeignKey("solvers.solver_id")` → FK requires the parent row to exist.
- **Seed migration `alembic/versions/0026_seed_initial_solvers.py` seeds `bullish_sweep_paper_v1`, `bearish_put_paper_v1`, `rsi_mean_revert_paper_v1`, `swing_entry_paper_v1` — NOT a bare `paper_v1`.** Confirmed: exact grep for `'paper_v1'` (not `*_paper_v1`) returns nothing in alembic/ or seed_solvers.py.
- `eod_review_agent.py:119` calls `_persist_solver_edits` with **no surrounding try/except**, and it runs *before* the YAML artifact save loop (`:121-131`), so a single bad proposal aborts the entire run and prevents proposal YAMLs from being written.

### Why it looked "intermittent"
The latest successful artifacts (`proposals/2026-06-05_*solver_edit*`) target **real** seeded solvers (`swing_entry_paper_v1`, `diversified_baseline_v1`, `zero_dte_sweep_paper_v1`) — those inserts satisfy the FK and succeed. The crash only happens on runs where the LLM literally uses `paper_v1` (as the prompt tells it to). Non-deterministic LLM output ⇒ "sometimes works, mostly broken."

### CHANGELOG corroboration (`CHANGELOG.md`)
- `:3618 EOD Agent FK Constraint: Fixed solver_edits insert by creating Solver stub before edit record` — this prior fix created the *child* stub but did not guarantee the *parent* (`paper_v1`) exists, so it left the current bug.
- `:25` (RCA 2026-06-08) — separate but related data-integrity issue: `trade_journal_entries.realized_pnl` was NULL for all 1,728 rows, so EOD/weekly aggregator read no PnL. Now patched, but EOD/weekly outputs prior to that were structurally blind to PnL.
- `:448 EOD Agent LLM calls failing with LogRecord conflict (2026-03-13)`, `:3616 EOD Agent Async Bug (missing await)`, `:3617 Proposal schema solver_mutation→solver_edit` — a long history of EOD breakage, consistent with "never reliably worked."

### Meta-search "evidence of failure" is actually absence of execution
- No `orion_meta_*` container in `docker ps -a`.
- No launchd/cron entry for `main_meta*`.
- All `MetaSearch` lines in `logs/orion.log` resolve to pytest tmp paths (e.g. `/T/pytest-of-jacobmcmillan/pytest-232/...`, `sqlite3.OperationalError`), i.e. unit/integration test runs — not production.
- `main_meta.py` self-scheduler bug: declares `SCHEDULED_HOUR_UTC = 22` (`:14`, "22:00 UTC") but the loop matches `now.hour == 18` in `America/New_York` (`:52`). The constant is dead and the comment is inconsistent; minor, but indicates the scheduler was never exercised in prod.

### LLM endpoint / config (`src/orion/config.py:309-314`)
`AgentSettings`: `model_name="glm-5.1"`, `ai_gateway_url` default `http://localhost:8002/v1` (Docker overrides to `host.docker.internal:8002/v1`), `ai_gateway_key` default `empire-ai-gateway-key`. Gateway confirmed LISTENING on :8002 (PID 30402 `cli-proxy`). `codex_client.py:194-225` posts to `{base_url}/chat/completions` with bearer key. **No evidence the LLM/gateway is a failure source** — tools complete in the logs.

### Test coverage (`uv run pytest tests/agents --co`)
125 tests collected, incl. `test_eod_agent.py`, `test_eod_agent_proposals.py`, `test_eod_review_agent.py`, `test_meta_search_edits.py`, `test_meta_weekly.py`, `test_meta_promotion.py`, etc. These use mocked/seeded DBs with valid parents, so they never reproduce the prod "missing `paper_v1` parent" FK violation — a test-fixture blind spot.

---

## (c) Ranked root-cause hypotheses (with confidence)

1. **[CONFIRMED, ~0.97] EOD persistence FK violation on `parent_solver_id='paper_v1'`.** Prompt tells the LLM to use `paper_v1`; no such solver is seeded; FK `solvers_parent_solver_id_fkey` rejects the insert; unguarded `_persist_solver_edits` aborts the whole run. Direct traceback in today's logs.
2. **[CONFIRMED, ~0.95] Meta-search/meta-weekly are never scheduled in production.** Behind unused `scheduled` Compose profile; no launchd/cron; no live containers; all "runs" in logs are pytest. They cannot have produced `meta_experiments`/`promotion_recommendations` in prod because they never executed.
3. **[HIGH, ~0.8] EOD run is not exception-isolated.** `_persist_solver_edits` at `eod_review_agent.py:119` runs before YAML artifact writes and has no try/except, so any single malformed/unsatisfiable proposal discards the entire EOD output (report metadata + all proposal YAMLs), amplifying #1.
4. **[MEDIUM, ~0.6] Dual/divergent EOD scheduling causes confusion and possible lease/stack mismatch.** Native ingestion trigger (01:05 UTC, localhost:5440 DB, gateway :8080) vs Docker eod-agent (timescaledb:5432, gateway host.docker.internal:8002). If the native trigger ever fires, it targets a different DB/gateway than the Docker agent and its errors are silently swallowed (`service.py:584`).
5. **[LOW-MED, ~0.4] `main_meta.py` scheduler constant/timezone inconsistency** (`SCHEDULED_HOUR_UTC=22` unused; loop uses `hour==18` ET). Would mis-fire if/when the service is ever enabled.
6. **[LOW, ~0.3] Upstream data emptiness for meta/weekly.** Pre-2026-06-08 `trade_journal_entries.realized_pnl` was all-NULL (CHANGELOG:25), so even a working weekly aggregator read no PnL. Now patched but historically a contributing "looks broken" factor.

---

## (d) Concrete fix plan (with effort estimates)

**F1 — Make `paper_v1` resolvable (root cause #1).** Effort: S (1–2h).
Pick ONE:
- (a) Seed a canonical `paper_v1` solver row (new alembic migration + `seed_solvers.py`) that the prompt's alias refers to; OR
- (b) Change the EOD prompt (`eod_review_agent.py:854,884`) to use a real seeded id (e.g. `diversified_baseline_v1`); OR
- (c) In `_persist_solver_edits`, resolve/validate `base_id` against existing solvers and skip+log (or map an alias `paper_v1 -> <active paper solver>`) before insert.
Recommend (a)+(c): seed the alias AND validate defensively so a bad LLM id can never FK-crash.

**F2 — Exception-isolate EOD persistence (root cause #3).** Effort: S (~1h).
Wrap the `_persist_solver_edits` call (`eod_review_agent.py:119`) in try/except that logs and continues to the YAML save loop, so report + proposal artifacts always persist even if one DB write fails. Add a per-proposal try/except inside `save_edits`.

**F3 — Actually schedule meta-search/meta-weekly (root cause #2).** Effort: M (2–4h).
Decide the deployment model and make it real:
- If Docker: bring up the `scheduled` profile (`docker compose --profile scheduled up -d`) in whatever brings the stack up, and document it; OR
- If native (matching ingestion/execution): add `scripts/launchd/com.empire.orion.meta-search.plist` + `meta-weekly.plist` + wrapper scripts, mirroring `run_ingestion_native.sh` (correct DB_URL/gateway).
Then verify a real run writes `meta_experiments` / `solver_edits` / `promotion_recommendations`.

**F4 — Fix `main_meta.py` scheduler (root cause #5).** Effort: XS (~20m).
Remove/align dead `SCHEDULED_HOUR_UTC`; make the trigger time single-sourced and tz-correct; add a structured log on each tick so future "is it scheduled?" questions are answerable from logs.

**F5 — Reconcile dual EOD scheduling (root cause #4).** Effort: S–M (1–3h).
Pick one EOD owner (Docker `eod-agent` OR native ingestion trigger) and disable the other to avoid split-brain DB/gateway targets. If native is the system of record, point Docker eod-agent off; if Docker is, remove/guard `_check_eod_trigger`.

**F6 — Close the test blind spot.** Effort: S (~1–2h).
Add a regression test that runs `_persist_solver_edits` against a DB where the parent solver does NOT exist and asserts graceful handling (no crash, edit skipped or alias resolved). Mirror for meta-search persistence.

Suggested order: F1 + F2 (unblock EOD today) → F6 (lock it) → F3 (turn meta on) → F4, F5 (hygiene).

---

## (e) Open questions for the owner
1. **Is `paper_v1` supposed to be a real solver, or an alias for the active paper solver?** That decides F1 (seed a row vs map alias vs change prompt). Which seeded solver is "the active paper solver" today — `diversified_baseline_v1`?
2. **Which EOD path is the intended system of record — the Docker `eod-agent` or the native ingestion 01:05 UTC trigger?** They target different DBs (5432 vs 5440) and gateways (8002 vs 8080).
3. **How are meta-search/meta-weekly meant to run in prod — Docker `scheduled` profile or native launchd?** Nothing currently schedules them. Should daily meta run after EOD, and weekly on Friday 17:30 ET as the code assumes?
4. **Is the `scheduled` Compose profile ever brought up anywhere** (a wrapper, a cron, a runbook)? I found no caller.
5. **Should EOD continue auto-creating `eod_derived` research solvers at all,** or only emit YAML proposals for human/meta review? (Affects whether F1/F2 should write to `solvers` or just artifacts.)
6. **Post the 2026-06-08 realized-PnL fix, has any weekly aggregation actually been run** against the now-populated `trade_journal_entries.realized_pnl` to confirm the read path works end-to-end?
