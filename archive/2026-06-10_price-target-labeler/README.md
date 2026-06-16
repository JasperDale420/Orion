# Price Target Labeler Archive (2026-06-10)

## Archived in this wave

- `legacy_code/main_price_target_labeler.py` (3,650 lines)

## Why this was archived

- The price-target labeling pipeline (`label_entry`, `run_labeling_loop`,
  `persist_labels`, `get_checkpoint_greeks`, entry-signal and checkpoint-price
  fetchers, `main`) is a deprecated, non-functional pipeline gated behind the
  `ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER` legacy flag. It is no longer wired
  into active runtime — the Docker `price_target_labeler` service block and its
  `depends_on` references were removed from `docker-compose.yml` in the same
  audit.
- Zero tests exercised this module directly.

## What was preserved before archiving

The module also held the live point-in-time feature-extraction functions shared
by ML scoring (`orion.ml.flow_enricher`) and historical backfill
(`orion.jobs.backfill_ml_features`). Those were extracted **first**, into
`src/orion/labeler/feature_extraction.py`, as a mechanical move that preserves
function signatures and the async/sync dual-path behavior exactly.

The 21 extracted public functions:

- GEX/VEX: `get_gex_at_entry`, `get_gex_rolling_averages`
- Window/flow: `get_window_features_at_entry`, `get_flow_aggression`,
  `get_institutional_flow_1w`
- Market tide: `get_market_tide_before_entry`
- Max pain / IV: `get_max_pain_distance`, `get_iv_rank_at_entry`
- Regime / timing: `get_regime_at_entry`, `get_entry_time_features`
- Underlying price: `get_underlying_price_at_entry`,
  `get_underlying_price_at_offset`
- Greeks / phases: `get_flow_greeks`, `get_p2_features`, `get_p3_features`,
  `get_phase1_bucket_features`
- Darkpool / rvol: `get_darkpool_metrics`, `get_rvol_metrics`
- Sector / earnings / ticker: `get_sector_correlation_features`,
  `get_earnings_proximity`, `get_ticker_info`

plus all their `_*_from_heber` sync helpers, coercion utilities, the UW-client
accessor, and shared module state (`_heber_reader`, fallback counters, ticker
cache).

## Importers repointed

- `src/orion/ml/flow_enricher.py` — now imports from
  `orion.labeler.feature_extraction`. The public
  `enrich_flow_for_scoring(...)` signature and its module path
  (`orion.ml.flow_enricher`) were **not** changed; `orion.execution.position_monitor`
  needs zero changes.
- `src/orion/jobs/backfill_ml_features.py` — now imports from
  `orion.labeler.feature_extraction`.

## Note

The archived file still contains its own self-contained copies of the extracted
functions (they were copied, not cut, since the whole module is archived as a
unit). It remains importable in isolation but is not referenced by any live code.
