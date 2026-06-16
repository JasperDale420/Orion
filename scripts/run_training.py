"""
Standalone ML training script.

Runs the full pattern mining + exit classifier pipeline locally,
using SQLite for insight persistence and a local model directory.

Output directory honors ORION_MODEL_DIR — set it to the live
`models/` directory (e.g. from a cron job) to retrain the bucket
scorers in place. When unset, defaults to `artifacts/models/`
under the project root for ad-hoc runs.
"""

import asyncio
import os
import sys
from pathlib import Path

DEFAULT_MODEL_DIR = Path(__file__).parent.parent / "artifacts" / "models"
MODEL_DIR = Path(os.environ.get("ORION_MODEL_DIR") or DEFAULT_MODEL_DIR)
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# Re-export so child orion modules see the same value (system_settings
# reads ORION_MODEL_DIR at import time).
os.environ["ORION_MODEL_DIR"] = str(MODEL_DIR)
os.environ.setdefault("HEBER_DATA_ROOT", "/Volumes/heber/data")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def main() -> None:
    from orion.storage.db import init_db
    from orion.ml.pattern_miner import run_all_pattern_mining

    print(f"Model output directory: {MODEL_DIR}")
    print(f"Heber data root: {os.environ['HEBER_DATA_ROOT']}")
    print("Initializing database (in-memory SQLite)...")

    await init_db()

    print("\n" + "=" * 60)
    print("STARTING ML TRAINING RUN")
    print("  16 entry models (4 buckets x 4 targets)")
    print("  4 exit classifiers (1 per bucket)")
    print("=" * 60 + "\n")

    summary = await run_all_pattern_mining()

    print("\n" + "=" * 60)
    print("TRAINING COMPLETE")
    print("=" * 60)
    print(f"Entry models trained: {len(summary.insights)}")
    print(f"Alerts: {len(summary.alerts)}")

    if summary.insights:
        print("\n--- Model Results ---")
        for model_type, insight in sorted(summary.insights.items()):
            status = "PASS" if insight.holdout_auc >= 0.55 else "LOW"
            print(
                f"  [{status}] {model_type}: "
                f"AUC={insight.holdout_auc:.3f} "
                f"(train={insight.train_auc:.3f}) "
                f"samples={insight.sample_size} "
                f"pos_rate={insight.positive_rate:.1%}"
            )

    if summary.alerts:
        print("\n--- Alerts ---")
        for alert in summary.alerts:
            print(f"  ! {alert}")

    # List saved model files
    model_files = list(MODEL_DIR.glob("*.pkl"))
    if model_files:
        print(f"\n--- Saved Models ({len(model_files)} files) ---")
        for f in sorted(model_files):
            size_kb = f.stat().st_size / 1024
            print(f"  {f.name} ({size_kb:.1f} KB)")

    print(f"\nModels saved to: {MODEL_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
