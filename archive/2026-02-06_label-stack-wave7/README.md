# Archive Batch: Label Stack Wave 7

Date: 2026-02-06

This archive stores legacy PRD 6.3 label jobs that are not wired into the active Orion runtime profile.

## Why these files were archived

- No active `docker-compose.yml` service runs these jobs.
- No active runtime module references them directly.
- They create a parallel labeling stack (`candidate_labels`/`labels_window`) that diverges from the currently used `price_target_labels` training path.

## Contents

### `legacy_code/`
- `label_job.py`: candidate-level triple-barrier labeling job for `candidate_labels` / `labels_event`.
- `window_label_job.py`: rollup-window forward-return labeling job for `labels_window`.

## Note

This is a soft archive. If PRD 6.3 label tables are revived, reintroduce these jobs via a single canonical runtime path and Heber-compatible contracts.
