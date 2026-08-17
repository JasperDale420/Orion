"""Translate the factor results into trading terms: expectancy, not hit rate.

A gate is only worth shipping if it raises EXPECTED RETURN PER TRADE net of cost, not if it
raises the hit rate.  On this label a higher hit rate is easy to buy with a worse payoff
ratio, so this script reports both and simulates the actual trade-count / expectancy
trade-off of gating on each candidate factor.

Cost model: measured from Orion's own 1,501 `entry_quote` records
    median spread_pct 1.94%, mean 2.85%, p90 6.36%, mean mid $8.41
Entry pays mid + phi * half_spread (phi = 0.25 non-0DTE, execution_engine.py:1129);
a normal exit hits the live bid, i.e. a full half-spread.  Round-trip drag is therefore
    (0.25 + 1.0) * spread_pct/2  ~= 0.63 * spread_pct
Heber's label already haircuts the TP leg by 4% (2 x option_spread_pct of 2%,
alert_labels.py:156 + checker.py:296) but haircuts NOTHING on the SL / expiry legs.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
SEED = 20260816
N_BOOT = 2000

# measured on Orion's own executed decisions (read-only query, 2026-08-16)
SPREAD_MED = 0.0194
SPREAD_MEAN = 0.0285
SPREAD_P90 = 0.0636
PHI = 0.25


def boot_mean_ci(y: np.ndarray, days: np.ndarray, n_boot: int = N_BOOT) -> tuple[float, float]:
    uniq, codes = np.unique(days, return_inverse=True)
    order = np.argsort(codes, kind="stable")
    s = np.searchsorted(codes[order], np.arange(len(uniq)))
    e = np.searchsorted(codes[order], np.arange(len(uniq)), side="right")
    idx_by_day = [order[a:b] for a, b in zip(s, e)]
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), size=len(uniq))
        idx = np.concatenate([idx_by_day[p] for p in pick])
        vals.append(y[idx].mean())
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def net_return(d: pd.DataFrame, spread: float) -> np.ndarray:
    """outcome_return net of Orion's round-trip drag, adding back Heber's TP-leg haircut
    so the cost is applied once and consistently on every leg."""
    r = d["outcome_return"].to_numpy(float).copy()
    heber_tp_haircut = 0.04
    hit_tp = d["outcome"].astype(str).eq("hit_tp").to_numpy()
    r = np.where(hit_tp, r + heber_tp_haircut, r)   # undo Heber's asymmetric haircut
    drag = (PHI + 1.0) * spread / 2.0               # entry payup + exit at the bid
    return r - drag


def main() -> None:
    u = pd.read_parquet(f"{OUT}/universe_panel.parquet")
    days = u["date"].to_numpy()
    out = []

    # ---------- 1. unconditional expectancy of the Orion-shaped universe ----------
    for label, spread in [("no_cost", 0.0), ("median_spread", SPREAD_MED),
                          ("mean_spread", SPREAD_MEAN), ("p90_spread", SPREAD_P90)]:
        r = net_return(u, spread)
        lo, hi = boot_mean_ci(r, days)
        out.append({"section": "unconditional", "variant": label, "n": len(u),
                    "mean_return": float(r.mean()), "lo95": lo, "hi95": hi,
                    "hit_tp_rate": float(u["y_tp"].mean())})

    # per bucket
    for b, g in u.groupby("bucket"):
        r = net_return(g, SPREAD_MED)
        lo, hi = boot_mean_ci(r, g["date"].to_numpy())
        out.append({"section": "by_bucket", "variant": b, "n": len(g),
                    "mean_return": float(r.mean()), "lo95": lo, "hi95": hi,
                    "hit_tp_rate": float(g["y_tp"].mean())})

    # per month -- regime stability of the base expectancy
    u = u.copy()
    u["_m"] = pd.to_datetime(u["date"]).dt.to_period("M").astype(str)
    for m, g in u.groupby("_m"):
        r = net_return(g, SPREAD_MED)
        out.append({"section": "by_month", "variant": m, "n": len(g),
                    "mean_return": float(r.mean()), "lo95": np.nan, "hi95": np.nan,
                    "hit_tp_rate": float(g["y_tp"].mean())})

    # ---------- 2. gate simulation ----------
    # For each candidate factor, drop the worst tercile (by the in-sample sign) and report
    # the change in trade count AND in net expectancy, in-sample and out-of-sample.
    dts = np.sort(u["date"].unique())
    cut = dts[int(0.6 * len(dts))]
    gates = []
    for fac in [c for c in u.columns if c.startswith("f_")]:
        x = u[fac].to_numpy(float)
        ok = np.isfinite(x)
        if ok.sum() < 300:
            continue
        sub = u[ok].copy()
        tr = sub[sub["date"] < cut]
        te = sub[sub["date"] >= cut]
        if len(tr) < 150 or len(te) < 100:
            continue
        # sign of the relationship, learned on TRAIN only
        try:
            edges = np.nanquantile(tr[fac].to_numpy(float), [1 / 3, 2 / 3])
        except Exception:
            continue
        if not np.all(np.isfinite(edges)) or edges[0] == edges[1]:
            continue
        tr_b = np.digitize(tr[fac].to_numpy(float), edges)
        r_tr = net_return(tr, SPREAD_MED)
        m_lo, m_hi = r_tr[tr_b == 0].mean(), r_tr[tr_b == 2].mean()
        drop_high = m_hi < m_lo          # if the top tercile is worse, gate it out
        keep_tr = (tr_b != 2) if drop_high else (tr_b != 0)

        te_b = np.digitize(te[fac].to_numpy(float), edges)
        keep_te = (te_b != 2) if drop_high else (te_b != 0)
        r_te = net_return(te, SPREAD_MED)
        if keep_te.sum() < 50:
            continue
        lo, hi = boot_mean_ci(r_te[keep_te], te["date"].to_numpy()[keep_te])
        base_lo, base_hi = boot_mean_ci(r_te, te["date"].to_numpy())
        gates.append({
            "factor": fac,
            "gate": f"drop {'top' if drop_high else 'bottom'} tercile (edges from train)",
            "n_train": int(len(tr)), "n_test": int(len(te)),
            "is_gain_pp": float((r_tr[keep_tr].mean() - r_tr.mean()) * 100),
            "oos_base_ret": float(r_te.mean()), "oos_base_lo95": base_lo, "oos_base_hi95": base_hi,
            "oos_gated_ret": float(r_te[keep_te].mean()), "oos_gated_lo95": lo, "oos_gated_hi95": hi,
            "oos_gain_pp": float((r_te[keep_te].mean() - r_te.mean()) * 100),
            "trades_kept_pct": float(keep_te.mean() * 100),
        })
    g = pd.DataFrame(gates).sort_values("oos_gain_pp", ascending=False)
    g.to_csv(f"{OUT}/gate_simulation.csv", index=False)
    pd.DataFrame(out).to_csv(f"{OUT}/expectancy.csv", index=False)

    print(pd.DataFrame(out).to_string(index=False))
    print()
    print(g.to_string(index=False))

    # ---------- 3. does the DTE hit-rate effect survive in EXPECTANCY? ----------
    u["dte_b"] = pd.cut(u["days_to_expiry"], [-1, 0, 3, 7, 14], labels=["0", "1-3", "4-7", "8-14"])
    rows = []
    for b, gg in u.groupby("dte_b", observed=True):
        r = net_return(gg, SPREAD_MED)
        lo, hi = boot_mean_ci(r, gg["date"].to_numpy())
        rows.append({"dte_bucket": str(b), "n": len(gg), "hit_tp": float(gg["y_tp"].mean()),
                     "mean_net_return": float(r.mean()), "lo95": lo, "hi95": hi,
                     "mean_win": float(r[r > 0].mean()) if (r > 0).any() else np.nan,
                     "mean_loss": float(r[r <= 0].mean()) if (r <= 0).any() else np.nan})
    dte_tab = pd.DataFrame(rows)
    dte_tab.to_csv(f"{OUT}/dte_expectancy.csv", index=False)
    print()
    print(dte_tab.to_string(index=False))

    with open(f"{OUT}/cost_model.json", "w") as fh:
        json.dump({"spread_pct_median": SPREAD_MED, "spread_pct_mean": SPREAD_MEAN,
                   "spread_pct_p90": SPREAD_P90, "phi_payup": PHI,
                   "roundtrip_drag_median": (PHI + 1) * SPREAD_MED / 2,
                   "source": "orion strategy_decisions.decision_trace_json.entry_quote, n=1501"}, fh, indent=2)


if __name__ == "__main__":
    main()
