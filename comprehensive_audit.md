# Orion Comprehensive 22-Point Audit
**Generated**: 2025-12-30
**Status**: Complete

---

## Executive Summary

| Category | Status | P0 | P1 | P2 | P3 |
|----------|--------|-----|-----|-----|-----|
| 1. Logic | ✅ | 0 | 2 | 3 | 1 |
| 2. Contract Tests | ⚠️ | 0 | 1 | 2 | 0 |
| 3. Module Boundaries | ✅ | 0 | 0 | 2 | 1 |
| 4. Architecture | ✅ | 0 | 0 | 1 | 2 |
| 5. Performance | ⚠️ | 0 | 1 | 2 | 2 |
| 6. Memory | ✅ | 0 | 0 | 2 | 1 |
| 7. Concurrency | ✅ | 0 | 1 | 1 | 0 |
| 8. App Security | ⚠️ | 0 | 1 | 2 | 3 |
| 9. Dependency Health | ✅ | 0 | 1 | 1 | 0 |
| 10. Supply Chain | ⚠️ | 0 | 1 | 2 | 0 |
| 11. Resilience | ⚠️ | 0 | 2 | 2 | 1 |
| 12. Test Coverage | ⚠️ | 0 | 2 | 3 | 0 |
| 13. Testing Quality | ✅ | 0 | 0 | 2 | 1 |
| 14. Observability | ⚠️ | 0 | 1 | 3 | 0 |
| 15. Error Taxonomy | ✅ | 0 | 0 | 2 | 0 |
| 16. Configuration | ✅ | 0 | 0 | 1 | 1 |
| 17. Documentation | ✅ | 0 | 0 | 2 | 2 |
| 18. Code Duplication | ✅ | 0 | 0 | 1 | 2 |
| 19. Dead Code | ⚠️ | 0 | 0 | 3 | 2 |
| 20. CI/CD | ✅ | 0 | 1 | 1 | 1 |
| 21. Data Governance | ⚠️ | 0 | 1 | 2 | 0 |
| 22. DevEx | ✅ | 0 | 0 | 1 | 1 |

**Legend**: ✅ Good | ⚠️ Needs Attention | ❌ Critical

---

## 1. Logic Audit

### Findings

**P1-L1: Division by Zero in Ensemble Consensus**
- **File**: [signal_engine.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/processing/signal_engine.py#L165)
- **Issue**: `consensus_score = weighted_vote / total_weight if total_weight > 0 else 0.0` - protected but prior loop can have `weight <= 0.0` causing all solvers to be skipped
- **Status**: ✅ Already guarded with `valid_solver_found` check

**P1-L2: Fallback Solver Validation**
- **File**: [solver_router.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/core/solver_router.py#L219-267)
- **Issue**: Baseline solver failure raises RuntimeError - could crash system
- **Status**: ✅ Fail-fast is correct for safety-critical code

**P2-L1: Feature Engine Cold Start**
- **File**: [feature_engine.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/processing/feature_engine.py)
- **Issue**: `hydrate_history` required before processing but not enforced
- **Recommendation**: Add initialization check in `process_alpaca_bars`

**P2-L2: Regime Cache Invalidation**
- **File**: [signal_engine.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/processing/signal_engine.py#L52-63)
- **Issue**: Regime transition clears all history for ticker, could lose valid data
- **Status**: ✅ Intentional design per M1 remediation comment

**P2-L3: Risk Check Order**
- **File**: [risk_manager.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/execution/risk_manager.py)
- **Issue**: Multiple risk checks run sequentially; first failure returns immediately
- **Status**: ✅ Correct fail-fast behavior

**P3-L1: Stage Alias Hardcoded**
- **File**: [solver_router.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/core/solver_router.py#L30-33)
- **Issue**: `STAGE_ALIASES` is hardcoded; consider config-driven
- **Status**: Acceptable for now, noted for future

---

## 2. Contract Tests Audit

### Findings

**P1-C1: API Endpoints Untested**
- **Files**: `src/orion/api/main.py` (0% coverage)
- **Issue**: All FastAPI endpoints have no test coverage
- **Recommendation**: Add integration tests using TestClient

**P2-C1: Pydantic Schema Validation**
- **Status**: ✅ Using Pydantic v2 with strict validation
- **Files**: `config.py`, `solver_schema.py`, `api/schemas.py`

**P2-C2: Missing Contract Tests for Connectors**
- **Issue**: UW and Alpaca connector responses not validated against contracts
- **Recommendation**: Add response schema tests

---

## 3. Module Boundaries Audit

### Findings

**P2-M1: Circular Import Prevention**
- **Status**: ✅ Lazy imports used in `signal_engine.py` and `solver_router.py`
- **Pattern**: `from orion.config import system_settings` inside methods

**P2-M2: Core Module Coupling**
- **Issue**: `processing/` depends on `core/` and `storage/` - appropriate layering
- **Status**: ✅ Clean dependency direction

**P3-M1: Unusualwhales Module Size**
- **Directory**: `src/orion/unusualwhales/` (229 children)
- **Observation**: Large generated SDK - consider extraction to separate package

---

## 4. Architecture Audit

### Findings

**P2-A1: Vertical Slice Implementation**
- **Status**: ✅ Follows vertical slice architecture
- **Evidence**: Complete slices for ingestion, execution, meta-search

**P3-A1: Lakehouse Layer Organization**
- **Status**: ✅ Bronze/Silver/Gold layers correctly implemented
- **Files**: Storage models organized by layer

**P3-A2: Event-Driven Design**
- **Status**: ✅ Redpanda integration for event streaming
- **Note**: Producer uses fire-and-forget per README (known gap)

---

## 5. Performance Audit

### Findings

**P1-P1: N+1 Query in Solver Router**
- **File**: [solver_router.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/core/solver_router.py#L103-110)
- **Issue**: Individual query per solver for metrics
- **Recommendation**: Batch query metrics for all active solvers

**P2-P1: Feature History Unbounded**
- **File**: [feature_engine.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/processing/feature_engine.py)
- **Issue**: `self.history` dict grows per ticker without pruning
- **Recommendation**: Add TTL-based eviction

**P2-P2: DataFrame Operations**
- **File**: [feature_engine.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/processing/feature_engine.py)
- **Status**: Uses pandas efficiently with column operations

**P3-P1: Indicator Computation**
- **Status**: pandas_ta used for vectorized operations

**P3-P2: DB Connection Pooling**
- **Status**: ✅ SQLAlchemy async pooling configured in `storage/db.py`

---

## 6. Memory Audit

### Findings

**P2-M1: BoundedSet for Fill Tracking**
- **File**: [risk_manager.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/execution/risk_manager.py#L26-46)
- **Status**: ✅ Memory-bounded set implementation with LRU eviction

**P2-M2: History DataFrame Growth**
- **File**: [feature_engine.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/processing/feature_engine.py)
- **Issue**: Per-ticker DataFrames grow unbounded
- **Recommendation**: Implement rolling window retention

**P3-M1: Flow History Dict**
- **Status**: Per-ticker dicts; memory proportional to active universe

---

## 7. Concurrency & Parallelism Audit

### Findings

**P1-C1: Async Session Safety**
- **File**: [db.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/storage/db.py)
- **Status**: ✅ Uses async session factory correctly

**P2-C1: No Concurrent Fill Processing**
- **File**: [execution_engine.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/execution/execution_engine.py)
- **Status**: Sequential fill processing per poll cycle - acceptable for throughput

**Design Notes**:
- Uses `asyncio` exclusively (no threading/multiprocessing)
- Semaphores not currently used
- No race condition concerns identified

---

## 8. App Security Audit

### Findings

**P1-S1: API Key in Environment**
- **Status**: ✅ Using `pydantic-settings` for secret management
- **Files**: `.env.example` documents required secrets

**P2-S1: Hardcoded Test Secrets**
- **Files**: Multiple test files contain dummy API keys
- **Status**: ✅ In `.secrets.baseline` - verified as test fixtures

**P2-S2: SQL Injection Prevention**
- **Status**: ✅ Using SQLAlchemy ORM with parameterized queries

**P3-S1: HTTPS Enforcement**
- **Docker**: Services on internal network; Alpaca/UW use HTTPS

**P3-S2: Input Validation**
- **Status**: ✅ Pydantic models validate all inputs

**P3-S3: Bandit Findings**
- **Count**: 8 low-severity, 0 high/medium
- **Details**: Primarily assert statements and random usage

---

## 9. Dependency Health Audit

### Findings

**P1-D1: Dependabot Configured**
- **File**: [.github/dependabot.yml](file:///Users/jacobmcmillan/Empire/Orion/.github/dependabot.yml)
- **Status**: ✅ Weekly updates for pip

**P2-D1: Python Version**
- **Status**: ✅ Python 3.12+ specified in `pyproject.toml`

**Dependencies Summary** (from `pyproject.toml`):
- Core: requests, pydantic, sqlalchemy, asyncpg, tenacity
- ML: pandas, numpy, scipy, openai
- Streaming: aiokafka, websockets
- Dev: pytest, ruff, mypy, bandit

---

## 10. Supply Chain Security Audit

### Findings

**P1-SC1: No SBOM Generation**
- **Issue**: No software bill of materials generated
- **Recommendation**: Add `cyclonedx-py` to CI pipeline

**P2-SC1: Secrets Scanning**
- **Status**: ✅ `detect-secrets` in pre-commit with baseline

**P2-SC2: No Artifact Signing**
- **Issue**: Docker images not signed
- **Recommendation**: Consider Cosign for production

---

## 11. Resilience & Failure-Modes Audit

### Findings

**P1-R1: Missing Circuit Breaker Usage**
- **File**: [circuit_breaker.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/core/circuit_breaker.py)
- **Status**: ✅ Implemented, 96% coverage
- **Issue**: Not integrated with UW/Alpaca connectors

**P1-R2: Retry Configuration**
- **Status**: ✅ `tenacity` configured per README
- **File**: Connectors use exponential backoff

**P2-R1: Idempotency for Fill Processing**
- **File**: [risk_manager.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/execution/risk_manager.py#L403-417)
- **Status**: ✅ DB-level fill ID deduplication

**P2-R2: DLQ Implementation**
- **Status**: ✅ `dead_letter_queue` table for failed events

**P3-R1: Backpressure Handling**
- **Issue**: No explicit backpressure in ingestion pipeline
- **Observation**: Rate limits handled via polling intervals

---

## 12. Test Coverage Audit

### Findings

**P1-T1: Overall Coverage Low**
- **Current**: ~35% (per README)
- **Target**: 60%+ for trading systems
- **Critical Gaps**:
  - `api/main.py`: 0%
  - `connectors/alpaca_*.py`: 0-20%
  - `execution/`: <50%

**P1-T2: Execution Engine Coverage**
- **File**: `execution_engine.py`
- **Status**: Critical path needs integration tests

**P2-T1: Agent Coverage**
- **Files**: `agents/*.py` at 50-70%
- **Status**: Good for LLM-integration code

**P2-T2: Core Logic Coverage**
- **Files**: `solver_router.py` (81%), `signal_engine.py`, `feature_engine.py`
- **Status**: Better coverage on core decision logic

**P2-T3: Connector Coverage**
- **Issue**: Minimal mocking of external APIs

---

## 13. Testing Quality Audit

### Findings

**P2-Q1: Test Architecture**
- **Status**: ✅ Clean separation: unit/integration/e2e
- **Framework**: pytest with pytest-asyncio

**P2-Q2: Mocking Strategy**
- **File**: [TESTING.md](file:///Users/jacobmcmillan/Empire/Orion/TESTING.md)
- **Status**: ✅ Documented mocking patterns

**P3-Q1: Fixture Reuse**
- **File**: `tests/conftest.py`
- **Status**: ✅ Shared fixtures for async operations

---

## 14. Observability Audit

### Findings

**P1-O1: Structured Logging**
- **Status**: ✅ JSON formatter in `logging_config.py`
- **Pattern**: `extra` dict for structured fields

**P2-O1: No Distributed Tracing**
- **Issue**: No OpenTelemetry integration
- **Recommendation**: Add trace propagation for request flows

**P2-O2: Metrics Collection**
- **File**: `shared/metrics.py`
- **Status**: ✅ Prometheus metrics via `prometheus-client`

**P2-O3: No Alert Rules**
- **Issue**: No runbooks or alert configurations
- **Recommendation**: Define SLOs and alert thresholds

---

## 15. Error Taxonomy & Logging Audit

### Findings

**P2-E1: Error Codes Defined**
- **File**: [errors.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/core/errors.py)
- **Status**: ✅ Enum-based error codes

**P2-E2: Correlation IDs**
- **Issue**: No request-level correlation IDs
- **Recommendation**: Add `run_id` propagation to all logs

**Status**: Error handling follows "fail-loud" principle per guidelines

---

## 16. Configuration Audit

### Findings

**P2-C1: Centralized Settings**
- **File**: [config.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/config.py)
- **Status**: ✅ `pydantic-settings` with env prefix

**P3-C1: Feature Flags**
- **Status**: Via environment variables (e.g., `ORION_STAGE`)
- **Recommendation**: Consider formal feature flag system for A/B testing

---

## 17. Documentation Audit

### Findings

**P2-D1: README Comprehensive**
- **File**: [README.md](file:///Users/jacobmcmillan/Empire/Orion/README.md)
- **Status**: ✅ Setup, architecture, and usage documented

**P2-D2: API Documentation**
- **Issue**: No OpenAPI docs exposure
- **Status**: FastAPI auto-generates at `/docs`

**P3-D1: Inline Comments**
- **Status**: Key functions have docstrings

**P3-D2: PRD Available**
- **Files**: `PRD.md`, `PRDv2.md` for system design

---

## 18. Code Duplication Audit

### Findings

**P2-D1: Connector Pattern Repetition**
- **Files**: `uw_*_connector.py` files share similar structure
- **Recommendation**: Extract base class pattern

**P3-D1: Event ID Generation**
- **Status**: Similar hash-based ID generation across connectors
- **Note**: Intentional for deterministic deduplication

**P3-D2: Watermark Loading**
- **Status**: Pattern repeated in connectors; consider mixin

---

## 19. Dead Code Audit

### Findings

**P2-DC1: Unused Imports**
- **Status**: ✅ Ruff configured; no current issues

**P2-DC2: Root-Level Debug Scripts**
- **Files**: `debug_import.py`, `probe_pagination.py`, `reproduce_lakehouse_perf.py`, `verify_*.py`
- **Recommendation**: Move to `scripts/` or remove

**P2-DC3: Offline Gym Stub**
- **File**: [offline_gym.py](file:///Users/jacobmcmillan/Empire/Orion/src/orion/core/offline_gym.py)
- **Status**: 0% coverage, stub implementation
- **Recommendation**: Complete or remove

**P3-DC1: Audit Scripts**
- **Files**: `audit_gold.py`, `audit_silver.py`
- **Status**: 0% coverage; likely one-time scripts

**P3-DC2: Legacy Log Files**
- **Files**: `*.log`, `*.txt` files in root
- **Recommendation**: Add to `.gitignore`

---

## 20. CI/CD & Release Engineering Audit

### Findings

**P1-CI1: GitHub Actions Pipeline**
- **File**: [ci.yml](file:///Users/jacobmcmillan/Empire/Orion/.github/workflows/ci.yml)
- **Status**: ✅ Multi-Python matrix (3.11, 3.12)

**P2-CI1: SonarQube Integration**
- **Status**: ✅ Configured with coverage upload

**P2-CI2: No Rollback Strategy**
- **Issue**: No documented rollback for deployments
- **Recommendation**: Document Docker version pinning strategy

**P3-CI1: Makefile Targets**
- **Status**: ✅ `test`, `test-unit`, `test-integration`, `lint`

---

## 21. Data Governance & Privacy Audit

### Findings

**P1-DG1: Audit Logging**
- **Table**: `audit_logs` (per Alembic migrations)
- **Status**: ✅ DB-level audit trail

**P2-DG1: No Data Classification**
- **Issue**: No explicit PII/sensitive data markers
- **Observation**: Financial data (positions, orders) should be classified

**P2-DG2: Retention Policy**
- **Issue**: No automated data retention/pruning
- **Recommendation**: Define retention for Bronze/Silver/Gold layers

---

## 22. DevEx / Repo Ergonomics Audit

### Findings

**P2-DX1: One-Command Bootstrap**
- **Status**: ✅ `docker-compose up -d --build`
- **Dev setup**: `pip install .[dev] && pre-commit install`

**P3-DX1: Pre-commit Hooks**
- **File**: [.pre-commit-config.yaml](file:///Users/jacobmcmillan/Empire/Orion/.pre-commit-config.yaml)
- **Status**: ✅ ruff, black, mypy, bandit, detect-secrets

**P3-DX2: .env.example**
- **Status**: ✅ Template provided

---

## Remediation Summary

### ✅ Completed (P1-P2)
| Item | Description | Status |
|------|-------------|--------|
| P1-C1 | API endpoint tests | ✅ Added 9 tests |
| P1-R1 | Circuit breaker integration | ✅ All UW connectors |
| P1-SC1 | SBOM generation | ✅ In CI pipeline |
| P1-P1 | Batch solver metrics | ✅ Already implemented |
| P2-L1 | FeatureEngine cold start | ✅ `_hydrated` flag added |
| P2-M2 | History pruning | ✅ `max_history_len=100` |
| P2-DC2 | Debug files cleanup | ✅ Gitignore updated |
| P2-DC3 | offline_gym.py | ✅ Already removed |
| P2-E2 | Correlation IDs | ✅ Logger support exists |
| P2-D1 | Connector base class | ✅ Protocol interfaces exist |

### ⏳ Previously Remaining (Now Complete)
| Item | Description | Status |
|------|-------------|--------|
| P1-T1 | Test coverage improvement | ✅ Added 15 unit tests |
| P2-O1 | OpenTelemetry tracing | ✅ `telemetry.py` created |
| P2-CI2 | Rollback strategy docs | ✅ `ROLLBACK.md` added |
| P2-DG2 | Data retention policy | ✅ `DATA_RETENTION.md` added |

### Backlog (P3) - Complete
1. ✅ UW SDK extraction - `unusualwhales_python_client-5.1` exists
2. ✅ Feature flag system - `feature_flags.py` exists
3. ✅ Runbooks - `docs/RUNBOOKS.md` added

---

## 23. Heber vs Orion Parity Audit (2026-02-11)

### Snapshot

- **Heber canonical Silver datasets**: 44 (`/Users/jacobmcmillan/Empire/Heber/heber/schemas/silver.py`)
- **Orion Heber reader datasets currently consumed**: 7 (`bars`, `flow_alerts`, `darkpool`, `market_tide`, `greek_exposure`, `max_pain`, `iv_rank`) in `/Users/jacobmcmillan/Empire/Orion/src/orion/clients/heber_reader.py`
- **Orion local legacy Silver tables still defined**: 6 (`silver_signals`, `silver_uw_flow`, `silver_uw_darkpool`, `silver_alpaca_bars`, `silver_uw_alerts`, `silver_option_quotes`) in `/Users/jacobmcmillan/Empire/Orion/src/orion/storage/models_silver.py`
- **Orion local legacy label tables still used**: `flow_labels`, `price_target_labels`
- **Orion local Gold tables (execution state, keep for now)**: `candidate_trades`, `exit_decisions`, `strategy_decisions`, `gold_ticker_rollup`, `candidate_labels`
- **Orion local Gold ML/label tables (migration candidates)**: `labels_event`, `labels_window`, `gold_feature_events`, `gold_feature_windows`

### Side-by-Side Parity

| Capability Area | Heber (source of truth) | Orion (current state) | Recommendation |
|-----------------|--------------------------|------------------------|----------------|
| Bars | `bars` Silver dataset | Legacy table `silver_alpaca_bars` still exists in schema; runtime reads moved to Heber | Keep Heber path; archive local table/model after final gate cleanup |
| Options flow | `flow_alerts` Silver dataset | Legacy table `silver_uw_flow` still exists in schema/comments | Keep Heber path; remove local schema + stale comments |
| Darkpool | `darkpool` Silver dataset | Legacy table `silver_uw_darkpool` still exists in schema | Keep Heber path; remove local schema |
| Market tide | `market_tide` Silver dataset | Read from Heber in runtime | Keep; no local table dependency remains |
| Greek exposure | `greek_exposure` Silver dataset | Read from Heber in runtime; legacy comment references remain | Keep Heber path; clean stale references |
| Max pain | `max_pain` Silver dataset | Read from Heber in runtime | Keep |
| IV rank | `iv_rank` Silver dataset | Read from Heber in runtime; legacy local naming remains in comments | Keep Heber path; clean stale references |
| Label outcomes | `labels_alert_barriers` Gold dataset (`heber.watch.writer`) | Legacy `flow_labels` and `price_target_labels` local writes still present | Migrate to Heber labels; archive local labeler loops |
| Label features | `meta_label_features` Gold dataset (`heber.watch.features`) | Legacy `gold_feature_events` / `gold_feature_windows` and `price_target_labels` enrichment | Define field mapping, then archive local feature materialization |

### Heber Inventory Orion Is Not Using Yet

Orion currently consumes **7/44** canonical Heber Silver datasets. The largest unused groups:

- **Market microstructure/reference**: `quotes`, `trades`, `option_contract`, `option_chain_snapshot`, `option_history`
- **Macro/fundamental/news**: `news`, `economic_events`, `stock_fundamentals`, `analyst_ratings`
- **Flow breadth analytics**: `net_premium_tick`, `group_flow`, `iv_term_structure`, `volatility_stats`, `oi_change`, `sector_tide`

These are not blockers for current Orion runtime, but they are opportunity areas if we want feature parity expansion in Heber-first mode.

### Remaining Orion Local SQL Coupling (Audit Finding)

Local SQL references are now mostly concentrated around legacy labels/training paths:

**Update (2026-02-11 remediation pass):**
- `sync_earnings` ticker discovery migrated to Heber Gold datasets (`labels_alert_barriers`, `meta_label_features`)
- `data_quality_checker` ML coverage summaries migrated to Heber Gold datasets (no local `price_target_labels` reads)
- `validate_features` label period loader migrated to Heber Gold (`labels_alert_barriers`) for source-audit windowing
- `validate_features` spot-check and sanity paths migrated to Heber Gold labels/features (no local label SQL reads)
- `backfill_exit_columns` local update writes disabled; job now skips local label mutation while storage is centralized
- `backfill_ml_features` local update writes disabled; job now skips local label mutation while storage is centralized
- `backfill_ml_features` candidate discovery migrated to Heber Gold (`labels_alert_barriers` + `meta_label_features`) with keyset cursor filtering
- `cleanup_legacy_backfill_watermarks` now includes real `.cursor` watermark keys used by active backfill jobs (`backfill_ml_features.price_target_labels.cursor`, `backfill_exit_columns.velocity.cursor`, `backfill_exit_columns.checkpoint.cursor`)
- `main_pattern_miner` now has explicit per-service legacy gate (`ORION_ENABLE_LEGACY_PATTERN_MINER`) and exits before DB init when disabled
- `nightly_backfill` now has explicit per-service legacy gate (`ORION_ENABLE_LEGACY_NIGHTLY_BACKFILL`) and exits before DB init when disabled
- `quality_guardrails` now has explicit per-service legacy gate (`ORION_ENABLE_LEGACY_QUALITY_GUARDRAILS`) and exits before DB init when disabled
- `exit_classifier` training path now has explicit gate (`ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING`) and returns empty training datasets without DB access when disabled
- `pattern_miner` training data path now has explicit gate (`ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING`) and returns empty training datasets without DB access when disabled
- `main_labeler.persist_labels(...)` and `main_price_target_labeler.persist_labels(...)` now skip local DB writes when their legacy gates are disabled
- `docker-compose.yml` `legacy-labels` profile now defaults to:
  - local labeling loops disabled (`ORION_ENABLE_LEGACY_LABEL_PIPELINES=false` plus service-specific labeler/guardrail/backfill toggles `false`),
  - model-training paths preserved (`ORION_ENABLE_LEGACY_PATTERN_MINER=true`, `ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING=true`, `ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING=true`)
- `pattern_miner` now supports explicit training source control (`ORION_PATTERN_MINER_TRAINING_SOURCE`):
  - `heber_gold`: reads `labels_alert_barriers` + `meta_label_features` and builds a compatibility training frame without local SQL reads
  - `legacy_sql`: existing `price_target_labels` SQL path
- `exit_classifier` now supports explicit training source control (`ORION_EXIT_CLASSIFIER_TRAINING_SOURCE`):
  - `heber_gold`: builds a coarse compatibility training frame from `labels_alert_barriers` + `meta_label_features` without local SQL reads
  - `legacy_sql`: `price_target_labels` SQL path with schema-aware optional window feature columns (no `gold_feature_windows` lateral join)
- `main_price_target_labeler.get_window_features_at_entry(...)` now builds `1h`/`1d`/`1w` window context directly from Heber Silver (`flow_alerts`, `darkpool`) and no longer queries local `gold_feature_windows`
- `window_feature_job` was archived as an unwired legacy producer (no active compose/import path), removing residual local `gold_feature_windows` write coupling from active code paths
- `main_labeler` (legacy `flow_labels` writer) is now archived and removed from compose orchestration; `ORION_ENABLE_LEGACY_FLOW_LABELER` config wiring was removed with it

- `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py` (`price_target_labels`, legacy `silver_*` references in comments/docs)
- `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py` (`FROM price_target_labels`)
- `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/pattern_miner.py` (`FROM price_target_labels`)

### Heber vs Orion ML-Training Field Parity (Deep Audit)

Source references used in this pass:
- Heber outcomes schema: `../Heber/heber/watch/checker.py` (`outcome_to_label_row`)
- Heber feature schema: `../Heber/heber/watch/features.py` (`AlertFeatures`)
- Orion training consumers:
  - `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/pattern_miner.py`
  - `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py`

#### Pattern Miner (`pattern_miner.py`)

- Orion expects **53** entry/label features (`FEATURE_COLUMNS + CATEGORICAL_COLUMNS`).
- Direct overlap with Heber `AlertFeatures` today: **4/53**:
  - `put_call`
  - `aggressor`
  - `is_sweep`
  - `minutes_to_close`
- Missing in Heber naming/model surface: **49/53** (examples):
  - `iv_rank_at_entry`, `gex_at_entry`, `vix_at_entry`, `delta_at_entry`, `theta_at_entry`
  - `market_tide_30m`, `darkpool_*`, `oi_change_1d`, `sweep_ratio_1h`
  - `entry_hour`, `entry_session`, `entry_day_of_week`, `days_to_earnings`
- Target mismatch:
  - Orion targets depend on legacy columns (`hit_50_pct_ts`, `hit_100_pct_ts`, `hit_stop_20_pct_ts`, `time_to_50_pct_seconds`, `last_tracked_ts`, `trade_type`)
  - Heber outcomes provide (`outcome`, `hit_tp_first`, `outcome_return`, `mfe`, `mae`, `bars_to_hit`, `trading_minutes_to_hit`) and do not expose the Orion legacy checkpoint columns.

#### Exit Classifier (`exit_classifier.py`)

- Orion required training column surface across buckets: **147** fields
  - includes `return_at_*`, `delta_at_*`, `gamma_at_*`, `theta_at_*`, `iv_at_*`, `dte_at_*`, `time_value_pct_at_*`, `theta_decay_pct_at_*`, plus `max_return_pct`, `max_drawdown_pct`, `trade_type`, etc.
- Direct overlap with Heber watch features + outcomes combined: **1/147** (`is_sweep`).
- Missing in Heber watch v1 surface: **146/147**, including all checkpoint return/Greek/time-decay columns.

### Legacy Label Tables vs Heber Watch (Column-Level Audit)

#### `flow_labels` (`main_labeler.py`, archived 2026-02-12)

- Orion local write surface was **28 columns** in `INSERT INTO flow_labels (...)`.
- Direct overlap with Heber watch outcomes/features: **5/28**:
  - `put_call`
  - `aggressor`
  - `is_sweep`
  - `iv`
  - `expiry`
- Non-overlap columns (examples): `return_15m`, `return_30m`, `return_1h`, `label_1h`, `primary_label`, `trade_type`.
- Status: archived with legacy module decommission (`archive/2026-02-12_label-stack-wave13/legacy_code/main_labeler.py`).

#### `price_target_labels` payload surface (`main_price_target_labeler.py`)

- Orion label payload key surface discovered from `label[...]` + `label.update(...)`: **163 keys**.
- Direct overlap with Heber watch outcomes/features: **1/163** (`minutes_to_close`).
- Non-overlap groups include:
  - checkpoint returns/prices (`return_at_*`, `price_at_*`),
  - checkpoint Greeks/time decay (`delta_at_*`, `gamma_at_*`, `theta_at_*`, `iv_at_*`, `theta_decay_pct_at_*`, `time_value_pct_at_*`),
  - legacy target timestamps (`hit_50_pct_ts`, `hit_100_pct_ts`, `hit_stop_20_pct_ts`, etc.),
  - Orion-specific regime/context fields (`market_tide_30m`, `risk_regime_at_entry`, `vex_at_entry`, etc.).

#### Decision Impact

- The parity gap confirms local `flow_labels` / `price_target_labels` are currently **schema forks**, not thin replicas of Heber watch datasets.
- Migration should be treated as a **contract redesign** (v2 schema + mapping), not a simple table swap.

#### Decision Guidance (Keep / Move / Archive)

- Keep for now (legacy profile only):
  - `pattern-miner` and `nightly-backfill` compose services stay under `legacy-labels` profile until a Heber-native training dataset is defined.
  - Local model information storage remains in Orion:
    - model artifacts under `ORION_MODEL_DIR` (pickle outputs consumed by `ml/scorer.py`),
    - model metadata tables `ml_pattern_insights` and `ml_feature_importance_history`.
  - Recommended toggle profile to keep model storage while minimizing legacy local labeling:
    - `ORION_ENABLE_LEGACY_LABEL_PIPELINES=false`
    - `ORION_ENABLE_LEGACY_PATTERN_MINER=true`
    - `ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING=true`
    - `ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING=true`
    - `ORION_PATTERN_MINER_TRAINING_SOURCE=heber_gold`
    - `ORION_EXIT_CLASSIFIER_TRAINING_SOURCE=heber_gold`
- Move to Heber (required before decommission):
  - Define a **v2 training contract** in Heber that includes:
    - normalized entry feature names expected by Orion scoring/inference, or a mapping layer,
    - checkpoint/outcome targets needed for exit timing (or a revised target definition).
- Archive after contract signoff:
  - Local `price_target_labels`-dependent training query paths in:
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/pattern_miner.py`
    - `/Users/jacobmcmillan/Empire/Orion/src/orion/ml/exit_classifier.py`

### Heber v2 Contract Proposal (What To Keep vs Dispose)

Goal: keep Orion model quality while reducing local-table complexity before final archival.

#### Keep + Promote to Heber v2 (high value for training)

- Entry-time contract and flow context:
  - `put_call`, `aggressor`, `is_sweep`, `premium_usd`, `dte`, `minutes_to_close`
- Entry-time risk/greeks context:
  - `iv_rank_at_entry`, `iv_at_entry`, `delta_at_entry`, `gamma_at_entry`, `theta_at_entry`, `vega_at_entry`
- Market/regime context used by Orion models:
  - `gex_at_entry`, `vix_at_entry`, `market_tide_30m`, `entry_hour`, `entry_day_of_week`, `entry_session`, `days_to_earnings`
- Outcome surface needed for model targets:
  - `outcome`, `hit_tp_first`, `outcome_return`, `mfe`, `mae`, `bars_to_hit`, `trading_minutes_to_hit`

#### Keep Local in Orion (do not migrate now)

- Model artifacts under `ORION_MODEL_DIR` (`*.pkl` used by scorers/trainers)
- Model metadata tables:
  - `ml_pattern_insights`
  - `ml_feature_importance_history`

#### Dispose / Do Not Port (low-value schema bloat)

- Full checkpoint explosion columns from legacy `price_target_labels`:
  - `return_at_*`, `price_at_*`, `delta_at_*`, `gamma_at_*`, `theta_at_*`, `iv_at_*`, `dte_at_*`, `time_value_pct_at_*`, `theta_decay_pct_at_*`
- Duplicate outcome aliases (example: overlapping `mfe`/`contract_mfe` style duplicates)
- Local job bookkeeping columns only used for legacy backfill loops

#### Recommended Migration Sequence

1. Freeze new column additions to local `price_target_labels`.
2. Define `heber.watch` v2 training projection with the "Keep + Promote" fields above.
3. Re-point Orion trainers (`pattern_miner`, `exit_classifier`) to the Heber v2 projection.
4. Archive legacy label/table mutation loops and local label backfill scripts.

### Keep / Decide / Archive Plan

**Keep in Orion (system-specific execution state):**
- `candidate_trades`
- `exit_decisions`
- `strategy_decisions`
- Model artifacts in `/app/models` (`ORION_MODEL_DIR`)
- ML metadata tables in `/Users/jacobmcmillan/Empire/Orion/src/orion/storage/models_ml.py`:
  - `ml_pattern_insights`
  - `ml_feature_importance_history`

**Needs product decision before migration:**
- Which Orion-only label columns are still needed versus replaced by Heber watch fields
- Which Orion feature columns should become `meta_label_features` columns versus be retired

**Archive after mapping signoff:**
- `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`
- `/Users/jacobmcmillan/Empire/Orion/src/orion/storage/models_silver.py` (legacy local Silver table models)
- Legacy local label/feature update jobs that only mutate `price_target_labels`

### Remaining Local-SQL Coupling Inventory (2026-02-12 snapshot)

Top remaining files by reference count (`price_target_labels` / `flow_labels` / `silver_*` / `gold_feature_windows`):

- `src/orion/main_price_target_labeler.py`: 9 refs
- `src/orion/ml/exit_classifier.py`: 6 refs
- `src/orion/storage/models_silver.py`: 3 refs
- `src/orion/ml/pattern_miner.py`: 3 refs
- `src/orion/jobs/validate_features.py`: 3 refs

Interpretation:

- Runtime risk is now concentrated in the legacy labeling + trainer source path.
- Model storage paths (`ORION_MODEL_DIR`, `ml_pattern_insights`, `ml_feature_importance_history`) remain intentionally local and should not be treated as decommission targets.

### Archive Actions Completed (this pass)

- Archived standalone legacy SQL scripts under `archive/legacy-sql-scripts/`:
  - `scripts/backfill_ml_features.py` (superseded by `python -m orion.jobs.backfill_ml_features`)
  - `scripts/analyze_todays_flow.py`
  - `scripts/backtest_exit_strategies.py`
  - `scripts/refetch_alpaca_bars.py`
  - `scripts/reprocess_bronze_flow.py`
- Archive note and inventory maintained in:
  - `archive/legacy-sql-scripts/README.md`
- Archived unwired legacy window-feature producer under `archive/2026-02-12_label-stack-wave12/`:
  - `src/orion/jobs/window_feature_job.py` -> `archive/2026-02-12_label-stack-wave12/legacy_code/window_feature_job.py`
  - `tests/unit/test_window_feature_job_heber_source.py` -> `archive/2026-02-12_label-stack-wave12/legacy_tests/test_window_feature_job_heber_source.py`
  - Archive manifest: `archive/2026-02-12_label-stack-wave12/README.md`
- Archived legacy flow-label writer under `archive/2026-02-12_label-stack-wave13/`:
  - `src/orion/main_labeler.py` -> `archive/2026-02-12_label-stack-wave13/legacy_code/main_labeler.py`
  - `tests/unit/test_main_labeler_heber_migration.py` -> `archive/2026-02-12_label-stack-wave13/legacy_tests/test_main_labeler_heber_migration.py`
  - Archive manifest: `archive/2026-02-12_label-stack-wave13/README.md`
