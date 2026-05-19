# Orion follow-ups — open items as of 2026-05-19

This is the consolidated list of issues still owed from the May 8-14
debugging + native-migration work. Sorted roughly by impact.

---

## P0 — actively losing trading opportunities

### 1. Ensemble consensus collapsed since May 14
**Symptom.** On May 14 the solver ensemble scored candidates at 0.65-0.70
and produced 204 EXECUTE decisions / 94 orders / 14+ fills (current
positions worth ~+$62k unrealized). Since then:

- May 15: 2 EXECUTE / 995 decisions. Reason on the SKIPs: `Ensemble Rejected (0.20 < 0.5)`.
- May 16-17: weekend.
- May 18: 0 EXECUTE / 320 decisions. Same reason — `Ensemble Rejected (0.20 < 0.5)` and `(0.25 < 0.5)`.
- May 19 (today, pre-market): nothing yet.

**Hypothesis ranked most-to-least likely:**

1. **The 4 frozen `SHORT_SWING` entry models drag the ensemble down.** They
   were last trained 2026-03-31 (49 days old) and are loaded with
   `stale_model_policy='warn'`. Their probability outputs are calibrated
   against March features; on current features they likely emit
   near-zero. If the solver ensemble averages bucket scores, a frozen
   bucket pulls the mean below threshold.
2. **Calibration drift from May 15 retrain inputs.** May 14's 14 fills
   showed up in the next day's training data as labeled outcomes. If
   any label-generation step or feature pipeline broke on that
   intra-day data, subsequent retrains poison the calibration. AUCs
   from the cron log are still 0.73-0.81 though, so the models can
   still discriminate — this would be a calibration issue, not a
   discrimination one.
3. **Regime shift.** Real market change between May 14 and May 15
   that the models can't generalise to.

**To investigate:**
- Add structured logging inside the solver router so each candidate's
  per-bucket model scores AND the weighted consensus calculation are
  visible. Right now we only see the final ensemble value, not how it
  was assembled.
- Roll back to the May 14 model artefacts (in `models/archive/2026-05-14T030000-0700/`)
  and observe whether ensemble scores climb back into the executable range.
- If yes → calibration drift. Diagnose what changed in training
  inputs between May 14 and May 15.
- Independently: train the SHORT_SWING entry models. See item #2.

### 2. `SHORT_SWING` entry models are 49 days old, retrain never touches them
**Symptom.** `models/SHORT_SWING_avoid_stop.pkl`,
`SHORT_SWING_hit_target_50.pkl`, `SHORT_SWING_hit_target_100.pkl`,
`SHORT_SWING_quick_winner.pkl` all mtime `Mar 31 16:49`. Every other
model has mtime within the last 7 days. The nightly cron
`scripts/run_nightly_retrain.sh` runs and reports OK, but
`scripts/run_training.py → run_all_pattern_mining()` does not iterate
the SHORT_SWING entry buckets — only POSITION and SWING.

**Verdict from May 14 RCA:** known training-loop bug or
data-availability gate excluding SHORT_SWING. The cron will faithfully
reproduce the gap every night until it's fixed.

**To fix:** trace `run_all_pattern_mining()`'s bucket iteration. Either
add SHORT_SWING (and 0DTE) to the loop, or document why they're
excluded and stop loading them via the scorer (so they don't drag the
ensemble).

### 3. `POSITION_avoid_stop` model is essentially noise
**Symptom.** CV AUC = 0.500 on a class-imbalanced label (87.9% positive
rate). The `[LOW]` save-guard catches it — the on-disk file isn't
overwritten — so this is mostly cosmetic, but the *previous* artefact
is still being loaded as if valid. The model contributes ~zero
discriminative signal to the ensemble.

**To fix:** drop the `avoid_stop` target for POSITION until either
class balance improves or the feature set actually predicts stop-outs.
Currently the label is effectively constant from the model's POV.

---

## P1 — silent state divergence (will bite us eventually)

### 4. Orion's `orders` table is out of sync with Alpaca
**Symptom.** All 94 Orion-submitted orders from May 14 show
`status = 'pending_new'` in the local `orders` table, even though
Alpaca returns `status = 'filled'` (for the 14 that filled) or
`status = 'expired'` (for the rest that expired at close).

The 8 open positions are real (visible on Alpaca, ~+$62k unrealized),
but the in-process risk manager doesn't know about them — they're not
in the `fills` table (which has 0 rows for the week) and not reflected
in `positions`/`risk_state`. **The `_sync_risk_from_gateway` lookup at
startup loads positions correctly, so risk isn't broken, but trade
journaling and post-trade analysis are.**

**Root cause guess:** `FillProcessor` polling never updates the orders
table back from `pending_new`. The poll loop in
`execution_engine.poll_fills` only refreshes account equity now (post
the May 8 throttle); the actual fill processing may have stopped firing.

**To fix:** verify `poll_fills` still calls `_process_single_fill` and
that it writes into both `fills` AND `orders.status`. If the throttle
broke the path, restore it. Also: backfill the order statuses for the
14 May-14 fills so analysis tools see the real positions.

### 5. `orion_ingestion` (Docker) is still flaky
**Symptom.** `restartcount = 351` since the May 8 recreate. Restarted
again at 04:00 today. Survives long enough to produce candidates but
not reliably for full sessions. RSS grows to >4 GiB during a cycle
(still the 5-day Heber gold growth that broke `data_quality` and
`execution` on May 13-14).

**Why we didn't fix it on May 14:** scope was execution only. Ingestion
"works" because each cycle finishes a polling pass before dying.

**To fix:** mirror the native-execution migration for ingestion. The
wrapper + plist is in `scripts/run_execution_native.sh` and
`scripts/launchd/com.empire.orion.execution.plist`; copy + adapt for
ingestion. Test against the lease guard (use `orion_ingestion_native`
as the lease owner id). Once stable, stop the Docker container.

### 6. `orion_data_quality` still cycles on its 5G cap
**Symptom.** Container is up but its `restartcount` has been climbing
slowly all week. Same `run_quality_checks` Heber-gold-read pipeline
that holds every DataFrame in a `results` dict for the full sweep. The
5G cap (raised May 13) keeps the OOMs from triggering global VM
pressure, but the container is still leaking through each cycle.

**To fix (the real one):** refactor `run_quality_checks()` to stream —
each check returns its summary stats only and frees the underlying
DataFrame before the next check runs. Most checks are aggregations;
a 24h flow DataFrame can become a 5-row summary trivially. This is
option B from the 2026-05-13 RCA (`predict/260513-2030-restart-loop-rca/RCA.md`).

---

## P2 — operational hygiene

### 7. `pending_orders` tz fix wasn't migrated via alembic
**Today's fix.** `src/orion/storage/models_risk.py` now declares
`DateTime(timezone=True)` on all three columns
(`updated_at_utc`, `processed_at_utc`, `created_at_utc`), and the live
DB columns were `ALTER`ed to match. This unblocks the in-flight order
persist that was failing with `asyncpg.DataError` since the table was
introduced.

**Owed:** an alembic revision that captures the column-type change so
a fresh `init_db()` produces the right schema and the `ALTER TABLE`
isn't a one-off operator manual step. Generate with
`uv run alembic revision --autogenerate -m "pending_orders tz fix"`.

### 8. VM-level OOM observability
**Symptom from May 13.** `docker inspect` doesn't surface
`CONSTRAINT_NONE / global_oom` kills. The Docker-VM dmesg was the
only signal that ingestion + execution were getting killed by the
kernel's last-resort OOM picker (not by their own cgroup caps). This
ate 5 days of pipeline downtime because nobody saw the cascade until
I dug into the kernel log.

**To fix:** sidecar that polls `dmesg` every 30s and reports new
`oom-kill:constraint=` events. Hook into your existing alert/Slack
path (whatever the trading-bot uses) so an OOM cascade is loud.

### 9. Drift alarm on `restartcount`
**Symptom.** The only way to spot a container in a tight restart loop
right now is `docker inspect` by hand. Worth a periodic check that
alerts if any orion container's `RestartCount` climbs more than N per
hour.

### 10. The 4 service-stops from May 13-14 are persistent runtime state
**Stopped at runtime, NOT in docker-compose:**
- `sonarqube` (code-quality tool, 1.55 GiB)
- `heber-health-monitor` (Heber's metrics sidecar, ~700 MiB)
- `heber-dataflow-health` (~105 MiB)
- `heber-compactor` (~583 MiB)
- `kairos`, `cerberus_trader` (your other trading systems, ~1.5 GiB combined)

The Docker version of `orion_data_quality` is up but I stopped it once
during the May 13 fix; it's been recreated since (probably by the May 13
docker-compose change).

**To decide per item:**
- `sonarqube` — start when you next do code review.
- `heber-*` — these are Heber's own pipeline maintenance; restart if you
  want Heber metrics/compaction. Orion's read path doesn't need them.
- `kairos`, `cerberus_trader` — your other Empire trading systems; start
  when you want them trading again.

---

## P3 — known but not blocking

### 11. Docker ingestion has the same Heber-gold-growth issue
Same diagnosis as `data_quality` — `FeatureEngine.hydrate_history`
reads Heber gold parquet, holds DataFrames during the hydration. 5 days
of partition growth pushes RSS past the 4 GiB → 6 GiB cap (we bumped
on May 14). Same streaming fix would help.

### 12. `Docker Desktop VM RAM is 16 GiB`
With Orion's current footprint (ingestion + execution + data_quality
+ position_monitor + feature_enrichment + pattern_miner + etc.), the
VM is permanently near its ceiling. **Raising to 24 GiB in Docker
Desktop preferences would give us back the headroom we keep burning
through Memcap bumps.** This is a UI click I can't make for you.

### 13. Trading-system attribution gap on the shared Alpaca account
The shared paper account has 102 non-Orion positions from your other
systems (3Roses, Cerberus, Kairos, etc.). The `client_order_id`
prefix scheme (`orion_…`) does work for attribution, but no current
tooling slices "what does each system contribute to the account-wide
PnL?" — Athena does post-trade analysis but I don't think it currently
slices by prefix. Worth confirming.

### 14. Bucket `0DTE` exit classifier is single-class
On May 14's training run: `Skipping exit classifier training for 0DTE:
single class labels`. All recent 0DTE positions exited with the same
outcome class, so the model can't discriminate. Either accumulate more
diverse 0DTE outcomes or drop 0DTE from the bucket scorer ensemble.

---

## What ran fine and is stable

For completeness — these are the things we built in the last 11 days
that are currently working and should not be touched:

- **Native `orion_execution` via launchd** —
  `scripts/run_execution_native.sh` +
  `~/Library/LaunchAgents/com.empire.orion.execution.plist`.
  Uptime as of 2026-05-19 02:30 PT: 4d 16h, restartcount 0.
- **Bare-EventEnvelope WS fix** (May 8) — `_is_bar_message` now
  detects bars without a top-level `type` field; bronze flow restored.
- **Nightly retrain cron** — running 03:00 PT daily, archives previous
  models to `models/archive/<ISO-ts>/`, retrains, restarts execution.
  Logs at `logs/cron_retrain.log`.
- **Shared-account drawdown false-trip** (May 8) — both
  `current_equity` and `peak_equity` now seed-once from Gateway and
  only move via Orion-attributed fills. See
  `memory/project_shared_alpaca_killswitch.md`.
- **Options tick rounding** (May 8) — `round_to_options_tick` snaps
  to $0.05 / $0.10 grid; no more 422 Unprocessable from Alpaca on
  sub-penny mid-quotes.
- **Auto-skip stale candidates** + freshness filter on
  `fetch_pending_candidates` (May 8) — stops execution from burning
  ML cycles on candidates that will fail the preflight `Data Lag`
  gate.
- **`pending_orders` tz fix** (today) — persists in-flight order
  exposure across restarts.
