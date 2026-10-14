"""Factor evaluation on the Heber labeled-alert dataset.

Runs on the panel built by build_dataset.py.  Never touches Orion's 22 executed
trades -- the evaluation population is the labeled UW alert universe.

Outputs:
  out/results.csv   one row per (factor, outcome, test)
  out/universe.csv  the filter funnel
  out/joint.csv     joint-model OOS AUC
"""

from __future__ import annotations

import json
import os
import warnings

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
SEED = 20260816
N_BOOT = 2000

# Heber barrier definition, read from Heber/heber/watch/checker.py:296 and
# heber/features/templates/alert_labels.py:115-156
LABEL_TP = 0.25 + 2 * 0.02  # effective TP on the OPTION MID = +29%
LABEL_SL = 0.15             # SL on the OPTION MID = -15%

# Orion's own per-bucket barriers (src/orion/execution/exit_fallback_rules.py:81)
ORION_BARRIERS = {"0DTE": (0.40, 0.30), "SHORT_SWING": (0.50, 0.35), "SWING": (0.60, 0.40)}

LIQUID = set(open(os.path.join(HERE, "liquid_universe.txt")).read().split())
ZERO_DTE_UNDERLYINGS = {"SPY", "QQQ", "IWM"}


# --------------------------------------------------------------------------
# universe
# --------------------------------------------------------------------------
def build_universe(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    funnel = []

    def step(name, d):
        funnel.append({"step": name, "n": len(d), "n_tickers": d["ticker"].nunique()})
        return d

    d = step("0_labeled_alerts_joined_to_alert_time_features", df)

    # drop the documented dead-label path (Heber CHANGELOG: 0DTE all-zero rows)
    dead = (df["bars_to_hit"].fillna(0) == 0) & (df["mfe"].fillna(0) == 0) & (df["mae"].fillna(0) == 0)
    d = step("1_drop_dead_zero_label_rows", d[~dead])

    # meta_label_features.side/aggressor/is_sweep are dead in this cache (aggressor NULL on
    # 96,071/96,072; side='mid' on 99.4%; is_sweep=1 on 781).  Orion never reads them either
    # -- it reads Silver flow_alerts and synthesises the aggressor (normalizer.py:99-111).
    # add_silver_flow.py reconstructs sv_is_sweep / sv_aggressor the same way.
    d = step("2a_silver_flow_recovered", d[d["sv_aggressor"].notna()])
    d = step("2b_sweep_only", d[d["sv_is_sweep"].fillna(0) == 1])
    d = step("3_buyer_initiated_ask_side", d[d["sv_aggressor"].eq("ASK")])

    d = step("4_dte_0_to_14", d[(d["days_to_expiry"] >= 0) & (d["days_to_expiry"] <= 14)])

    prem_floor = np.where(d["days_to_expiry"] >= 4, 100_000.0, 50_000.0)
    d = step("5_bucket_premium_floor", d[d["premium"].fillna(0) >= prem_floor])

    d = step("6_liquid_universe_allowlist", d[d["ticker"].isin(LIQUID)])

    ok0 = ~((d["days_to_expiry"] == 0) & (~d["ticker"].isin(ZERO_DTE_UNDERLYINGS)))
    d = step("7_0dte_index_etf_only", d[ok0])

    # how much of the Orion-shaped universe the Heber dead-label bug destroys
    same_but_dead = df[dead]
    sb = same_but_dead
    sb = sb[sb["sv_is_sweep"].fillna(0) == 1]
    sb = sb[sb["sv_aggressor"].eq("ASK")]
    sb = sb[(sb["days_to_expiry"] >= 0) & (sb["days_to_expiry"] <= 14)]
    pf = np.where(sb["days_to_expiry"] >= 4, 100_000.0, 50_000.0)
    sb = sb[sb["premium"].fillna(0) >= pf]
    sb = sb[sb["ticker"].isin(LIQUID)]
    funnel.append({"step": "X_orion_shaped_rows_LOST_to_dead_label_bug", "n": len(sb),
                   "n_tickers": sb["ticker"].nunique()})

    return d.copy(), pd.DataFrame(funnel)


def nested_populations(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Nested relaxations of the Orion filter, so power can be traded against fidelity."""
    dead = (df["bars_to_hit"].fillna(0) == 0) & (df["mfe"].fillna(0) == 0) & (df["mae"].fillna(0) == 0)
    base = df[~dead]
    base = base[base["sv_aggressor"].notna()]
    sweep_ask = base[(base["sv_is_sweep"].fillna(0) == 1) & base["sv_aggressor"].eq("ASK")]
    dte = sweep_ask[(sweep_ask["days_to_expiry"] >= 0) & (sweep_ask["days_to_expiry"] <= 14)]
    pf = np.where(dte["days_to_expiry"] >= 4, 100_000.0, 50_000.0)
    prem = dte[dte["premium"].fillna(0) >= pf]
    strict = prem[prem["ticker"].isin(LIQUID)]
    strict = strict[~((strict["days_to_expiry"] == 0) & (~strict["ticker"].isin(ZERO_DTE_UNDERLYINGS)))]
    return {
        "P1_orion_strict": strict.copy(),
        "P2_no_universe_allowlist": prem.copy(),
        "P3_no_premium_floor": dte.copy(),
        "P4_sweep_ask_any_dte": sweep_ask.copy(),
        "P5_unfiltered_labeled": base.copy(),
    }


def add_outcomes(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    dte = d["days_to_expiry"]
    d["bucket"] = np.where(dte < 1, "0DTE", np.where(dte <= 3, "SHORT_SWING", "SWING"))

    # Primary label: option-contract mid hit +29% before -15% (Heber definition)
    d["y_tp"] = d["hit_tp_first"].astype(float)
    # Continuous option return at barrier resolution
    d["y_ret"] = d["outcome_return"].astype(float)

    # Orion-barrier proxy from the observed contract MFE/MAE path.  The label only
    # records the ORDER of the +29/-15 pair, so at Orion's wider barriers the order
    # is unknown when both are touched -> report an optimistic/pessimistic bracket.
    tp = d["bucket"].map(lambda b: ORION_BARRIERS[b][0]).astype(float)
    sl = d["bucket"].map(lambda b: ORION_BARRIERS[b][1]).astype(float)
    hit_tp = d["mfe"] >= tp
    hit_sl = d["mae"] <= -sl
    d["y_orion_opt"] = hit_tp.astype(float)                       # optimistic: TP if ever touched
    d["y_orion_pess"] = (hit_tp & ~hit_sl).astype(float)          # pessimistic: only clean wins
    d["orion_ambiguous"] = (hit_tp & hit_sl).astype(float)
    return d


def add_underlying_returns(d: pd.DataFrame) -> pd.DataFrame:
    """Attach the UNDERLYING forward 1d/5d return, signed by the option's direction.

    This is a stock-return outcome, not an option P&L; a long option buyer's payoff is
    convex in it and net of theta.  Reported as a secondary outcome only.
    """
    import glob

    import pyarrow.parquet as pq

    d = d.copy()
    is_call = d["put_call"].astype(str).str.upper().str.startswith("C")
    for ds, col in [("labels_returns_1d", "return_1d"), ("labels_returns_5d", "return_5d")]:
        fs = sorted(glob.glob(os.path.expanduser(f"~/.heber-cache/data/gold/dataset={ds}/**/*.parquet"), recursive=True))
        frames = []
        for f in fs:
            try:
                frames.append(pq.read_table(f, columns=["instrument_key", "ts_event", col, "ts_available"]).to_pandas())
            except Exception:
                pass
        if not frames:
            continue
        r = pd.concat(frames, ignore_index=True)
        r["ts_available"] = pd.to_datetime(r["ts_available"], utc=True).astype("datetime64[ns, UTC]")
        r = r.sort_values("ts_available").drop_duplicates(["instrument_key", "ts_event"], keep="last")
        r["ticker"] = r["instrument_key"].astype(str).str.replace("^equity:", "", regex=True)
        r["date"] = pd.to_datetime(r["ts_event"]).dt.date.astype(str)
        r = r[["ticker", "date", col]].drop_duplicates(["ticker", "date"], keep="last")
        d = d.merge(r, on=["ticker", "date"], how="left")
        ic = d["put_call"].astype(str).str.upper().str.startswith("C")
        d[f"y_und_{col}"] = np.where(ic, 1.0, -1.0) * pd.to_numeric(d[col], errors="coerce")
        print(f"  underlying outcome {col}: coverage {d[f'y_und_{col}'].notna().mean():.1%}")
    return d


# --------------------------------------------------------------------------
# factors
# --------------------------------------------------------------------------
def add_factors(d: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    d = d.copy()
    is_call = d["put_call"].astype(str).str.upper().str.startswith("C")
    d["is_call"] = is_call.astype(float)

    defs: dict[str, str] = {}

    def F(name, series, desc):
        d[name] = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
        defs[name] = desc

    # --- alert-time (meta_label_features, PIT-safe by construction) -----------
    F("f_iv", d["iv"], "F1/F15 input: contract implied vol at the alert (meta_label_features.iv)")
    F("f_iv_rank", d["iv_rank"], "F1: IV rank at the alert (0-100), alert-time")
    F("f_rv20", d["realized_vol_20d"], "F15: trailing 20d realized vol of the underlying, alert-time")
    F(
        "f_vrp",
        np.log(pd.to_numeric(d["realized_vol_20d"], errors="coerce").clip(lower=1e-6)
               / pd.to_numeric(d["iv"], errors="coerce").clip(lower=1e-6)),
        "F1 Goyal-Saretto (2009): log(RV20 / IV). HIGH = vol is cheap -> buy.",
    )
    # Hu & Jacobs: E[call ret] decreasing in underlying vol; E[put ret] increasing.
    F(
        "f_hujacobs",
        np.where(is_call, -1.0, 1.0) * pd.to_numeric(d["realized_vol_20d"], errors="coerce"),
        "F15 Hu & Jacobs: -RV20 for calls, +RV20 for puts. HIGH = favorable side of the vol sort.",
    )
    # Heber writes log_moneyness = ln(K/S) (heber/watch/features.py:448-450), which is
    # OTM-positive for calls but ITM-positive for puts.  Re-sign it so positive always
    # means "further out of the money", which is what Boyer-Vorkink's lottery result is about.
    F("f_logmoney", d["log_moneyness"], "Raw Heber ln(K/S) -- NOT direction-aware, kept for reference")
    F(
        "f_otm",
        np.where(is_call, 1.0, -1.0) * pd.to_numeric(d["log_moneyness"], errors="coerce"),
        "F10 Boyer-Vorkink: direction-aware OTM-ness. +ln(K/S) for calls, -ln(K/S) for puts. "
        "HIGH = deeper OTM = the lottery wing the literature says to avoid.",
    )
    F("f_abs_delta", pd.to_numeric(d["delta"], errors="coerce").abs(), "F10 delta magnitude at the alert")
    F("f_dte", d["days_to_expiry"], "DTE bucket driver")
    F("f_log_prem", np.log(pd.to_numeric(d["premium"], errors="coerce").clip(lower=1.0)), "Premium size (log $)")
    F("f_vol_oi", d["volume_oi_ratio"], "F14 volume/OI at the alert (NOT open-buy volume - see literature caveat)")
    F(
        "f_ask_share",
        d.get("sv_net_ask_share"),
        "F5 own-print buyer conviction: (ask_prem - bid_prem)/(ask_prem + bid_prem) on THIS "
        "sweep, from Silver flow_alerts. HIGH = more one-sidedly buyer-initiated.",
    )
    F("f_trade_count", d.get("sv_trade_count"), "Number of prints composing the sweep")
    F("f_und_1d", d["underlying_1d_return"], "F11 1-day underlying return (reversal/momentum)")
    F("f_und_5d", d["underlying_5d_return"], "F11 5-day underlying return")
    F("f_und_30d", d["underlying_30d_return"], "30-day underlying return")
    # direction-aligned momentum: does the underlying already move the sweep's way?
    F(
        "f_mom_align_5d",
        np.where(is_call, 1.0, -1.0) * pd.to_numeric(d["underlying_5d_return"], errors="coerce"),
        "5d underlying return signed by the option's direction. HIGH = trading with the trend.",
    )
    F("f_maxpain_dist", d["max_pain_distance_pct"], "Distance of spot from max-pain strike (alert-time vendor field)")
    F("f_minutes_open", d["minutes_since_open"], "Minutes since the open at the alert")
    F("f_minutes_close", d["minutes_to_close"], "Minutes to the close at the alert")

    # --- event-time aggregated prior flow (strictly < alert ts) ---------------
    F(
        "f_prior_flow",
        d["prior_net_prem_24h"],
        "F5 Pan-Poteshman analogue: signed net premium of STRICTLY PRIOR alerts on the same "
        "ticker in the trailing 24h (+ = bullish). Event-time, no daily aggregate.",
    )
    # direction-aligned version
    F(
        "f_prior_flow_align",
        np.where(is_call, 1.0, -1.0) * pd.to_numeric(d["prior_net_prem_24h"], errors="coerce"),
        "F5 aligned: prior signed net premium in the direction of THIS candidate. "
        "HIGH = the prior imbalance agrees with the sweep.",
    )

    # --- strictly-lagged daily EOD gold (ts_available <= alert ts_event) ------
    F("f_mom5d_lag", d.get("mom_momentum_5d"), "F11/F6: lagged daily 5d momentum (gold, strict as-of)")
    F("f_mom20d_lag", d.get("mom_momentum_20d"), "Lagged daily 20d momentum (gold, strict as-of)")
    F("f_rsi14_lag", d.get("mom_rsi_14"), "Lagged RSI(14) (gold, strict as-of)")
    F("f_tox_lag", d.get("tox_flow_toxicity_1d"), "F13: lagged Heber flow_toxicity_1d (in-house composite, not VPIN)")
    F("f_osiv_lag", d.get("os_current_iv"), "Lagged options_sentiment current_iv")
    F("f_osivrank_lag", d.get("os_iv_rank"), "F1: lagged options_sentiment iv_rank")
    F("f_oibuild_lag", d.get("oim_oi_buildup_ratio"), "F14: lagged OI buildup ratio")
    F("f_oimom_lag", d.get("oim_oi_change_momentum_5d"), "F14: lagged 5d OI change momentum")
    F("f_ts_slope_lag", d.get("ivs_term_structure_slope"), "F8: lagged Heber term_structure_slope (NOT Vasquez's construction)")
    F("f_gamma_lag", d.get("gxp_net_gamma_exposure"), "F9: lagged UNSIGNED call+put gamma sum. NOT dealer GEX.")
    F("f_netbull_lag", d.get("flw_net_bull_premium_lr"), "F5 daily-aggregate variant: lagged net_bull_premium_lr")
    F("f_sweepshare_lag", d.get("flw_sweep_volume_share"), "Lagged sweep share of option volume")
    F("f_vol20_lag", d.get("vol_vol_20d"), "F15/F2: lagged daily realized vol_20d (gold)")
    F(
        "f_hujacobs_lag",
        np.where(is_call, -1.0, 1.0) * pd.to_numeric(d.get("vol_vol_20d"), errors="coerce"),
        "F15 on the strictly-lagged gold vol_20d instead of the alert-time RV.",
    )
    F("f_mktgex_lag", d.get("gxr_net_gex"), "F9 market-wide: lagged gex_regime net_gex (index only, 1 symbol)")

    return d, defs


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def day_cluster_boot(
    days: np.ndarray, groups: dict, stat_fn, n_boot: int = N_BOOT, seed: int = SEED
) -> tuple[float, float]:
    """Bootstrap CI by resampling whole DAYS (clustered), not rows."""
    rng = np.random.default_rng(seed)
    uniq = np.unique(days)
    idx_by_day = {dd: np.flatnonzero(days == dd) for dd in uniq}
    vals = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([idx_by_day[p] for p in pick])
        v = stat_fn(idx)
        if v is not None and np.isfinite(v):
            vals.append(v)
    if len(vals) < 50:
        return (np.nan, np.nan)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def quantile_bins(x: np.ndarray, q: int) -> np.ndarray:
    """Assign 0..q-1 bins; returns -1 for NaN. Falls back to fewer bins on ties."""
    out = np.full(len(x), -1, dtype=int)
    ok = np.isfinite(x)
    if ok.sum() < 50:
        return out
    try:
        b = pd.qcut(x[ok], q, labels=False, duplicates="drop")
    except Exception:
        return out
    out[ok] = b.astype(int)
    return out


def spread_test(sub: pd.DataFrame, fac: str, out_col: str, nq: int, n_boot: int = N_BOOT) -> dict | None:
    """Top-vs-bottom quantile spread + Spearman, both with day-clustered bootstrap CIs.

    One set of day resamples drives the spread CI, the Spearman CI and the bootstrap
    two-sided p-value, so the three are mutually consistent and cost one pass.
    """
    x = sub[fac].to_numpy(dtype=float)
    y = sub[out_col].to_numpy(dtype=float)
    days = sub["date"].to_numpy()
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 200:
        return None
    x, y, days = x[ok], y[ok], days[ok]
    bins = quantile_bins(x, nq)
    nb = bins.max() + 1
    if nb < 2:
        return None
    hi, lo = nb - 1, 0
    is_hi, is_lo = bins == hi, bins == lo

    point = y[is_hi].mean() - y[is_lo].mean()
    rho = stats.spearmanr(x, y).statistic

    uniq, day_codes = np.unique(days, return_inverse=True)
    order = np.argsort(day_codes, kind="stable")
    starts = np.searchsorted(day_codes[order], np.arange(len(uniq)))
    ends = np.searchsorted(day_codes[order], np.arange(len(uniq)), side="right")
    idx_by_day = [order[s:e] for s, e in zip(starts, ends)]

    rng = np.random.default_rng(SEED)
    sp_draws, rho_draws = [], []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), size=len(uniq))
        idx = np.concatenate([idx_by_day[p] for p in pick])
        a, c = y[idx][is_hi[idx]], y[idx][is_lo[idx]]
        if len(a) >= 5 and len(c) >= 5:
            sp_draws.append(a.mean() - c.mean())
        xi = x[idx]
        if len(np.unique(xi)) >= 3:
            r = stats.spearmanr(xi, y[idx]).statistic
            if np.isfinite(r):
                rho_draws.append(r)
    sp_draws = np.asarray(sp_draws)
    rho_draws = np.asarray(rho_draws)

    def pct(a):
        return (float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))) if len(a) >= 50 else (np.nan, np.nan)

    ci, rho_ci = pct(sp_draws), pct(rho_draws)
    if len(sp_draws) < 50:
        p = np.nan
    else:
        frac = (sp_draws <= 0).mean() if point > 0 else (sp_draws >= 0).mean()
        p = min(1.0, 2 * max(frac, 1.0 / len(sp_draws)))

    return {
        "n": int(ok.sum()),
        "n_days": int(len(uniq)),
        "n_bins": int(nb),
        "n_hi": int(is_hi.sum()),
        "n_lo": int(is_lo.sum()),
        "q_hi_mean": float(y[is_hi].mean()),
        "q_lo_mean": float(y[is_lo].mean()),
        "spread": float(point),
        "spread_lo95": ci[0],
        "spread_hi95": ci[1],
        "spearman": float(rho),
        "spearman_lo95": rho_ci[0],
        "spearman_hi95": rho_ci[1],
        "p_boot": float(p),
    }


def oos_test(sub: pd.DataFrame, fac: str, out_col: str, nq: int) -> dict:
    """Time-blocked: bin edges AND the sign hypothesis come from the first 60% of dates."""
    dts = np.sort(sub["date"].unique())
    if len(dts) < 20:
        return {}
    cut = dts[int(0.6 * len(dts))]
    tr = sub[sub["date"] < cut]
    te = sub[sub["date"] >= cut]
    x_tr = tr[fac].to_numpy(dtype=float)
    ok_tr = np.isfinite(x_tr) & np.isfinite(tr[out_col].to_numpy(dtype=float))
    if ok_tr.sum() < 200 or len(te) < 200:
        return {}
    try:
        edges = np.nanquantile(x_tr[ok_tr], np.linspace(0, 1, nq + 1))
    except Exception:
        return {}
    edges = np.unique(edges)
    if len(edges) < 3:
        return {}
    edges[0], edges[-1] = -np.inf, np.inf

    def eval_block(blk):
        x = blk[fac].to_numpy(dtype=float)
        y = blk[out_col].to_numpy(dtype=float)
        ok = np.isfinite(x) & np.isfinite(y)
        x, y, dd = x[ok], y[ok], blk["date"].to_numpy()[ok]
        if len(y) < 100:
            return None
        b = np.digitize(x, edges[1:-1])
        hi, lo = b.max(), b.min()
        if hi == lo:
            return None

        def sp(idx):
            bb, yy = b[idx], y[idx]
            a, c = yy[bb == hi], yy[bb == lo]
            if len(a) < 5 or len(c) < 5:
                return None
            return a.mean() - c.mean()

        pt = sp(np.arange(len(y)))
        ci = day_cluster_boot(dd, {}, sp, n_boot=1000)
        return pt, ci, len(y)

    a = eval_block(tr)
    b = eval_block(te)
    if a is None or b is None:
        return {}
    return {
        "is_spread": a[0], "is_n": a[2],
        "oos_spread": b[0], "oos_lo95": b[1][0], "oos_hi95": b[1][1], "oos_n": b[2],
        "oos_sign_agrees": bool(np.sign(a[0]) == np.sign(b[0])) if a[0] and b[0] else False,
        "split_date": str(cut),
    }


def bh_qvalues(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    ok = np.isfinite(p)
    q = np.full(len(p), np.nan)
    pv = p[ok]
    n = len(pv)
    if n == 0:
        return q
    order = np.argsort(pv)
    ranked = pv[order]
    adj = ranked * n / (np.arange(n) + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(adj, 1.0)
    q[ok] = out
    return q


def min_detectable_effect(n: int, base_rate: float, n_days: int) -> float:
    """Two-sided 5%, 80% power, two equal groups of n/5 (top vs bottom quintile),
    inflated by a day-cluster design effect using the observed alerts/day."""
    m = n / 5.0
    p = base_rate
    se = np.sqrt(2 * p * (1 - p) / m)
    mde = (1.96 + 0.84) * se
    if n_days > 1:
        k = n / n_days
        icc = 0.02  # modest within-day correlation; the bootstrap CIs are the real check
        deff = 1 + (k - 1) * icc
        mde *= np.sqrt(max(deff, 1.0))
    return float(mde)


# --------------------------------------------------------------------------
def main() -> None:
    panel = pd.read_parquet(f"{OUT}/panel.parquet")
    print(f"panel rows={len(panel):,}")

    uni, funnel = build_universe(panel)
    print(funnel.to_string(index=False))
    funnel.to_csv(f"{OUT}/universe.csv", index=False)

    pops_raw = nested_populations(panel)
    pops = {}
    for k, v in pops_raw.items():
        v = add_underlying_returns(add_outcomes(v))
        v, defs = add_factors(v)
        pops[k] = v
        print(f"{k}: n={len(v):,} days={v['date'].nunique()} tickers={v['ticker'].nunique()} "
              f"hit_tp={v['y_tp'].mean():.3f}")
    uni = pops["P1_orion_strict"]
    with open(f"{OUT}/factor_definitions.json", "w") as fh:
        json.dump(defs, fh, indent=2)

    outcomes = ["y_tp", "y_ret", "y_orion_opt", "y_orion_pess", "y_und_return_1d", "y_und_return_5d"]
    outcomes = [o for o in outcomes if o in uni.columns]
    factors = [c for c in uni.columns if c.startswith("f_")]

    rows = []
    for pop_name, pop in pops.items():
        nq = 5 if len(pop) >= 2500 else 3
        nb = N_BOOT if len(pop) < 20000 else 500
        for fac in factors:
            for oc in outcomes:
                if oc not in pop.columns:
                    continue
                r = spread_test(pop, fac, oc, nq, n_boot=nb)
                if r is None:
                    continue
                r.update({"population": pop_name, "factor": fac, "outcome": oc, "nq": nq})
                if pop_name in ("P1_orion_strict", "P2_no_universe_allowlist", "P3_no_premium_floor"):
                    r.update(oos_test(pop, fac, oc, nq))
                rows.append(r)

    res = pd.DataFrame(rows)
    # BH over the primary family: the Orion-strict population, all factor x outcome tests
    prim = res["population"].eq("P1_orion_strict")
    res["q_value"] = np.nan
    res.loc[prim, "q_value"] = bh_qvalues(res.loc[prim, "p_boot"].to_numpy())
    res = res.sort_values(["population", "p_boot"])
    res.to_csv(f"{OUT}/results.csv", index=False)
    print(f"\nwrote results.csv rows={len(res)}")

    # coverage report
    cov = pd.DataFrame(
        {"factor": factors, "coverage_orion_like": [uni[f].notna().mean() for f in factors],
         "n_nonnull": [int(uni[f].notna().sum()) for f in factors]}
    ).sort_values("coverage_orion_like", ascending=False)
    cov.to_csv(f"{OUT}/factor_coverage.csv", index=False)
    print(cov.to_string(index=False))

    # power
    base = uni["y_tp"].mean()
    nd = uni["date"].nunique()
    mde = min_detectable_effect(len(uni), base, nd)
    print(f"\nbase hit_tp rate={base:.3f}  n={len(uni)}  days={nd}  MDE(quintile spread, 80% power)={mde*100:.1f}pp")
    with open(f"{OUT}/power.json", "w") as fh:
        json.dump({"n": int(len(uni)), "n_days": int(nd), "base_rate": float(base),
                   "mde_pp_quintile_spread": float(mde * 100)}, fh, indent=2)

    # ---------------- joint model ----------------
    joint_rows = []
    dts = np.sort(uni["date"].unique())
    cut = dts[int(0.6 * len(dts))]
    for oc in ["y_tp", "y_orion_pess"]:
        top = (
            res[prim & res["outcome"].eq(oc) & res["factor"].str.startswith("f_")]
            .dropna(subset=["p_boot"]).nsmallest(6, "p_boot")["factor"].tolist()
        )
        cols = top + ["f_dte", "f_log_prem"]
        cols = list(dict.fromkeys(cols))
        sub = uni.dropna(subset=cols + [oc]).copy()
        tr, te = sub[sub["date"] < cut], sub[sub["date"] >= cut]
        if len(tr) < 300 or len(te) < 200 or te[oc].nunique() < 2:
            joint_rows.append({"outcome": oc, "status": "insufficient", "n_train": len(tr), "n_test": len(te)})
            continue
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score
        from sklearn.preprocessing import StandardScaler

        X = tr[cols].to_numpy(float)
        mu, sd = X.mean(0), X.std(0) + 1e-9
        D = pd.get_dummies(tr["bucket"]).reindex(columns=["0DTE", "SHORT_SWING", "SWING"], fill_value=0).to_numpy(float)
        Dt = pd.get_dummies(te["bucket"]).reindex(columns=["0DTE", "SHORT_SWING", "SWING"], fill_value=0).to_numpy(float)
        m = LogisticRegression(max_iter=2000, C=0.5)
        m.fit(np.hstack([(X - mu) / sd, D]), tr[oc].to_numpy(float))
        Xt = (te[cols].to_numpy(float) - mu) / sd
        pr = m.predict_proba(np.hstack([Xt, Dt]))[:, 1]
        auc = roc_auc_score(te[oc].to_numpy(float), pr)

        dd = te["date"].to_numpy()
        yv = te[oc].to_numpy(float)

        def auc_fn(idx):
            if len(np.unique(yv[idx])) < 2:
                return None
            return roc_auc_score(yv[idx], pr[idx])

        lo, hi = day_cluster_boot(dd, {}, auc_fn, n_boot=1000)
        # trading-terms: hit rate of the top decile of predicted p vs the all-pass baseline
        k = max(int(0.2 * len(pr)), 20)
        topk = np.argsort(pr)[-k:]
        joint_rows.append({
            "outcome": oc, "status": "ok", "features": ",".join(cols),
            "n_train": len(tr), "n_test": len(te), "split_date": str(cut),
            "oos_auc": float(auc), "oos_auc_lo95": lo, "oos_auc_hi95": hi,
            "baseline_auc": 0.5,
            "oos_base_rate": float(yv.mean()),
            "oos_top20pct_rate": float(yv[topk].mean()),
            "oos_top20pct_delta_pp": float((yv[topk].mean() - yv.mean()) * 100),
        })
    pd.DataFrame(joint_rows).to_csv(f"{OUT}/joint.csv", index=False)
    print("\n", pd.DataFrame(joint_rows).to_string(index=False))

    # by-month stability of the headline factors
    stab_rows = []
    uni["_m"] = pd.to_datetime(uni["date"]).dt.to_period("M").astype(str)
    for fac in factors:
        for oc in ["y_tp"]:
            for m, g in uni.groupby("_m"):
                r = spread_test(g, fac, oc, 3)
                if r:
                    stab_rows.append({"factor": fac, "outcome": oc, "month": m,
                                      "n": r["n"], "spread": r["spread"]})
    pd.DataFrame(stab_rows).to_csv(f"{OUT}/monthly_stability.csv", index=False)
    print("wrote monthly_stability.csv")

    uni.to_parquet(f"{OUT}/universe_panel.parquet", index=False)


if __name__ == "__main__":
    main()
