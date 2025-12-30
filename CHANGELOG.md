# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- Added `ensemble_consensus_threshold` configuration to `SystemSettings` (default 0.5) for configurable signal decision threshold
- Added singleton pattern to `CircuitBreaker` class for more efficient instantiation
- **Logic Audit P2-L1**: Added `_hydrated` flag to `FeatureEngine` to warn when `process_alpaca_bars()` is called before `hydrate_history()` - prevents silent cold-start indicator degradation
- **M5: Atomic fill processing** - Implemented DB-backed idempotency for fill processing to enable multi-instance deployments:
  - `_record_fill_in_db()` uses atomic INSERT with ON CONFLICT DO NOTHING
  - `_is_fill_processed_in_db()` checks DB source of truth
  - `_load_processed_fills_from_db()` pre-warms cache on startup (last 30 days)
  - In-memory cache used as fast-path optimization, DB is source of truth

### Changed
- `SignalEngine.decide()` now uses configurable consensus threshold from `system_settings.ensemble_consensus_threshold` instead of hardcoded 0.5
- **M1 Consolidation**: `main_execution.py` is now a thin wrapper (38 lines) that delegates to `ExecutionService.run()` - all execution logic consolidated in single source of truth
- `ExecutionService._save_decision()` now handles full signal persistence (SignalLive + TradeJournalEntry) for EXECUTE decisions

### Fixed
- Fixed `_calculate_projected_exposure` return type annotation from `Tuple[float, float, float]` to `Tuple[float, float]` to match actual 2-value return in `risk_manager.py`
- Fixed `check_status` inner function return type from `None` to `bool` in `circuit_breaker.py`
- Fixed `fetch_state` inner function return type from `None` to `dict[str, Any]` in `circuit_breaker.py`
- Fixed `pending_orders` type hint to use standard `Tuple[str, float]` instead of lowercase `tuple` in `risk_manager.py`
- **Meta Learning Fixes**:
  - Added missing `_log_experiment()` method to `MetaSearchAgent` (critical - was blocking evolution cycle)
  - Fixed `_load_context()` referencing `self.store` instead of `self.vector_store`
  - Fixed `_fetch_silver_events()` defining but never calling `fetch_bars_and_flow`
  - Removed duplicate validation check in `SolverRiskConfig.check_global_limits()`
  - Fixed deprecated `datetime.utcnow()` usage in `SolverEdit.created_at_utc`
- **Processing Test Fixes**:
  - Rewrote `test_signal_engine.py` to test current async `SignalEngine.decide()` API with proper mocking
  - Fixed `test_normalizer.py` key assertions: `call_put` → `put_call`, `flags` → `flags_json`
- **Jobs Module Fixes**:
  - Fixed SQL `is None` → `.is_(None)` in `label_job.py` (was never matching rows)
  - Added missing `db_write(_process_and_save)` call in `label_job.py`
  - Fixed deprecated `datetime.utcnow()` → `datetime.now(timezone.utc)` in `seed_solvers.py`
  - Fixed deprecated `asyncio.get_event_loop()` → `asyncio.run()` in `dlq_consumer.py`
- **Connector Async Fixes**:
  - Fixed blocking `time.sleep()` → `await asyncio.sleep()` in `uw_alerts_connector.py` and `uw_darkpool_connector.py`
- **Parallel Processing Fixes**:
  - Fixed fire-and-forget `asyncio.create_task()` → `asyncio.ensure_future()` in `feature_engine.py`
  - Fixed deprecated `datetime.utcnow()` → `datetime.now(timezone.utc)` in `main_ingest.py`, `models_risk.py`, `bar_gap_scan.py`
- **Performance Audit Fixes**:
  - Added connection pool config (`pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`) to `db.py`
- **Memory Audit Fixes**:
  - Added `BoundedSet` class for `processed_fill_ids` in `risk_manager.py` (max 10000 entries)
  - Added `flow_max_size_per_ticker` cap (500) in `feature_engine.py` to prevent memory growth

### Removed
- Removed unused `get_pending_candidates()` function from `main_execution.py` that referenced non-existent columns
- Removed unused `update_candidate_status()` function from `main_execution.py`
- Removed unused `_persist_signal_live()` method from `ExecutionService` in `service.py`
- Removed unused `_persist_trade_journal()` method from `ExecutionService` in `service.py`
- Removed obsolete comments and unused imports (`datetime`, `timezone`)
- Removed duplicate execution logic from `main_execution.py` (~250 lines of code eliminated)
