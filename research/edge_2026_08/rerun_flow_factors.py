"""Re-test only the F5 signed-prior-flow factors after the ask/bid fix.

The first pass built `prior_net_prem_24h` from `meta_label_features.side`, which is 'mid'
on 99.4% of rows -- so the factor was identically zero and F5 was never actually tested.
add_silver_flow.py now rebuilds it from the Silver ask/bid premium split.  This script
re-runs the same tests on the same populations for those factors only, so the full
evaluate_factors.py sweep (~40 min, dominated by the 67k-row P5 population) does not have
to be repeated.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from evaluate_factors import (
    add_outcomes,
    add_underlying_returns,
    bh_qvalues,
    nested_populations,
    oos_test,
    spread_test,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")


def main() -> None:
    panel = pd.read_parquet(f"{OUT}/panel.parquet")
    pops = nested_populations(panel)
    rows = []
    for name, p in pops.items():
        p = add_underlying_returns(add_outcomes(p)).copy()
        is_call = p["put_call"].astype(str).str.upper().str.startswith("C")
        p["f_prior_flow"] = pd.to_numeric(p["prior_net_prem_24h"], errors="coerce")
        p["f_prior_flow_align"] = np.where(is_call, 1.0, -1.0) * p["f_prior_flow"]
        p["f_prior_cnt"] = pd.to_numeric(p["prior_alert_cnt_24h"], errors="coerce")
        nz = (p["f_prior_flow"].fillna(0) != 0).mean()
        print(f"{name}: n={len(p):,} prior_flow nonzero={nz:.1%}")
        nq = 5 if len(p) >= 2500 else 3
        nb = 2000 if len(p) < 20000 else 500
        for fac in ["f_prior_flow", "f_prior_flow_align", "f_prior_cnt"]:
            for oc in ["y_tp", "y_ret", "y_orion_pess", "y_und_return_1d", "y_und_return_5d"]:
                if oc not in p.columns:
                    continue
                r = spread_test(p, fac, oc, nq, n_boot=nb)
                if r is None:
                    continue
                r.update({"population": name, "factor": fac, "outcome": oc, "nq": nq})
                if name.startswith(("P1", "P2", "P3")):
                    r.update(oos_test(p, fac, oc, nq))
                rows.append(r)
    res = pd.DataFrame(rows)
    prim = res["population"].eq("P1_orion_strict")
    res["q_value"] = np.nan
    res.loc[prim, "q_value"] = bh_qvalues(res.loc[prim, "p_boot"].to_numpy())
    res.to_csv(f"{OUT}/results_flow.csv", index=False)
    cols = ["population", "factor", "outcome", "n", "spread", "spread_lo95", "spread_hi95",
            "spearman", "p_boot", "oos_spread", "oos_sign_agrees"]
    print(res[cols].sort_values(["factor", "population"]).to_string(index=False))


if __name__ == "__main__":
    main()
