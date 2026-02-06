# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

- **Gateway/Heber Parity Audit (Pass 1)**: Added a migration-focused audit document at `docs/ORION_GATEWAY_HEBER_PARITY_AUDIT_2026-02-05.md`
  - Includes integration gap analysis against `../Data-Gateway` and `../Heber`
  - Includes technical debt backlog and keep/migrate/dispose framing for features and labels
  - Includes migration sequence and open architecture decisions
- **HeberReader Contract Tests**: Added `tests/unit/test_heber_reader.py` to cover:
  - Catalog health endpoint contract (`/health`)
  - Silver parquet reads with instrument/as-of filtering
  - Gold parquet reads with symbol/as-of filtering
- **Parity Matrix Extension**: Expanded `docs/ORION_GATEWAY_HEBER_PARITY_AUDIT_2026-02-05.md` with:
  - Column-level labels parity table (Orion vs Heber)
  - Column-level features parity table (Orion vs Heber)
  - Explicit keep/migrate/dispose decisions per feature/label family
- **Gateway Stream Contract Tests**: Added `tests/connectors/test_gateway_stream_client.py` to validate:
  - Gateway `type=data` + `envelope` + `data` bar message parsing
  - Invalid bar rejection behavior
  - Pre-connect subscription queue behavior
- **Gateway/Heber Config Mapping Tests**: Added coverage in `tests/unit/test_config_centralization.py` for:
  - `DATA_GATEWAY_*` env mappings
  - legacy `GATEWAY_*` alias compatibility
  - `HEBER_*` env mappings into centralized settings
- **Heber Labeler Migration Tests**: Added `tests/unit/test_main_labeler_heber_migration.py` covering:
  - Heber flow payload normalization into labeler records
  - Alias-field handling for mixed flow schemas
- **Heber Feature-Enrichment Source Tests**: Added `tests/unit/test_feature_enrichment_heber_source.py` for:
  - Top-ticker extraction from Heber flow frames
  - Recent-window filtering behavior
  - Graceful handling when expected columns are missing
- **Gateway/Heber Parity Audit (Pass 3)**: Extended `docs/ORION_GATEWAY_HEBER_PARITY_AUDIT_2026-02-05.md` with:
  - 2026-02-06 status updates for completed migration items
  - Current SQL-coupling technical-debt counts and highest-concentration files
  - Remaining high-priority integration gaps and wave-2 archive candidates
- **Gateway/Heber Parity Audit (Pass 4)**: Added deep audit coverage for:
  - `main_price_target_labeler`, `ml/flow_enricher`, and SQL-coupled backfill/validation jobs
  - Severity-ranked findings including a concrete backfill runtime bug and train/inference feature-semantics drift
  - Module-by-module migration readiness and updated P0/P1/P2 backlog
- **Backfill Signature Regression Test**: Added `tests/unit/test_backfill_ml_features_signature.py` to enforce the `get_sector_correlation_features(ticker, entry_ts)` call contract.
- **Gateway/Heber Parity Audit (Pass 5)**: Continued audit with:
  - validation of the backfill runtime bug fix
  - SQL portability debt findings (`date_trunc`, Postgres casts/operators) in migration-critical modules
- **Gateway/Heber Parity Audit (Pass 6)**: Added function-level migration map for `main_price_target_labeler`:
  - source-by-source migration targets into Heber datasets
  - safe slice order for incremental migration
  - explicit parity gates before additional archival
- **Gateway/Heber Parity Audit (Pass 7)**: Continued audit with:
  - active-service keep/migrate/retire matrix from `docker-compose` runtime wiring
  - darkpool contract-drift finding (`darkpool` vs `darkpool_trades`) across Data Gateway, Heber, and Orion reader paths
  - ML dependency mapping for `pattern_miner`, `exit_classifier`, and `nightly_backfill`
  - refreshed SQL-coupling hotspot heatmap and updated archival readiness wave
- **Gateway/Heber Parity Audit (Pass 8)**: Continued audit with:
  - deep review of `validate_features`, `data_quality_checker`, and `window_feature_job` migration readiness
  - schema/column contract mismatch mapping between legacy Orion SQL assumptions and Heber Silver canon
  - feature-lineage drift finding in validation mapping vs current checkpoint quote source usage
  - DST scheduling-risk finding in fixed-offset market-time logic
- **Gateway/Heber Parity Audit (Pass 9)**: Continued audit with:
  - live-pipeline gap finding: current runtime wiring does not produce UW-flow signals required by active flow rules
  - deployment drift finding: compose profile lacks ingestion service entry despite ingestion-based assumptions
  - dual-write debt finding: Data Gateway pulls are persisted back into Orion-local silver tables
  - auth-contract mismatch finding in `sync_earnings` (`Authorization` token client vs Gateway `X-Gateway-Key` requirement)
- **Gateway/Heber Parity Audit (Pass 10)**: Continued audit with:
  - execution path split-brain finding (`main_execution.py` vs `execution/service.py`) and active deployment path verification
  - ML prefilter candidate-contract mismatch finding (nullable option fields vs required scorer inputs)
  - inference enrichment coupling finding (`flow_enricher` still bound to Orion-local `silver_*`/`gold_feature_windows`)
  - inactive ML flow processor wiring review and archive/consolidation guidance
- **Gateway/Heber Parity Audit (Pass 11)**: Continued audit with:
  - runtime entrypoint drift findings across execution queue path, rollup generation, and compose wiring
  - changelog-to-code mismatch finding for execution consolidation claims vs current deployed codepath
  - labeling-stack fragmentation analysis (`flow_labels`, `price_target_labels`, and PRD 6.3 label jobs) with archive-wave guidance

### Changed

- **Legacy UW/Main-Ingest Archival**: Archived inactive pre-migration code, tests, and scripts under `archive/2026-02-05_gateway-heber-migration/`
  - Archived deprecated ingestion/UW connector implementations to `archive/.../legacy_code/`
  - Archived legacy tests coupled to removed modules (`orion.main_ingest`, `orion.connectors.uw_flow_connector`) to `archive/.../legacy_tests/`
  - Archived legacy UW backfill scripts to `archive/.../legacy_scripts/`
  - Added archive manifest: `archive/2026-02-05_gateway-heber-migration/README.md`
- **HeberReader Data Access Path**: Replaced unsupported HTTP reads (`/silver/read`, `/gold/read`) with Heber-compatible access:
  - Silver and Gold reads now use Heber parquet layout from `HEBER_DATA_ROOT`
  - Catalog calls limited to supported endpoints (for example `/health`, `/datasets`)
- **GatewayStreamClient Message Handling**:
  - Added support for Data Gateway websocket payload shape (`type=data`, `feed=bars`, `envelope`, `data`)
  - Uses envelope-provided `event_id` when present for idempotency parity
  - Normalizes `symbol`/`ticker` keys into payload for downstream Alpaca bar normalization
  - Queues subscriptions requested before websocket connection and flushes them on startup
- **Centralized Gateway/Heber Runtime Config**:
  - Added `system_settings` fields for `data_gateway_url`, `data_gateway_api_key`, `heber_catalog_url`, `heber_data_root`, and `orion_use_gateway`
  - Added backward-compatible alias support (`GATEWAY_*` -> `DATA_GATEWAY_*`)
  - Refactored Gateway/Heber callers to use centralized config (`gateway_stream_client`, `heber_reader`, UW enrichment connectors, `sync_earnings`, `main_feature_enrichment`)
  - Removed hardcoded default Gateway API key fallback in UW connectors
- **Main Labeler Data Source**:
  - Migrated `main_labeler.py` read path from local `silver_uw_flow` SQL queries to Heber-backed `HeberReader.read_flow(...)`
  - Migrated price lookup for label horizons to Heber bars (`HeberReader.read_bars(...)`)
  - Kept `flow_labels` persistence in local Orion DB for compatibility during transition
- **Feature Enrichment Active-Ticker Discovery**:
  - Updated `main_feature_enrichment.py` to source active tickers from Heber flow data first
  - Retained local SQL discovery as a fallback path for operational safety

### Fixed

- **Alpaca Connection Limit**: Fixed `connection limit exceeded` error by migrating `AlpacaStreamConnector` to use Data Gateway's WebSocket multiplexer
  - New `GatewayStreamClient` connects to Gateway's `/ws` endpoint instead of directly to Alpaca
  - `ORION_USE_GATEWAY=true` (default) routes all streaming through Gateway
  - Eliminates competing WebSocket connections that exceed Algo Trader Pro's 1-connection limit
- **Backfill Runtime TypeError**: Fixed wrong-arity call in `backfill_ml_features.py` by updating `get_sector_correlation_features` invocation to match the two-argument function signature.

### Added

- **Exit Classifier Window Features**: Added 10 window features from `gold_feature_windows` to exit classifier training
  - `call_put_imbalance`, `sweep_ratio`, `flow_count` for 1h/1d/1w periods
  - `dp_volume_1d`, `call_put_ratio_1d/1w` for dark pool context
  - Uses LATERAL JOIN to look up historical window features at entry time
- **Checkpoint Greeks Infrastructure**: Modified labeler to fetch Greeks from Alpaca at each checkpoint
  - `get_real_checkpoint_prices` now returns delta, gamma, theta, vega, iv per checkpoint
  - Greeks added to label dict in `build_label` function
  - Note: INSERT statement update for persistence is a follow-up task

- **Quant Audit Phase 2 Remediation**: Comprehensive risk and ML fixes
  - **Projected Gamma Check**: `_check_greeks_limits` now uses projected gamma (current + trade) instead of just current
  - **Vega Exposure Limits**: New `max_portfolio_vega` (200) and `max_position_vega` (50) in `RiskSettings`
  - **check_options_order** now accepts `vega` parameter for comprehensive Greeks checking
  - **portfolio_vega** tracking in RiskManager for IV exposure monitoring
  - **Heuristic Score Cap**: Fallback scorer capped at 0.50 to prevent untrained buckets generating live signals
  - **Model Freshness Validation**: `ORION_MAX_MODEL_AGE_DAYS` env var (default 14) - stale models are skipped
  - **Slippage Tracking**: `process_fill` accepts `expected_price` and logs slippage in basis points
  - New test file: `tests/unit/test_risk_greeks_v2.py` with 12 test cases for Greeks fixes
- **Correlation-Aware Position Sizing**: Reduce position size when correlated with existing holdings
  - New `CorrelationAdjuster` class calculates rolling correlation with portfolio
  - `calculate_size_with_correlation()` async method in RiskManager
  - Auto-wired in `ExecutionEngine.__init__` when enabled
  - Config: `correlation_size_scaling`, `correlation_threshold` (0.70), `correlation_penalty_factor` (0.30)
  - Disabled by default (`ORION_RISK_CORRELATION_SIZE_SCALING=false`) for safe rollout
  - New test file: `tests/unit/test_correlation_adjuster.py` with 10 test cases
  - Full Risk Management section added to README

### Fixed

- **EOD Agent Async Bug**: Fixed missing `await` on `session.execute()` in `performance_tracker.py` `get_daily_accuracy()` and `get_weekly_performance()` functions
- **EOD Agent Proposal Schema**: Fixed LLM prompt to match `ProposalBuilder` validation - changed `solver_mutation` to `solver_edit`, added required `evidence_pointers`, `test_plan` fields
- **EOD Agent FK Constraint**: Fixed `solver_edits` insert by creating Solver stub before edit record
- **ML Scoring Feature Mismatch**: Fixed MLScorer receiving only 2/53 features during inference
  - Created `flow_enricher.py` module with `enrich_flow_for_scoring()` that queries same DB sources as labeler
  - Now populates 21/53 features: GEX, VEX, market tide, IV rank, VIX, regimes, max pain distance
  - Added `score_enriched()` async method to MLScorer for real-time enrichment
  - Updated `main_ingest.py` to use `process_flows_enriched()` for feature parity with training
- **Alpaca Trading Connector**: Added `client_order_id` parameter to `submit_market_order()` to match execution engine calls

### Added

- **Drift-Triggered Pattern Mining**: Retrain ML models when high feature drift detected
  - New `orion/core/drift_trigger.py` module with flag coordination
  - EOD agent sets drift flag when any feature PSI > 0.25
  - Pattern miner checks for drift flag every hour (in addition to Mon/Fri schedule)
  - Immediate model retraining when drift detected
- **Expanded ML Features**: Added 33 new entry-time features to pattern miner
  - Options Greeks: `delta_at_entry`, `gamma_at_entry`, `theta_at_entry`, `vega_at_entry`, `iv_at_entry`, `iv_vs_hv_ratio`
  - Volume/OI: `volume_at_entry`, `open_interest_at_entry`, `rvol_1h`, `rvol_daily`
  - Flow: `ask_side_ratio`, `sweep_ratio_1h`, `same_ticker_premium_1h`
  - Timing: `entry_hour`, `minutes_to_close`, `entry_session`, `entry_day_of_week`
  - Context: `spy_correlation_5d`, `spy_return_1h`, `days_to_earnings`, `sector`
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
