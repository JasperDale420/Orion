# Label Stack Wave 12 Archive (2026-02-12)

## Archived in this wave

- `legacy_code/window_feature_job.py`
- `legacy_tests/test_window_feature_job_heber_source.py`

## Why this was archived

- `window_feature_job` was not wired into active runtime orchestration (no compose service/import path).
- It wrote local `gold_feature_windows`, which conflicts with Heber-first data centralization.
- Window feature reads in Orion were migrated to direct Heber-derived aggregation in `main_price_target_labeler.get_window_features_at_entry(...)`.

## What stays local in Orion

- Model artifacts in `ORION_MODEL_DIR`
- ML metadata tables (`ml_pattern_insights`, `ml_feature_importance_history`)
