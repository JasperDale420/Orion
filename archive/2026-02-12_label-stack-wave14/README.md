# Label Stack Wave 14 Archive (2026-02-12)

## Archived in this wave

- `legacy_code/backfill_exit_columns.py`
- `legacy_tests/test_backfill_exit_columns_selection.py`

## Why this was archived

- `backfill_exit_columns` was effectively decommissioned in active runtime: local writes were already disabled and the job no longer contributes to centralized Heber storage.
- `nightly_backfill` now runs only ML-feature backfill, removing the obsolete checkpoint backfill stage.
- Keeping the old implementation in active paths added maintenance cost without runtime value.

## Operational note

- Legacy watermark cleanup still includes old `backfill_exit_columns.*` cursor keys so stale state can be removed from databases.
