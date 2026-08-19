"""Robustness re-runs demanded by the adversarial review (2026-08-16).

Each block answers one review finding with a measurement rather than an argument.

R1  CRITICAL "features are stamped after the entry price".  Measure the real lag
    distribution and re-run the surviving factors on the subset where the feature record
    was written close to the alert.
R2  HIGH "P1 is not orion_strict".  Add the entry filters the funnel omitted -- ET entry
    window, contract-volume floor, delta band -- and report how the population moves.
R3  HIGH "the joint model's features were chosen on the full sample".  Refit with feature
    selection done inside the training period only, plus a maturity embargo.
R4  HIGH "survivorship".  Compare rows retained vs rows dropped by the dead-label filter
    on every observable covariate.
R5  MEDIUM "identical proxy outcomes double-count the BH family".  Recompute q-values
    after removing the duplicate.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
from scipy import stats

from evaluate_factors import (
    LIQUID,
    ZERO_DTE_UNDERLYINGS,
    add_factors,
    add_outcomes,
    bh_qvalues,
    spread_test,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
ET_WINDOWS = {"0DTE": ((9, 35), (15, 0)), "SHORT_SWING": ((9, 35), (15, 30)), "SWING": ((9, 30), (16, 0))}
MIN_CONTRACT_VOLUME = {"0DTE": 500, "SHORT_SWING": 200, "SWING": 200}
DELTA_BAND = (0.25, 0.60)
SPREAD_MED = 0.0194
PHI = 0.25


def base_pop(panel: pd.DataFrame) -> pd.DataFrame:
    dead = (panel["bars_to_hit"].fillna(0) == 0) & (panel["mfe"].fillna(0) == 0) & (panel["mae"].fillna(0) == 0)
    b = panel[~dead]
    b = b[b["sv_aggressor"].notna()]
    b = b[(b["sv_is_sweep"].fillna(0) == 1) & b["sv_aggressor"].eq("ASK")]
    b = b[(b["days_to_expiry"] >= 0) & (b["days_to_expiry"] <= 14)]
    pf = np.where(b["days_to_expiry"] >= 4, 100_000.0, 50_000.0)
    b = b[b["premium"].fillna(0) >= pf]
    b = b[b["ticker"].isin(LIQUID)]
    b = b[~((b["days_to_expiry"] == 0) & (~b["ticker"].isin(ZERO_DTE_UNDERLYINGS)))]
    return b.copy()


def net_return(d: pd.DataFrame, spread: float = SPREAD_MED) -> np.ndarray:
    r = d["outcome_return"].to_numpy(float).copy()
    r = np.where(d["outcome"].astype(str).eq("hit_tp"), r + 0.04, r)
    return r - (PHI + 1.0) * spread / 2.0


def main() -> None:
    panel = pd.read_parquet(f"{OUT}/panel.parquet")
    lines: list[str] = []

    def say(s=""):
        print(s)
        lines.append(str(s))

    # ---------------- R1 : feature availability lag ----------------
    lag = panel["mlf_lag_s"].astype(float)
    say("R1  meta_label_features ts_available - alert ts_event, seconds")
    q = np.nanpercentile(lag, [0, 1, 5, 25, 50, 75, 90, 95, 99, 100])
    say(pd.Series(q, index=["min", "p1", "p5", "p25", "p50", "p75", "p90", "p95", "p99", "max"]).round(1).to_string())
    for t in [60, 300, 900, 3600, 86400]:
        say(f"    <= {t:>6}s : {(lag <= t).mean():.1%} of rows")
    say("  -> the earlier claim 'PIT-safe, 1st percentile 12.7s' quoted the 1st percentile of a")
    say("     distribution whose MEDIAN is 18 minutes and whose 90th percentile is 6.8 DAYS.")
    say("     56% of feature rows were written more than a day after the alert (backfill), so a")
    say("     backfilled field computed from post-alert data would leak. WITHDRAWN as stated.")
    say()

    p1 = add_factors(add_outcomes(base_pop(panel)))[0]
    factors = [c for c in p1.columns if c.startswith("f_")]
    watch = ["f_dte", "f_vol_oi", "f_abs_delta", "f_ask_share", "f_otm", "f_hujacobs", "f_vrp",
             "f_prior_flow_align", "f_log_prem", "f_iv_rank"]

    rows = []
    for cap, name in [(np.inf, "all"), (3600.0, "lag<=1h"), (900.0, "lag<=15m")]:
        sub = p1[p1["mlf_lag_s"].astype(float) <= cap]
        say(f"R1  P1 restricted to {name}: n={len(sub)} days={sub['date'].nunique()}")
        for f in watch:
            for oc in ["y_tp", "y_ret"]:
                r = spread_test(sub, f, oc, 3, n_boot=1000)
                if r:
                    r.update({"subset": name, "factor": f, "outcome": oc})
                    rows.append(r)
    lag_tab = pd.DataFrame(rows)
    if len(lag_tab):
        piv = lag_tab.pivot_table(index=["factor", "outcome"], columns="subset",
                                  values=["spread", "p_boot", "n"])
        say(piv.round(3).to_string())
        lag_tab.to_csv(f"{OUT}/r1_lag_sensitivity.csv", index=False)
    say()

    # ---------------- R2 : the entry filters the funnel omitted ----------------
    d = p1.copy()
    et = pd.to_datetime(d["ts_event"], utc=True).dt.tz_convert("America/New_York")
    hm = list(zip(et.dt.hour, et.dt.minute))
    win_ok, vol_ok = [], []
    for (h, m), bkt, vol in zip(hm, d["bucket"], d["volume"]):
        s, e = ET_WINDOWS[bkt]
        win_ok.append(s <= (h, m) < e)
        v = MIN_CONTRACT_VOLUME[bkt]
        vol_ok.append(True if pd.isna(vol) else float(vol) >= v)
    d["_win"] = win_ok
    d["_vol"] = vol_ok
    dl = pd.to_numeric(d["delta"], errors="coerce").abs()
    d["_delta_ok"] = dl.isna() | ((dl >= DELTA_BAND[0]) & (dl <= DELTA_BAND[1]))
    say("R2  entry filters the original funnel omitted (all are in flow_rules.py:194-224)")
    say(f"    ET entry window        : keeps {d['_win'].mean():.1%}  -> n={int(d['_win'].sum())}")
    say(f"    contract-volume floor  : keeps {d['_vol'].mean():.1%}  -> n={int(d['_vol'].sum())}")
    say(f"    delta band 0.25-0.60   : keeps {d['_delta_ok'].mean():.1%} -> n={int(d['_delta_ok'].sum())}")
    strict = d[d["_win"] & d["_vol"] & d["_delta_ok"]]
    say(f"    ALL THREE together     : n={len(strict)} (was {len(d)}), days={strict['date'].nunique()}, "
        f"tickers={strict['ticker'].nunique()}, hit_tp={strict['y_tp'].mean():.3f}")
    say(f"    net expectancy at median spread: {net_return(strict).mean():+.4f} "
        f"(unrestricted {net_return(d).mean():+.4f})")
    say("    NOTE: delta is NULL on 100% of Orion's LIVE candidates, so the band is inert live but")
    say("          active here (64% coverage) -- the two populations are not the same object.")
    r2 = []
    for f in watch:
        for oc in ["y_tp", "y_ret"]:
            r = spread_test(strict, f, oc, 3, n_boot=1000)
            if r:
                r.update({"factor": f, "outcome": oc})
                r2.append(r)
    if r2:
        t2 = pd.DataFrame(r2)
        t2.to_csv(f"{OUT}/r2_full_entry_filters.csv", index=False)
        say(t2[["factor", "outcome", "n", "spread", "spread_lo95", "spread_hi95", "p_boot"]]
            .round(3).to_string(index=False))
    say()

    # ---------------- R3 : honest joint model ----------------
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    say("R3  joint model with TRAIN-ONLY feature selection + maturity embargo")
    dts = np.sort(p1["date"].unique())
    cut = dts[int(0.6 * len(dts))]
    # embargo: the label window is at most ~5 trading hours (measured), so a 1-trading-day
    # embargo more than covers outcome maturity for train rows near the split.
    emb = dts[max(int(0.6 * len(dts)) - 1, 0)]
    tr_all = p1[p1["date"] < emb]
    te_all = p1[p1["date"] >= cut]
    say(f"    split {cut} (embargo drops the last train date), train={len(tr_all)} test={len(te_all)}")
    for oc in ["y_tp"]:
        scored = []
        for f in factors:
            r = spread_test(tr_all, f, oc, 3, n_boot=400)
            if r:
                scored.append((f, r["p_boot"], r["n"]))
        scored.sort(key=lambda z: z[1])
        # require decent coverage so the NaN drop does not gut the training set
        picked = [f for f, _, n in scored if n >= 0.8 * len(tr_all)][:5]
        say(f"    features selected on TRAIN only: {picked}")
        sub = p1.dropna(subset=picked + [oc])
        tr, te = sub[sub["date"] < emb], sub[sub["date"] >= cut]
        if len(tr) < 200 or len(te) < 100 or te[oc].nunique() < 2:
            say("    insufficient rows after NaN drop")
            continue
        X = tr[picked].to_numpy(float)
        mu, sd = X.mean(0), X.std(0) + 1e-9
        D = pd.get_dummies(tr["bucket"]).reindex(columns=["0DTE", "SHORT_SWING", "SWING"], fill_value=0).to_numpy(float)
        Dt = pd.get_dummies(te["bucket"]).reindex(columns=["0DTE", "SHORT_SWING", "SWING"], fill_value=0).to_numpy(float)
        m = LogisticRegression(max_iter=2000, C=0.5).fit(np.hstack([(X - mu) / sd, D]), tr[oc].to_numpy(float))
        pr = m.predict_proba(np.hstack([(te[picked].to_numpy(float) - mu) / sd, Dt]))[:, 1]
        yv = te[oc].to_numpy(float)
        auc = roc_auc_score(yv, pr)
        dd = te["date"].to_numpy()
        uniq, codes = np.unique(dd, return_inverse=True)
        idx_by_day = [np.flatnonzero(codes == i) for i in range(len(uniq))]
        rng = np.random.default_rng(20260816)
        vals = []
        for _ in range(1000):
            pick = rng.integers(0, len(uniq), size=len(uniq))
            idx = np.concatenate([idx_by_day[p] for p in pick])
            if len(np.unique(yv[idx])) > 1:
                vals.append(roc_auc_score(yv[idx], pr[idx]))
        lo, hi = np.percentile(vals, [2.5, 97.5])
        say(f"    OOS AUC = {auc:.3f}  95% CI [{lo:.3f}, {hi:.3f}]  (n_train={len(tr)}, n_test={len(te)})")
        # expectancy of trading only the top half of the score
        r_te = net_return(te)
        top = pr >= np.median(pr)
        say(f"    net expectancy: all test {r_te.mean():+.4f} | top-half score {r_te[top].mean():+.4f}")
        pd.DataFrame([{"outcome": oc, "features": ",".join(picked), "oos_auc": auc,
                       "lo95": lo, "hi95": hi, "n_train": len(tr), "n_test": len(te),
                       "exp_all": float(r_te.mean()), "exp_top_half": float(r_te[top].mean())}]
                     ).to_csv(f"{OUT}/r3_joint_honest.csv", index=False)
    say()

    # ---------------- R4 : survivorship ----------------
    say("R4  retained vs dropped by the dead-label filter, Orion-shaped rows only")
    dead = (panel["bars_to_hit"].fillna(0) == 0) & (panel["mfe"].fillna(0) == 0) & (panel["mae"].fillna(0) == 0)
    shaped = panel[panel["sv_aggressor"].eq("ASK") & (panel["sv_is_sweep"].fillna(0) == 1)]
    shaped = shaped[(shaped["days_to_expiry"] >= 0) & (shaped["days_to_expiry"] <= 14)]
    pf = np.where(shaped["days_to_expiry"] >= 4, 100_000.0, 50_000.0)
    shaped = shaped[shaped["premium"].fillna(0) >= pf]
    shaped = shaped[shaped["ticker"].isin(LIQUID)]
    sd_ = dead.reindex(shaped.index)
    cmp_cols = ["days_to_expiry", "premium", "volume", "open_interest", "volume_oi_ratio",
                "spot_price", "contract_price", "log_moneyness", "delta", "iv", "iv_rank",
                "realized_vol_20d", "entry_price", "minutes_since_open"]
    comp = pd.DataFrame({
        "retained_median": shaped[~sd_][cmp_cols].median(),
        "dropped_median": shaped[sd_][cmp_cols].median(),
        "retained_n": shaped[~sd_][cmp_cols].notna().sum(),
        "dropped_n": shaped[sd_][cmp_cols].notna().sum(),
    })
    ks = []
    for c in cmp_cols:
        a = shaped[~sd_][c].dropna().astype(float)
        b = shaped[sd_][c].dropna().astype(float)
        ks.append(stats.ks_2samp(a, b).pvalue if len(a) > 20 and len(b) > 20 else np.nan)
    comp["ks_p"] = ks
    comp.to_csv(f"{OUT}/r4_survivorship.csv")
    say(comp.round(4).to_string())
    say(f"    retained n={int((~sd_).sum())}  dropped n={int(sd_.sum())}")
    say(f"    dropped rows by DTE: {shaped[sd_]['days_to_expiry'].value_counts().head(8).to_dict()}")
    say(f"    retained rows by DTE: {shaped[~sd_]['days_to_expiry'].value_counts().head(8).to_dict()}")
    say()

    # ---------------- R5 : BH family without the duplicate proxy ----------------
    res = pd.read_csv(f"{OUT}/results.csv")
    prim = res["population"].eq("P1_orion_strict") & ~res["outcome"].eq("y_orion_opt")
    q = bh_qvalues(res.loc[prim, "p_boot"].to_numpy())
    res.loc[prim, "q_value_dedup"] = q
    res.to_csv(f"{OUT}/results.csv", index=False)
    say(f"R5  BH recomputed over {int(prim.sum())} tests after removing the identical y_orion_opt proxy")
    say(res.loc[prim].nsmallest(8, "q_value_dedup")[
        ["factor", "outcome", "n", "spread", "p_boot", "q_value", "q_value_dedup"]].round(4).to_string(index=False))

    # ---------------- measured label window (found while checking the review) ------
    say()
    say("EXTRA  measured label window, window_duration_hours = window_end - alert_time (wall clock)")
    say(panel.groupby("horizon")["window_duration_hours"].describe()[["count", "25%", "50%", "75%"]].round(3).to_string())
    say("    Heber POLL_CONFIG (models.py:199-212) declares INTRADAY 4h / SWING 120h / LEAP 720h.")
    say("    The DATA says INTRADAY ~4 trading hours (21.4h wall clock across an overnight),")
    say("    SWING ~3 trading hours, LEAP ~5 trading hours -- stable in every month Feb..Aug.")
    say("    So the label resolves over ~3 trading hours for 3-21 DTE contracts, not 5 days.")
    say("    Orion's SHORT_SWING max-hold is 54h and SWING is 168h. Different holding period.")

    with open(f"{OUT}/review_fixes.txt", "w") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
