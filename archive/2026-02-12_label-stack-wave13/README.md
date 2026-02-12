# Label Stack Wave 13 Archive (2026-02-12)

## Archived in this wave

- `legacy_code/main_labeler.py`
- `legacy_tests/test_main_labeler_heber_migration.py`

## Why this was archived

- `main_labeler` only produced local `flow_labels`, which is superseded by Heber-centered labeling datasets.
- The service had no active non-legacy runtime dependencies and is now removed from compose orchestration.
- Decommissioning this path reduces Orion-local SQL coupling without affecting model artifact retention.

## What remains active

- `main_price_target_labeler` helper functions still used by flow enrichment and backfill logic.
- Local model retention paths:
  - `ORION_MODEL_DIR`
  - `ml_pattern_insights`
  - `ml_feature_importance_history`
