"""Build the point-in-time factor evaluation dataset.

Base population: Heber gold `labels_alert_barriers` (one row per UW flow alert that the
Heber watch service tracked to a triple-barrier outcome on the OPTION CONTRACT mid).

Joins:
  * `meta_label_features` on alert_id  -- computed synchronously at alert intake, so it is
    point-in-time safe by construction (ts_available is seconds after alert_time).
  * daily EOD gold feature tables via a STRICT as-of join on
    feature.ts_available <= alert.ts_event, per underlying ticker.  This is the only
    join that is honest: these tables are whole-session aggregates whose ts_available is
    stamped at batch-write time.

Every gold table in the cache is write-duplicated (up to 180x).  Each is deduped on its
natural key before use.

Output: parquet at research/edge_2026_08/out/panel.parquet
"""

from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

CACHE = os.path.expanduser("~/.heber-cache/data")
GOLD = f"{CACHE}/gold"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def _files(dataset: str, version: str = "version=v1") -> list[str]:
    pat = f"{GOLD}/dataset={dataset}/**/{version}/**/*.parquet"
    fs = sorted(glob.glob(pat, recursive=True))
    return [f for f in fs if os.path.getsize(f) > 0]


TS_COLS = ("ts_event", "ts_available", "alert_time")


def _normalize(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Add any columns the file's schema is missing, and force uniform ts precision."""
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    for c in TS_COLS:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    return df[cols]


def read_gold(dataset: str, cols: list[str], version: str = "version=v1") -> pd.DataFrame:
    """Read a gold dataset, tolerating corrupt files and per-file schema drift."""
    frames = []
    bad = 0
    for f in _files(dataset, version):
        try:
            have = set(pq.read_schema(f).names)
            t = pq.read_table(f, columns=[c for c in cols if c in have])
            frames.append(_normalize(t.to_pandas(), cols))
        except Exception:
            bad += 1
    if bad:
        print(f"  [{dataset}] skipped {bad} unreadable file(s)", file=sys.stderr)
    if not frames:
        raise RuntimeError(f"no readable files for {dataset}")
    df = pd.concat(frames, ignore_index=True)
    print(f"  [{dataset}] raw rows={len(df):,}")
    return df


def dedup(df: pd.DataFrame, key: list[str], keep: str = "last", order: str = "ts_available") -> pd.DataFrame:
    if order in df.columns:
        df = df.sort_values(order, kind="mergesort")
    n0 = len(df)
    df = df.drop_duplicates(subset=key, keep=keep)
    print(f"    dedup on {key}: {n0:,} -> {len(df):,} ({n0 / max(len(df), 1):.1f}x)")
    return df


def asof_join(
    base: pd.DataFrame,
    feat: pd.DataFrame,
    value_cols: list[str],
    prefix: str,
    by: str = "ticker",
) -> pd.DataFrame:
    """Strict PIT as-of: newest feature row whose ts_available <= alert ts_event."""
    f = feat[[by, "ts_available"] + value_cols].dropna(subset=[by, "ts_available"])
    f = f.sort_values("ts_available", kind="mergesort")
    b = base[[by, "ts_event"]].copy()
    b["_row"] = np.arange(len(b))
    b = b.sort_values("ts_event", kind="mergesort")
    merged = pd.merge_asof(
        b,
        f,
        left_on="ts_event",
        right_on="ts_available",
        by=by,
        direction="backward",
        allow_exact_matches=False,
    )
    merged = merged.sort_values("_row")
    out = base.copy()
    for c in value_cols:
        out[f"{prefix}{c}"] = merged[c].to_numpy()
    # staleness in days between the feature's availability and the alert
    out[f"{prefix}stale_days"] = (
        (base["ts_event"].to_numpy() - merged["ts_available"].to_numpy())
        / np.timedelta64(1, "D")
    )
    cov = out[f"{prefix}{value_cols[0]}"].notna().mean()
    print(f"    asof {prefix}: coverage {cov:.1%}")
    return out


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    prov: list[str] = []

    # ---------------- labels ----------------
    lab_cols = [
        "alert_id", "occ_symbol", "underlying", "put_call", "ts_event", "ts_available",
        "horizon", "outcome", "hit_tp_first", "mfe", "mae", "bars_to_hit",
        "contract_mfe", "contract_mae", "contract_mfe_adj", "contract_mae_adj",
        "outcome_return", "entry_price", "spot_at_alert", "trading_minutes_to_hit",
        "window_duration_hours",
    ]
    lab = read_gold("labels_alert_barriers", lab_cols)
    prov.append(f"labels_alert_barriers raw={len(lab)}")
    # Heber's own guidance for the documented 1.3x concurrency dup: keep FIRST
    lab = lab.sort_values("ts_available", kind="mergesort")
    lab = dedup(lab, ["alert_id"], keep="first", order="ts_available")
    prov.append(f"labels_alert_barriers dedup={len(lab)}")

    # ---------------- alert-time features (PIT-safe by construction) -------------
    mlf_cols = [
        "alert_id", "alert_time", "symbol", "strike", "expiry", "put_call",
        "days_to_expiry", "premium", "volume", "open_interest", "volume_oi_ratio",
        "alert_type", "side", "aggressor", "spot_price", "contract_price",
        "moneyness", "log_moneyness", "delta", "gamma", "theta", "vega", "iv",
        "underlying_30d_return", "underlying_5d_return", "underlying_1d_return",
        "realized_vol_20d", "iv_rank", "gex", "vex", "max_pain_strike",
        "max_pain_distance_pct", "market_tide_net_premium", "market_tide_direction",
        "hour_of_day", "minutes_since_open", "minutes_to_close", "day_of_week",
        "is_bullish", "is_bearish", "is_sweep", "is_block", "is_unusual",
        "ts_available",
    ]
    mlf = read_gold("meta_label_features", mlf_cols)
    mlf = dedup(mlf, ["alert_id"], keep="last", order="ts_available")
    mlf = mlf.rename(columns={"ts_available": "mlf_ts_available"})
    prov.append(f"meta_label_features dedup={len(mlf)}")

    df = lab.merge(mlf, on="alert_id", how="inner", suffixes=("", "_mlf"))
    print(f"labels x meta_label_features inner join: {len(df):,}")
    prov.append(f"joined_panel={len(df)}")

    df["ticker"] = df["underlying"].astype(str)
    df["date"] = df["ts_event"].dt.tz_convert("UTC").dt.date

    # -------- PIT sanity: meta_label_features must not be stamped before the alert ----
    df["mlf_lag_s"] = (df["mlf_ts_available"] - df["ts_event"]).dt.total_seconds()

    # ---------------- daily EOD gold features (strict as-of) ----------------
    specs = [
        ("momentum_features", ["momentum_1d", "momentum_5d", "momentum_20d", "rsi_14", "macd"], "mom_"),
        ("flow_toxicity_features", ["flow_toxicity_1d", "toxicity_acceleration"], "tox_"),
        ("options_sentiment_features", ["iv_rank", "current_iv", "market_call_put_ratio", "market_net_volume"], "os_"),
        ("oi_momentum_features", ["oi_buildup_ratio", "new_position_signal", "oi_change_momentum_5d"], "oim_"),
        ("iv_surface_features", ["term_structure_slope", "iv_change_1d"], "ivs_"),
        ("greek_exposure_features", ["net_gamma_exposure", "net_delta_exposure", "put_call_gamma_ratio"], "gxp_"),
        ("flow_features", ["net_premium_24h", "net_bull_premium_lr", "sweep_volume_share", "call_put_premium_ratio", "total_premium_24h"], "flw_"),
        ("volatility_features", ["vol_5d", "vol_20d", "parkinson_vol_20d"], "vol_"),
    ]
    for dsname, cols, prefix in specs:
        try:
            f = read_gold(dsname, ["instrument_key", "ts_event", "ts_available"] + cols)
        except Exception as e:  # dataset absent
            print(f"  [{dsname}] SKIPPED: {e}", file=sys.stderr)
            continue
        f = dedup(f, ["instrument_key", "ts_event"], keep="last", order="ts_available")
        f["ticker"] = f["instrument_key"].astype(str).str.replace("^equity:", "", regex=True)
        f = f[~f["ticker"].str.contains(":", na=False)]
        df = asof_join(df, f, cols, prefix)
        prov.append(f"{dsname} dedup={len(f)}")

    # market-wide daily series -> as-of on a constant key
    for dsname, cols, prefix in [
        ("gex_regime_features", ["net_gex", "gex_regime", "gex_flip_distance"], "gxr_"),
        ("market_tide_context_features", ["market_sentiment_score", "market_premium_momentum"], "mtc_"),
    ]:
        try:
            f = read_gold(dsname, ["ts_event", "ts_available"] + cols)
        except Exception as e:
            print(f"  [{dsname}] SKIPPED: {e}", file=sys.stderr)
            continue
        f = dedup(f, ["ts_event"], keep="last", order="ts_available")
        f["ticker"] = "__MKT__"
        df["__k"] = df["ticker"]
        df["ticker"] = "__MKT__"
        df = asof_join(df, f, cols, prefix)
        df["ticker"] = df["__k"]
        df = df.drop(columns="__k")

    # The prior-signed-flow factor (F5) is built in add_silver_flow.py, because it needs the
    # ask/bid premium split that only Silver flow_alerts carries -- meta_label_features.side
    # is 'mid' on 99.4% of rows and its aggressor column is empty.

    df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce")
    df["date"] = df["date"].astype(str)
    for c in df.columns:
        if df[c].dtype == object and c not in ("alert_id", "occ_symbol"):
            try:
                df[c] = df[c].astype("string")
            except Exception:
                df[c] = df[c].astype(str)
    df.to_parquet(f"{OUT}/panel.parquet", index=False)
    with open(f"{OUT}/provenance.txt", "w") as fh:
        fh.write("\n".join(prov) + "\n")
        fh.write(f"panel_rows={len(df)}\n")
        fh.write(f"date_min={df['ts_event'].min()}\ndate_max={df['ts_event'].max()}\n")
    print(f"\nWROTE {OUT}/panel.parquet rows={len(df):,} cols={df.shape[1]}")
    print(df["ts_event"].min(), "->", df["ts_event"].max())


if __name__ == "__main__":
    main()
