# Archive Batch: Runtime Consolidation Wave 6

Date: 2026-02-06

This archive stores the queue-driven execution path that is not wired into the current deployed runtime profile.

## Why these files were archived

- `docker-compose.yml` runs `python -m orion.main_execution` for execution.
- `ExecutionService` and `CandidateQueue` were not referenced by active runtime entrypoints.
- Keeping both DB-polling and queue-based execution paths active created split-brain maintenance risk.

## Contents

### `legacy_code/`
- `execution_service.py`: queue-driven execution service implementation.
- `candidate_queue.py`: async singleton queue used by archived execution service.

### `legacy_tests/`
- `test_candidate_queue.py`: tests specific to the archived queue implementation.

## Note

This is a soft archive, not deletion. Files can be referenced during migration cleanup and removed permanently after final sign-off.
