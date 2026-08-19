# Edge research — factor evaluation on the Heber labeled-alert dataset (2026-08)

ClearML task: **`ce5e3666053e45aa88136b23df102932`**
(project `Orion/edge-research`, task `factor-eval-2026-08-16`,
UI: http://127.0.0.1:9080/projects/bd9d7f34c24b45719600d9b36f287781/experiments/ce5e3666053e45aa88136b23df102932)

Git commit at run time: **`3a856e94bb7e56c77b8b368725e46b724e1a9664`**
(worktree `claude/orion-debug-2ab92b`; the task brief quoted `4d729571`, which is not this
worktree's HEAD — the run is stamped with the commit that actually produced it.)

Read `results.md` for the findings. This file is how to rerun.

## What this evaluates

Candidate return-predictive factors for Orion's flow-following long-option entries, measured
on **Heber's labeled UW alert universe**, not on Orion's 22 executed round trips. The label is
a triple barrier on the **option contract mid**, so it is the right kind of outcome for a long
option buyer — but its barriers are Heber's (+29% / −15%), not Orion's (+40..75% / −30..45%).

## Rerun

```bash
cd /Users/jacobmcmillan/Empire/Orion/.claude/worktrees/orion-debug-2ab92b

# 1. join labels x alert-time features x strictly-lagged daily gold features (~8 min)
uv run python research/edge_2026_08/build_dataset.py

# 2. recover Orion's entry fields (is_sweep, synthesised aggressor) from Silver flow_alerts (~4 min)
uv run python research/edge_2026_08/add_silver_flow.py

# 3. run the factor tests (~40 min; the P5 unfiltered population dominates the runtime)
uv run python research/edge_2026_08/evaluate_factors.py

# 4. re-test the F5 signed-flow factors (fast; they need the Silver ask/bid split from step 2)
cd research/edge_2026_08 && uv run --project ../.. python rerun_flow_factors.py

# 5. trading-terms translation: expectancy, gate simulation, stop-granularity sensitivity
uv run python research/edge_2026_08/gate_simulation.py

# 6. the robustness re-runs the adversarial review demanded (feature-lag caps, the omitted
#    entry filters, an honest train-only joint model, survivorship, BH dedup)
cd research/edge_2026_08 && uv run --project ../.. python review_fixes.py

# 7. attach config + artifacts to the ClearML task
uv run --with clearml python research/edge_2026_08/clearml_register.py
```

Everything is read-only against `~/.heber-cache/data` and the live Orion DB. No `src/` changes.

## Files

| File | What |
|---|---|
| `build_dataset.py` | Builds `out/panel.parquet`. Dedups every gold table on its natural key (they are write-duplicated up to 86x in this cache). Joins `meta_label_features` on `alert_id` (PIT-safe by construction) and every daily EOD gold table with a **strict** `feature.ts_available <= alert.ts_event` as-of join. |
| `add_silver_flow.py` | Recovers `is_sweep` and the synthesised `aggressor` from Silver `flow_alerts`, because `meta_label_features.side/aggressor/is_sweep` are effectively dead in this cache. Mirrors `src/orion/processing/normalizer.py:99-111`. |
| `evaluate_factors.py` | Universe funnel, factor construction, quantile-spread + Spearman with day-clustered bootstrap CIs, time-blocked OOS, Benjamini-Hochberg q-values, joint logistic model, by-month stability. |
| `out/panel.parquet` | The joined panel (96,072 labeled alerts). |
| `out/universe.csv` | The filter funnel, n at each step. |
| `out/results.csv` | One row per (population, factor, outcome). |
| `out/factor_coverage.csv` | Non-null coverage of each factor inside the Orion-strict population. |
| `out/joint.csv` | Joint-model OOS AUC. |
| `out/monthly_stability.csv` | Per-month tercile spread of every factor. |
| `out/power.json` | Minimum detectable effect. |
| `out/r1_lag_sensitivity.csv` | Factor results restricted to small feature-availability lag. |
| `out/r2_full_entry_filters.csv` | Factor results with the ET window / volume floor / delta band added. |
| `out/r3_joint_honest.csv`, `out/r3b_dte_ablation.csv` | Train-only joint model and the DTE ablation. |
| `out/r4_survivorship.csv` | Retained vs dead-label-dropped covariate comparison. |
| `out/review_fixes.txt` | Console record of all the review re-runs. |
| `out/codex_review_clean.md` | The adversarial review, verbatim. |
| `results.md` | The write-up, including the adversarial review and disposition table. |

## Point-in-time discipline (the thing that makes or breaks this)

Every Heber gold feature table is a **whole-session daily aggregate** whose `ts_available` is
stamped at batch-write time (`Heber/heber/features/pipelines/base.py:22-30` calls
`datetime.now(UTC)`). Joining one to an intraday alert on the calendar date is look-ahead:
a 09:45 sweep would see the afternoon's flow.

This study therefore uses `merge_asof(..., direction="backward", allow_exact_matches=False)`
on `ts_available` vs the alert's `ts_event`, per ticker. That is why coverage for the lagged
gold factors sits at 15-55% rather than the 79-98% a same-day join would report. The
per-factor staleness (`*_stale_days`) is carried in the panel so it can be audited.

`meta_label_features` is the exception: it is computed synchronously at alert intake
(`Heber/heber/watch/consumer.py:_extract_and_store_features`), and the measured
`ts_available - ts_event` lag is positive on **100%** of rows (1st percentile 12.7 s).

## Known data defects this study works around

* Gold write-duplication up to 86x — every table is deduped on its natural key.
* One corrupt `volatility_features` parquet (unreadable footer) — skipped.
* `meta_label_features` has six different file schemas; missing columns are filled with NaN.
* `labels_alert_barriers` 1.3x concurrency dup — deduped `keep="first"` per Heber's own guidance.
* 0DTE dead labels (`bars_to_hit==0 & mfe==0 & mae==0`, Heber CHANGELOG "known issues, not
  yet fixed") — dropped, and the count of Orion-shaped rows lost that way is reported.
