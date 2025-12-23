````markdown
# promotion_gates.md

This document defines **promotion gates** for moving a strategy from **research → shadow → paper → limited live → scaled live**.  
These gates are designed to prevent “backtest hallucinations” (overfitting/leakage) and to ensure the pipeline is operationally safe.

> Not financial advice. This is an engineering + statistical safety checklist for deployment.

---

## 1) Definitions

- **Strategy Version**: a versioned bundle of:
  - rule set (`ruleset_id`)
  - feature definitions (`feature_set_id`)
  - model artifact (optional; `model_version`)
  - execution + risk config (`risk_profile_id`)
- **Candidate**: a rule-fired trade opportunity (pre-ML).
- **Meta-model**: ML classifier/regressor that decides whether to take a candidate and/or how to size it.
- **OOS**: out-of-sample evaluation period that is never used to fit parameters.
- **Walk-forward**: rolling time split: train on past, test on the next segment.
- **Purged / embargoed CV**: time-series CV that removes training samples overlapping label windows and blocks “near-future leakage”.

---

## 2) Always-On Safety Gates (Hard Blocks)

Trading MUST be disabled (or auto-kill) when any of these are true:

### 2.1 Data Health (Block)
- UW ingestion heartbeat missing for **> 60s** during PRE/REG/POST (polling mode)
- Alpaca 1m bars missing for **> 2 consecutive minutes** for ≥ **10%** of tickers in universe
- End-to-end lag exceeds:
  - UW events: **> 60s** behind real time (polling)  
  - Alpaca bars: **> 10s** behind real time
- Hot store writes failing for **> 30s** or lake writes failing for **> 5 min**
- DLQ growth rate exceeds **N=100 events/min** for **> 5 min**
- Schema drift detected where required fields become NULL for **> 1%** of incoming events

### 2.2 Execution Health (Block)
- Alpaca trading API auth failures
- Order submit error rate > **3%** over last **20** orders
- Fill reconciliation failing (orders not matching fills within **5 min**)

### 2.3 Risk Kill Switch (Auto-Pause Trading)
These limits apply once PAPER/LIVE is enabled:
- Daily realized PnL ≤ **-1.0%** of equity → pause new entries for rest of day
- Rolling 5 trading-day drawdown ≤ **-3.0%** of equity → pause + require human review
- Any single position loss > **-1.25%** of equity (should be impossible under sizing; indicates bug or gap) → pause + incident

---

## 3) Stage Model

| Stage | Trades? | Orders go to Alpaca? | Goal |
|------:|:-------:|:---------------------:|------|
| 0. Research Dataset | No | No | prove pipeline + basic edge |
| 1. Shadow | No | No | prove live signals match offline logic + timing |
| 2. Paper | Yes | Paper | prove fills/slippage + live robustness |
| 3. Limited Live | Yes | Live (small) | prove real-money robustness |
| 4. Scaled Live | Yes | Live | controlled growth |

Promotion is **per Strategy Version**.

---

## 4) Stage 0 → Stage 1 (Research → Shadow) Gates

### 4.1 Data Sufficiency (Minimum)
- At least **60 trading days** of stored events + Alpaca prices usable for labels  
  (If only intraday signals: at least **120** full sessions recommended.)
- At least:
  - **≥ 300 candidates** (rule fired) total, and
  - **≥ 100 candidates OOS** (in a held-out test segment)

If your rules are very selective, reduce counts only with explicit justification and add longer time.

### 4.2 Backtest Integrity (Must Pass)
- Time-based split only (no random shuffles)
- **Leakage checks** pass:
  - feature timestamps ≤ decision timestamp
  - label windows do not “peek” into future data
- Costs/slippage included in backtest:
  - default equity slippage: **10–25 bps** per side (configurable)
  - plus commissions/fees where applicable
- Backtest reproducible:
  - pinned config + dataset snapshot + code commit hash

### 4.3 Statistical Validation (Baseline “Reasonable” Thresholds)
Run **walk-forward** with ≥ **5 folds** (each test fold ≥ 10 trading days *or* ≥ 30 candidates).

Require ALL:
- **OOS mean trade return (net)** > 0  
  (Net = after slippage/fees; for candidates that become trades after meta-model, evaluate trades.)
- **OOS profit factor** ≥ **1.10**  
  (gross wins / gross losses; net outcomes)
- **Fold consistency**:
  - ≥ **3/5** folds have positive net expectancy
  - worst fold profit factor not below **0.90**
- **Bootstrap confidence** (recommended):
  - bootstrap 95% CI lower bound of mean net return ≥ **0**  
  (If sample is small, allow a tiny negative like -1 bp, but document it.)

### 4.4 Model-Specific (If Meta-Model Enabled)
- Evaluate rule-only baseline vs meta-model filtered:
  - meta-model must improve at least one of:
    - profit factor +0.05
    - drawdown reduction ≥ 10%
    - Sharpe-like metric improvement (optional)
- Calibration sanity:
  - predicted probability monotonic vs realized win rate (binned)

If meta-model doesn’t help, keep it off.

✅ If all pass → enable **Shadow** for this strategy version.

---

## 5) Stage 1 → Stage 2 (Shadow → Paper) Gates

Shadow runs live ingestion + live signal generation, but no orders.  
Goal: verify operational correctness.

Minimum shadow duration:
- **10 trading days** AND at least **50 signals** generated (or 50 candidates if candidates are the unit)

Shadow gates:
- Signal parity:
  - live-produced signals must match offline replay for same inputs with **≥ 99%** agreement  
    (Allow small differences only from polling timing; must be explainable and logged.)
- Latency:
  - signal generation lag from event arrival ≤ **60s** (polling mode)
- Data gaps:
  - no more than **1** incident of UW gap > 2 minutes per week (otherwise fix ingestion)

✅ If all pass → enable **Paper**.

---

## 6) Stage 2 → Stage 3 (Paper → Limited Live) Gates

Paper trading proves that “the backtest wasn’t lying about fills.”

Minimum paper duration:
- **20 trading days** OR **100 fills**, whichever is larger

Paper performance gates (net of fees):
- Net PnL ≥ **0** over the paper window  
  (If market regime is unusual, allow slight negative but require improvements documented.)
- Max drawdown ≤ **2.5%** of equity (paper sizing should be conservative)
- Profit factor ≥ **1.05**
- Slippage control:
  - median slippage ≤ **1.5×** backtest slippage assumption
  - 95th percentile slippage ≤ **3×** assumption
- Operational:
  - order error rate < **1%**
  - fills reconcile within **5 minutes** for ≥ **99%** of fills

✅ If all pass → enable **Limited Live** with small risk.

---

## 7) Stage 3 → Stage 4 (Limited Live → Scaled Live) Gates

Limited Live must start small.

### 7.1 Limited Live Defaults (Starting Point)
- risk per trade: **0.10%–0.25%** of equity
- max concurrent positions: **3–5**
- max position per ticker: **5%** of equity
- trade only tickers with:
  - price ≥ **$5**
  - 20D ADV ≥ **1,000,000** shares (or equivalent liquidity rule)

### 7.2 Promotion to Scaled Live Requirements
Minimum:
- **30 trading days** AND **100 fills**

Performance gates:
- Net PnL > **0**
- Profit factor ≥ **1.08**
- Max drawdown ≤ **2.0%** of equity (given small sizing)
- No single-day loss-trigger event more than **once** (daily pause threshold)

Stability gates:
- Performance by rule_id: no rule contributes > **80%** of profits  
  (avoid a single fragile “hero rule”)
- Concentration: top ticker contributes < **50%** of profits over the window  
  (avoid one-name luck)

✅ If all pass → gradually increase risk caps (next section).

---

## 8) Scaling Policy (Controlled Growth)

Scale in steps no more frequent than **weekly**:
- Step up risk per trade by **+0.10%** equity increments
- Increase max positions by **+2** increments

At each step, require the prior 2-week window to meet:
- profit factor ≥ **1.05**
- drawdown ≤ **2.5%**
- no major incidents

Never exceed (starting conservative defaults):
- risk per trade **1.0%** equity
- max positions **15**
- max ticker exposure **10%**

---

## 9) Demotion Rules (When to Step Back)

A strategy version is automatically demoted one level (or paused) if:

### 9.1 Performance Regression
- Rolling 20-trade net expectancy < **0** (after costs), OR
- Rolling 10-trading-day profit factor < **0.95**, OR
- Rolling drawdown exceeds:
  - Paper: **> 3.5%**
  - Live: **> 3.0%**

### 9.2 Drift / Non-Stationarity
If feature drift triggers:
- PSI (Population Stability Index) > **0.25** on core features over a week, OR
- model calibration degrades (predicted bins no longer align)

Then:
- pause new entries for affected strategy
- require EOD agent report + human review
- consider retrain, rule tweaks, or new regime filters

### 9.3 Data/Execution Incidents
Any of:
- repeated ingestion gaps
- repeated order failures
- reconciliation mismatches

→ pause trading and open incident ticket.

---

## 10) Change Management Gates (LLM Agent Suggestions)

The EOD LLM agent may propose:
- config tweaks (thresholds, filters, risk limits)
- new rules
- feature changes
- model retraining
- code changes

**No proposal goes straight to Live.** The workflow is:

1) Proposal → PR/config patch + test plan + expected impact  
2) Automated checks:
   - unit tests
   - pipeline integration test (ingest → features → signal)
   - backtest regression suite
3) Re-run walk-forward backtest with the proposal
4) If it passes, it is promoted to **Shadow** for ≥ 5 days
5) Then Paper (if behavior changes meaningfully)
6) Then Limited Live again if changes are major

### 10.1 “Config-only fast path” (optional)
Allowed ONLY if:
- change is within pre-approved bounds (e.g., threshold ±10%)
- it does not alter label definitions or feature computations
- shadow parity remains ≥ 99%
Otherwise, treat as major change.

---

## 11) Required Artifacts Per Promotion

Every promotion decision must write a record containing:
- `strategy_version_id` (ruleset + model + config hash)
- dataset coverage stats
- evaluation windows used
- key metrics (PF, expectancy, drawdown, slippage)
- links/pointers to:
  - backtest report
  - paper trade report (if applicable)
  - live report (if applicable)
- approval record (human or policy)

---

## 12) Recommended Starting Thresholds (Config Defaults)

These are good “first run” defaults that err on safety:

```yaml
promotion_gates:
  research:
    min_trading_days: 60
    min_candidates_total: 300
    min_candidates_oos: 100
    walk_forward_folds: 5
    min_positive_folds: 3
    oos_profit_factor_min: 1.10
    worst_fold_pf_min: 0.90
    bootstrap_ci_lower_min: 0.0

  shadow:
    min_days: 10
    min_signals: 50
    parity_min: 0.99
    max_signal_lag_seconds: 60

  paper:
    min_days: 20
    min_fills: 100
    profit_factor_min: 1.05
    max_drawdown_pct: 2.5
    median_slippage_mult_max: 1.5
    p95_slippage_mult_max: 3.0

  limited_live:
    min_days: 30
    min_fills: 100
    profit_factor_min: 1.08
    max_drawdown_pct: 2.0

risk_defaults:
  limited_live:
    risk_per_trade_pct: 0.25
    max_positions: 5
    max_ticker_exposure_pct: 5.0
  kill_switch:
    daily_loss_pause_pct: 1.0
    rolling_5d_drawdown_pause_pct: 3.0
````

---

## 13) Notes

* Early on, assume regimes will change and your edge will be fragile. Promotions should be conservative.
* Prefer **fewer, well-tested rules** over a large mined zoo.
* Meta-models should be treated as *filters*, not as magic alpha generators, until proven otherwise.