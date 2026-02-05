# Archive Batch: Gateway/Heber Migration Legacy Cleanup

Date: 2026-02-05

This archive stores Orion code that is no longer aligned with the active migration path (Data Gateway + Heber).

## Why these files were archived

- They depend on removed module paths (`orion.main_ingest`, `orion.connectors.uw_flow_connector`, etc.).
- They represent legacy direct-UW polling/ingestion assumptions now replaced by centralized ingestion.
- Keeping them in active paths causes import/test noise and slows migration work.

## Contents

### `legacy_code/`
- Legacy ingestion entrypoint and deprecated UW connector implementations.

### `legacy_tests/`
- Tests coupled to removed legacy modules.
- These tests should be replaced with new contract tests around:
  - `orion.ingestion.service`
  - Gateway WebSocket envelope handling
  - Heber-backed dataset reads

### `legacy_scripts/`
- Scripts importing legacy UW connector modules.
- Backfill/migration scripts should be rebuilt against the new data-access layer.

## Note

This is a soft archive, not deletion. Files can be referenced during parity migration and then permanently removed after signoff.
