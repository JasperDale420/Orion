"""Train exit classifiers only (entry models already trained)."""

import asyncio
import os
import sys
from pathlib import Path

MODEL_DIR = Path(__file__).parent.parent / "artifacts" / "models"
os.environ.setdefault("ORION_MODEL_DIR", str(MODEL_DIR))
os.environ.setdefault("HEBER_DATA_ROOT", "/Volumes/heber/data")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def main() -> None:
    from orion.storage.db import init_db
    from orion.ml.exit_classifier import train_all_exit_classifiers

    await init_db()

    print("=" * 60)
    print("TRAINING EXIT CLASSIFIERS (4 buckets)")
    print("=" * 60 + "\n")

    results = await train_all_exit_classifiers()

    print("\n" + "=" * 60)
    print("EXIT CLASSIFIER RESULTS")
    print("=" * 60)

    for bucket, data in sorted(results.items()):
        auc = data.get("auc", 0)
        samples = data.get("samples", 0)
        status = "PASS" if auc >= 0.55 else "LOW"
        print(f"  [{status}] {bucket}_exit: AUC={auc:.3f} samples={samples}")

    model_files = list(MODEL_DIR.glob("*_exit.pkl"))
    if model_files:
        print(f"\n--- Saved Exit Models ({len(model_files)} files) ---")
        for f in sorted(model_files):
            size_kb = f.stat().st_size / 1024
            print(f"  {f.name} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    asyncio.run(main())
