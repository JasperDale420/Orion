# Orion Gateway + Heber Parity Audit (Pass 1)

Date: 2026-02-05
Scope: `Orion` compared against `../Data-Gateway` and `../Heber`
Author: Codex audit pass

## 1) Executive Summary

Orion is in a partial migration state. The old UW ingestion path has been removed from active code, but the replacement path is not fully wired.

Current state:
- Orion ingestion is effectively Alpaca-only in runtime flow (`src/orion/ingestion/service.py`), while flow/darkpool-dependent downstream jobs still assume local UW-backed SQL tables (`silver_uw_flow`, etc.).
- Orion `HeberReader` contract was aligned on 2026-02-05 to supported Heber access (catalog endpoints + local Silver/Gold parquet layout). The remaining gap is adoption across downstream jobs still tied to local Orion SQL tables.
- Gateway streaming integration exists, but Orion’s stream client currently does not parse Data Gateway’s actual WebSocket payload shape (`type: data` + `envelope`), creating a high risk of silent data starvation.
- A significant amount of legacy code/tests/scripts still referenced removed modules (`orion.main_ingest`, `orion.connectors.uw_flow_connector`), blocking test collection and increasing maintenance drag.

Bottom line:
- We need a two-track migration: 
  1. Stabilize Orion runtime contracts with Gateway/Heber.
  2. Decide which Orion-specific feature/label logic should move into Heber vs be retired.

## 2) Architecture Delta (What Changed vs What Still Assumes Old World)

### Intended target architecture
- Data ingestion centralized in Data Gateway.
- Durable bronze/silver/gold storage and zero-leakage semantics centralized in Heber.
- Orion consumes curated datasets/signals, focuses on strategy/risk/execution.

### Actual Orion runtime behavior today
- Ingestion loop no longer polls UW directly (`src/orion/ingestion/service.py` comment and removed `_poll_uw` path).
- Ingestion loop currently processes Alpaca bars only (`_poll_alpaca_events` path).
- Feature/rule code still has UW flow branches (`process_uw_flow`, `process_uw_flow_events`) but runtime never feeds UW events in current ingestion path.

### Remaining old assumptions
- Labeling/enrichment jobs still query local UW-derived SQL tables:
  - `silver_uw_flow`, `silver_market_tide`, `silver_greek_exposure`, `silver_max_pain`, `silver_iv_rank`
  - Primary examples: `src/orion/main_price_target_labeler.py`, `src/orion/main_labeler.py`, `src/orion/main_feature_enrichment.py`, `src/orion/jobs/backfill_ml_features.py`

## 3) Integration Findings (Gateway + Heber)

## High

1. `HeberReader` contract mismatch with Heber APIs (Resolved)
- Prior state: Orion `HeberReader` called `/silver/read` and `/gold/read`, which are not exposed by Heber catalog API.
- Current state (commit `6273889`, 2026-02-05): reader now uses supported access paths:
  - Catalog endpoints for metadata/health (`/health`, `/datasets`)
  - Heber parquet layout for Silver/Gold reads (`HEBER_DATA_ROOT`)
- Residual impact: integration consumers still need migration from Orion-local SQL tables to Heber datasets.

2. Gateway WebSocket payload mismatch in Orion client (Resolved)
- Data Gateway sends stream payloads as `{type: "data", envelope: {...}, data: {...}}` (`../Data-Gateway/gateway/main.py`, `_on_stream_data`).
- Current state (commit `1185476`, 2026-02-05): `GatewayStreamClient` now parses `type=data` + `feed=bars` messages, consumes `envelope` + `data`, and normalizes symbols before emitting `BronzeEvent`.
- Residual impact: downstream jobs still need migration off Orion-local SQL feature/label tables.

3. Test suite is structurally broken by removed modules
- Removed modules (`orion.main_ingest`, `orion.connectors.uw_flow_connector`) are still imported in many tests.
- Reproduced failures:
  - `pytest -o addopts='-q' tests/connectors/test_uw_flow.py` -> `ModuleNotFoundError: orion.connectors.uw_flow_connector`
  - `pytest -o addopts='-q' tests/unit/test_eod_wrapper.py` -> `ModuleNotFoundError: orion.main_ingest`
- Impact: migration regressions are harder to detect due test noise.
- Status in this pass: addressed by archiving those legacy tests into `archive/2026-02-05_gateway-heber-migration/legacy_tests/`; replacement tests are still required.

## Medium

4. Environment variable contract drift (Mostly Resolved)
- Orion uses multiple naming families: `GATEWAY_URL`, `DATA_GATEWAY_URL`, `GATEWAY_API_KEY`, `DATA_GATEWAY_API_KEY`, plus legacy UW vars.
- Current state (commit `006db38`, 2026-02-05): centralized settings now exist in `src/orion/config.py` (`data_gateway_url`, `data_gateway_api_key`, `heber_catalog_url`, `heber_data_root`, `orion_use_gateway`) with backward-compatible alias support.
- Residual impact: some non-Gateway legacy env usage remains outside migration scope.

5. Mixed data ownership model (SQL-local vs lakehouse)
- Orion still writes and depends on local SQL silver tables for UW-derived context while migration intent is Heber ownership.
- Impact: duplicate sources of truth and schema drift.

6. Hardcoded default gateway key in several connectors (Resolved)
- Example defaults like `gw_orion_trading_key_55555` in UW connectors.
- Current state (commit `006db38`, 2026-02-05): active connectors now read centralized settings and fail fast when keys are not configured.

## Low

7. Documentation drift
- README and historical docs still describe `main_ingest.py` and direct UW connector paths that are no longer current.

## 4) Feature/Label Parity: Orion vs Heber

This is the first pass to frame keep/migrate/dispose decisions.

### Orion strengths worth preserving
- Rich options-specific label and checkpoint feature space in `main_price_target_labeler.py`:
  - Checkpoint returns (intraday to multi-week)
  - Checkpoint greeks/time-decay fields
  - Flow aggression/rvol/darkpool window features
  - Regime and sector correlation context

### Heber strengths already in place
- Canonical envelope + zero-leakage semantics (`ts_available`) in writer/firewall/sdk.
- Standardized silver schemas for `flow_alerts`, `darkpool`, `market_tide`, `greek_exposure`, etc.
- Label infrastructure and watch-based barrier labeling path (`heber/watch`, `heber/gold`, feature views).

### Proposed keep/migrate/dispose (initial)

Keep in Orion (for now):
- Strategy/risk/execution decisioning and solver/meta-agent logic.
- Any features tightly bound to live execution policy and broker constraints.

Migrate to Heber (recommended):
- Canonical feature generation currently tied to local SQL UW tables.
- Core label generation datasets intended for model training.
- Reference/derived market context tables duplicated from Gateway/Heber feeds.

Dispose or archive in Orion (recommended):
- Legacy UW polling connector stack and tests depending on removed modules.
- Legacy backfill scripts that require removed connector imports.

## 5) Archive Actions Completed In This Pass

Archived into:
- `archive/2026-02-05_gateway-heber-migration/legacy_code/`
- `archive/2026-02-05_gateway-heber-migration/legacy_tests/`
- `archive/2026-02-05_gateway-heber-migration/legacy_scripts/`

Code archived:
- Legacy ingestion entrypoint and UW connector implementations that were already deprecated.

Tests archived:
- Legacy test files that import removed modules (`orion.main_ingest`, `orion.connectors.uw_flow_connector`, etc.).

Scripts archived:
- Legacy UW backfill scripts that import removed connector modules.

## 6) Technical Debt Backlog (Migration-Critical)

P0 (Completed in this migration pass):
1. Gateway stream client message parsing now consumes `type=data` + `envelope` payload shape.
2. Central config for Gateway/Heber URLs and keys added in `src/orion/config.py`; active callers migrated.

P1:
1. Refactor remaining label/enrichment jobs to read from Heber datasets (or a single sanctioned data-access layer) instead of local UW silver SQL tables.
2. Rebuild tests around `orion.ingestion.service` and new integration contracts.
3. Update README/docs to match new architecture and command paths.

P2:
1. Define canonical feature/label schema ownership between Orion and Heber (single source of truth per dataset family).
2. Remove stale generated artifacts/docs that keep reintroducing deprecated paths.

## 7) Recommended Migration Sequence

1. Runtime contract hardening
- Fix Gateway stream parsing. (Completed 2026-02-05)
- Fix Heber read client contract. (Completed 2026-02-05)

2. Data-access consolidation
- Introduce one Orion data-access facade for Gateway/Heber.
- Migrate `main_feature_enrichment`, `main_labeler`, `main_price_target_labeler`, and `jobs/backfill_ml_features` in that order.

3. Parity validation
- Build side-by-side checks for key feature columns and label outcomes between Orion legacy and Heber-backed paths.

4. Deletion phase
- After parity signoff, remove archived legacy code permanently.

## 8) Open Decisions Needed

1. Which label families are canonical going forward?
- Heber barrier labels vs Orion extensive checkpoint labels vs hybrid.

2. Should Orion keep local SQL silver feature tables at all?
- Or become a pure consumer of Heber silver/gold datasets.

3. Where should feature engineering live?
- Keep strategy-specific transforms in Orion; move reusable data transforms and training features to Heber.

## 9) Column-Level Parity Matrix (Pass 2)

Reference sources used for this table:
- Orion: `src/orion/main_price_target_labeler.py`, `src/orion/main_labeler.py`, `src/orion/jobs/backfill_ml_features.py`
- Heber: `../Heber/heber/features/templates/*.py`, `../Heber/heber/features/pipelines/alert_labels.py`

### 9.1 Labels Parity

| Orion label columns | Heber equivalent columns | Parity status | Decision |
| --- | --- | --- | --- |
| `return_at_5m`, `return_at_10m`, `return_at_15m`, `return_at_30m`, `return_at_1h`, `return_at_2h`, `return_at_4h`, `return_at_8h`, `return_at_1d`, `return_at_2d`, `return_at_3d`, `return_at_1w`, `return_at_2w`, `return_at_3w`, `return_at_4w`, `return_at_eod` | `return_1d`, `return_5d`, `return_10d`, `return_20d` (`labels.py`) | Partial mismatch (Orion has richer intraday+multiweek checkpoint grid) | Keep Orion checkpoint grid short-term; design Heber `labels_alert_checkpoints` dataset if these are still training-critical |
| `hit_50_pct_ts`, `hit_75_pct_ts`, `hit_100_pct_ts`, `hit_150_pct_ts`, `hit_stop_20_pct_ts`, `first_exit_type` | `hit_tp_first`, `bars_to_hit`, `mfe`, `mae`, `mfe_adj`, `mae_adj` (`alert_labels.py`) | Conceptual overlap but different target semantics | Make Heber barrier labels canonical for model training; treat Orion hit-threshold labels as experimental/strategy-specific |
| `return_15m`, `return_30m`, `return_1h`, `return_2h`, `label_15m`, `label_30m`, `label_1h`, `label_2h`, `primary_label` (`main_labeler`) | No direct Heber table today | No parity | Decommission `main_labeler` path after Heber-backed replacement is live |
| `vix_at_entry`, `vix_regime_at_entry` | `vix_at_alert`, `vix_regime` (`alert_labels.py`) | High parity | Migrate to Heber canonical names and remove Orion duplicates |

### 9.2 Feature Parity

| Orion feature columns | Heber equivalent columns | Parity status | Decision |
| --- | --- | --- | --- |
| `ask_side_ratio`, `sweep_ratio_1h`, `same_ticker_premium_1h`, `institutional_flow_1w` | `total_premium_24h`, `call_premium_24h`, `put_premium_24h`, `call_put_premium_ratio`, `net_premium_24h`, `sweep_count_24h` (`flow.py`) | Partial mismatch (windowing granularity differs) | Add Orion short-window flow features to Heber flow template v2; retire Orion-local recomputation |
| `darkpool_15m`, `darkpool_30m`, `darkpool_1h`, `darkpool_4h`, `darkpool_1d`, `darkpool_3d`, `darkpool_1w`, `darkpool_2w`, `darkpool_4w`, `darkpool_volume_1h` | No direct Heber darkpool-window feature template | Gap | Migrate darkpool window feature generation into Heber Gold; then remove Orion SQL-dependent functions |
| `gex_at_entry`, `vex_at_entry`, `max_pain_distance_pct`, `iv_rank_at_entry`, `market_tide_30m`, `market_tide_direction` | Heber has Silver datasets and feed mapping support, but no canonical Gold template for these columns yet | Gap | Build shared Heber context feature dataset; keep Orion-only copies temporary |
| `trend_regime_at_entry`, `vol_regime_at_entry`, `risk_regime_at_entry`, `session_regime_at_entry` | No direct multi-axis regime template in Heber features currently | Gap | Keep in Orion short-term; evaluate migration once regime definitions are standardized across projects |
| `spy_correlation_5d`, `spy_return_1h` | `corr_spy_20d`, `corr_spy_60d`, `beta_60d`, `alpha_20d`, `rel_strength_20d` (`cross_asset.py`) | Partial overlap | Adopt Heber cross-asset features as canonical where possible; only keep Orion 1h-specific tactical variants if needed |
| `rvol_30m`, `rvol_1h`, `rvol_daily`, `rvol_3d`, `rvol_weekly`, `rvol_monthly` | No direct RVOL template in Heber | Gap | Promote RVOL generation to Heber Gold feature template |
| `overnight_gap_pct`, `vwap_distance_pct`, `minutes_to_close`, `price_change_5d_prior`, `earnings_in_dte_window` | Partial overlap with Heber momentum/volatility templates (`momentum.py`, `volatility.py`) but not exact columns | Partial mismatch | Preserve in Orion initially; migrate reusable definitions with explicit contracts |
| `delta_at_entry`, `gamma_at_entry`, `theta_at_entry`, `vega_at_entry`, `rho_at_entry`, `iv_at_entry`, `open_interest_at_entry`, `volume_at_entry`, plus checkpoint Greeks/time-decay families (`delta_at_*`, `gamma_at_*`, `theta_at_*`, `vega_at_*`, `iv_at_*`, `dte_at_*`, `theta_decay_pct_at_*`, `time_value_pct_at_*`) | No direct Heber equivalent today | Major gap | Keep in Orion until Heber defines option-greeks checkpoint schema; then migrate if training value justifies storage cost |

### 9.3 Dispose Candidates (After Parity Signoff)

- Orion-local SQL-only label loop in `src/orion/main_labeler.py`
- Orion-local backfill logic in `src/orion/jobs/backfill_ml_features.py` that recomputes features already moved to Heber
- Any duplicated feature columns where Heber canonical datasets provide equal or better definitions

### 9.4 Keep Candidates (Orion-Owned for Now)

- Execution-policy and broker-coupled features
- Strategy-specific experimental targets not intended for shared training corpora
- Fast-iteration research labels that have not yet met canonical data quality gates

---

This audit now includes pass-2 column parity mapping and pass-3 migration status. Next pass should execute remaining data-access migration and archive decisions.

## 10) Pass 3 Status Update (2026-02-06)

### 10.1 Completed Since Pass 2

- `main_labeler` now reads flow + bars from Heber (`src/orion/main_labeler.py`) while preserving local `flow_labels` persistence for compatibility.
- `main_feature_enrichment` now discovers active tickers from Heber flow first, with local SQL fallback (`src/orion/main_feature_enrichment.py`).
- Gateway/Heber config centralization and websocket envelope parsing are now in production code and covered by new tests.

### 10.2 Current Technical Debt Snapshot (from repo scan)

Observed SQL-coupled references in active code:

| Table token | Approx refs in `src/orion` |
| --- | --- |
| `silver_uw_flow` | 67 |
| `silver_market_tide` | 13 |
| `silver_greek_exposure` | 15 |
| `silver_max_pain` | 9 |
| `silver_iv_rank` | 4 |

Top concentration by file:
- `src/orion/jobs/validate_features.py` (49 refs)
- `src/orion/main_price_target_labeler.py` (22 refs)
- `src/orion/ml/flow_enricher.py` (11 refs)

### 10.3 Remaining Integration Gaps (High Priority)

1. `main_price_target_labeler` remains tightly coupled to Orion-local silver tables.
- Still queries `silver_uw_flow`, `silver_market_tide`, `silver_greek_exposure`, `silver_max_pain`, `silver_iv_rank` directly.
- This is the largest single blocker for true Gateway+Heber parity.

2. ML enrichment and backfill path still assumes Orion-local UW SQL data.
- `src/orion/ml/flow_enricher.py`, `src/orion/jobs/backfill_ml_features.py`, `src/orion/jobs/window_feature_job.py`, and `src/orion/jobs/data_quality_checker.py` are still local-SQL-centric.
- These jobs need a shared Heber-backed data-access facade to avoid repeated schema logic.

3. Cross-project runtime default mismatch to align.
- Orion defaults `data_gateway_url` to `http://localhost:8080` (`src/orion/config.py`).
- Heber alert-label pipeline currently defaults `DATA_GATEWAY_URL` to `http://localhost:8000` (`../Heber/heber/features/pipelines/alert_labels.py`).
- This should be standardized to prevent environment-specific drift.

### 10.4 Archive Candidates for Step 1 (Do Not Remove Yet)

These are likely removable after migration parity is signed off:
- `src/orion/main_option_quote_tracker.py` (depends on local `silver_uw_flow` + `silver_option_quotes` checkpoint loop)
- `src/orion/jobs/backfill_historical_gex.py` (local historical GEX backfill path)
- `src/orion/jobs/backfill_exit_columns.py` (legacy local backfill path)

Recommendation:
- Keep these in active tree until Heber-backed replacements are validated in staging.
- Then archive as a single wave (`archive/2026-02-xx_gateway-heber-migration-wave2/`) to reduce rollback complexity.

## 11) Pass 4 Deep Audit (2026-02-06)

Scope in this pass:
- `src/orion/main_price_target_labeler.py` (2,944 LOC)
- `src/orion/ml/flow_enricher.py` (1,069 LOC)
- `src/orion/jobs/backfill_ml_features.py` (522 LOC)
- `src/orion/jobs/window_feature_job.py` (241 LOC)
- `src/orion/jobs/data_quality_checker.py` (564 LOC)
- `src/orion/jobs/validate_features.py` (507 LOC)

### 11.1 Findings (Ordered by Severity)

#### High

1. Core label pipeline is still fully SQL-coupled and not Heber-backed.
- `main_price_target_labeler` still queries `silver_uw_flow`, `silver_market_tide`, `silver_greek_exposure`, `silver_max_pain`, `silver_iv_rank`, and `silver_uw_darkpool`.
- No `HeberReader` usage exists in this file.
- Impact: largest remaining parity blocker; training labels remain tied to Orion-local silver tables.

2. Concrete runtime bug in backfill path: incorrect function call signature.
- `src/orion/jobs/backfill_ml_features.py:445` calls `get_sector_correlation_features(ticker, sector, entry_ts)`.
- `src/orion/main_price_target_labeler.py:1467` defines `get_sector_correlation_features(ticker: str, entry_ts: datetime)`.
- Impact: raises `TypeError` in backfill execution, causing feature backfill failures for affected records.

3. ML enrichment/backfill stack has zero Heber read-path adoption.
- `flow_enricher`, `backfill_ml_features`, `window_feature_job`, `data_quality_checker`, and `validate_features` do not use `HeberReader`.
- All still depend on Orion SQL tables as source-of-truth.
- Impact: duplicate ownership and schema drift against Heber datasets.

#### Medium

4. Feature semantics diverge across training/inference/backfill paths.
- `entry_session` classification differs across modules:
  - `main_price_target_labeler.py:671` uses `OPEN/MID/CLOSE`.
  - `flow_enricher.py:210` uses `OPEN/MID/CLOSE` but with different cutoff assumptions.
  - `backfill_ml_features.py:122` uses `early/midday/afternoon/late`.
- `minutes_to_close` logic diverges (`20:00 UTC` vs `21:00 UTC`) between `main_price_target_labeler.py:1173`, `backfill_ml_features.py:221`, and `flow_enricher.py:202`.
- Impact: train/inference skew and non-deterministic feature behavior.

5. Validation tooling source-map drift.
- `validate_features` maps RVOL features to `silver_uw_flow` (`src/orion/jobs/validate_features.py:344-349`) while label computation pulls RVOL from `silver_alpaca_bars` (`src/orion/main_price_target_labeler.py:952`).
- Impact: false confidence from audit checks and misattributed data lineage.

6. Direct env/SDK lookups remain duplicated outside centralized config.
- Duplicate `_get_uw_client` env reads in:
  - `src/orion/main_price_target_labeler.py:1620`
  - `src/orion/jobs/backfill_ml_features.py:66`
- Impact: inconsistent runtime behavior and avoidable credential/config drift.

#### Low

7. Significant logic duplication increases migration risk.
- Shared function names between labeler and backfill include:
  `_get_uw_client`, `get_entry_time_features`, `get_flow_greeks`, `get_ticker_info`, `get_underlying_price_at_entry`, `get_underlying_price_at_offset`.
- Impact: high chance of silent divergence during future edits.

### 11.2 Module-Level Migration Readiness

| Module | Current parity status | Keep / migrate / archive |
| --- | --- | --- |
| `main_price_target_labeler.py` | Low parity (SQL-coupled, large surface area) | Keep temporarily; migrate read-path to Heber in phases |
| `ml/flow_enricher.py` | Low parity (SQL-coupled; duplicate logic) | Keep temporarily; consolidate behind Heber data facade |
| `jobs/backfill_ml_features.py` | Low parity + runtime bug | Keep temporarily; fix bug, then migrate/possibly retire |
| `jobs/window_feature_job.py` | Medium parity (window logic useful, source is SQL) | Migrate logic to Heber Gold pipeline; archive Orion job after parity |
| `jobs/data_quality_checker.py` | Low parity (checks local SQL feeds) | Rebuild against Heber dataset coverage + freshness |
| `jobs/validate_features.py` | Low parity (legacy source map assumptions) | Rewrite validation map for Heber datasets and canonical ownership |

### 11.3 Updated Priority Backlog

P0:
1. Fix `backfill_ml_features` signature bug (`get_sector_correlation_features` callsite).
2. Define and implement one Orion read facade that resolves all flow/bar/darkpool/context reads via Heber-first APIs.

P1:
1. Migrate `main_price_target_labeler` off direct `silver_uw_*` queries using the facade.
2. Align feature semantics (`entry_session`, `minutes_to_close`) across labeler, enricher, and backfill.
3. Rewrite `validate_features` source mapping to reflect actual feature lineage and Heber ownership.

P2:
1. Move reusable window aggregation to Heber Gold features; retire `window_feature_job` in Orion.
2. Replace SQL-based quality checks with Heber dataset freshness/completeness checks.

### 11.4 Archival Readiness Snapshot (Wave 2)

Not ready to archive yet (still functionally required for parity coverage):
- `src/orion/main_price_target_labeler.py`
- `src/orion/ml/flow_enricher.py`
- `src/orion/jobs/backfill_ml_features.py`
- `src/orion/jobs/window_feature_job.py`
- `src/orion/jobs/data_quality_checker.py`
- `src/orion/jobs/validate_features.py`

Ready to archive after replacement verification remains unchanged from pass 3:
- `src/orion/main_option_quote_tracker.py`
- `src/orion/jobs/backfill_historical_gex.py`
- `src/orion/jobs/backfill_exit_columns.py`

## 12) Pass 5 Continuation (2026-02-06)

### 12.1 What Was Fixed During This Audit Pass

1. Backfill runtime signature bug fixed.
- Updated `src/orion/jobs/backfill_ml_features.py` to call:
  `get_sector_correlation_features(ticker, entry_ts)`.
- Added regression test:
  `tests/unit/test_backfill_ml_features_signature.py`.
- This prevents the `TypeError` path identified in pass 4.

### 12.2 Additional Technical-Debt Findings

1. Postgres-specific SQL in core feature code reduces test/runtime portability.
- `main_price_target_labeler` uses `date_trunc(...)` at:
  - `src/orion/main_price_target_labeler.py:987`
  - `src/orion/main_price_target_labeler.py:1020`
  - `src/orion/main_price_target_labeler.py:1053`
- Additional Postgres-specific casts/operators exist across critical modules:
  - `src/orion/main_price_target_labeler.py:344` (`::date`)
  - `src/orion/ml/flow_enricher.py:665` (`::float`)
  - `src/orion/ml/flow_enricher.py:667` (`::text`)
  - `src/orion/jobs/window_feature_job.py:100` (`::text`)
- Impact: local SQLite-backed test runs or fallback envs can fail with SQL function/operator errors, reducing confidence in migration safety.

### 12.3 Updated Action Priorities

P0 completed:
- Backfill signature mismatch fix + regression test.

P1 updated:
1. Normalize SQL portability assumptions for audit-critical jobs.
- Either enforce Postgres-only execution contract explicitly in tests/docs,
  or provide compatibility shims for local/SQLite test paths.

2. Continue Heber-first migration for `main_price_target_labeler` via facade.

3. Standardize feature semantics shared across labeler/enricher/backfill:
- `entry_session` buckets
- `minutes_to_close` calculation baseline.

## 13) Pass 6 Continuation (2026-02-06)

### 13.1 Function-Level Migration Map (`main_price_target_labeler`)

The primary migration risk is concentrated in `src/orion/main_price_target_labeler.py`.
To reduce blast radius, migrate by function clusters instead of rewriting the file in one pass.

| Function cluster | Current Orion source | Target source after migration | Decision |
| --- | --- | --- | --- |
| `get_entry_signals`, `get_subsequent_prices` | `silver_uw_flow` | Heber Silver `feed=flow_alerts` + option bars path | Migrate first (critical path) |
| `get_opposing_flow`, `get_flow_aggression`, `get_institutional_flow_1w`, `get_p2_features`, `get_p3_features` | `silver_uw_flow` | Heber Silver `flow_alerts` and derived Gold context datasets | Migrate |
| `get_gex_at_entry` | `silver_greek_exposure` | Heber Silver Greek exposure feed | Migrate |
| `get_market_tide_before_entry`, regime tide component | `silver_market_tide` | Heber Silver market tide feed | Migrate |
| `get_max_pain_distance` | `silver_max_pain` | Heber Silver max-pain feed | Migrate |
| `get_iv_rank_at_entry` | Derived from `silver_uw_flow.iv` history | Heber flow history (or canonical IV-rank Gold view) | Migrate, then canonicalize |
| `get_darkpool_volume` / `get_darkpool_metrics` | `silver_uw_darkpool` | Heber Silver `feed=darkpool_trades` | Migrate |
| `get_underlying_price_at_entry`, `get_underlying_price_at_offset`, RVOL/HV/VWAP/52w calculations | `silver_alpaca_bars` | Heber Silver `feed=bars` | Migrate |
| `get_real_checkpoint_prices` | `silver_option_quotes` (Orion-local) | Keep local until Heber has canonical checkpoint quote dataset | Keep temporary |
| `get_ticker_info`, earnings/sector helpers | UW API + `silver_ticker_info` | Prefer Heber-backed reference dataset where available; fallback to API | Migrate partially |
| `persist_labels` | `price_target_labels` local table | Keep local during transition; later swap to Heber Gold writer | Keep temporary |

### 13.2 Suggested Slice Order for Safe Migration

1. Build `labeler_data_access.py` facade with Heber-backed implementations for:
- flow, bars, darkpool, gex, market-tide, max-pain.

2. Migrate read-only feature helpers first:
- no schema writes, easy parity diffing.

3. Migrate label-selection path:
- `get_entry_signals`, `get_subsequent_prices`, `label_entry`.

4. Keep `persist_labels` local until parity signoff:
- then evaluate switching to Heber Gold dataset output.

### 13.3 Parity Gate Before Any Further Archival

Do not archive additional labeler/backfill modules until the following are true:
- Heber-backed labeler output count matches legacy count within tolerance over the same date window.
- Key label columns (`return_at_*`, `first_exit_type`, `max_drawdown_pct`) pass side-by-side checks.
- Feature null-rate and range checks match or improve vs legacy baseline.

## 14) Pass 7 Continuation (2026-02-06)

### 14.1 SQL-Coupling Heatmap (Repo-Wide Refresh)

A repo-wide scan of legacy table/dataset names shows remaining coupling concentration in a small set of files:

| File | Legacy refs count |
| --- | --- |
| `src/orion/jobs/validate_features.py` | 67 |
| `src/orion/main_price_target_labeler.py` | 30 |
| `src/orion/ml/flow_enricher.py` | 14 |
| `src/orion/jobs/window_feature_job.py` | 7 |
| `src/orion/jobs/data_quality_checker.py` | 7 |
| `src/orion/ml/exit_classifier.py` | 6 |
| `src/orion/jobs/backfill_historical_gex.py` | 6 |
| `src/orion/jobs/backfill_exit_columns.py` | 6 |
| `src/orion/jobs/backfill_ml_features.py` | 5 |

Implication:
- Migration risk is now highly localized. We do not need a whole-repo rewrite to get parity; we need focused migration work on these modules.

### 14.2 Active Runtime Services: Keep/Migrate/Retire Matrix

Current compose wiring confirms these jobs/services are still live and must be included in parity validation:
- `labeler` -> `orion.main_labeler` (`docker-compose.yml:59`)
- `price_target_labeler` -> `orion.main_price_target_labeler` (`docker-compose.yml:74`)
- `feature_enrichment` -> `orion.main_feature_enrichment` (`docker-compose.yml:90`)
- `option_quote_tracker` -> `orion.main_option_quote_tracker` (`docker-compose.yml:106`)
- `pattern-miner` -> `orion.main_pattern_miner` (`docker-compose.yml:207`)
- `nightly-backfill` -> `orion.jobs.nightly_backfill` (`docker-compose.yml:224`)

| Service/module | Current role | Decision | Parity gate before retire/archive |
| --- | --- | --- | --- |
| `main_labeler` | Core flow label loop; now Heber-backed for reads | Keep | Verify event-count and label parity by day/ticker |
| `main_price_target_labeler` | Main label + feature derivation surface | Migrate (high priority) | Complete Heber facade migration + side-by-side label diffs |
| `main_feature_enrichment` | Context enrichment scheduling | Keep temporarily | Replace SQL fallback dependency and confirm feature-null rates |
| `main_option_quote_tracker` | Option quote checkpoint input | Keep temporarily | Heber canonical quote/checkpoint dataset exists and is consumed |
| `jobs/nightly_backfill` | Orchestrates ML/exit backfills | Keep temporarily | Backfill inputs fully Heber-aligned and stable |
| `main_pattern_miner` | Model training from local labels | Keep temporarily | Downstream training source of truth decision finalized |

### 14.3 Contract Drift: Darkpool Naming Mismatch

Observed naming split across systems:
- Data Gateway emits darkpool envelopes with `feed="darkpool"` (`../Data-gateway/gateway/core/uw_poller.py:365`).
- Heber Silver schema keys include `"darkpool"` (`../Heber/heber/schemas/silver.py:122`).
- Heber writer partitions Silver by `feed={envelope.feed}` and maps darkpool fields under `envelope.feed == "darkpool"` (`../Heber/heber/writer/silver.py:40`, `../Heber/heber/writer/silver.py:76`).
- Orion `HeberReader` currently reads darkpool from `_SILVER_DARKPOOL_DATASET = "darkpool_trades"` and path `silver/feed={dataset}` (`src/orion/clients/heber_reader.py:28`, `src/orion/clients/heber_reader.py:206`).
- Heber catalog dataset list still advertises `darkpool_trades` (`../Heber/heber/catalog/datasources.py:178`).

Risk:
- If Silver partitions are written to `feed=darkpool` (as current Gateway->Heber pipeline implies), Orion reads against `feed=darkpool_trades` will silently return empty DataFrames.

Action:
1. Normalize on one canonical name (`darkpool` recommended, because it matches envelope/feed and silver schema keying).
2. Make Orion `HeberReader` darkpool dataset name configurable with backward-compatible aliasing.
3. Add a contract test that asserts non-empty read path for both accepted aliases during transition.

### 14.4 Downstream ML Coupling Still Tied to Local Label Tables

Critical dependencies still point to Orion-local label/window tables:
- Exit classifier training joins `price_target_labels` + `gold_feature_windows` (`src/orion/ml/exit_classifier.py:441`, `src/orion/ml/exit_classifier.py:444`).
- Pattern miner training queries `price_target_labels` directly (`src/orion/ml/pattern_miner.py:216`).
- Nightly backfill orchestrator runs local backfill jobs (`src/orion/jobs/nightly_backfill.py:17`, `src/orion/jobs/nightly_backfill.py:18`, `src/orion/jobs/nightly_backfill.py:69`, `src/orion/jobs/nightly_backfill.py:74`).

Decision:
- Keep these training paths in Orion for now, but treat them as transitional data products.
- Do not archive ML/backfill modules until a source-of-truth decision is made for training labels/features (Orion-local vs Heber Gold).

### 14.5 Updated Archival Readiness (Wave 3)

Ready to archive now:
- No new modules promoted to "ready now" in this pass.

Candidate to archive after replacement verification:
- `src/orion/main_option_quote_tracker.py`
- `src/orion/jobs/backfill_historical_gex.py`
- `src/orion/jobs/backfill_exit_columns.py`

Explicitly not ready to archive:
- `src/orion/main_price_target_labeler.py`
- `src/orion/ml/flow_enricher.py`
- `src/orion/jobs/backfill_ml_features.py`
- `src/orion/jobs/window_feature_job.py`
- `src/orion/jobs/data_quality_checker.py`
- `src/orion/jobs/validate_features.py`
- `src/orion/ml/exit_classifier.py`
- `src/orion/ml/pattern_miner.py`

### 14.6 Decision Inputs Needed Before Step 1 (Archive Execution)

Before we execute additional archival/removal, we need explicit decisions on:
1. Training source of truth:
- Keep `price_target_labels` local in Orion, or migrate to a canonical Heber Gold dataset.

2. Darkpool canonical dataset name:
- `darkpool` vs `darkpool_trades` as the long-term feed/dataset key.

3. Feature ownership split:
- Which Orion-only derived features should be promoted into Heber Gold versus intentionally retired.

## 15) Pass 8 Continuation (2026-02-06)

### 15.1 Validation/Quality Jobs Are Still Bound to Legacy SQL Contracts

The highest-coupling job modules (`validate_features`, `data_quality_checker`, `window_feature_job`) are still coded against Orion-local legacy table/column contracts:
- `silver_uw_flow`, `silver_uw_darkpool`, `silver_alpaca_bars` (for example `src/orion/jobs/validate_features.py:286`, `src/orion/jobs/data_quality_checker.py:176`, `src/orion/jobs/window_feature_job.py:105`).

This conflicts with Heber Silver canonical naming and columns:
- Heber bars schema: `symbol`, `ts_event`, `bar_start_ts`, `open/high/low/close` (`../Heber/heber/schemas/silver.py:9`, `../Heber/heber/schemas/silver.py:25`).
- Heber flow schema (`flow_alerts`): `symbol/underlying`, `ts_event`, `premium` (`../Heber/heber/schemas/silver.py:80`, `../Heber/heber/schemas/silver.py:95`, `../Heber/heber/schemas/silver.py:100`).
- Heber darkpool schema (`darkpool`): `symbol/underlying`, `ts_event`, `size`, `price` (`../Heber/heber/schemas/silver.py:122`, `../Heber/heber/schemas/silver.py:137`, `../Heber/heber/schemas/silver.py:138`, `../Heber/heber/schemas/silver.py:139`).

| Legacy assumption in Orion jobs | Heber canonical equivalent | Migration note |
| --- | --- | --- |
| `ticker` | `symbol`/`instrument_key` | Add a single normalization helper; stop per-job aliasing |
| `flow_ts_utc` | `ts_event` | Standardize event-time column in shared facade |
| `premium_usd` | `premium` | Add compatibility alias during transition |
| `dark_ts_utc` | `ts_event` | Reuse same event-time adapter |
| `size_shares` / `trade_price` | `size` / `price` | Required for darkpool feature parity |
| `bar_start_ts_utc` | `bar_start_ts` | Required for bar gap/staleness checks |

Risk:
- These jobs can produce false "missing/stale" alerts or incorrect validation outcomes when reading Heber-backed data without compatibility adapters.

### 15.2 Feature-Lineage Mapping Drift in Validation Logic

`validate_features` currently documents return checkpoints as sourced from `silver_uw_flow` (`src/orion/jobs/validate_features.py:351` to `src/orion/jobs/validate_features.py:360`).

But current labeler behavior uses option quote checkpoints:
- `get_real_checkpoint_prices` reads from `silver_option_quotes` (`src/orion/main_price_target_labeler.py:401`, `src/orion/main_price_target_labeler.py:414`, `src/orion/main_price_target_labeler.py:2279`).

Risk:
- Validation source audits can report "green" while checking the wrong source lineage.

Action:
1. Treat `FEATURE_SOURCE_MAPPING` as a migration-controlled artifact and split it into:
- `source_of_truth_current` (actual runtime lineage)
- `source_of_truth_target` (post-Heber target lineage).
2. Add a small CI check that compares mapping entries to actual query helpers used by labeler/backfill modules.

### 15.3 Timezone Scheduling Debt (DST Drift Risk)

Two operational jobs use fixed `UTC-5` math for ET scheduling:
- `nightly_backfill.get_next_run_time` (`src/orion/jobs/nightly_backfill.py:39`).
- `data_quality_checker` market-hours constants assume fixed UTC conversion (`src/orion/jobs/data_quality_checker.py:31` to `src/orion/jobs/data_quality_checker.py:33`).

Risk:
- During daylight saving periods, schedules and market-hours gating can drift by one hour.

Action:
- Replace fixed offsets with timezone-aware conversions (`America/New_York`) in shared scheduling utilities.

### 15.4 Archive-Execution Guidance for Step 1

For these three hotspot jobs, preserve intent but retire implementation:
- Keep:
  - "feature validation" objective
  - "data quality alerting" objective
  - "window aggregation for ML context" objective
- Dispose/archive after replacement:
  - `src/orion/jobs/validate_features.py`
  - `src/orion/jobs/data_quality_checker.py`
  - `src/orion/jobs/window_feature_job.py`

Condition before archival:
- Heber-native replacements exist and pass side-by-side checks for at least one full trading week.

## 16) Pass 9 Continuation (2026-02-06)

### 16.1 Live Pipeline Gap: UW Strategies Cannot Fire in Current Runtime

Current ingestion runtime behavior is Alpaca-only:
- `_run_cycle()` only polls Alpaca events (`src/orion/ingestion/service.py:204`).
- UW polling was removed and only comments remain (`src/orion/ingestion/service.py:275`).
- `HeberReader` is instantiated but not used for reads (`src/orion/ingestion/service.py:60`).

Rule generation still depends on UW flow signals:
- Rule engine loads flow entry rules (`src/orion/processing/rule_engine.py:17` to `src/orion/processing/rule_engine.py:47`).
- Those rules require `signal.signal_type == "UW_FLOW"` (`src/orion/processing/rules/flow_rules.py:23`, `src/orion/processing/rules/flow_rules.py:174`, `src/orion/processing/rules/flow_rules.py:273`).
- Alpaca bar processing emits `OHLCV_1M` signals (`src/orion/processing/feature_engine.py:513`).

Result:
- With current runtime wiring, UW-dependent candidate generation is effectively inactive unless UW events are injected out-of-band.

### 16.2 Deployment Drift: Compose Does Not Run Ingestion Service

`docker-compose.yml` currently runs labelers, enrichment, execution, and backfills, but no ingestion service entry:
- Services include `labeler`, `price_target_labeler`, `feature_enrichment`, `execution`, `pattern-miner`, `nightly-backfill` (`docker-compose.yml:47`, `docker-compose.yml:61`, `docker-compose.yml:76`, `docker-compose.yml:108`, `docker-compose.yml:196`, `docker-compose.yml:209`).
- No service runs `python -m orion.ingestion`.

At the same time, ingestion entrypoint docs state that it reads Heber flow/darkpool:
- `src/orion/ingestion/__main__.py:8`.
- But service implementation does not yet read flow/darkpool from Heber.

Risk:
- Operational assumptions about "live UW-driven execution" may not match actual running services.

### 16.3 Integration Debt: Dual-Write Shadow Silver in Orion

Feature enrichment currently fetches from Data Gateway and writes back into Orion-local `silver_*` tables:
- Connector initialization via Gateway in `main_feature_enrichment` (`src/orion/main_feature_enrichment.py:237` to `src/orion/main_feature_enrichment.py:245`).
- Connectors persist to local tables:
  - `silver_greek_exposure` (`src/orion/connectors/uw_greek_exposure_connector.py:118`)
  - `silver_market_tide` (`src/orion/connectors/uw_market_tide_connector.py:86`)
  - `silver_max_pain` (`src/orion/connectors/uw_max_pain_connector.py:115`)
  - `silver_iv_rank` (`src/orion/connectors/uw_iv_rank_connector.py:87`)

Given Gateway+Heber already provide canonical feed handling, this creates:
- duplicate ingestion paths,
- schema/availability drift risk,
- extra failure surfaces with unclear source-of-truth.

### 16.4 Sync Earnings Auth-Contract Mismatch

`sync_earnings` builds an `UnusualWhalesClient` against Gateway URL with token `"gateway"`:
- `src/orion/jobs/sync_earnings.py:27`, `src/orion/jobs/sync_earnings.py:142`.

That client sends `Authorization: Bearer <token>`:
- `src/orion/unusualwhales/client.py:98`, `src/orion/unusualwhales/client.py:130`.

Gateway UW endpoints require `X-Gateway-Key`:
- `../Data-gateway/gateway/api/deps.py:103`, `../Data-gateway/gateway/api/deps.py:111`.
- UW route handlers depend on `require_api_key` (for example `../Data-gateway/gateway/api/uw/earnings.py:26` and `../Data-gateway/gateway/api/uw/options.py:55`).

Risk:
- Earnings sync can fail authorization in production while appearing as intermittent fetch errors.

### 16.5 Test Coverage Gap for New Integration Boundary

Current tests cover Heber reader basics and ticker extraction, but not end-to-end Gateway auth contracts or live UW signal availability:
- Heber reader tests: `tests/unit/test_heber_reader.py`
- Feature enrichment ticker extraction only: `tests/unit/test_feature_enrichment_heber_source.py`

No dedicated tests found for:
- `sync_earnings` Gateway auth header contract,
- connector->Gateway endpoint/auth compatibility,
- runtime guarantee that ingestion produces UW signals when Heber has flow data.

### 16.6 Updated P0 Priorities

P0:
1. Re-enable UW signal production in runtime by implementing Heber->BronzeEvent ingestion adapters in `IngestionService`.
2. Add ingestion service to deployment profile (or document intentional non-use explicitly with equivalent replacement path).
3. Fix `sync_earnings` to use `X-Gateway-Key` auth path compatible with Gateway dependencies.

P1:
1. Replace dual-write feature enrichment connectors with Heber-first reads where feasible.
2. Add integration tests for Gateway auth and Heber-backed UW signal pipeline viability.

### 16.7 Archival Readiness Update (Wave 4)

Not ready to archive:
- `src/orion/processing/rules/flow_rules.py` (still active strategy logic)
- `src/orion/processing/rule_engine.py` (orchestration still required)
- `src/orion/ingestion/service.py` (needs completion, not retirement)

Candidate to archive after runtime completion:
- Legacy UW event persistence branches in `src/orion/processing/persistence.py` that mirror canonical Heber silver once Heber->signal adapters are productionized and validated.

## 17) Pass 10 Continuation (2026-02-06)

### 17.1 Execution Path Split-Brain (Two Live Implementations)

There are two execution-loop implementations with different behavior:
- `src/orion/main_execution.py` (DB polling style, large monolith).
- `src/orion/execution/service.py` (queue-driven `ExecutionService`).

Compose currently runs `python -m orion.main_execution`:
- `docker-compose.yml:124`.

Risk:
- Fixes/features can be added to one path but not the other, creating silent behavior drift.
- Operationally, there is no single confirmed source-of-truth execution loop.

### 17.2 ML Prefilter Contract Mismatch Can False-Skip Rule Candidates

`SignalEngine` builds ML prefilter input from `CandidateTrade`:
- `src/orion/processing/signal_engine.py:106` to `src/orion/processing/signal_engine.py:123`.

It sends:
- `premium_usd` from `candidate.premium`,
- `put_call` from `candidate.option_type`,
- `dte` from `candidate.expiration_date`.

But rule-generated candidates typically do not populate those option fields:
- base candidate factory sets only core fields (`src/orion/processing/rules/base.py:86` to `src/orion/processing/rules/base.py:94`).
- flow rules mostly attach context under `evidence` / `execution_params` (`src/orion/processing/rules/flow_rules.py:223` to `src/orion/processing/rules/flow_rules.py:243`).
- `CandidateTrade` option fields are nullable (`src/orion/storage/models_gold.py:33` to `src/orion/storage/models_gold.py:39`).

Risk:
- ML prefilter can evaluate incomplete candidate payloads and reject otherwise valid rule signals.

### 17.3 Inference Enrichment Still Uses Orion-Local Silver Contracts

Enrichment used for score parity remains SQL-local:
- `enrich_flow_for_scoring` hits `silver_uw_flow`, `silver_uw_darkpool`, `silver_alpaca_bars`, `gold_feature_windows` (`src/orion/ml/flow_enricher.py:346`, `src/orion/ml/flow_enricher.py:392`, `src/orion/ml/flow_enricher.py:483`, `src/orion/ml/flow_enricher.py:1018`).

This means inference parity work is still coupled to Orion-local tables rather than Heber canonical datasets.

### 17.4 ML Flow Processor Exists but Is Not Wired Into Active Runtime

`MLFlowProcessor` provides enriched scoring path:
- `src/orion/ml/flow_processor.py:22`, `src/orion/ml/flow_processor.py:83`.

No active runtime module currently references this processor for ingestion/execution orchestration.

Risk:
- Parallel "intended" ML-first flow path exists but is not part of deployed service flow, increasing maintenance and confusion.

### 17.5 Updated Priorities

P0:
1. Standardize on one execution entrypoint (`main_execution` vs `ExecutionService`) and retire the other path.
2. Fix ML prefilter input contract:
- either populate option fields on `CandidateTrade` for rule-based candidates,
- or build prefilter inputs from `candidate.evidence`/source signal payload instead of nullable option columns.

P1:
1. Move `flow_enricher` queries behind Heber facade equivalents to remove direct `silver_*` coupling.
2. Decide whether to wire `MLFlowProcessor` into production flow or archive it as inactive.

### 17.6 Archival Readiness Update (Wave 5)

Candidate for archive after entrypoint consolidation:
- one of:
  - `src/orion/main_execution.py`
  - `src/orion/execution/service.py`

Not ready to archive:
- `src/orion/processing/signal_engine.py`
- `src/orion/processing/rules/flow_rules.py`
- `src/orion/ml/flow_enricher.py`

## 18) Pass 11 Continuation (2026-02-06)

### 18.1 Queue-Driven Execution Path Is Unwired in Deployment

Queue-based execution primitives are isolated from the deployed runtime:
- `CandidateQueue` usage appears only in `src/orion/execution/service.py` (`src/orion/execution/service.py:89`, `src/orion/execution/service.py:180`, `src/orion/execution/service.py:221`).
- `ExecutionService` is only defined there and not referenced by other runtime modules (`src/orion/execution/service.py:28`).
- Compose deploys `python -m orion.main_execution`, not `ExecutionService` (`docker-compose.yml:124`).
- Active execution path polls DB directly for unprocessed candidates (`src/orion/main_execution.py:48`, `src/orion/main_execution.py:254`).

Risk:
- Queue-specific tests/changes can pass while production behavior remains unaffected.
- Two execution mental models remain in code, slowing migration and incident response.

### 18.2 Rollup Guardrails Are Soft-Disabled in Current Compose Profile

Rollup generation and rollup enforcement are split across inactive/active paths:
- Rollup job is started by ingestion service initialization (`src/orion/ingestion/service.py:123` to `src/orion/ingestion/service.py:129`).
- Compose does not run ingestion service (`docker-compose.yml` service list; no `orion.ingestion` command).
- Compose execution explicitly disables rollup requirement (`docker-compose.yml:123`).
- Preflight only hard-rejects on missing rollups when the config flag is enabled (`src/orion/execution/signal_preflight.py:140` to `src/orion/execution/signal_preflight.py:148`).

Risk:
- Signal preflight can run without intended rollup completeness guarantees, reducing decision-quality safeguards.

### 18.3 Changelog-to-Code Drift on Execution Consolidation

The changelog currently states:
- "`main_execution.py` is now a thin wrapper (38 lines) that delegates to `ExecutionService.run()`" (`CHANGELOG.md:310`).

Current code state does not match:
- `main_execution.py` is still a full loop implementation (~363 lines) with candidate polling, preflight, execution, and exit-rule evaluation (`src/orion/main_execution.py:201` to `src/orion/main_execution.py:357`).
- `ExecutionService` remains separate and unwired to compose (`src/orion/execution/service.py:28`, `docker-compose.yml:124`).

Risk:
- Operational/debug assumptions based on changelog can target the wrong codepath.

### 18.4 Labeling Stack Fragmentation (Three Parallel Label Pipelines)

Three distinct label stacks coexist:
1. Heber-read -> `flow_labels` writer:
- `main_labeler` reads flow from Heber and persists to `flow_labels` (`src/orion/main_labeler.py:140`, `src/orion/main_labeler.py:318`).
2. Price-target label pipeline:
- `main_price_target_labeler` builds `price_target_labels` (`src/orion/main_price_target_labeler.py:348`, `src/orion/main_price_target_labeler.py:2705`).
3. PRD 6.3 candidate/window labels:
- `label_job` writes `candidate_labels`/`labels_event` (`src/orion/jobs/label_job.py:11`, `src/orion/jobs/label_job.py:162`, `src/orion/jobs/label_job.py:182`).
- `window_label_job` writes `labels_window` (`src/orion/jobs/window_label_job.py:11`, `src/orion/jobs/window_label_job.py:135`).

Only `price_target_labels` is directly consumed by active ML trainers:
- `pattern_miner` training query (`src/orion/ml/pattern_miner.py:216`).
- `exit_classifier` training query (`src/orion/ml/exit_classifier.py:441`).

Risk:
- Duplicate labeling logic increases maintenance burden and schema drift while only one label family materially drives model training.

### 18.5 Updated Priorities

P0:
1. Select canonical execution path now (DB polling vs queue) and archive the non-canonical path.
2. Re-enable rollup guarantees by either:
- restoring ingestion/rollup runtime, or
- creating a dedicated rollup service in compose and turning `ORION_REQUIRE_ROLLUPS_FOR_SIGNALS_LIVE` back on.
3. Correct changelog drift for execution architecture to prevent operator error.

P1:
1. Collapse to one label family for model training (`price_target_labels` or Heber-gold successor) and mark others as compatibility-only or archive candidates.

### 18.6 Archival Readiness Update (Wave 6)

Archive candidates after execution decision:
- If DB-polling path remains canonical:
  - `src/orion/execution/service.py`
  - `src/orion/shared/candidate_queue.py`
  - `tests/unit/test_candidate_queue.py`
- If queue path becomes canonical:
  - `src/orion/main_execution.py`

Archive candidates after label consolidation:
- `src/orion/main_labeler.py` + `flow_labels` path (if `price_target_labels` or Heber-gold labels remain canonical and no external consumer depends on `flow_labels`)
- `src/orion/jobs/label_job.py`
- `src/orion/jobs/window_label_job.py`

## 19) Pass 12 Continuation (2026-02-06)

### 19.1 Gateway Auth Contract Drift in Active Feature Enrichment Runtime

Feature-enrichment connectors require Gateway key auth:
- Connectors build `X-Gateway-Key` from `system_settings.data_gateway_api_key` (`src/orion/connectors/uw_greek_exposure_connector.py:27` to `src/orion/connectors/uw_greek_exposure_connector.py:29`, similarly `src/orion/connectors/uw_market_tide_connector.py:27`, `src/orion/connectors/uw_iv_rank_connector.py:27`, `src/orion/connectors/uw_max_pain_connector.py:27`).

Current compose service config sets `GATEWAY_URL` but not Gateway key env:
- Feature enrichment env includes `GATEWAY_URL` and `UW_API_KEY` (`docker-compose.yml:87` to `docker-compose.yml:90`).
- No `DATA_GATEWAY_API_KEY` or `GATEWAY_API_KEY` env is set for that service.

Gateway API enforces key header:
- `require_api_key` rejects missing `X-Gateway-Key` (`../Data-gateway/gateway/api/deps.py:101` to `../Data-gateway/gateway/api/deps.py:115`).

Risk:
- Feature enrichment can run while silently persisting little/no fresh data due auth failures.

### 19.2 Direct Alpaca Bypass Still Exists in Option Quote Pipeline

`main_option_quote_tracker` still bypasses Data Gateway:
- Instantiates `AlpacaOptionGreeksConnector` directly (`src/orion/main_option_quote_tracker.py:180`).
- Connector calls Alpaca endpoint `https://data.alpaca.markets` using direct APCA headers (`src/orion/connectors/alpaca_option_greeks_connector.py:25`, `src/orion/connectors/alpaca_option_greeks_connector.py:37` to `src/orion/connectors/alpaca_option_greeks_connector.py:39`).

Data Gateway already exposes Alpaca options routes:
- `/api/v1/alpaca/options/snapshots/{underlying}` and related options endpoints (`../Data-gateway/gateway/api/alpaca/options.py:239`).

Risk:
- Orion bypasses centralized provider throttling, auth, and observability layers that the migration intended to standardize.

### 19.3 Label/Data Product Decision Matrix (Keep vs Migrate vs Dispose)

| Label/Feature Family | Current Producer Path | Active Consumer Path | Heber/Gateway Integration Status | Decision |
| --- | --- | --- | --- | --- |
| `flow_labels` | `main_labeler` (`src/orion/main_labeler.py:318`) | no in-repo consumer found (search references only in producer) | Reads from Heber, but writes local SQL only | **Dispose/Archive candidate** after external consumer check |
| `price_target_labels` | `main_price_target_labeler` (`src/orion/main_price_target_labeler.py:2705`) | `pattern_miner`, `exit_classifier`, backfills (`src/orion/ml/pattern_miner.py:216`, `src/orion/ml/exit_classifier.py:441`) | Heavy local `silver_*` SQL coupling; no Heber gold write | **Keep + migrate to Heber gold** |
| `candidate_labels` / `labels_event` / `labels_window` | `label_job` + `window_label_job` (`src/orion/jobs/label_job.py:162`, `src/orion/jobs/window_label_job.py:135`) | no active compose service wiring | Legacy PRD path, not part of current deployed loop | **Archive candidate** after deprecation notice |
| `gold_feature_windows` | `window_feature_job` / rollup-adjacent jobs (`src/orion/jobs/window_feature_job.py:193`) | `exit_classifier`, `flow_enricher` (`src/orion/ml/exit_classifier.py:444`, `src/orion/ml/flow_enricher.py:1031`) | Consumer-critical, producer wiring unclear in compose | **Keep, but migrate producer to explicit Heber-aligned service** |

### 19.4 Heber Gold Contract Gap for Orion Label Outputs

Heber gold datasets require canonical time/instrument semantics (including `instrument_key`, `ts_event`, `ts_available`) per contract:
- `../Heber/docs/data_contract.md`.

Current Orion label outputs are local-table schemas keyed around `ticker`, `entry_ts`, and event IDs:
- `flow_labels` insert shape (`src/orion/main_labeler.py:318`).
- `price_target_labels` persistence path (`src/orion/main_price_target_labeler.py:2705`).

No `write_gold(...)` integration usage found in Orion runtime modules.

Risk:
- Even when labels/features are high-value, they are not yet publishable through Heber-native reproducible/as-of semantics.

### 19.5 Updated Priorities

P0:
1. Fix compose env contract for Gateway-backed connectors (`DATA_GATEWAY_API_KEY`/`GATEWAY_API_KEY`) and add startup fail-fast if key is missing.
2. Migrate option quote checkpoint pipeline to Gateway-backed Alpaca options endpoints to remove direct provider bypass.
3. Declare one canonical label family for model training (recommendation: `price_target_labels` lineage), and mark others deprecated.

P1:
1. Define Heber gold schema for retained label products with `instrument_key` + `ts_event` + `ts_available`.
2. Move retained label/feature writers behind Heber SDK write paths; keep local SQL only as temporary cache during migration.

### 19.6 Archival Readiness Update (Wave 7)

High-confidence archive candidates (after one explicit stakeholder sign-off on external consumers):
- `src/orion/main_labeler.py` + `flow_labels` pipeline
- `src/orion/jobs/label_job.py`
- `src/orion/jobs/window_label_job.py`

Conditional archive candidates (after Heber-native replacements are live):
- local dual-write enrichment connectors that persist `silver_greek_exposure` / `silver_market_tide` / `silver_max_pain` / `silver_iv_rank`

## 20) Pass 13 Continuation (2026-02-06)

### 20.1 Archival Executed: Queue-Driven Execution Path (Wave 6)

Wave-6 archive action completed:
- `src/orion/execution/service.py` -> `archive/2026-02-06_runtime-consolidation-wave6/legacy_code/execution_service.py`
- `src/orion/shared/candidate_queue.py` -> `archive/2026-02-06_runtime-consolidation-wave6/legacy_code/candidate_queue.py`
- `tests/unit/test_candidate_queue.py` -> `archive/2026-02-06_runtime-consolidation-wave6/legacy_tests/test_candidate_queue.py`
- archive manifest added at `archive/2026-02-06_runtime-consolidation-wave6/README.md`

Rationale:
- compose runtime executes `orion.main_execution` (`docker-compose.yml:124`);
- no active runtime module referenced `ExecutionService`;
- `CandidateQueue` usage was isolated to archived service path.

### 20.2 Post-Archive Runtime State

Execution runtime source-of-truth is now unambiguous in repo:
- `src/orion/main_execution.py` is the active execution entrypoint.

Residual risk:
- If any future branch expects queue-driven execution, reintroduction should happen only via explicit runtime switch + compose wiring rather than parallel shadow path.

### 20.3 Updated Priorities

P0:
1. Complete auth-contract hardening for Gateway-backed services (feature enrichment + earnings sync) to prevent silent data starvation.
2. Start Wave-7 archival decisions for label fragmentation (`flow_labels` and PRD 6.3 label jobs) after external consumer confirmation.

P1:
1. Migrate retained label and feature writers to Heber-gold contracts (`instrument_key`, `ts_event`, `ts_available`) and deprecate local SQL-only sinks.

## 21) Pass 14 Continuation (2026-02-06)

### 21.1 Heber Already Has Canonical Alert-Label Gold Path (Good News)

Heber provides a first-class alert-label pipeline that already writes to Gold with as-of semantics:
- Pipeline orchestration: `../Heber/heber/features/pipelines/alert_labels.py:43`.
- Gold write via SDK contract (`instrument_key`, `ts_event`, `ts_available`): `../Heber/heber/features/pipelines/alert_labels.py:231`, `../Heber/heber/sdk/client.py:409` to `../Heber/heber/sdk/client.py:433`.
- Label schema includes barrier outcomes and availability timestamps: `../Heber/heber/features/templates/alert_labels.py:474` to `../Heber/heber/features/templates/alert_labels.py:496`.

Implication:
- Orion `flow_labels` and parts of local label persistence can be retired in favor of Heber-native gold labels once contract gaps below are fixed.

### 21.2 Cross-Repo Contract Gap: Heber Alert Pipeline vs Data Gateway Options API

Heber option-bar fetch currently calls:
- `GET {gateway}/api/v1/alpaca/options/bars` with `symbols=<csv>` (`../Heber/heber/features/pipelines/alert_labels.py:362` to `../Heber/heber/features/pipelines/alert_labels.py:368`).

Data Gateway currently exposes:
- `GET /api/v1/alpaca/options/{contract}/bars` (single contract path) (`../Data-gateway/gateway/api/alpaca/options.py:109`).
- No matching `/options/bars` handler found in gateway routes (catalog lists it, but no route implementation in `gateway/api/alpaca/options.py`).

Risk:
- Heber contract-label enrichment can fail at runtime due endpoint shape mismatch, blocking parity replacement of Orion checkpoint labeling.

### 21.3 Cross-Repo Auth Gap: Heber Alert Pipeline Missing Gateway Key Header

Data Gateway Alpaca options endpoints require API-key dependency:
- `client: Client = Depends(require_api_key)` on options routes (`../Data-gateway/gateway/api/alpaca/options.py:116`).
- `require_api_key` enforces `X-Gateway-Key` (`../Data-gateway/gateway/api/deps.py:103` to `../Data-gateway/gateway/api/deps.py:115`).

Heber alert pipeline gateway calls do not set auth headers:
- Request call has no `headers=` containing `X-Gateway-Key` (`../Heber/heber/features/pipelines/alert_labels.py:361` to `../Heber/heber/features/pipelines/alert_labels.py:369`).

Risk:
- Even with endpoint path fixed, pipeline can still fail with 401 in secured environments.

### 21.4 What To Keep and Add to Heber vs Dispose in Orion

Keep and migrate to Heber:
1. `price_target_labels` outcome semantics used by active trainers (`src/orion/ml/pattern_miner.py:216`, `src/orion/ml/exit_classifier.py:441`), but publish as Heber gold datasets with canonical columns.
2. Entry-time feature bundle used for model training (`src/orion/ml/pattern_miner.py:36` to `src/orion/ml/pattern_miner.py:106`) as separate Heber gold features dataset.
3. Checkpoint option-state features used by exit classifier (`src/orion/ml/exit_classifier.py:377` to `src/orion/ml/exit_classifier.py:420`) as optional Heber gold extension dataset.

Dispose/archive in Orion:
1. `flow_labels` pipeline (already non-consumer in repo) after external-consumer verification.
2. PRD 6.3 label jobs (`label_job`, `window_label_job`) unless reattached to active runtime and Heber contract.

### 21.5 Updated Priorities

P0:
1. Align Heber `alert_labels` gateway contract (endpoint shape + `X-Gateway-Key`) before using it as Orion replacement.
2. Define Heber dataset split for Orion training parity:
- `labels_alert_barriers` (outcomes),
- `features_alert_entry` (entry context),
- `features_alert_checkpoints` (checkpoint Greeks/returns, if retained).

P1:
1. Decommission Orion-local label sinks once Heber parity datasets satisfy current pattern-miner and exit-classifier queries.

## 22) Pass 15 Continuation (2026-02-06)

### 22.1 Archival Executed: Legacy PRD 6.3 Label Jobs (Wave 7)

Wave-7 archive action completed:
- `src/orion/jobs/label_job.py` -> `archive/2026-02-06_label-stack-wave7/legacy_code/label_job.py`
- `src/orion/jobs/window_label_job.py` -> `archive/2026-02-06_label-stack-wave7/legacy_code/window_label_job.py`
- archive manifest added at `archive/2026-02-06_label-stack-wave7/README.md`

Evidence used:
- no active compose entrypoints reference these jobs (`docker-compose.yml`).
- no active `src/` references to these module paths after archival sweep.

### 22.2 Label Stack State After Wave 7

Active label/training path remains:
- `price_target_labels` via `main_price_target_labeler`.

Archived inactive alternatives:
- queue-based execution-linked label jobs (`label_job`, `window_label_job`).

Still active but disposal candidate:
- `main_labeler` (`flow_labels`) pending external-consumer confirmation.

### 22.3 Updated Priorities

P0:
1. Resolve Data Gateway contract/auth gaps blocking Heber alert-label pipeline parity (`options bars` route shape + `X-Gateway-Key`).
2. Define and implement Heber gold datasets for retained Orion training columns, then begin controlled retirement of `price_target_labels`.

P1:
1. Confirm external consumers of `flow_labels`; archive `main_labeler` if no consumer exists.

## 23) Pass 16 Continuation (2026-02-06)

### 23.1 `flow_labels` Appears Write-Only Inside This Repo

Current reference sweep shows:
- `flow_labels` is read/written only by `main_labeler` itself (`src/orion/main_labeler.py:122`, `src/orion/main_labeler.py:318`).
- No in-repo API, ML trainer, or job consumer reads `flow_labels`.
- Compose still deploys the labeler service (`docker-compose.yml:59`).

Risk:
- Running a write-only service consumes resources and can mislead operators into assuming downstream usage that does not exist.

### 23.2 Active Training Dependencies Are Centered on `price_target_labels`

Active in-repo consumers continue to use `price_target_labels`:
- pattern mining (`src/orion/ml/pattern_miner.py:216`),
- exit classifier (`src/orion/ml/exit_classifier.py:441`),
- backfill/validation jobs (`src/orion/jobs/backfill_ml_features.py:286`, `src/orion/jobs/validate_features.py:248`, `src/orion/jobs/nightly_backfill.py:4`).

Implication:
- Migration and parity should prioritize `price_target_labels` successor datasets in Heber; `flow_labels` should be considered compatibility-only until proven externally required.

### 23.3 Updated Priorities

P0:
1. Verify whether any external dashboard/consumer depends on `flow_labels`.
2. If none, move `labeler` compose service behind an opt-in profile and schedule archival of `main_labeler`.

P1:
1. Redirect any true `flow_labels` consumer to Heber `labels_alert_barriers` (after Gateway contract/auth fixes) instead of maintaining local Orion table writes.

## 24) Pass 17 Continuation (2026-02-06)

### 24.1 Compose Env Contracts Still Drift from Gateway/Auth Requirements

Feature enrichment runtime:
- Connectors send `X-Gateway-Key` only when `system_settings.data_gateway_api_key` is set (`src/orion/connectors/uw_greek_exposure_connector.py:27` to `src/orion/connectors/uw_greek_exposure_connector.py:29`, similarly market tide/iv/max pain connectors).
- Compose service config sets `GATEWAY_URL` but does not set `DATA_GATEWAY_API_KEY`/`GATEWAY_API_KEY` (`docker-compose.yml:86` to `docker-compose.yml:90`).

Price-target labeler runtime:
- Service config sets `GATEWAY_URL` only (`docker-compose.yml:71` to `docker-compose.yml:74`).
- Labeler still performs direct UW API lookup via `UW_API_KEY` for ticker/earnings metadata (`src/orion/main_price_target_labeler.py:1624` to `src/orion/main_price_target_labeler.py:1629`), not Gateway.

Risk:
- Gateway-backed requests can degrade to repeated 401/empty results, while direct-UW fallback behavior remains inconsistent with centralization goals.

### 24.2 Archival Executed: Orphaned Integration Modules (Wave 8)

Wave-8 archive action completed:
- `src/orion/connectors/uw_ticker_info_connector.py` -> `archive/2026-02-06_integration-debt-wave8/legacy_code/uw_ticker_info_connector.py`
- `src/orion/jobs/backfill_historical_gex.py` -> `archive/2026-02-06_integration-debt-wave8/legacy_code/backfill_historical_gex.py`
- archive manifest added at `archive/2026-02-06_integration-debt-wave8/README.md`

Rationale:
- both modules had no active in-repo runtime wiring in compose/service entrypoints,
- both represented side-paths that bypass or duplicate current Gateway/Heber migration direction.

### 24.3 Updated Priorities

P0:
1. Normalize service env contracts in compose:
- add `DATA_GATEWAY_API_KEY` where Gateway routes are used,
- decide whether to remove direct `UW_API_KEY` dependencies from `main_price_target_labeler` or wire them explicitly as transitional.
2. Add startup fail-fast for Gateway-backed services when URL is present but API key is missing.

P1:
1. Complete migration of ticker/earnings metadata lookups in labeling flow to Gateway/Heber canonical paths and retire remaining direct-UW client usage.

## 25) Pass 18 Continuation (2026-02-06)

### 25.1 `sync_earnings` Gateway Contract Is Broken by Path and Auth Shape

Current Orion implementation:
- `sync_earnings` builds a UW SDK client using `base_url=f"{gateway_url}/api/v1/uw"` with bearer token auth (`src/orion/jobs/sync_earnings.py:27`, `src/orion/jobs/sync_earnings.py:142`, `src/orion/unusualwhales/client.py:56`, `src/orion/unusualwhales/client.py:98`).
- Orion UW SDK earnings endpoints are hardcoded as `/api/earnings/...` (`src/orion/unusualwhales/api/earnings/get_premarket.py:26`, `src/orion/unusualwhales/api/earnings/get_afterhours.py:26`, `src/orion/unusualwhales/api/earnings/get_ticker_earnings.py:18`).

Resulting request path shape becomes:
- `{gateway}/api/v1/uw/api/earnings/...` (extra `/api` segment), not Gateway route shape.

Data Gateway route/auth contract:
- earnings routes are `/api/v1/uw/earnings/...` (`../Data-gateway/gateway/api/uw/earnings.py:22`, `../Data-gateway/gateway/api/uw/earnings.py:45`, `../Data-gateway/gateway/api/uw/earnings.py:68`),
- protected by `require_api_key` (Gateway key contract), not UW bearer token (`../Data-gateway/gateway/api/uw/earnings.py:26`, `../Data-gateway/gateway/api/uw/earnings.py:49`, `../Data-gateway/gateway/api/uw/earnings.py:72`).

Risk:
- daily sync/backfill can fail silently or return empty payloads, producing stale `silver_earnings_calendar` features used by labeling/training.

### 25.2 Label Ontology Drift: Orion `price_target_labels` vs Heber Gold Labels

Heber label contract today:
- `labels_alert_barriers`/`labels_alert_intraday`/`labels_alert_swing` centered on barrier outcomes and compact context fields (`../Heber/features/feature_views/alert_labels.py:27`, `../Heber/features/feature_views/alert_labels.py:43`, `../Heber/features/feature_views/alert_labels.py:80`, `../Heber/features/feature_views/alert_labels.py:130`).

Orion label contract today:
- `main_price_target_labeler` builds a wide, checkpoint-heavy + enrichment-heavy row with dynamic insert into `price_target_labels` (`src/orion/main_price_target_labeler.py:2093`, `src/orion/main_price_target_labeler.py:2438`, `src/orion/main_price_target_labeler.py:2528`, `src/orion/main_price_target_labeler.py:2684`, `src/orion/main_price_target_labeler.py:2705`).

Implication:
- direct replacement of Orion training table with current Heber label views is not parity-complete.
- migration needs explicit split:
  1. keep Heber barrier labels as decision/outcome labels,
  2. migrate Orion ML enrichment/checkpoint columns into Heber feature datasets (or a dedicated training-fact gold dataset),
  3. then decommission local `price_target_labels`.

### 25.3 Archival Executed: Deprecated Runner Debt (Wave 9)

Wave-9 archive action completed:
- `src/orion/run_agent.py` -> `archive/2026-02-06_runner-debt-wave9/legacy_code/run_agent.py`
- `src/orion/paper_live_harness.py` -> `archive/2026-02-06_runner-debt-wave9/legacy_code/paper_live_harness.py`
- archive manifest added at `archive/2026-02-06_runner-debt-wave9/README.md`

Rationale:
- neither file is wired in compose/runtime entrypoints,
- `run_agent.py` is an explicit deprecated stub,
- harness logic depended on legacy runner assumptions.

### 25.4 Updated Priorities

P0:
1. Replace `sync_earnings` UW-SDK-through-gateway usage with direct Gateway client calls using canonical routes (`/api/v1/uw/earnings/*`) and `X-Gateway-Key`.
2. Define the Heber target for Orion `price_target_labels` parity as two artifacts:
- outcome labels (existing barrier views),
- training-fact features (checkpoint/entry-context fields currently local to Orion).

P1:
1. Run a repo-wide cleanup plan for remaining local scripts/jobs that assume deprecated runner paths.

## 26) Pass 19 Continuation (2026-02-06)

### 26.1 `sync_todays_earnings` Overwrites Provider Dates with Local `today`

Current behavior:
- `sync_todays_earnings` computes `today = date.today()` and passes that value into every upsert call (`src/orion/jobs/sync_earnings.py:29`, `src/orion/jobs/sync_earnings.py:32`, `src/orion/jobs/sync_earnings.py:33`, `src/orion/jobs/sync_earnings.py:50`).
- Data Gateway provider path emits normalized earnings rows with per-record date from payload (`../Data-gateway/gateway/providers/uw.py:740`, `../Data-gateway/gateway/providers/uw.py:796`, `../Data-gateway/gateway/providers/uw.py:852`).

Risk:
- when upcoming earnings are not on local `today` (or on non-trading days), Orion can store wrong `report_date` values in `silver_earnings_calendar`, corrupting downstream `days_to_earnings` / post-earnings features.

### 26.2 Earnings Sync Runtime Is Still Coupled to Ingestion Startup Path

Current call graph:
- `sync_todays_earnings` is invoked from ingestion service init (`src/orion/ingestion/service.py:116` to `src/orion/ingestion/service.py:119`).
- no other active runtime service path in this repo invokes it directly (only module CLI path remains in the job file itself).

Risk:
- if ingestion runtime is not active in deployment, earnings calendar freshness can drift even before resolving Gateway contract mismatches.

### 26.3 Updated Priorities

P0:
1. Fix daily earnings sync semantics to use record-level provider date fields (with strict parse/validation) instead of forcing `date.today()`.
2. Decouple earnings sync from ingestion startup by scheduling an explicit daily job path (or service profile) so it remains active in current deployment topology.

P1:
1. Add a parity check job comparing recent `silver_earnings_calendar.report_date` values against Gateway payload dates to detect drift early.

## 27) Pass 20 Continuation (2026-02-06)

### 27.1 Heber Watch Quote Pull Uses Nonexistent/Unauthorized Gateway Contract

Current Heber watch usage:
- watch consumer requests `GET {gateway}/api/v1/alpaca/options/quotes` with `symbols=<occ>` and no Gateway auth header (`../Heber/heber/watch/consumer.py:417` to `../Heber/heber/watch/consumer.py:420`).
- snapshot poller uses the same route and query shape for batched symbols (`../Heber/heber/watch/poller.py:164` to `../Heber/heber/watch/poller.py:167`).

Data Gateway contract:
- options quote route is per-contract path `GET /api/v1/alpaca/options/{contract}/quotes` (`../Data-gateway/gateway/api/alpaca/options.py:157`).
- route requires API key via `require_api_key` (`../Data-gateway/gateway/api/alpaca/options.py:160`).

Risk:
- Heber watch/monitoring flow can return 404/401 or empty quote paths, degrading alert-watch label quality and producing silent parity gaps versus intended Gateway-centralized reads.

### 27.2 Updated Priorities

P0:
1. Align Heber watch quote-fetch contract to Gateway routes (either per-contract fetches or a newly added bulk quotes route in Data Gateway).
2. Add explicit Gateway auth wiring (`X-Gateway-Key`) for watch consumer/poller HTTP clients.

P1:
1. Add integration tests in Heber that validate quote-fetch success against live Gateway route catalog to catch route-shape drift early.

## 28) Pass 21 Continuation (2026-02-06)

### 28.1 Ops/Remediation Job Inventory Still Largely Unwired in Active Runtime

Current reference sweep indicates several jobs are not wired through compose/runtime entrypoints and are mainly self-contained CLIs and/or test-only references:

- `dlq_consumer` appears referenced only by itself + unit test (`src/orion/jobs/dlq_consumer.py:129`, `tests/unit/test_dlq_consumer.py:5`).
- `monitor_system` appears referenced only by unit test (`tests/unit/test_monitor_system.py:10`).
- `reconcile_backfill` appears referenced only by remediation unit test (`tests/unit/test_remediation_rules.py:5`).
- `gatekeeper` has no in-repo callers beyond its own module CLI (`src/orion/jobs/gatekeeper.py:198`).
- `seed_solvers` has no in-repo references in current sweep.

Risk:
- operator expectations drift: job files exist but are not part of the currently deployed service topology, making incident response and ownership unclear.

### 28.2 Updated Priorities

P0:
1. Produce an explicit “operational jobs matrix” (owner, trigger path, cadence, required env, runtime profile) for all `src/orion/jobs/*` modules.
2. For jobs with no active owner/cadence, move to archive wave after sign-off.

P1:
1. For jobs that are retained, add compose `tools` profile wiring and runbook links so they are intentionally operable instead of implicitly dormant.

## 29) Pass 22 Continuation (2026-02-06)

### 29.1 Cross-Repo Default URL Drift: Heber Uses `:8000` for Data Gateway

Data Gateway canonical local port:
- Gateway README and compose standardize API at `http://localhost:8080` (`../Data-gateway/README.md:81`, `../Data-gateway/README.md:88`, `../Data-gateway/docker-compose.yml:8`).

Heber defaults still point Data Gateway usage to `http://localhost:8000`:
- alert-label pipeline default (`../Heber/heber/features/pipelines/alert_labels.py:40`, `../Heber/heber/features/pipelines/alert_labels.py:540`),
- watch consumer default (`../Heber/heber/watch/consumer.py:35`),
- watch CLI default (`../Heber/heber/watch/__main__.py:23`),
- Heber README watch env docs (`../Heber/README.md:78`).

Conflict:
- In Heber local topology, `:8000` is lakeFS (`../Heber/README.md:27`), not Data Gateway.

Risk:
- out-of-box runs can silently target the wrong service, causing quote/label pipelines to fail or behave unpredictably even before route/auth fixes are applied.

### 29.2 Updated Priorities

P0:
1. Normalize Heber Data Gateway defaults to `http://localhost:8080` across code + docs (`alert_labels`, watch modules, README).
2. Add startup validation in Heber Gateway-dependent services that detects obvious service mismatches (for example lakeFS response signature on expected Gateway URL) and fails fast.

P1:
1. Add an environment contract test asserting Data Gateway URL consistency between Orion, Heber, and Data Gateway defaults.

## 30) Pass 23 Continuation (2026-02-06)

### 30.1 Orion Flow Persistence Still Performs Direct Alpaca Greeks Calls

Current Orion persistence path:
- `persist_silver_from_bronze` enriches UW flows via `_enrich_flows_with_greeks` before write (`src/orion/processing/persistence.py:142`, `src/orion/processing/persistence.py:263` to `src/orion/processing/persistence.py:264`).
- enrichment uses `AlpacaOptionGreeksConnector.get_greeks_batch` (`src/orion/processing/persistence.py:113`, `src/orion/processing/persistence.py:123`).
- connector calls Alpaca snapshots directly (`https://data.alpaca.markets/v1beta1/options/snapshots`) with Alpaca credentials (`src/orion/connectors/alpaca_option_greeks_connector.py:25`, `src/orion/connectors/alpaca_option_greeks_connector.py:164`).

Active call path evidence:
- ingestion runtime imports and invokes persistence module (`src/orion/ingestion/service.py:29`, `src/orion/ingestion/service.py:448`).
- DLQ replay path also reuses same enrichment flow (`src/orion/jobs/dlq_consumer.py:14`, `src/orion/jobs/dlq_consumer.py:162`).

Risk:
- even where Gateway/Heber are intended as canonical data path, ingestion-side enrichment still depends on direct provider credentials and provider availability from Orion runtime.
- this can produce contract drift (fields populated from direct provider semantics vs Gateway-normalized semantics) and reintroduce external rate-limit/credential failures into Orion.

### 30.2 Updated Priorities

P0:
1. Replace ingestion-time direct Alpaca Greeks enrichment with Gateway-backed contract reads (or drop enrichment at ingestion and source Greeks from Heber canonical datasets).
2. Define one canonical ownership point for option Greeks (Gateway vs Orion local enrichment) and remove duplicate path.

P1:
1. Add parity checks ensuring Greeks columns in Orion training paths are sourced from the selected canonical contract and not mixed across direct and Gateway paths.

## 31) Pass 24 Continuation (2026-02-06)

### 31.1 Heber-Backed Orion Services Are Not Fully Wired in Compose Runtime

Runtime behavior:
- `main_labeler` and `main_feature_enrichment` instantiate `HeberReader` (`src/orion/main_labeler.py:21`, `src/orion/main_labeler.py:34`, `src/orion/main_feature_enrichment.py:21`, `src/orion/main_feature_enrichment.py:42`).
- `HeberReader` defaults `heber_data_root` to `/Volumes/heber/data` (`src/orion/config.py:85` to `src/orion/config.py:87`) and returns empty frames when silver paths are absent (`src/orion/clients/heber_reader.py:206` to `src/orion/clients/heber_reader.py:208`).

Compose wiring:
- `labeler` and `feature_enrichment` do not set `HEBER_DATA_ROOT` and do not mount Heber data volume; they only mount repo source (`docker-compose.yml:55`, `docker-compose.yml:84`) and set DB/Gateway/UW env.

Observed consequence:
- Heber read paths can become inert in containerized runtime; `feature_enrichment` then falls back to Orion-local SQL ticker discovery (`src/orion/main_feature_enrichment.py:91` to `src/orion/main_feature_enrichment.py:110`), while `main_labeler` has no local-flow fallback in current path (`src/orion/main_labeler.py:134` to `src/orion/main_labeler.py:145`).

Risk:
- deployment appears Heber-integrated in code, but runtime behavior can remain local/empty depending on container filesystem wiring.

### 31.2 Updated Priorities

P0:
1. Wire explicit Heber data access in compose for Heber-dependent services (`HEBER_DATA_ROOT` + volume mount or remote-read strategy).
2. Add startup checks that fail fast when Heber data root is unreachable for services that require it (`main_labeler`, `main_feature_enrichment`).

P1:
1. Decide whether `main_feature_enrichment` local SQL fallback is acceptable as a transitional mode; if yes, gate behind explicit flag and emit prominent startup warning.

## 32) Pass 25 Continuation (2026-02-06)

### 32.1 Duplicate Outcome-Tracking Stacks: Orion Local Tables vs Heber Watch Gold Labels

Heber stack:
- watch service tracks alert outcomes and writes `labels_alert_barriers` into Gold (`../Heber/heber/watch/writer.py:1`, `../Heber/heber/watch/writer.py:30`, `../Heber/heber/watch/writer.py:94` to `../Heber/heber/watch/writer.py:99`).

Orion stack:
- `main_option_quote_tracker` polls checkpoint quotes and writes `silver_option_quotes` (`src/orion/main_option_quote_tracker.py:63`, `src/orion/main_option_quote_tracker.py:101`, `src/orion/main_option_quote_tracker.py:129`, `src/orion/main_option_quote_tracker.py:180`).
- `main_price_target_labeler` depends on Orion-local `silver_uw_flow` + `silver_option_quotes` + `price_target_labels` (`src/orion/main_price_target_labeler.py:347` to `src/orion/main_price_target_labeler.py:349`, `src/orion/main_price_target_labeler.py:401` to `src/orion/main_price_target_labeler.py:415`).

Risk:
- maintaining both stacks increases operational and contract-drift cost, and delays retirement of Orion-local silver/label tables after centralization.

### 32.2 Updated Priorities

P0:
1. Choose one canonical outcome-tracking path (Heber watch labels vs Orion `main_option_quote_tracker` + `main_price_target_labeler`) and publish retirement criteria for the non-canonical stack.
2. If Heber watch is canonical, design a parity bridge for Orion models still requiring wide checkpoint features currently in `price_target_labels`.

P1:
1. Add migration scorecard mapping each `price_target_labels` training field to either Heber watch labels, Heber feature datasets, or explicit deprecation.

## 33) Pass 26 Continuation (2026-02-06)

### 33.1 Nightly Backfill Runtime Still Depends on Direct UW Credentials Not Wired in Compose

Active runtime path:
- `nightly-backfill` service runs `orion.jobs.nightly_backfill` (`docker-compose.yml:209` to `docker-compose.yml:224`).
- this orchestrator executes `run_ml_backfill(...)` (`src/orion/jobs/nightly_backfill.py:18`, `src/orion/jobs/nightly_backfill.py:69`).
- `backfill_ml_features` still fetches ticker metadata via direct UW client requiring `UW_API_KEY` (`src/orion/jobs/backfill_ml_features.py:66` to `src/orion/jobs/backfill_ml_features.py:76`, `src/orion/jobs/backfill_ml_features.py:90` to `src/orion/jobs/backfill_ml_features.py:100`).

Compose gap:
- `nightly-backfill` env includes `DB_URL` + `GATEWAY_URL` only, no `UW_API_KEY` (`docker-compose.yml:220` to `docker-compose.yml:224`).

Risk:
- nightly backfill can silently skip/under-populate sector/earnings-related features when UW credentials are absent, reducing training parity and completeness.

### 33.2 Nightly Scheduler Uses Fixed UTC-5 Offset (DST Drift Risk)

Current scheduler logic:
- computes ET by hardcoding `timedelta(hours=-5)` (`src/orion/jobs/nightly_backfill.py:39` to `src/orion/jobs/nightly_backfill.py:59`).

Risk:
- run time shifts by one hour during daylight-saving periods, causing off-target operational windows.

### 33.3 Updated Priorities

P0:
1. Remove direct UW dependency from nightly backfill path (prefer Gateway/Heber metadata source) or wire explicit transitional credentials with hard-fail visibility.
2. Replace fixed-offset ET scheduling with timezone-aware conversion to avoid DST drift.

P1:
1. Add completeness checks for nightly backfill outputs (feature population thresholds) and alert when expected enrichment columns regress.

## 34) Pass 27 Continuation (2026-02-06)

### 34.1 Ingestion Runtime Still Does Not Consume Heber Flow/Darkpool Despite Migration Comments

Current behavior:
- ingestion initializes `HeberReader` and comments that UW data now comes from Heber (`src/orion/ingestion/service.py:57` to `src/orion/ingestion/service.py:60`),
- but `_run_cycle` only appends Alpaca events and never calls `read_flow` / `read_darkpool` (`src/orion/ingestion/service.py:200` to `src/orion/ingestion/service.py:205`),
- while downstream rule path still expects `UW_FLOW` events (`src/orion/ingestion/service.py:323` to `src/orion/ingestion/service.py:325`).

Entrypoint/docs drift:
- ingestion module docstring claims it reads flow/darkpool from Heber (`src/orion/ingestion/__main__.py:7` to `src/orion/ingestion/__main__.py:10`), which is not true in active `_run_cycle` logic.

Risk:
- UW flow-driven features/rules can be starved in runtime while code/docs imply parity is already achieved.

### 34.2 Gateway Response-Shape Drift in Orion UW Enrichment Connectors

Greek exposure mismatch:
- Orion connector aggregates `call_gamma`/`put_gamma`/`call_vanna`/`put_vanna`/`call_charm`/`put_charm` (`src/orion/connectors/uw_greek_exposure_connector.py:57` to `src/orion/connectors/uw_greek_exposure_connector.py:73`),
- Gateway provider returns strike-level rows keyed as `gamma_exposure`, `call_volume`, `put_volume`, `call_oi`, `put_oi` (`../Data-gateway/gateway/providers/uw.py:2434` to `../Data-gateway/gateway/providers/uw.py:2444`).

Max pain mismatch:
- Orion expects `max_pain` (`src/orion/connectors/uw_max_pain_connector.py:61`),
- Gateway normalizes as `max_pain_strike` (`../Data-gateway/gateway/providers/uw.py:1362` to `../Data-gateway/gateway/providers/uw.py:1364`).

IV rank mismatch:
- Orion expects `iv_high`/`iv_low`/`iv_30d` (`src/orion/connectors/uw_iv_rank_connector.py:70` to `src/orion/connectors/uw_iv_rank_connector.py:73`),
- Gateway provides `one_year_high`/`one_year_low` (and does not expose `iv_30d` in normalized model) (`../Data-gateway/gateway/providers/uw.py:1425` to `../Data-gateway/gateway/providers/uw.py:1430`).

Risk:
- enrichment tables can silently fill with zeros/null-equivalents, degrading model feature quality while pipelines appear healthy.

### 34.3 Feature-Enrichment Gateway Auth Contract Is Not Wired in Compose

Connector auth behavior:
- UW connectors only set `X-Gateway-Key` if configured key is present (`src/orion/connectors/uw_greek_exposure_connector.py:25` to `src/orion/connectors/uw_greek_exposure_connector.py:28`).

Runtime wiring:
- `main_feature_enrichment` instantiates Gateway connectors with URL only (`src/orion/main_feature_enrichment.py:237` to `src/orion/main_feature_enrichment.py:243`),
- compose `feature_enrichment` sets `GATEWAY_URL` but no gateway API key env (`docker-compose.yml:86` to `docker-compose.yml:90`).

Gateway contract:
- UW endpoints used by these connectors are API-key protected (`../Data-gateway/gateway/api/uw/flow_analytics.py:24`, `../Data-gateway/gateway/api/uw/market.py:107`).

Risk:
- connector calls can degrade to repeated 401 paths and near-zero persisted enrichment output.

### 34.4 Darkpool Feed Naming Drift Breaks Orion Heber Read Path

Orion Heber read target:
- `HeberReader` points darkpool reads to `feed=darkpool_trades` (`src/orion/clients/heber_reader.py:28`, `src/orion/clients/heber_reader.py:165`, `src/orion/clients/heber_reader.py:206`).

Upstream canonical event feed:
- Gateway UW poller wraps darkpool events with `feed="darkpool"` (`../Data-gateway/gateway/core/uw_poller.py:362` to `../Data-gateway/gateway/core/uw_poller.py:366`),
- Heber Silver writer partitions by `envelope.feed` (`../Heber/heber/writer/silver.py:33`, `../Heber/heber/writer/silver.py:40`).

Risk:
- Orion `read_darkpool(...)` can point at a non-existent partition path in the centralized pipeline and return empty data even when darkpool events are being ingested.

### 34.5 Updated Priorities

P0:
1. Implement actual Heber flow/darkpool ingestion in `IngestionService` (or remove UW-flow pipeline branches and document migration state explicitly).
2. Fix UW enrichment connector field mappings to Gateway normalized response contracts (`spot-exposures`, `max-pain`, `iv-rank`) and add regression tests for non-zero/expected mappings.
3. Wire `DATA_GATEWAY_API_KEY`/`GATEWAY_API_KEY` into feature-enrichment runtime and fail fast on missing key when Gateway mode is enabled.

P1:
1. Resolve darkpool feed naming (`darkpool` vs `darkpool_trades`) with a single canonical dataset alias across Data Gateway -> Heber writer -> Orion `HeberReader`.

## 35) Pass 28 Continuation (2026-02-06)

### 35.1 Orion Admin `/flows` Endpoint Still Bypasses Gateway/Heber Canonical Flow APIs

Current Orion behavior:
- Orion API imports and queries local `SilverOptionFlow` table directly (`src/orion/api/main.py:24`, `src/orion/api/main.py:495` to `src/orion/api/main.py:531`),
- `/flows` returns Orion-local row shape from `silver_uw_flow` (`src/orion/api/main.py:479` to `src/orion/api/main.py:556`).

Centralized contract exists upstream:
- Data Gateway exposes canonical UW flow APIs with auth + pagination/caching (`../Data-gateway/gateway/api/uw/flow.py:23` to `../Data-gateway/gateway/api/uw/flow.py:46`, `../Data-gateway/gateway/api/uw/flow.py:49` to `../Data-gateway/gateway/api/uw/flow.py:75`).

Risk:
- Orion and Gateway can present divergent flow records/response contracts,
- local table dependency keeps Orion API coupled to legacy SQL ingestion health instead of centralized data ownership.

### 35.2 Shared MCP Server Stack Appears Orphaned Relative to Active Runtime Paths

Observed state:
- Orion still includes MCP client surface for Alpaca/UW tools (`src/orion/clients/mcp_server.py:21` to `src/orion/clients/mcp_server.py:200`),
- client exports remain in package init (`src/orion/clients/__init__.py:11` to `src/orion/clients/__init__.py:20`),
- runtime usage appears absent outside self/tests (no references beyond `clients/__init__.py` and `tests/clients/test_mcp_server.py`),
- compose still runs dedicated `mcp-server` with direct provider credentials (`docker-compose.yml:269` to `docker-compose.yml:283`).

Risk:
- unnecessary operational surface and secret exposure for a path not currently tied to core ingestion/labeling/execution flows,
- architectural confusion (parallel direct-provider path) during Gateway/Heber centralization.

### 35.3 MCP Endpoint Defaults Are Misaligned With Compose Networking

Current config:
- MCP client defaults to `http://localhost:8001` (`src/orion/clients/mcp_server.py:18`),
- compose exposes MCP as host `8090:8001` and service name `mcp-server` (`docker-compose.yml:269` to `docker-compose.yml:275`),
- no compose-wide `MCP_SERVER_URL` wiring for Orion services.

Risk:
- if MCP path is reactivated, default connectivity can fail in both common contexts:
  - host runs (service published on `8090`, not `8001`),
  - container runs (`localhost` resolves to same container, not `mcp-server`).

### 35.4 Updated Priorities

P0:
1. Decide whether Orion `/flows` remains a product API; if yes, make it a Gateway-backed proxy (or Heber-backed facade) instead of direct `silver_uw_flow` SQL.
2. Decide whether Shared MCP Server is still in-scope; if not, archive `orion.clients.mcp_server` + related tests and remove `mcp-server` compose service.

P1:
1. If MCP is retained, standardize endpoint/auth config (`MCP_SERVER_URL`) and align to centralized Gateway/Heber ownership boundaries.

## 36) Pass 29 Continuation (2026-02-06)

### 36.1 MetaSearch Event Loader Regression: Data Fetch Coroutine Is Defined But Never Executed

Current behavior:
- `evaluate_variant(...)` relies on `_fetch_silver_events(...)` for bars/flow inputs (`src/orion/agents/meta_search_agent.py:840` to `src/orion/agents/meta_search_agent.py:843`),
- inside `_fetch_silver_events(...)`, nested `fetch_bars_and_flow(...)` is defined (`src/orion/agents/meta_search_agent.py:996`) but never invoked (no call site in module),
- function returns default-empty `alpaca_events`, `flow_events`, `price_data` (`src/orion/agents/meta_search_agent.py:992` to `src/orion/agents/meta_search_agent.py:995`, `src/orion/agents/meta_search_agent.py:1094`).

Observed drift:
- changelog previously marked this bug as fixed (`CHANGELOG.md:403` to `CHANGELOG.md:407`), but current code path still has the no-call regression.

Risk:
- meta-search evaluation can degrade to persistent `no_data`/no-candidate outcomes, blocking meaningful solver evolution while appearing operational.

### 36.2 MetaSearch/Weekly Evolution Path Is Still Hard-Coupled to Orion Local Silver Tables

Current data contract:
- `_fetch_silver_events` imports and queries `SilverAlpacaBar` + `SilverOptionFlow` directly (`src/orion/agents/meta_search_agent.py:990`, `src/orion/agents/meta_search_agent.py:998`, `src/orion/agents/meta_search_agent.py:1052`),
- no HeberReader/Gateway facade use in this path,
- weekly automation entrypoint runs the same MetaSearchAgent (`src/orion/main_meta_weekly.py:19`, `src/orion/main_meta_weekly.py:61`, `src/orion/main_meta_weekly.py:118`).

Risk:
- solver evolution/training feedback can drift from centralized Gateway/Heber canonical datasets,
- migration parity is undermined in adaptive components even if core ingestion paths are eventually centralized.

### 36.3 Analytics Agents Depend on Local Ingestion Tables While Compose Still Omits Ingestion Service

Data dependency:
- EOD review gathers ingestion health and regime context from local `BronzeEvent` + `SilverAlpacaBar` tables (`src/orion/agents/eod_review_agent.py:358` to `src/orion/agents/eod_review_agent.py:363`, `src/orion/agents/eod_review_agent.py:393` to `src/orion/agents/eod_review_agent.py:399`).

Runtime wiring:
- compose runs `eod-agent` and `meta-weekly` (`docker-compose.yml:146` to `docker-compose.yml:163`, `docker-compose.yml:178` to `docker-compose.yml:195`),
- no ingestion service is present in compose service list.

Risk:
- EOD/weekly analytics can run on stale/sparse local ingestion telemetry, reducing reliability of drift detection and solver mutation decisions.

### 36.4 Updated Priorities

P0:
1. Fix `_fetch_silver_events` by executing the data-fetch coroutine (and add regression test to prevent recurrence).
2. Decide canonical data source for MetaSearch evaluation (Gateway/Heber facade vs Orion local silver) and align weekly evolution path to it.

P1:
1. Either add ingestion service back to compose for analytics correctness, or reroute EOD/weekly analytics to canonical Gateway/Heber-backed datasets with explicit freshness checks.

## 37) Pass 30 Continuation (2026-02-06)

### 37.1 Pre-commit Secret Baseline Is Coupled to Large/Generated and Archived Files (Commit-Loop Instability)

Current hygiene contract:
- pre-commit runs `detect-secrets` against `.secrets.baseline` (`.pre-commit-config.yaml:24` to `.pre-commit-config.yaml:28`),
- baseline tracks many findings in large/generated/archived files including `codebase.md` and archived legacy files (`.secrets.baseline:169` to `.secrets.baseline:185`, `.secrets.baseline:185` to `.secrets.baseline:284`).

Observed debt signals:
- `codebase.md` is very large and mutable (`98302` lines; ~`3.3M`) and is currently tracked (`codebase.md` file stats),
- baseline line-number entries for these files are high-churn by nature, creating repeated baseline rewrites and non-deterministic commit friction.

Risk:
- engineering throughput degradation during migration (hook churn, forced retries, frequent manual baseline staging),
- increased chance of bypassing hooks (`--no-verify`) under schedule pressure, reducing trust in hygiene controls.

### 37.2 Weekly Meta Scheduler Has Exact-Minute Trigger With No Catch-up Window

Current scheduler logic:
- scheduled mode triggers only when `weekday == Friday` and exact `hour == 17` and `minute == 30` (`src/orion/main_meta_weekly.py:109` to `src/orion/main_meta_weekly.py:114`),
- polling loop sleeps for 60 seconds between checks (`src/orion/main_meta_weekly.py:133` to `src/orion/main_meta_weekly.py:134`).

Risk:
- if process restarts late, clock drifts, or loop wake-up misses the exact minute, weekly evolution may skip an entire week with no catch-up execution.

### 37.3 Updated Priorities

P0:
1. Reduce `.secrets.baseline` volatility by excluding generated/aggregate artifacts (for example `codebase.md`) and archived wave folders from secret-scan scope, then regenerate baseline once.
2. Add deterministic pre-commit guidance for migration branches (single source of truth for baseline refresh command).

P1:
1. Replace exact-minute weekly trigger with a bounded execution window/catch-up rule (for example first run after Friday 17:30 ET if not yet executed for current week).

## 38) Pass 31 Continuation (2026-02-06)

### 38.1 HeberReader Hardcodes Silver Feed Names Instead of Using Catalog Feed-Resolution Contract

Current Orion behavior:
- `HeberReader` pins dataset names in constants (`bars`, `flow_alerts`, `darkpool_trades`) and builds parquet path directly as `silver/feed={dataset}` (`src/orion/clients/heber_reader.py:26` to `src/orion/clients/heber_reader.py:28`, `src/orion/clients/heber_reader.py:206`),
- no Orion call sites use Heber catalog feed-resolution (`/api/v1/feeds/resolve`) despite catalog support (`../Heber/heber/catalog/api.py:300` to `../Heber/heber/catalog/api.py:306`, `../Heber/heber/catalog/service.py:136` to `../Heber/heber/catalog/service.py:142`).

Cross-repo naming drift context:
- Data Gateway UW poller emits darkpool as `feed="darkpool"` (`../Data-gateway/gateway/core/uw_poller.py:362` to `../Data-gateway/gateway/core/uw_poller.py:366`),
- Heber writer/storage schema keys also use `darkpool` (`../Heber/heber/writer/silver.py:76`, `../Heber/heber/storage/iceberg_catalog.py:895`),
- while Heber catalog datasources and PRD-facing dataset inventory still promote `darkpool_trades` (`../Heber/heber/catalog/datasources.py:178`).

Risk:
- Orion remains brittle to catalog/producer naming drift and repeats contract mismatch failures already observed in darkpool reads.

### 38.2 HeberReader Filter Fallback Can Silently Drop Instrument Filtering (Data Contamination + Scaling Risk)

Current implementation:
- `_read_silver_dataset` applies `instrument_key` filters when symbols are provided (`src/orion/clients/heber_reader.py:210` to `src/orion/clients/heber_reader.py:214`),
- `_read_parquet` fallback path (on filter errors) re-reads entire dataset without filters (`src/orion/clients/heber_reader.py:289` to `src/orion/clients/heber_reader.py:296`),
- post-read path only applies time/as-of filters, not instrument filter re-application (`src/orion/clients/heber_reader.py:218` to `src/orion/clients/heber_reader.py:223`).

Risk:
- symbol-scoped reads can expand to whole-feed data under fallback conditions, polluting downstream feature/label computations and increasing memory/latency under larger Silver datasets.

### 38.3 Updated Priorities

P0:
1. Move Orion Heber feed selection to catalog-resolved mapping (provider+feed -> silver dataset) rather than hardcoded dataset strings.
2. Add a single canonical darkpool dataset alias policy across Data Gateway, Heber catalog, and Heber writer/storage keys.

P1:
1. In `HeberReader`, re-apply instrument filtering after fallback full-read (or hard-fail instead of broad fallback) and add guardrail tests for symbol-scoped reads.

## 39) Pass 32 Continuation (2026-02-06)

### 39.1 Heber Watch Builds Data Gateway URLs Inconsistently (`/api/v1` Mixed Inline vs Base URL)

Current Heber watch behavior:
- poller and consumer hardcode `/api/v1` into quote URLs (`../Heber/heber/watch/poller.py:165`, `../Heber/heber/watch/consumer.py:418`),
- feature-enrichment paths do not include `/api/v1` and assume provider root under base URL (`../Heber/heber/watch/features.py:319`, `../Heber/heber/watch/features.py:366`, `../Heber/heber/watch/features.py:452`),
- compose sets `DATA_GATEWAY_URL` without API prefix (`../Heber/docker-compose.yml:268`).

Gateway contract:
- provider routers are mounted at `/api/v1/uw` and `/api/v1/alpaca` (`../Data-gateway/gateway/api/uw/__init__.py:32`, `../Data-gateway/gateway/api/alpaca/__init__.py:19`).

Risk:
- with `DATA_GATEWAY_URL=http://host.docker.internal:8080`, watch components still construct incompatible path families (some prefixed, some not), guaranteeing partial request failure;
- with `DATA_GATEWAY_URL` including `/api/v1`, prefixed call sites become `/api/v1/api/v1/...` and fail.

### 39.2 Market-Context Enrichment Uses a Nonexistent Stock Bars Path Shape

Current Heber watch enrichment request:
- calls `GET {gateway}/alpaca/stocks/bars` with `symbol` as query param (`../Heber/heber/watch/features.py:452` to `../Heber/heber/watch/features.py:455`).

Gateway stock-bars contract:
- route is `GET /api/v1/alpaca/stocks/{symbol}/bars` (path param, not query-only symbol) (`../Data-gateway/gateway/api/alpaca/stock.py:25` to `../Data-gateway/gateway/api/alpaca/stock.py:31`).

Risk:
- market-context enrichment (returns/volatility fields) silently degrades due to repeated non-200 responses and fallback behavior, reducing label-quality parity and feature completeness.

### 39.3 Heber Watch/Label Pipelines Lack Gateway API-Key Injection Despite Required Auth Contract

Gateway auth contract:
- Gateway endpoints require `X-Gateway-Key` header via `require_api_key` (`../Data-gateway/gateway/api/deps.py:101` to `../Data-gateway/gateway/api/deps.py:116`).

Current Heber call paths:
- watch consumer/poller/enrichment issue `httpx` requests without auth headers (`../Heber/heber/watch/consumer.py:417` to `../Heber/heber/watch/consumer.py:420`, `../Heber/heber/watch/poller.py:164` to `../Heber/heber/watch/poller.py:167`, `../Heber/heber/watch/features.py:321` to `../Heber/heber/watch/features.py:323`, `../Heber/heber/watch/features.py:374` to `../Heber/heber/watch/features.py:376`, `../Heber/heber/watch/features.py:461` to `../Heber/heber/watch/features.py:463`),
- alert-labels pipeline option-bars fetch also omits auth headers (`../Heber/heber/features/pipelines/alert_labels.py:361` to `../Heber/heber/features/pipelines/alert_labels.py:369`),
- watch CLI/config surface exposes `DATA_GATEWAY_URL` but no gateway-key configuration (`../Heber/heber/watch/__main__.py:23`, `../Heber/heber/watch/__main__.py:47`, `../Heber/heber/features/pipelines/alert_labels.py:540`).

Risk:
- Heber watch labeling and feature enrichment can fail with 401 responses in any environment enforcing Gateway auth, leading to silent parity erosion (missing entry prices, missing enrichment, incomplete contract labels).

### 39.4 Updated Priorities

P0:
1. Standardize Heber watch Gateway URL contract to one rule: `gateway_base_url` excludes `/api/v1`, and all clients build paths through a shared helper that prepends `/api/v1` exactly once.
2. Fix watch market-context enrichment path to `GET /api/v1/alpaca/stocks/{symbol}/bars` and add integration tests for bars/chain/iv-rank URL construction.
3. Add explicit Gateway auth wiring for Heber call paths (`DATA_GATEWAY_KEY` env + `X-Gateway-Key` header injection in shared HTTP client code).

P1:
1. Add startup validation in Heber watch: fail fast when `gateway_base_url` includes `/api/v1` or when required endpoints return non-contract responses.

## 40) Pass 33 Continuation (2026-02-06)

### 40.1 Heber Watch and Label Pipelines Call Batch Options Endpoints Not Exposed by Data Gateway API Router

Current Heber calls:
- watch quote polling and entry-price lookups call `GET /api/v1/alpaca/options/quotes?symbols=...` (`../Heber/heber/watch/poller.py:165` to `../Heber/heber/watch/poller.py:167`, `../Heber/heber/watch/consumer.py:418` to `../Heber/heber/watch/consumer.py:420`),
- alert-label contract labeling calls `GET /api/v1/alpaca/options/bars?symbols=...` (`../Heber/heber/features/pipelines/alert_labels.py:361` to `../Heber/heber/features/pipelines/alert_labels.py:368`).

Current Data Gateway router contract:
- options bars route is single-contract `GET /api/v1/alpaca/options/{contract}/bars` (`../Data-gateway/gateway/api/alpaca/options.py:109` to `../Data-gateway/gateway/api/alpaca/options.py:116`),
- options quotes route is single-contract `GET /api/v1/alpaca/options/{contract}/quotes` (`../Data-gateway/gateway/api/alpaca/options.py:157` to `../Data-gateway/gateway/api/alpaca/options.py:161`),
- no batch `/options/bars` or `/options/quotes` route exists in this router.

Provider capability drift:
- Alpaca provider already supports batch option bars/quotes internally (`../Data-gateway/gateway/providers/alpaca.py:521` to `../Data-gateway/gateway/providers/alpaca.py:548`, `../Data-gateway/gateway/providers/alpaca.py:572` to `../Data-gateway/gateway/providers/alpaca.py:583`), but router does not surface equivalent batch endpoints.

Risk:
- watch service cannot reliably fetch live option quotes for watch lifecycle updates/entry pricing through current Gateway API contract,
- contract-label pipeline cannot fetch option bars through current Gateway API contract,
- both paths silently degrade label quality and parity when HTTP failures are swallowed.

### 40.2 Data Gateway `/catalog` Endpoint Inventory Is Out of Sync With Implemented Alpaca Routes

Catalog advertises endpoints including `/options/bars` and `/stocks/bars` (`../Data-gateway/gateway/api/catalog.py:34` to `../Data-gateway/gateway/api/catalog.py:35`, `../Data-gateway/gateway/api/catalog.py:47`),
while Alpaca router currently exposes `/stocks/{symbol}/bars` and `/stocks/bars/latest` plus single-contract option bars/quotes (`../Data-gateway/gateway/api/alpaca/stock.py:25`, `../Data-gateway/gateway/api/alpaca/stock.py:191`, `../Data-gateway/gateway/api/alpaca/options.py:109`, `../Data-gateway/gateway/api/alpaca/options.py:157`).

Risk:
- integration clients that trust `/catalog` can target stale/nonexistent paths, increasing 404-driven data loss and migration confusion.

### 40.3 Updated Priorities

P0:
1. Decide and implement canonical batch options API contract at Gateway router level (`/options/quotes` and `/options/bars` with `symbols` query), or immediately patch Heber watch/label clients to single-contract routes with bounded batching.
2. Add contract tests spanning Heber watch + label pipelines against live Gateway route inventory to prevent silent 404 fallbacks.

P1:
1. Reconcile `/catalog` endpoint inventory with actual router exports and add CI checks to fail on catalog/router drift.

## 41) Pass 34 Continuation (2026-02-06)

### 41.1 `main_execution.py` Still Contains Broken/Dead Candidate-Status Helpers Contrary to Changelog Removal Claim

Current code state:
- `main_execution.py` still defines `get_pending_candidates()` and `update_candidate_status()` (`src/orion/main_execution.py:165`, `src/orion/main_execution.py:177`),
- both functions reference `CandidateTrade.status`/`updated_at_utc` (`src/orion/main_execution.py:170`, `src/orion/main_execution.py:184` to `src/orion/main_execution.py:185`),
- `CandidateTrade` model does not define `status` or `updated_at_utc` columns (`src/orion/storage/models_gold.py:14` to `src/orion/storage/models_gold.py:52`),
- active execution loop uses `fetch_pending_candidates()` instead (`src/orion/main_execution.py:48`, `src/orion/main_execution.py:254`).

Changelog mismatch:
- changelog states these helpers were removed (`CHANGELOG.md:451` to `CHANGELOG.md:452`), but they remain in current runtime module.

Risk:
- dormant paths become latent runtime faults if reused (SQL/model attribute errors),
- operators and maintainers cannot rely on changelog statements for current execution-path behavior, increasing migration/debugging time.

### 41.2 Execution-Consolidation Changelog Claim Remains Out of Sync With Live Module Shape

Changelog states:
- "`main_execution.py` is now a thin wrapper (38 lines) that delegates to `ExecutionService.run()`" (`CHANGELOG.md:413`).

Current code:
- `main_execution.py` remains a full execution loop module (`363` lines; `src/orion/main_execution.py`), with candidate polling, preflight, execution, and exit-rule loops (`src/orion/main_execution.py:236` to `src/orion/main_execution.py:357`).

Risk:
- architectural runbooks and migration tasks can target non-existent consolidation state, causing incorrect remediation and duplicated work.

### 41.3 Updated Priorities

P1:
1. Remove or archive dead helper functions in `main_execution.py` (`get_pending_candidates`, `update_candidate_status`) and add a regression test that forbids references to non-existent `CandidateTrade` columns.
2. Correct changelog entries to match actual execution architecture and add a simple CI guardrail (for example, assert expected `main_execution.py` size/entrypoint contract when “thin-wrapper” claims are introduced).
