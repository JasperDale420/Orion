# Factor evaluation on the Heber labeled-alert dataset

**ClearML task:** `ce5e3666053e45aa88136b23df102932` — project `Orion/edge-research`, task
`factor-eval-2026-08-16`, type `data_processing`.
**Git commit:** `3a856e94bb7e56c77b8b368725e46b724e1a9664` (branch `claude/orion-debug-2ab92b`).
The brief quoted HEAD `4d729571`; that is not this worktree's HEAD, so the run is stamped
with the commit that actually produced it.
**Run date:** 2026-08-16. **Seed:** 20260816. **Bootstrap:** 2,000 day-clustered resamples
(500 on the 67k-row robustness population).
**Adversarial review:** Codex `gpt-5.6-terra`, high effort, verdict **DO NOT SHIP**. Verbatim
in §10. Six of my draft claims were withdrawn or downgraded as a result; §9 is what survives.

---

## Headline (post-review)

**No factor should be gated, tilted, or shadow-logged as an act-on candidate on this
evidence.** That was the draft conclusion and it survives. Almost every *reason* I originally
gave for it did not.

Three things killed the positive candidates, and all three came out of checks the review
demanded:

1. **The features are not point-in-time in the way I claimed.** `meta_label_features`'s
   `ts_available` lags the alert by a **median of 18 minutes**, and **56% of rows were written
   more than a day later** (p90 = 6.8 days) — these are backfills. My draft cited the *1st
   percentile* (12.7 s) as evidence of PIT safety. Restricting to rows written within 15
   minutes of the alert, **both positive candidates evaporate**: `f_abs_delta` goes from
   +12.3 pp (p = 0.012) to −0.5 pp (p = 0.91), `f_vol_oi` from +5.8 pp to −1.7 pp (p = 0.79).
2. **Survivorship is real and points straight at the candidates.** The 431 Orion-shaped rows
   dropped by Heber's dead-label bug differ significantly from the 1,205 retained on
   `open_interest` (median 2,177 vs 3,602, KS p < 1e-4), `volume_oi_ratio` (1.19 vs 0.65,
   KS p < 1e-4), and `realized_vol_20d` (0.41 vs 0.33, p = 0.002). The filter selectively
   removes the high-volume/OI tail — which is exactly where `f_vol_oi` is supposed to carry
   information.
3. **The only robust signal is the label's own machinery.** A model containing `f_dte`
   alone gets OOS AUC **0.679 [0.574, 0.765]**; the five-feature train-selected model gets
   0.673 [0.559, 0.772] — no better. Gating on `f_dte` *loses* 2.2 pp of simulated
   expectancy. Removing `f_dte` from the model drops the top-half-score expectancy to
   **−2.5%**, worse than not ranking at all.

**Withdrawn outright:** the claim that Orion's entry universe has −3.4% expectancy (it is a
Heber-label simulation, not Orion P&L); the claim that a 30-second monitor would recover 4.1 pp
(the monitor defaults to **60 s**, tightening to 30 s only while a 0DTE is open — and this
study contains zero 0DTE); the "shadow-log `f_vol_oi` and `f_abs_delta`" recommendation.

**One new finding of independent value, discovered while checking the review:** Heber's
`POLL_CONFIG` declares SWING windows of 120 trading hours and LEAP of 720
(`Heber/heber/watch/models.py:199-212`), but the **data** says the realised windows are
**~3 trading hours for SWING and ~5 for LEAP**, stable in every month from February to August.
The label resolves in about three trading hours. Orion's SHORT_SWING max-hold is 54 h and its
SWING is 168 h. The label and the strategy are not measuring the same holding period, and
Heber should be told.

---

## 1. Data, joins and point-in-time discipline

### Inputs (all read-only)

| Dataset | Path | Rows read | Deduped to | dt range |
|---|---|---:|---:|---|
| `labels_alert_barriers` | `~/.heber-cache/data/gold/dataset=labels_alert_barriers/project=watch/version=v1` | 194,575 | 149,234 (`alert_id`, keep-first) | 2026-01-28 → 2026-08-14 |
| `meta_label_features` | `.../dataset=meta_label_features/.../v1` | 116,243 | 116,243 (`alert_id`) | 2026-02-05 → 2026-08-14 |
| `momentum_features` | `.../v1` | 2,820,837 | 235,374 (12.0x dup) | 2026-03-03 → 2026-08-07 |
| `flow_toxicity_features` | `.../v1` | 5,691,975 | 84,198 (67.6x dup) | 2026-01-28 → 2026-08-14 |
| `flow_features` | `.../v1` | 5,838,038 | 67,669 (86.3x dup) | 2026-03-03 → 2026-08-07 |
| `options_sentiment_features` | `.../v1` | 420,192 | 13,953 (30.1x dup) | 2026-03-09 → 2026-08-14 |
| `oi_momentum_features` | `.../v1` | 257,340 | 12,495 (20.6x dup) | 2026-03-24 → 2026-08-12 |
| `iv_surface_features` | `.../v1` | 36,125 | 12,776 (2.8x dup) | 2026-04-29 → 2026-08-13 |
| `greek_exposure_features` | `.../v1` | 549,188 | 45,191 (12.2x dup) | 2026-02-09 → 2026-08-12 |
| `volatility_features` | `.../v1` | 2,777,861 | 235,308 (11.8x; 1 corrupt file skipped) | — |
| `gex_regime_features` / `market_tide_context_features` | `.../v1` | 6,698 / 6,376 | 69 / 106 | market-wide daily |
| `labels_returns_1d` / `_5d` | `.../v1` | — | on `(ticker, date)` | secondary outcome only |
| Silver `flow_alerts` | `~/.heber-cache/data/silver/feed=flow_alerts` | 3,096,930 | 910,026 unique `event_id` | 2026-01-28 → 2026-08-14 |
| Orion DB `strategy_decisions` | live TimescaleDB, read-only | 1,501 `entry_quote` records | — | last 60 days |

**Joined panel: 96,072 labeled alerts, 2026-02-05 → 2026-08-14** (`out/panel.parquet`).

### Point-in-time rules applied — and their limits

* Daily EOD gold tables join with `merge_asof(direction="backward",
  allow_exact_matches=False)` on **`feature.ts_available <= alert.ts_event`**, per ticker.
  **The review is right that this is necessary but not sufficient**: `ts_available` is a
  batch-*write* clock, not a data-cutoff or first-observability stamp
  (`Heber/heber/features/pipelines/base.py:22-30` assigns `datetime.now(UTC)` when absent).
  All daily-factor results are **PIT-unverified**, not PIT-safe. `keep="last"` over up to 86
  duplicates may also select a later revision; payload equality was not proven.
* `meta_label_features` joins on `alert_id`. **My draft's "PIT-safe by construction" claim is
  withdrawn.** Measured `ts_available − ts_event` (`out/review_fixes.txt`, R1):

  | pct | min | p1 | p5 | p25 | **p50** | p75 | **p90** | p99 |
  |---|---:|---:|---:|---:|---:|---:|---:|---:|
  | seconds | 1.1 | 12.7 | 62 | 299 | **1,086** | 4,362 | **587,008** | 675,981 |

  Only 2.5% of rows are stamped within 60 s and only 24.2% within 15 minutes. A field that a
  backfill recomputed from post-alert data would leak, and the label's return is measured from
  an entry price struck *before* the feature row existed.
* The aggregated prior-flow factor uses `rolling("24h", closed="left")` on event-time rows, so
  the current alert is excluded by construction. This one is clean.

### A join that had to be rebuilt

`meta_label_features.side` / `.aggressor` / `.is_sweep` are dead in this cache: `aggressor`
NULL on **96,071 / 96,072**, `side = 'mid'` on 99.4%, `is_sweep = 1` on 781. Orion does not
read them either — it reads Silver `flow_alerts` and **synthesises** the aggressor
(`src/orion/processing/normalizer.py:99-111`: `ask_prem > bid_prem → ASK`).
`add_silver_flow.py` reproduces that, joining Silver on `event_id = alert_id`: **97.3%
coverage**; recovered ASK 47,969 / BID 44,351 / MID 1,122; `is_sweep = 1` on **21,133**.

---

## 2. Universe funnel

`out/universe.csv`.

| Step | n | tickers |
|---|---:|---:|
| 0. labeled alerts joined to alert-time features | 96,072 | 2,171 |
| 1. drop Heber's dead zero-label rows | 69,809 | 1,963 |
| 2a. Silver flow row recovered | 67,179 | 1,923 |
| 2b. `is_sweep = 1` | 15,141 | 967 |
| 3. buyer-initiated (synthesised aggressor = ASK) | 7,405 | 759 |
| 4. DTE 0–14 | 4,348 | 511 |
| 5. bucket premium floor ($50k / $100k) | 1,643 | 184 |
| 6. liquid-universe allowlist | 1,205 | 58 |
| 7. 0DTE restricted to SPY/QQQ/IWM | **1,205** | **58** |
| — Orion-shaped rows *lost* to the dead-label bug | 431 | 47 |

### The population is NOT `orion_strict` — renamed on review

The review correctly caught that the funnel omits three live entry filters
(`flow_rules.py:194-224`). Adding them (`out/r2_full_entry_filters.csv`):

| Omitted filter | Keeps | n |
|---|---:|---:|
| ET entry window (per bucket) | 93.3% | 1,124 |
| contract-volume floor (500 / 200) | 93.5% | 1,127 |
| delta band 0.25–0.60 (when known) | 83.1% | 1,001 |
| **all three together** | **73.3%** | **883** |

With all three, n = 883 / 53 days / 53 tickers, `hit_tp` 17.1%, simulated net expectancy
−2.90% (vs −3.39%). Conclusions are unchanged: `f_dte` still −12.4 pp (p = 0.002), `f_vol_oi`
falls to +5.5 pp (p = 0.186), `f_abs_delta` to +4.9 pp (p = 0.448).

**Also note the delta band is not comparable across the two worlds:** delta is NULL on 100% of
Orion's *live* candidates so the band is inert in production, but it is 64% populated here and
therefore active. The labeled population is not the live population. The name
`P1_orion_strict` in `out/results.csv` should be read as **`P1_orion_shaped`**.

Nested robustness populations: P2 (1,643, no allowlist), P3 (4,348, no premium floor),
P4 (7,405, sweep+ASK any DTE), P5 (67,179, unfiltered labeled). **These are not five
independent replications** — they nest, share most alerts, and share the same Feb–Mar regime.
They are a sensitivity analysis, not corroboration.

### Composition facts that constrain everything below

1. **Zero 0DTE rows.** Composition is SHORT_SWING (1–3 DTE) 255 / SWING (4–14) 950. Nothing
   here speaks to `rule_0dte_sweep_v2`.
2. **Time-lumpy:** Feb 239, Mar 600, Apr 38, May 12, Jun 13, Jul 72, Aug 231 — 70% is
   Feb–Mar 2026. Only 67 nominal day-clusters. This is one short, non-stationary episode.
3. **Survivorship is confirmed material and non-random** (`out/r4_survivorship.csv`):

| Covariate | Retained median (n=1,205) | Dropped median (n=431) | KS p |
|---|---:|---:|---:|
| `open_interest` | 3,602 | 2,177 | **<0.0001** |
| `volume_oi_ratio` | 0.647 | 1.189 | **<0.0001** |
| `minutes_since_open` | 76 | 86 | **<0.0001** |
| `realized_vol_20d` | 0.333 | 0.409 | **0.0016** |
| `days_to_expiry` | 7 | 7 | **0.0007** |
| `spot_price` | 438.6 | 418.0 | 0.0007 |
| `premium` | 166,775 | 176,582 | 0.65 |
| `delta` | 0.319 | 0.344 | 0.20 |
| `sv_net_ask_share` | 1.00 | 1.00 | 0.94 |

All 17 DTE = 0 rows are in the dropped set. The drop is not missing-at-random with respect to
`volume_oi_ratio`, which **is** the `f_vol_oi` factor.

---

## 3. Outcomes, and the label-vs-Orion mismatch

Read from Heber source. `Heber/heber/watch/checker.py:156`: `ret = (snap.mid_px −
entry_price) / entry_price` on the polled **option contract mid**. TP `0.25 + 2×0.02` →
**+29% effective**; SL **−15%**; ties to TP (`checker.py:296-310`).

**Measured label window** (`window_duration_hours = window_end − alert_time`, wall clock,
`checker.py:254`), median by horizon and stable Feb→Aug:

| Horizon | POLL_CONFIG declares | **Data says** |
|---|---:|---:|
| INTRADAY (DTE ≤ 2) | 4 h, 5-min poll | ~4 trading h (21.4 h wall clock across an overnight) ✓ |
| SWING (DTE ≤ 21) | **120 h**, 15-min poll | **~3 trading hours** ✗ |
| LEAP | **720 h**, 1-hr poll | **~5 trading hours** ✗ |

So 96% of this study's rows are labeled over roughly **three trading hours**, against Orion's
54 h (SHORT_SWING) and 168 h (SWING) max-holds. This is a defect in Heber worth reporting
independently of the factor question.

**Why the label is not an Orion P&L proxy** (review CRITICAL, accepted in full):

* entry price comes from a heterogeneous fallback chain — sweep `contract_px` → later live
  quote → payload bid/ask mid → last trade → an unflagged `$1.00` floor
  (`consumer.py:624-638`). (In this population the floor never fires: 0 / 1,205 rows have
  `entry_price == 1.0`; median $5.20, mean $8.07, close to Orion's own $8.41 mean entry mid.)
* exits are a mid-based ±29%/−15% watch barrier, over ~3 trading hours.
* Orion exits on +40/50/60/75% and −30/35/40/45% barriers plus max-hold, no-progress and
  drawdown rules, executed as marketable limits against a live bid.

Adding 4 pp back to TP payouts cannot repair different stopping times. **Every expectancy
number in this document is a Heber-label simulation, not an Orion P&L estimate.**

**Orion-barrier proxies are unusable.** The watch terminates at Heber's own barrier, so
`mfe`/`mae` are truncated: **0 of 1,205** rows have both `mfe ≥ 0.50` and `mae ≤ −0.15`, and
`y_orion_opt` / `y_orion_pess` are *identical* in every row. Orion's real barrier structure
is untestable here. The identical pair also inflated the BH family; q-values recomputed
without the duplicate are in `out/results.csv` as `q_value_dedup`.

---

## 4. Power

`out/power.json`. n = 1,205; 67 days; base `hit_tp` 17.6%; quintile groups ~241. Two-sided
α = 0.05, 80% power, day-cluster design effect at 18 alerts/day:

> **Minimum detectable quintile hit-rate spread ≈ 11.2 percentage points.**

Literature-scale effects translate to roughly 1–4 pp. **This dataset cannot see them.** A null
here means "no detectable effect of deployable size in this sample and proxy", never "the
predictor does not work".

---

## 5. Results

### 5.1 Survives BH at q < 0.20 (161 P1 tests; `q_value_dedup` after removing the duplicate proxy)

| Factor | Outcome | n | Q-hi − Q-lo | 95% CI | Spearman | p | q | OOS spread | OOS sign agrees |
|---|---|---:|---:|---|---:|---:|---:|---:|---|
| `f_dte` | `y_tp` | 1205 | **−13.3 pp** | [−19.4, −8.6] | −0.148 | 0.001 | 0.027 | −29.2 pp | yes |
| `f_vol_oi` | Orion proxy | 1189 | +4.6 pp | [+1.8, +7.8] | +0.127 | 0.001 | 0.027 | +4.5 pp | yes |
| `f_ask_share` | underlying 1d | 319 | +1.0 pp | [+0.3, +2.6] | +0.158 | 0.001 | 0.027 | — | — |
| `f_abs_delta` | `y_ret` | 768 | **+11.8 pp** | [+3.9, +20.2] | +0.253 | 0.003 | 0.069 | +13.8 pp | yes |
| `f_vol_oi` | `y_tp` | 1189 | +8.4 pp | [+2.0, +14.2] | +0.096 | 0.012 | 0.193 | +11.8 pp | yes |

Everything else — VRP (F1), Hu-Jacobs (F15), signed prior flow (F5), OTM-ness (F10), term
structure (F8), unsigned gamma (F9), OI momentum (F14), flow toxicity (F13), reversal (F11),
premium size, time of day — has q > 0.20 with a CI crossing zero. Full table `out/results.csv`.

**BH over 161 tests is the wrong family for a deployment decision.** It does not cover the
alternate populations, the by-month cuts, the OOS gate search, or the post-hoc selection of a
"best" factor. Treat §5.1 as exploratory screening only; a Romano-Wolf / max-statistic
correction over the whole gate-selection workflow with a locked final holdout is what a
shipping decision needs.

### 5.2 The feature-lag stress test — this is what kills the candidates

`out/r1_lag_sensitivity.csv`. Same tests, restricted to rows whose feature record was written
close to the alert.

| Factor | Outcome | all (n≈640) | lag ≤ 1 h (n≈510) | lag ≤ 15 m (n≈319) |
|---|---|---|---|---|
| `f_abs_delta` | `y_ret` | **+12.3 pp, p=0.012** | +6.6 pp, p=0.144 | **−0.5 pp, p=0.910** |
| `f_vol_oi` | `y_tp` | +5.8 pp, p=0.244 | +4.2 pp, p=0.380 | **−1.7 pp, p=0.794** |
| `f_ask_share` | `y_tp` | −5.5 pp, p=0.178 | −5.8 pp, p=0.184 | −5.5 pp, p=0.316 |
| `f_dte` | `y_tp` | **−17.0 pp, p=0.002** | −16.0 pp, p=0.002 | **−15.2 pp, p=0.010** |
| `f_hujacobs` (F15) | `y_tp` | +0.8 pp, p=0.902 | −3.7 pp, p=0.612 | −9.9 pp, p=0.204 |
| `f_vrp` (F1) | `y_tp` | −6.7 pp, p=0.236 | +0.2 pp, p=1.000 | +1.5 pp, p=0.876 |
| `f_prior_flow_align` (F5) | `y_tp` | −1.1 pp, p=0.774 | −0.8 pp, p=0.900 | +8.6 pp, p=0.116 |

Only `f_dte` — the label-machinery factor — is stable across the lag restriction. Every
candidate that depended on a *fundamental* field (delta, IV, vol/OI) is a function of feature
lag, i.e. of how long after the alert Heber got round to writing the row.

### 5.3 Cross-population sign consistency (`y_tp`, sensitivity not confirmation)

| Factor | P1 (1,205) | P2 (1,643) | P3 (4,348) | P4 (7,405) | P5 (67,179) |
|---|---:|---:|---:|---:|---:|
| `f_dte` | −13.3 | −13.2 | −10.6 | −20.3 | −18.4 |
| `f_vol_oi` | +8.4 | +3.7 | +3.6 | +7.1 | +8.8 |
| `f_ask_share` | −5.3 | −6.1 | −4.6 | −1.9 | +0.1 |
| `f_abs_delta` | +3.9 | +5.6 | +4.9 | +2.0 | **−3.3** |
| `f_otm` | +0.7 | +3.1 | +5.7 | +1.1 | +2.8 |
| `f_hujacobs` (F15) | +0.6 | −5.9 | −6.7 | −4.4 | −2.4 |
| `f_vrp` (F1) | −6.3 | −2.5 | −1.0 | −0.7 | −3.0 |
| `f_prior_flow_align` (F5) | −2.0 | −1.5 | −1.1 | −0.1 | +0.3 |

`f_otm` positive means **deeper OTM → higher hit rate**, the opposite of Boyer-Vorkink — and
again the volatility mechanic, not a preference.

### 5.4 Joint model, honestly refit (`out/r3_joint_honest.csv`, `out/r3b_dte_ablation.csv`)

The draft's model selected features on full-sample p-values; the review called that
contamination, not "mild optimism". Refit with selection **inside the training period only**,
plus a one-trading-day maturity embargo (which more than covers the measured ~3-hour label
window). Split 2026-05-13.

| Model | OOS AUC | 95% CI | n train / test | expectancy: all test | top-half score |
|---|---:|---|---:|---:|---:|
| train-selected 5 features (incl `f_dte`) | 0.673 | [0.559, 0.772] | 794 / 204 | −0.61% | **+0.46%** |
| same, **`f_dte` removed** | 0.654 | [0.524, 0.761] | 794 / 204 | −0.61% | **−2.50%** |
| **`f_dte` alone** | **0.679** | [0.574, 0.765] | 885 / 319 | −1.12% | +0.23% |
| `f_vol_oi` alone | 0.630 | [0.509, 0.728] | 879 / 309 | −0.76% | +1.64% |

The honest AUC is *higher* than the contaminated one (0.673 vs 0.527), because train-only
selection happened to pick high-coverage features and nearly doubled the usable training set.
**But the ablation settles what it means:** `f_dte` alone matches the full model, and dropping
`f_dte` sends the top-half expectancy to −2.50%. The model is the DTE effect, and the DTE
effect is the label's horizon and polling machinery. **AUC is not expectancy.**

### 5.5 Effect sizes in trading terms — gate simulation (`out/gate_simulation.csv`)

Learn tercile edges *and* the sign on the first 60% of dates; drop the worse tercile; evaluate
simulated net expectancy on the last 40% (n_test ≈ 319).

| Factor | Gate | OOS base | OOS gated | Δ | Kept | Gated 95% CI |
|---|---|---:|---:|---:|---:|---|
| `f_abs_delta` | drop bottom tercile | −1.12% | +2.38% | +3.50 pp | 67% | [−2.0%, +8.1%] |
| `f_mom_align_5d` | drop bottom | −1.12% | +1.94% | +3.06 pp | 62% | [−3.6%, +9.0%] |
| `f_ask_share` | drop top | −1.12% | +1.48% | +2.60 pp | 45% | [−4.5%, +10.2%] |
| `f_vol_oi` | drop bottom | −0.76% | +0.85% | +1.62 pp | 66% | [−3.1%, +5.6%] |
| **`f_dte`** | drop bottom | −1.12% | −3.32% | **−2.20 pp** | 79% | [−6.6%, −0.3%] |
| `f_prior_flow` | drop bottom | −0.95% | −3.82% | −2.87 pp | 77% | [−8.4%, +1.1%] |
| `f_osivrank_lag` | drop top | −1.83% | −5.98% | −4.15 pp | 20% | [−13.9%, −0.2%] |

**Not one gate has an OOS CI excluding zero on the improving side.** These are one split, and
the "best" was chosen post hoc from 29 candidates — so even +3.5 pp is a selection statistic,
not an estimate.

**The asymmetry that matters:** the *only* factor clearing multiple-testing correction on hit
rate destroys expectancy, with a CI excluding zero on the wrong side.

| DTE bucket | n | hit_tp | simulated net return | 95% CI | mean win | mean loss |
|---|---:|---:|---:|---|---:|---:|
| 1–3 | 255 | 27.8% | −4.00% | [−11.4%, +5.7%] | +42.3% | −28.8% |
| 4–7 | 441 | 16.1% | −4.74% | [−7.5%, −2.0%] | +25.8% | −20.3% |
| 8–14 | 509 | 13.8% | −1.91% | [−4.6%, +1.4%] | +20.9% | −14.4% |

Hit rate falls monotonically in DTE; expectancy does not. **Any gate tuned on `hit_tp_first`
is tuned on the wrong quantity.**

---

## 6. Costs — a conditional quote scenario, not realised cost

From Orion's live DB, `strategy_decisions.decision_trace_json->'entry_quote'`, n = 1,501:
mean `spread_pct` **2.85%**, median **1.94%**, p90 **6.36%**, mean mid **$8.41**. The 25%
headline cap is not binding.

Drag modelled as entry pay-up (`mid + 0.25 × half-spread`, `execution_engine.py:1129`) plus an
exit at the live bid: `(0.25 + 1.0) × spread/2` ≈ **1.21% / 1.78% / 3.98%** at
median / mean / p90.

**Review-mandated caveat, accepted:** this is a *conditional quote simulation*. Live code
rounds limits, may use fallback marks, and exits with a marketable limit; and `entry_quote`
is present on only **1,501 / 2,052** EXECUTE decisions — 27% missing, plausibly selected
rather than missing at random. Realised entry/exit fills, fill rates and cancellations are
required before any cost number is asserted.

| Cost variant | Simulated mean return | 95% CI |
|---|---:|---|
| gross | −2.18% | [−4.65%, +0.13%] |
| median spread | −3.39% | [−5.87%, −1.08%] |
| mean spread | −3.96% | [−6.44%, −1.65%] |
| p90 spread | −6.15% | [−8.63%, −3.84%] |

**This is the Heber-label simulation's expectancy, not Orion's.** Monthly: Feb −8.3% (n=239),
Mar −2.4% (n=600), Apr −5.9% (n=38), May −8.9% (n=12), Jun +5.1% (n=13), Jul +1.5% (n=72),
Aug −2.2% (n=231).

---

## 7. Stop granularity — measurement priority retained, quantified benefit withdrawn

`out/stop_granularity.csv`. Realised stop fills in the label average **−24.0%** against a
−15.0% barrier (mean overshoot 9.0 pp, median 4.0 pp, worst −83 pp).

| Stop realisation | Simulated mean return net of median cost |
|---|---:|
| as labeled | −3.39% |
| fills at −20% | −1.58% |
| fills at −18% | −0.67% |
| fills at exactly −15% | +0.70% |

**Withdrawn:** that Orion's monitor cadence recovers this. Checked at review: the monitor
default is **`check_interval_seconds: int = 60`**
(`src/orion/execution/position_monitor.py:1384`), tightened to `min(interval, 30)` **only
while a 0DTE position is open** (`:1444-1447`) — and this study has zero 0DTE rows. A monitor
tick is also not a fill: it still needs a quote, a cancel, a limit submission, and may defer.
And Orion's stops are −30/−35/−40%, not −15%, so the −15% counterfactual is not Orion's.

**Retained:** the *measurement* priority. Orion has no exit-side quote capture at all, so its
realised slippage-past-stop is currently unmeasurable. That instrumentation is a prerequisite
for any exit-parameter work and is cheaper than any of the modelling above.

---

## 8. Errors found and corrected during the run

* **F5 was never tested in the first pass.** The prior-flow factor was built from
  `meta_label_features.side` ('mid' on 99.4% of rows) and was identically zero on all 1,205
  rows. Rebuilt from the Silver ask/bid premium split (nonzero on 83.3% of panel rows) and
  re-run: still null (P1 spread −2.0 pp, p = 0.57).
* Six distinct file schemas in `meta_label_features`; a naive column-projected read silently
  dropped 27 files / 50,672 rows. Fixed by per-file schema intersection.
* Gold write-duplication up to 86x; every table deduped on its natural key.
* `add_silver_flow.py` was not idempotent (produced `_x`/`_y` columns on rerun). Fixed.

---

## 9. Conclusions that survive review

**C1 — retained, narrowed.** No study-supported gate or size tilt should be deployed. Not one
gate has an out-of-sample expectancy interval excluding zero on the improving side, and the
study cannot detect effects of the size the literature reports (MDE 11.2 pp vs 1–4 pp).

**C2 — the decision is retained; the mechanism is withdrawn.** Do not gate on DTE. But "short
contracts are more volatile in percent terms" is not established here: DTE is confounded with
the label's own horizon and polling machinery (INTRADAY 4 h / 5-min poll vs SWING ~3 h /
15-min poll), so DTE predicts different label plumbing, not demonstrated option behaviour.

**C3 — withdrawn.** `f_vol_oi` and `f_abs_delta` are **not** shadow-log candidates. Both
vanish when features are restricted to those written within 15 minutes of the alert
(+12.3 pp → −0.5 pp; +5.8 pp → −1.7 pp), `f_abs_delta` flips sign on the 67k population, and
`volume_oi_ratio` is one of the covariates on which the dead-label drop is significantly
non-random. They are exploratory observations only.

**C4 — qualified.** The literature's top three for this machine (F15 Hu-Jacobs, F1 VRP, F5
signed prior flow) show **no detectable effect of deployable size in this sample and proxy**.
That is not evidence they do not work.

**C5 — causal diagnosis withdrawn.** Replace "the base rate is the problem" with: the
Heber-label cost simulation is negative across every cost assumption, and no conditional
estimate in this study is precise enough to support deployment either way.

**C6 — measurement priority retained, quantified benefit withdrawn.** Instrument the exit
path before doing more entry modelling. The next measurement bundle should capture: decision
availability time, accepted and rejected entry orders, entry fills, exit trigger time, the
prevailing quote at trigger, order, fill, and completion latency.

**New, and independent of all the above:** report to Heber that the realised SWING label window
is ~3 trading hours and LEAP ~5, against `POLL_CONFIG`'s declared 120 h and 720 h
(`heber/watch/models.py:199-212`). Until that is resolved, every downstream consumer of
`labels_alert_barriers` — including Orion's pattern miner and ML scorer — is training on a
~3-hour holding period it probably believes is five days.

---

## 10. Adversarial review, verbatim

Reviewer: Codex `gpt-5.6-terra`, high reasoning effort, run synchronously via
`codex-companion.mjs task`. Full text also at `out/codex_review_clean.md`.

See §10 of the returned `codex_review_verbatim` field and `out/codex_review_clean.md` — the
review is reproduced there in full, unedited.

### Disposition summary

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | CRITICAL | Features available after the label's entry time | **Accepted in full.** Measured the real lag (median 18 min, p90 6.8 days); re-ran with lag caps; both positive candidates die. PIT claim withdrawn; all daily-factor results relabelled PIT-unverified. |
| 2 | CRITICAL | The label is not an Orion P&L proxy | **Accepted in full.** All expectancy numbers relabelled "Heber-label simulation". Verified the $1.00 entry floor never fires here (0/1,205). |
| 3 | HIGH | P1 is not `orion_strict` | **Accepted.** Added the three omitted filters (n 1,205 → 883); conclusions unchanged; population renamed `P1_orion_shaped`. |
| 4 | HIGH | DTE confounded with label horizon | **Accepted.** Mechanism withdrawn; and I found the confound is worse than stated — the realised SWING window is ~3 h, not 120 h. |
| 5 | HIGH | Joint model contaminated by full-sample selection | **Accepted.** Refit train-only with embargo (AUC 0.673 [0.559, 0.772]) and added the decisive ablation: `f_dte` alone matches it. |
| 6 | HIGH | `ts_available` insufficient for the daily tables | **Accepted, not fixable here.** Relabelled PIT-unverified; duplicate payload equality not proven. |
| 7 | HIGH | Survivorship material, direction unknown | **Accepted and quantified.** Retained-vs-dropped KS tests: `open_interest`, `volume_oi_ratio`, `realized_vol_20d`, `minutes_since_open`, DTE all differ at p < 0.002. Direction now known and it undercuts `f_vol_oi`. |
| 8 | HIGH | C6 overreaches on monitor cadence | **Accepted in full.** Verified the 60 s default and the 0DTE-only 30 s tightening. Quantified benefit withdrawn. |
| 9 | HIGH | Cost number is a conditional quote simulation | **Accepted.** Relabelled; 27% `entry_quote` missingness flagged as possible selection. |
| 10 | MEDIUM | Five populations are not five replications | **Accepted.** Relabelled sensitivity. |
| 11 | MEDIUM | BH family is too narrow; identical proxies double-count | **Accepted.** Duplicate removed and q recomputed (`q_value_dedup`); §5.1 relabelled exploratory screening; Romano-Wolf named as the requirement for a shipping decision. |
| 12 | MEDIUM | 67 clusters, Feb–Mar concentration, overlapping holds | **Accepted.** Called a pilot. |
| 13 | MEDIUM | "Null everywhere" overclaims | **Accepted.** C4 reworded to "no detectable effect of deployable size". |

Nothing was overridden; no finding was classified as immaterial.

---

## 11. What this study cannot tell you

* Anything about **0DTE** (`rule_0dte_sweep_v2`) — zero rows survive, and all 17 dropped.
* Anything about **Orion's actual exit barriers** — the label path truncates at ±29%/−15%
  over ~3 trading hours.
* Anything about **contract selection** — `labels_alert_barriers` prices only the swept OCC
  contract.
* Anything about **skew (F3), volatility spread (F4), or IV-based sizing** — these need a
  timestamped pre-decision option-chain snapshot that does not exist.
* Whether **Orion's expectancy is positive or negative**. This study measures a Heber label,
  not Orion's P&L.
