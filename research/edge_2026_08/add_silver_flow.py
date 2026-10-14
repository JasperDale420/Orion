"""Recover Orion's entry fields for each labeled alert from Silver flow_alerts.

`meta_label_features` carries `side`/`aggressor`/`is_sweep`, but in the cache they are
effectively dead: aggressor is NULL on 96,071/96,072 panel rows, side is 'mid' on 99.4%,
and is_sweep is 1 on only 781.  Orion never reads those columns -- it reads Silver
flow_alerts and SYNTHESISES the aggressor itself:

    src/orion/processing/normalizer.py:99-111
        ask_prem > bid_prem -> "ASK";  bid_prem > ask_prem -> "BID";  else "MID"

So the honest way to reconstruct Orion's entry universe on the labeled alerts is to join
the labels back to Silver on event_id (= Heber's alert_id, heber/watch/consumer.py:890)
and re-derive is_sweep + aggressor exactly the way the live normalizer does.
"""

from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
SILVER = os.path.expanduser("~/.heber-cache/data/silver/feed=flow_alerts")

COLS = [
    "event_id", "ts_event", "is_sweep", "total_ask_side_prem", "total_bid_side_prem",
    "premium", "volume", "open_interest", "volume_oi_ratio", "all_opening_trades",
    "has_floor", "has_multileg", "is_unusual", "spot_px", "contract_px", "strike",
    "trade_count", "total_size",
]


def main() -> None:
    files = [f for f in sorted(glob.glob(f"{SILVER}/**/*.parquet", recursive=True)) if os.path.getsize(f) > 0]
    frames, bad = [], 0
    for f in files:
        try:
            have = set(pq.read_schema(f).names)
            t = pq.read_table(f, columns=[c for c in COLS if c in have]).to_pandas()
            for c in COLS:
                if c not in t.columns:
                    t[c] = np.nan
            frames.append(t[COLS])
        except Exception:
            bad += 1
    print(f"silver flow_alerts files={len(files)} unreadable={bad}")
    s = pd.concat(frames, ignore_index=True)
    print(f"silver rows={len(s):,}")
    s["ts_event"] = pd.to_datetime(s["ts_event"], utc=True, errors="coerce")
    s = s.sort_values("ts_event").drop_duplicates("event_id", keep="last")
    print(f"silver unique event_id={len(s):,}")

    ask = pd.to_numeric(s["total_ask_side_prem"], errors="coerce").fillna(0.0)
    bid = pd.to_numeric(s["total_bid_side_prem"], errors="coerce").fillna(0.0)
    s["sv_aggressor"] = np.where(ask > bid, "ASK", np.where(bid > ask, "BID", "MID"))
    s["sv_is_sweep"] = s["is_sweep"].map(
        lambda v: bool(v) if not pd.isna(v) else False
    ).astype(int)
    s["sv_ask_prem"] = ask
    s["sv_bid_prem"] = bid
    s["sv_net_ask_share"] = (ask - bid) / (ask + bid).replace(0, np.nan)
    keep = [
        "event_id", "sv_aggressor", "sv_is_sweep", "sv_ask_prem", "sv_bid_prem",
        "sv_net_ask_share", "all_opening_trades", "has_floor", "has_multileg",
        "is_unusual", "trade_count", "total_size", "volume_oi_ratio",
    ]
    s = s[keep].rename(columns={
        "all_opening_trades": "sv_all_opening", "has_floor": "sv_has_floor",
        "has_multileg": "sv_has_multileg", "is_unusual": "sv_is_unusual",
        "trade_count": "sv_trade_count", "total_size": "sv_total_size",
        "volume_oi_ratio": "sv_volume_oi_ratio",
    })

    panel = pd.read_parquet(f"{OUT}/panel.parquet")
    # idempotent: a rerun must not produce _x/_y suffixed duplicates
    panel = panel.drop(
        columns=[c for c in panel.columns
                 if c.startswith("sv_") or c in ("event_id", "prior_net_prem_24h", "prior_alert_cnt_24h")],
        errors="ignore",
    )
    n0 = len(panel)
    panel = panel.merge(s, left_on="alert_id", right_on="event_id", how="left")
    hit = panel["sv_aggressor"].notna().mean()
    print(f"panel {n0:,} -> silver join coverage {hit:.1%}")
    print(panel["sv_aggressor"].value_counts(dropna=False).to_dict())
    print("sv_is_sweep:", panel["sv_is_sweep"].value_counts(dropna=False).to_dict())
    # ---- F5: aggregated signed flow from STRICTLY PRIOR alerts (event-time, no leak) ----
    # Pan-Poteshman's predictive object is the aggregated buyer-initiated imbalance, not a
    # single print.  Signed premium of each alert = (ask_prem - bid_prem), signed +1 for
    # calls / -1 for puts, summed over the trailing 24h with the current alert EXCLUDED
    # (`closed="left"`), per underlying.
    panel = panel.sort_values("ts_event", kind="mergesort").reset_index(drop=True)
    sgn = np.where(panel["put_call"].astype(str).str.upper().str.startswith("C"), 1.0, -1.0)
    net = panel["sv_ask_prem"].fillna(0.0) - panel["sv_bid_prem"].fillna(0.0)
    panel["_signed_prem"] = sgn * net

    parts_sum, parts_cnt = [], []
    for _, g in panel.groupby("ticker", sort=False):
        s = g.set_index("ts_event")["_signed_prem"]
        parts_sum.append(s.rolling("24h", closed="left").sum())
        parts_cnt.append(s.rolling("24h", closed="left").count())
    panel["prior_net_prem_24h"] = pd.concat(parts_sum).to_numpy()
    panel["prior_alert_cnt_24h"] = pd.concat(parts_cnt).to_numpy()
    panel = panel.drop(columns="_signed_prem")
    nz = (panel["prior_net_prem_24h"].fillna(0) != 0).mean()
    print(f"prior_net_prem_24h nonzero on {nz:.1%} of rows")

    panel.to_parquet(f"{OUT}/panel.parquet", index=False)
    print("rewrote panel.parquet with sv_* columns + prior-flow aggregate")


if __name__ == "__main__":
    main()
