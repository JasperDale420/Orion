"""Attach the full run config + output artifacts to the pre-registered ClearML task."""

from __future__ import annotations

import json
import os

from clearml import Task

TASK_ID = "ce5e3666053e45aa88136b23df102932"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

CONFIG = {
    "git_commit": "3a856e94bb7e56c77b8b368725e46b724e1a9664",  # pragma: allowlist secret
    "git_branch": "claude/orion-debug-2ab92b",
    "brief_quoted_commit": "4d729571 (not this worktree's HEAD; run stamped with actual HEAD)",
    "seed": 20260816,
    "n_bootstrap": 2000,
    "n_bootstrap_large_population": 500,
    "bootstrap_clustering": "whole trading days resampled with replacement",
    "multiple_testing": (
        "Benjamini-Hochberg over the 161 P1 factor x outcome tests (q_value); recomputed over 127 "
        "after removing the identical y_orion_opt proxy (q_value_dedup). The review notes this "
        "family is too narrow for a deployment decision -- Romano-Wolf over the whole gate-selection "
        "workflow with a locked holdout is what shipping would require."
    ),
    "oos_split": "time-blocked, first 60% of distinct dates = train (bin edges + sign), last 40% = test",
    "inputs": {
        "gold_root": "~/.heber-cache/data/gold",
        "silver_root": "~/.heber-cache/data/silver",
        "orion_db": "postgresql://orion@localhost:5440/orion_db (READ ONLY, entry_quote only)",
        "labels_alert_barriers": {"raw_rows": 194575, "dedup_rows": 149234,
                                  "dt_range": "2026-01-28..2026-08-14", "key": "alert_id keep-first"},
        "meta_label_features": {"raw_rows": 116243, "dedup_rows": 116243,
                                "dt_range": "2026-02-05..2026-08-14", "key": "alert_id keep-last"},
        "silver_flow_alerts": {"raw_rows": 3096930, "unique_event_id": 910026,
                               "dt_range": "2026-01-28..2026-08-14"},
        "lagged_gold_dedup_rows": {
            "momentum_features": 235374, "flow_toxicity_features": 84198,
            "options_sentiment_features": 13953, "oi_momentum_features": 12495,
            "iv_surface_features": 12776, "greek_exposure_features": 45191,
            "flow_features": 67669, "volatility_features": 235308,
            "gex_regime_features": 69, "market_tide_context_features": 106,
        },
        "orion_entry_quote_records": 1501,
    },
    "joined_panel_rows": 96072,
    "panel_date_range": "2026-02-05T19:29:03Z .. 2026-08-14T16:59:54Z",
    "point_in_time_rule": (
        "meta_label_features joined on alert_id. MEASURED lag ts_available - ts_event: p1 12.7s, "
        "MEDIAN 1086s (18 min), p90 587008s (6.8 days); only 24.2% within 15 min. 56% are backfills. "
        "NOT PIT-safe as originally claimed. Daily EOD gold tables joined with "
        "merge_asof(backward, allow_exact_matches=False) on feature.ts_available <= alert.ts_event "
        "per ticker -- necessary but NOT sufficient, since ts_available is a batch-WRITE clock "
        "(pipelines/base.py:22-30 assigns datetime.now(UTC)), so those results are PIT-UNVERIFIED. "
        "The prior-flow aggregate uses rolling('24h', closed='left') and is clean."
    ),
    "populations": {
        "P1_orion_shaped": {"n": 1205, "days": 67, "tickers": 58, "base_hit_tp": 0.1759,
                            "note": "labelled P1_orion_strict in results.csv; renamed on review -- "
                                    "it omits the ET window / contract-volume floor / delta band. "
                                    "With all three added: n=883, 53 days, hit_tp 0.171"},
        "P2_no_universe_allowlist": {"n": 1643, "days": 71, "tickers": 184},
        "P3_no_premium_floor": {"n": 4348, "days": 78, "tickers": 511},
        "P4_sweep_ask_any_dte": {"n": 7405, "days": 86, "tickers": 759},
        "P5_unfiltered_labeled": {"n": 67179, "days": 94, "tickers": 1923},
    },
    "label_definition": {
        "basis": "OPTION CONTRACT MID, ret = (mid - entry)/entry (Heber checker.py:156)",
        "effective_tp": 0.29, "sl": 0.15, "ties": "to TP",
        "polling_declared": {"INTRADAY": "5min/4h", "SWING": "15min/120h", "LEAP": "1h/720h"},
        "polling_MEASURED_window": {"INTRADAY": "~4 trading h", "SWING": "~3 trading h",
                                    "LEAP": "~5 trading h",
                                    "note": "window_duration_hours contradicts POLL_CONFIG for "
                                            "SWING and LEAP in every month Feb..Aug -- report to Heber"},
    },
    "cost_model": {
        "source": "orion strategy_decisions entry_quote n=1501 of 2052 EXECUTE (27% missing, "
                  "possible selection); this is a CONDITIONAL QUOTE SIMULATION, not realized cost",
        "spread_pct_median": 0.0194, "spread_pct_mean": 0.0285, "spread_pct_p90": 0.0636,
        "entry_payup_phi": 0.25,
        "roundtrip_drag": "(phi + 1) * spread_pct / 2",
    },
    "power": {"mde_pp_quintile_hit_rate_spread": 11.24, "alpha": 0.05, "power": 0.80},
    "outputs": {"dir": OUT, "results": "results.csv", "writeup": "results.md"},
    "known_limitations": [
        "P1 contains zero 0DTE rows (Heber dead-label bug)",
        "MFE/MAE truncated at Heber's own barrier -> Orion's +40..75%/-30..45% barriers untestable",
        "70% of P1 rows fall in Feb-Mar 2026; only 67 day-clusters",
        "431 Orion-shaped rows dropped by the dead-label filter; retained vs dropped differ "
        "significantly on open_interest, volume_oi_ratio, realized_vol_20d, minutes_since_open, DTE",
        "meta_label_features ts_available lags the alert by a MEDIAN of 18 minutes; 56% of rows "
        "were written >1 day later (backfill). Positive candidates vanish under a 15-minute lag cap",
        "daily gold ts_available is a batch-write clock, not a data cutoff -> PIT-UNVERIFIED",
        "measured label window is ~3 trading hours for SWING and ~5 for LEAP, NOT the 120h/720h "
        "declared in Heber POLL_CONFIG (models.py:199-212)",
        "all expectancy figures are Heber-label simulations, not Orion P&L",
    ],
    "adversarial_review": {
        "reviewer": "codex gpt-5.6-terra, high effort, codex-companion.mjs task",
        "verdict": "DO NOT SHIP any factor gate, tilt, or claimed Orion expectancy improvement",
        "artifact": "codex_review_clean.md",
        "claims_withdrawn": ["C3 shadow-log candidates", "C5 causal diagnosis",
                             "C6 quantified 4.1pp monitor benefit", "C2 mechanism",
                             "meta_label_features PIT-safe-by-construction"],
    },
}


def main() -> None:
    t = Task.get_task(task_id=TASK_ID)
    # the registration process exited, which closes the task; reopen it to attach results
    t.mark_started(force=True)
    t.connect(CONFIG, name="study_config")
    for name in ["results.csv", "results_flow.csv", "universe.csv", "factor_coverage.csv",
                 "joint.csv", "monthly_stability.csv", "gate_simulation.csv", "expectancy.csv",
                 "dte_expectancy.csv", "stop_granularity.csv", "power.json",
                 "factor_definitions.json", "cost_model.json", "provenance.txt",
                 "r1_lag_sensitivity.csv", "r2_full_entry_filters.csv", "r3_joint_honest.csv",
                 "r3b_dte_ablation.csv", "r4_survivorship.csv", "review_fixes.txt",
                 "codex_review_clean.md"]:
        p = os.path.join(OUT, name)
        if os.path.exists(p):
            t.upload_artifact(name=name, artifact_object=p, wait_on_upload=True)
            print("uploaded", name)
    for name in ["results.md", "README.md"]:
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            t.upload_artifact(name=name, artifact_object=p, wait_on_upload=True)
            print("uploaded", name)
    t.mark_completed()
    print("TASK", t.id, t.get_output_log_web_page())


if __name__ == "__main__":
    main()
