# Schema Rollout Runbook

## Exit Classifier Refresh Strategy

Use `ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY` to control how `pattern-miner` refreshes `price_target_labels` schema metadata before training exit classifiers.

Supported values:
- `off` (aliases: `disabled`, `none`, `false`): no forced schema refresh.
- `prefetch_once` (aliases: `once`): force one refresh before bucket loop.
- `per_bucket` (aliases: `each_bucket`, `each`): force refresh before every bucket.

## Which Strategy to Use

Use `prefetch_once` when:
- No schema migration is running during this training window.
- You want lower query overhead.
- You only need one upfront schema cache warm-up.

Use `per_bucket` when:
- A schema rollout is active (new checkpoint/feature columns may appear mid-run).
- You are validating post-migration behavior across all buckets.
- You prefer safety over extra metadata-query cost.

## Recommended Rollout Sequence

1. Before migration window: set `ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY=prefetch_once`.
2. During migration window: set `ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY=per_bucket`.
3. After migration stabilizes: return to `prefetch_once` or `off`.

## Verification

Check logs for:
- `exit_training_schema_forced_refresh` (forced refresh executed).
- `exit_training_schema_refresh_strategy_invalid` (bad strategy value; fallback applied).

If strategy env is invalid, runtime falls back to legacy flags:
- `ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH`
- `ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET`
