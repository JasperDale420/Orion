# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Fixed
- **Trade Execution Flow**: Complete end-to-end execution pipeline from ML candidates to broker orders
  - Fixed `SignalEngine` to fetch current price and set `limit_price` in execution params
  - Fixed `RiskSettings` to use percentage-based limits instead of fixed USD amounts
    - `max_order_size_pct`: 5% of account equity (was fixed $5,000)
    - `max_ticker_exposure_pct`: 10% of account equity (was fixed $10,000)
  - Fixed `risk_manager.calculate_size()` to cap position size at max_order_size_pct
  - Fixed `ExecutionEngine` side parameter conversion (`side.value` instead of `str(side)`)
  - Fixed `TradeJournalEntry` field names to match model schema (`decision_id` not `journal_id`)
  - Disabled rollup requirement for testing via `ORION_REQUIRE_ROLLUPS_FOR_SIGNALS_LIVE=false`

### Added
- **Options Trading Pipeline**: Trade options contracts instead of equities based on UW flow signals
  - New `CandidateTrade` fields: `option_symbol`, `strike_price`, `expiration_date`, `option_type`, `underlying_price`, `premium`
  - New `AlpacaOptionsConnector` with `submit_option_order()`, `get_option_quote()`, OCC symbol generation
  - `ExecutionEngine` routes to options path when `candidate.option_symbol` is present
  - Premium-based sizing: max 2% of equity per options trade (`max_option_premium_pct`)
  - DTE minimum check (3 days by default) prevents trading very short-dated options
  - New risk settings: `max_option_premium_pct`, `min_dte`, `max_option_positions`

- **Weekly Meta Agent Evolution**: Friday EOD comprehensive analysis and solver evolution
  - New `WeeklyDataAggregator` class aggregates EOD reports, trade data, and ML insights
  - `run_weekly_evolution()` method analyzes execution quality, ML drift, and generates mutations
  - Execution quality analysis: fill rate, rejection rate, health classification
  - ML drift detection: tracks AUC trends across model buckets
  - Automated solver mutation proposals based on top-performing features
  - New `main_meta_weekly.py` CLI with `--dry-run` and `--scheduled` modes
  - Scheduled Friday 5:30 PM EST via `meta-weekly` docker-compose service

- **Expanded ML Targets**: 4 targets for multi-dimensional trade scoring
  - `hit_target_50`: 50% profit before 20% stop (original)
  - `avoid_stop`: Avoid 20% stop entirely (original)
  - `hit_target_100`: High conviction runner - 100% profit before stop
  - `quick_winner`: Fast exit - 50% profit within 1 hour
  - New `MultiTargetScorer` class with `score_all()`, `get_composite_score()`, and `get_trade_signal()`
  - 4 buckets × 4 targets = 16 models (up from 8)

- **Bucket-Specific Exit Classifiers**: ML-based exit timing for each trade bucket
  - 0DTE: Checkpoints at 5m, 10m, 15m, 30m, 1h, EOD (AUC=0.935)
  - SHORT_SWING: Checkpoints at 30m, 1h, 2h, 4h, 8h, EOD (AUC=0.896)
  - SWING: Checkpoints at 1h, 4h, 8h, EOD, 1d, 2d, 1w, 2w (AUC=0.904)
  - POSITION: Checkpoints at 1d, 2d, 3d, 1w, 2w, 3w, 4w (AUC=0.955)
  - DB columns added: price_at_2w/3w/4w, return_at_2w/3w/4w
  - **Greeks at all checkpoints**: delta, gamma, theta, vega, IV fetched from Alpaca
  - **Time decay features**: DTE, theta_decay_pct, time_value_pct at each checkpoint
  - Bucket-aware heuristic fallbacks with tuned take-profit/stop-loss thresholds
  - Training target: "Did exiting at checkpoint capture ≥80% of max return?"

- **Position Monitor & Exit Execution**: Automated position management
  - `PositionMonitor` class syncs with Alpaca broker positions
  - Tracks max return, max drawdown for trailing stop logic
  - Evaluates ML exit classifier for each position
  - `AlpacaTradingConnector` extended with `get_all_positions()`, `close_position()`
  - `main_position_monitor.py` CLI with `--interval`, `--dry-run`, `--once` modes
  - `position-monitor` docker-compose service (60s check interval)

- **ML Model Persistence Pipeline**: End-to-end wiring of ML models for live trading
  - `pattern_miner.py` now saves trained models to `/app/models/{bucket}_{target}.pkl`
  - Models saved with metadata (feature names, creation timestamp, model type)
  - Conditional save: only persists models with holdout AUC >= 0.55
  - `scorer.py` rewritten to load bucket-specific models (0DTE, SHORT_SWING, SWING, POSITION)
  - Automatic bucket classification based on DTE for flow scoring
  - Graceful fallback to heuristic scorer when no model available
- **EOD Agent Service**: New `eod-agent` docker service that runs daily after market close
  - `main_eod.py` entry point with `MarketSchedule` integration (waits for 16:30 ET)
  - Mounts `~/.codex` for credentials passthrough
- **Codex CLI Client**: `codex_client.py` async wrapper for headless `codex exec` calls
  - `run_codex_completion()` for subprocess-based LLM execution
  - `build_chat_prompt()` and `extract_json_from_response()` helpers
- **Reasoning Level Config**: `ORION_REASONING_LEVEL` env var (default: `extra_high`)
- **ML Pattern Mining Layer**: Automated LightGBM-based pattern discovery
  - `src/orion/ml/pattern_miner.py` - trains on `price_target_labels`, extracts decision tree rules
  - `src/orion/ml/schemas.py` - Pydantic models for pattern insights
  - `src/orion/storage/models_ml.py` - Database tables (`ml_pattern_insights`, `ml_feature_importance_history`)
  - **8 models**: 4 trade buckets (0DTE, SHORT_SWING, SWING, POSITION) × 2 targets (hit_target_50, avoid_stop)
  - Bucket-specific lookback windows: 10 days (0DTE) to 90 days (POSITION)
  - `pattern-miner` docker service runs Mon + Fri after market close
  - EOD agent prompt updated to interpret ML insights (AUC scores, top rules, feature importance)
- **EOD → MetaAgent Solver Generation Pipeline**: Automated solver mutation and creation
  - EOD agent can now propose `solver_mutation` type with structured ops (modify_param, add_rule, toggle_feature)
  - `refine_and_promote()` method: iteratively backtests solver, sends results to MetaAgent for refinement until score threshold met
  - Auto-promotes to paper trading when `composite_score >= 0.5` (max 3 refinement iterations)
  - New solvers in `research` stage stay there if refinement fails; successful ones go directly to `paper`
  - Lineage tracked in `solver_edits` table for evolutionary tracing
- **Multi-Axis Regime System**: PRD Regime Upgrade implementation
  - 5 regime enums: `TrendRegime`, `VolRegime`, `RiskRegime`, `SessionRegime`, `VIXRegime`
  - `MultiAxisRegimeDetector` class with Market Tide integration for risk scoring
  - `RegimeRiskManager` with position sizing multipliers per regime axis
  - `silver_vix_data` and `silver_regime_history` tables
  - `vix_connector.py` for VIX/VVIX data ingestion
  - SignalEngine blocks trading during SHOCK regime
  - 6 new regime columns in `price_target_labels` for ML features
- **UW Feature Endpoints**: Market context enrichment for ML
  - 4 new silver tables: `silver_greek_exposure`, `silver_market_tide`, `silver_max_pain`, `silver_iv_rank`
  - 4 new connectors: GEX/Vanna, Market Tide, Max Pain, IV Rank
  - `feature_enrichment` service polls endpoints at configured intervals
  - 7 new entry feature columns in `price_target_labels` (GEX, Tide, Max Pain, IV Rank)
- **ML Feature Validation System**: Comprehensive audit tooling for all 130+ features
  - `src/orion/jobs/validate_features.py` - 3 validation modes (spot-check, sanity, audit-sources)
  - `FEATURE_SOURCE_MAPPING` documents all 60+ features and their source tables
  - Data source audit covers all 8 silver tables with gap detection
  - 7 automated sanity checks (Greeks ranges, time features, volume constraints)
- **Enhanced Backfill Job**: `backfill_ml_features.py` now populates 50+ features
  - Darkpool metrics (9 window sizes: 15m, 30m, 1h, 4h, 1d, 3d, 1w, 2w, 4w)
  - RVOL metrics (5 windows: 30m, 1h, daily, 3d, weekly)
  - Flow aggression (ask_side_ratio, sweep_ratio_1h, same_ticker_premium_1h)
  - Regime features (trend, vol, risk, session, VIX regimes)
  - Market tide and institutional flow

### Changed
- Reduced ingestion polling interval from 5 minutes to 1 minute for more responsive data capture
- Configured UW connectors (flow, darkpool, alerts) with explicit 5-minute lookback on cold start (darkpool API has max 200 items per request)

### Added
- Added `ensemble_consensus_threshold` configuration to `SystemSettings` (default 0.5) for configurable signal decision threshold
- Added singleton pattern to `CircuitBreaker` class for more efficient instantiation
- **Dynamic Exit Strategies**: Implemented 6 flow-based exit rules per PDF spec
  - `PositionManager` class tracks open positions with entry context (IV, premium, sweep count)
  - Exit rules: SentimentReversal, NetPremiumDecline, VolumeOIDivergence, WaningMomentum, IVContraction, OpposingClusters
  - `close_position()` method in ExecutionEngine for exit order execution
  - `ExitDecision` table for tracking exit triggers
  - Backtest shows 96.5% exit rate, avg 3.3 min to exit on historical data
- **Logic Audit P2-L1**: Added `_hydrated` flag to `FeatureEngine` to warn when `process_alpaca_bars()` is called before `hydrate_history()` - prevents silent cold-start indicator degradation
- **Contract Tests P1-C1**: Added 9 new API endpoint tests covering `/promotions`, `/search`, `/rollups`, and `/flows` - API test coverage now comprehensive
- **Dead Code P2-DC2**: Updated `.gitignore` to exclude debug output files (`*.txt`, `coverage.json`) and removed 8 tracked debug files from repository
- **M5: Atomic fill processing** - Implemented DB-backed idempotency for fill processing to enable multi-instance deployments:
  - `_record_fill_in_db()` uses atomic INSERT with ON CONFLICT DO NOTHING
  - `_is_fill_processed_in_db()` checks DB source of truth
  - `_load_processed_fills_from_db()` pre-warms cache on startup (last 30 days)
  - In-memory cache used as fast-path optimization, DB is source of truth
- **0DTE Entry Signal**: `ZeroDTESweepRule` based on price target analysis
  - Criteria: DTE=0, sweep, $100-150K premium, ASK aggressor
  - Confidence boost for puts (0% historical stop rate) and market open hour
  - 50% profit target, 20% stop loss
- **Price Target Labeling System**: Tracks option price over time
  - `price_target_labels` table: max return, drawdown, target hit times
  - `main_price_target_labeler.py` service for continuous labeling
  - Tracks 50%, 75%, 100%, 150% profit targets and 20% stop loss
- **PriceTargetExitRule**: Exit rule for profit target/stop loss based exits

### Changed
- `SignalEngine.decide()` now uses configurable consensus threshold from `system_settings.ensemble_consensus_threshold` instead of hardcoded 0.5
- **M1 Consolidation**: `main_execution.py` is now a thin wrapper (38 lines) that delegates to `ExecutionService.run()` - all execution logic consolidated in single source of truth
- `ExecutionService._save_decision()` now handles full signal persistence (SignalLive + TradeJournalEntry) for EXECUTE decisions

### Fixed
- Fixed `overnight_gap_pct` calculation to correctly find prior trading day close (handles weekends/holidays)
- Fixed `vwap_distance_pct` calculation to use bar closest to entry timestamp instead of day's open
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
