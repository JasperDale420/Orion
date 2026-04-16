"""One-off script to retrain 0DTE pattern-miner models with a widened window.

Context: The default 0DTE bucket config uses window_days=10, but 0DTE volume in
Heber Gold is sparse (~20-60 labeled samples per 30 days), so the production
pattern miner consistently skips 0DTE with "Insufficient Heber samples: 0 < 50".

This script temporarily widens the window to 90 days so the 0DTE bucket gets
enough labeled samples to train. It reuses the full pattern-miner pipeline
(same preprocessing, feature set, walk-forward CV, save_model) so the resulting
artifacts are byte-compatible with SWING/POSITION (106 features + categorical_mappings).

Usage (from any Empire/Orion checkout):
    HEBER_DATA_ROOT=/Volumes/heber/data \\
    DB_URL="$ORION_DB_URL" \\
    uv run python scripts/retrain_0dte.py

Set DB_URL to the TimescaleDB connection string used by the running
orion_timescaledb container (see docker compose env or .env).
"""

import asyncio


async def main() -> None:
    from orion.ml.feature_config import TARGETS
    from orion.ml.model_training import (
        extract_feature_importance,
        prepare_features,
        save_model,
        train_model,
    )
    from orion.ml.training_data import fetch_training_data, prefetch_heber_gold_data

    print("Prefetching Heber gold data...")
    prefetched = await prefetch_heber_gold_data()
    if prefetched is None:
        print("ERROR: Heber gold data unavailable after retries")
        return

    bucket_name = "0DTE"
    bucket_config = {
        "filter": "trade_type = '0DTE'",
        "window_days": 90,
        "min_samples": 50,
        "quick_winner_seconds": 3600,
    }

    targets = list(TARGETS.keys()) + ["quick_winner"]
    results: dict[str, dict] = {}

    for target in targets:
        model_type = f"{bucket_name}_{target}"
        print(f"\n=== Training {model_type} (window={bucket_config['window_days']}d) ===")

        df, feature_names = await fetch_training_data(
            window_days=bucket_config["window_days"],
            min_samples=bucket_config["min_samples"],
            trade_type_filter=bucket_config["filter"],
            quick_winner_seconds=bucket_config["quick_winner_seconds"],
            prefetched=prefetched,
        )

        if df is None or df.empty:
            print("  SKIPPED: no data after merge + filter")
            results[model_type] = {"status": "no_data"}
            continue

        X, feature_names, cat_mappings = prepare_features(df, feature_names)  # noqa: N806
        y = df[f"target_{target}"]

        if y.nunique() < 2:
            print(f"  SKIPPED: target has no variance (pos_rate={y.mean():.3f})")
            results[model_type] = {"status": "no_variance", "samples": len(df)}
            continue

        try:
            dates = df["entry_ts"] if "entry_ts" in df.columns else None
            model, train_auc, holdout_auc = train_model(X, y, dates=dates)
        except Exception as e:
            print(f"  FAILED: {e}")
            results[model_type] = {"status": "train_failed", "error": str(e)}
            continue

        pos_rate = float(y.mean())
        print(f"  samples={len(df)}, pos_rate={pos_rate:.3f}, "
              f"train_auc={train_auc:.3f}, holdout_auc={holdout_auc:.3f}")

        if holdout_auc >= 0.55:
            path = save_model(model, model_type, feature_names, cat_mappings)
            print(f"  SAVED: {path}")
            results[model_type] = {
                "status": "saved",
                "samples": len(df),
                "train_auc": train_auc,
                "holdout_auc": holdout_auc,
                "path": str(path),
            }
        else:
            print(f"  NOT SAVED: AUC {holdout_auc:.3f} below 0.55 threshold")
            results[model_type] = {
                "status": "auc_below_threshold",
                "samples": len(df),
                "train_auc": train_auc,
                "holdout_auc": holdout_auc,
            }

        top = extract_feature_importance(model, feature_names, top_k=5)
        print("  Top 5 features:")
        for f in top:
            print(f"    {f.feature}: {f.importance:.1f}")

    print("\n=== Summary ===")
    for mt, r in results.items():
        print(f"  {mt}: {r}")


if __name__ == "__main__":
    asyncio.run(main())
