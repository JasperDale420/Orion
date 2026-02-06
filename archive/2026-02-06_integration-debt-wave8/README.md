# Archive Batch: Integration Debt Wave 8

Date: 2026-02-06

This archive stores orphaned integration modules that are not referenced by active Orion runtime entrypoints.

## Why these files were archived

- `uw_ticker_info_connector.py` had no in-repo imports/usages and represented a parallel path to direct UW lookups.
- `backfill_historical_gex.py` was a standalone direct-UW backfill script not wired into compose/services and duplicated newer Gateway-centric enrichment paths.

## Contents

### `legacy_code/`
- `uw_ticker_info_connector.py`
- `backfill_historical_gex.py`

## Note

This is a soft archive. If these capabilities are needed again, restore behind explicit Gateway/Heber contracts and service wiring.
