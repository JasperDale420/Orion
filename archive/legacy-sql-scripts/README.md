# Archived Legacy SQL Scripts

These scripts are archived because they depend on Orion-local legacy SQL tables and are not part of the current Heber-first migration path.

Archived items:
- `backfill_ml_features.py`: legacy standalone script superseded by `python -m orion.jobs.backfill_ml_features`.
- `analyze_todays_flow.py`: one-off local `silver_uw_flow`/`price_target_labels` analysis script.
- `backtest_exit_strategies.py`: local-table backtest helper over `price_target_labels` + `silver_uw_flow`.
- `refetch_alpaca_bars.py`: local `silver_alpaca_bars` repair utility for legacy bar store.
- `reprocess_bronze_flow.py`: local bronze->silver reprocessor for legacy `silver_uw_flow` normalization.

Do not add new dependencies to archived scripts.
