"""Diagnose training data pipeline to find where samples are lost."""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

MODEL_DIR = Path(__file__).parent.parent / "artifacts" / "models"
os.environ.setdefault("ORION_MODEL_DIR", str(MODEL_DIR))
os.environ.setdefault("HEBER_DATA_ROOT", "/Volumes/heber/data")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def main() -> None:
    import pandas as pd
    from orion.clients.heber_reader import get_heber_reader
    from orion.ml.pattern_miner import (
        _normalize_heber_outcomes,
        _normalize_heber_features,
    )
    from orion.ml.heber_utils import coerce_dataframe

    reader = get_heber_reader()
    now = datetime.now(UTC)

    print("=" * 70)
    print("DATA PIPELINE DIAGNOSTIC")
    print("=" * 70)

    # Step 1: Read raw datasets
    print("\n[1] Reading raw Gold datasets...")
    outcomes_payload = reader.read_gold_features(dataset="labels_alert_barriers", asof_time=now)
    features_payload = reader.read_gold_features(dataset="meta_label_features", asof_time=now)

    outcomes_raw = coerce_dataframe(outcomes_payload)
    features_raw = coerce_dataframe(features_payload)
    print(f"    outcomes_raw: {len(outcomes_raw)} rows")
    print(f"    features_raw: {len(features_raw)} rows")
    print(f"    outcomes columns: {sorted(outcomes_raw.columns.tolist())}")
    print(f"    features columns: {sorted(features_raw.columns.tolist())}")

    # Step 2: Normalize (this maps alert_id -> event_id, etc.)
    print("\n[2] Normalizing...")
    outcomes = _normalize_heber_outcomes(outcomes_raw)
    features = _normalize_heber_features(features_raw)
    print(f"    Normalized outcomes: {len(outcomes)} rows")
    print(f"    Normalized features: {len(features)} rows")

    # Step 3: Event ID overlap
    print("\n[3] Event ID overlap...")
    outcome_ids = set(outcomes["event_id"].dropna().unique())
    feature_ids = set(features["event_id"].dropna().unique())
    overlap = outcome_ids & feature_ids
    print(f"    Unique event_ids in outcomes: {len(outcome_ids)}")
    print(f"    Unique event_ids in features: {len(feature_ids)}")
    print(f"    Overlapping event_ids: {len(overlap)}")
    print(f"    Outcomes with NO matching features: {len(outcome_ids - feature_ids)}")
    print(f"    Features with NO matching outcomes: {len(feature_ids - outcome_ids)}")

    # Step 4: Merge (left join like the real pipeline)
    print("\n[4] Merging on event_id (left join)...")
    merged = outcomes.merge(features, on="event_id", how="left")
    print(f"    Merged rows: {len(merged)}")

    # Step 5: days_to_expiry distribution
    print("\n[5] days_to_expiry distribution...")
    if "days_to_expiry" in merged.columns:
        dte = pd.to_numeric(merged["days_to_expiry"], errors="coerce")
        non_null = dte.notna().sum()
        print(f"    Non-null: {non_null} / {len(merged)}")
        print(f"    Null/NaN: {dte.isna().sum()}")
        if non_null > 0:
            print(f"    Min: {dte.min()}, Max: {dte.max()}, Mean: {dte.mean():.1f}")

        print(f"\n    Bucket breakdown (BEFORE cutoff):")
        print(f"      0DTE (dte == 0):      {(dte == 0).sum()}")
        print(f"      SHORT_SWING (1-2):    {((dte >= 1) & (dte <= 2)).sum()}")
        print(f"      SWING (3-14):         {((dte >= 3) & (dte <= 14)).sum()}")
        print(f"      POSITION (15+):       {(dte >= 15).sum()}")
        print(f"      NaN/missing:          {dte.isna().sum()}")

        print(f"\n    DTE value counts (top 30):")
        vc = dte.value_counts().sort_index()
        for val, count in vc.head(30).items():
            label = ""
            if val == 0:
                label = " [0DTE]"
            elif 1 <= val <= 2:
                label = " [SHORT_SWING]"
            elif 3 <= val <= 14:
                label = " [SWING]"
            elif val >= 15:
                label = " [POSITION]"
            print(f"      dte={val:.0f}: {count}{label}")
        if len(vc) > 30:
            print(f"      ... ({len(vc) - 30} more values)")
    else:
        print("    WARNING: 'days_to_expiry' NOT FOUND after merge!")
        print(f"    Merged columns: {sorted(merged.columns.tolist())}")

    # Step 6: entry_ts cutoff per bucket
    print("\n[6] entry_ts cutoff impact per bucket...")
    entry_ts = pd.to_datetime(merged["entry_ts"], utc=True, errors="coerce")
    print(f"    entry_ts range: {entry_ts.min()} to {entry_ts.max()}")

    buckets = {
        "0DTE": {"dte_filter": lambda d: d == 0, "window_days": 10, "min_samples": 50},
        "SHORT_SWING": {"dte_filter": lambda d: (d >= 1) & (d <= 2), "window_days": 20, "min_samples": 50},
        "SWING": {"dte_filter": lambda d: (d >= 3) & (d <= 14), "window_days": 45, "min_samples": 30},
        "POSITION": {"dte_filter": lambda d: d >= 15, "window_days": 90, "min_samples": 20},
    }

    dte_vals = pd.to_numeric(merged["days_to_expiry"], errors="coerce") if "days_to_expiry" in merged.columns else None

    for bucket_name, cfg in buckets.items():
        cutoff = now - timedelta(days=cfg["window_days"])
        if dte_vals is not None:
            bucket_mask = cfg["dte_filter"](dte_vals)
            bucket_rows = merged[bucket_mask]
            bucket_ts = entry_ts[bucket_mask]
            after_cutoff = bucket_rows[bucket_ts >= cutoff]
        else:
            bucket_rows = merged
            after_cutoff = merged[entry_ts >= cutoff]

        print(f"\n    {bucket_name} (window={cfg['window_days']}d, cutoff={cutoff.strftime('%Y-%m-%d %H:%M')}, min={cfg['min_samples']}):")
        print(f"      Total in bucket: {len(bucket_rows)}")
        print(f"      After cutoff:    {len(after_cutoff)}")
        verdict = "OK" if len(after_cutoff) >= cfg["min_samples"] else "INSUFFICIENT"
        print(f"      Verdict:         {verdict} ({len(after_cutoff)} vs min {cfg['min_samples']})")
        if len(bucket_rows) > 0 and dte_vals is not None:
            bts = pd.to_datetime(bucket_rows["entry_ts"], utc=True, errors="coerce")
            print(f"      entry_ts range:  {bts.min()} to {bts.max()}")

    # Step 7: Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Raw outcomes:     {len(outcomes_raw)}")
    print(f"  Raw features:     {len(features_raw)}")
    print(f"  After normalize:  {len(outcomes)} outcomes, {len(features)} features")
    print(f"  Event ID overlap: {len(overlap)} / {len(outcome_ids)} outcomes")
    print(f"  After merge:      {len(merged)}")


if __name__ == "__main__":
    asyncio.run(main())
