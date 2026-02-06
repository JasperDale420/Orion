# Runner Debt Archive Wave 9 (2026-02-06)

This archive wave captures deprecated strategist-runner code that no longer participates in active Orion runtime flows.

## Archived Files

- `legacy_code/run_agent.py`
  - Status before archival: deprecated stub with no active execution logic and explicit warning to use `main_execution.py`.
- `legacy_code/paper_live_harness.py`
  - Status before archival: legacy smoke harness coupled to deprecated `run_agent` module and non-existent strategist path.

## Why Archived

- No active docker-compose service or runtime path invokes these files.
- `run_agent.py` no longer performs strategy execution.
- `paper_live_harness.py` depended on deprecated/broken runner assumptions.

## Restore

If needed for historical analysis:

1. Move files from this archive folder back into `src/orion/`.
2. Reconcile with current execution architecture centered on `orion.main_execution`.
