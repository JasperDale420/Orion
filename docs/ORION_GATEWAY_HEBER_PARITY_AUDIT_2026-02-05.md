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
- `flow_enricher` market-context path is now delegated to shared labeler helpers (`get_rvol_metrics`, `get_phase1_bucket_features`, `get_p3_features`) in `src/orion/ml/flow_enricher.py`, removing local SQL-heavy market-context queries and keeping parity logic centralized.

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

Targeted update (2026-02-09):
- `src/orion/ml/flow_enricher.py` now has only one direct high-priority `silver_*` dependency (`silver_greek_exposure` for rolling GEX averages); market-context SQL references were removed in favor of labeler helper delegation.

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

## 42) Pass 35 Continuation (2026-02-06)

### 42.1 Orion GatewayStreamClient Treats Subscriptions as Successful Before Server ACK

Current Orion behavior:
- `_send_subscribe()` sends subscription JSON and immediately updates local subscription state without waiting for server response (`src/orion/connectors/gateway_stream_client.py:149` to `src/orion/connectors/gateway_stream_client.py:162`),
- `_send_unsubscribe()` similarly mutates local state without confirmation (`src/orion/connectors/gateway_stream_client.py:175` to `src/orion/connectors/gateway_stream_client.py:188`),
- receive loop discards ack/system frames and has no explicit handling/logging path for `type="error"` responses (`src/orion/connectors/gateway_stream_client.py:294` to `src/orion/connectors/gateway_stream_client.py:300`).

Gateway contract behavior:
- WebSocket handler returns structured error frames for invalid feeds/symbols/permissions/subscription limits (`../Data-gateway/gateway/api/websocket.py:287` to `../Data-gateway/gateway/api/websocket.py:300`, `../Data-gateway/gateway/api/websocket.py:321` to `../Data-gateway/gateway/api/websocket.py:336`, `../Data-gateway/gateway/api/websocket.py:342` to `../Data-gateway/gateway/api/websocket.py:349`, `../Data-gateway/gateway/api/websocket.py:537` to `../Data-gateway/gateway/api/websocket.py:540`).

Risk:
- Orion can believe symbols are subscribed while Gateway rejected them, causing silent data starvation and stale universe behavior.

### 42.2 Orion Stream Client Uses Ambiguous Legacy Feed Name (`bars`) Instead of Canonical Feed IDs

Current Orion behavior:
- subscribes with `feed="bars"` (`src/orion/connectors/gateway_stream_client.py:154`), not canonical feed IDs documented in Gateway discovery.

Gateway discovery contract:
- `/catalog/feeds` documents canonical IDs such as `stock_bars`, `option_bars`, etc. (`../Data-gateway/gateway/api/catalog.py:596` to `../Data-gateway/gateway/api/catalog.py:607`, `../Data-gateway/gateway/api/catalog.py:644` to `../Data-gateway/gateway/api/catalog.py:649`),
- stream router currently tolerates legacy feed values via substring matching/fallback logic (`../Data-gateway/gateway/api/websocket.py:364` to `../Data-gateway/gateway/api/websocket.py:366`, `../Data-gateway/gateway/core/stream.py:34` to `../Data-gateway/gateway/core/stream.py:60`).

Risk:
- integration currently depends on permissive fallback behavior; if feed normalization tightens, Orion subscriptions can fail without obvious client-side error handling.

### 42.3 Updated Priorities

P0:
1. In `GatewayStreamClient`, wait for and validate subscription/unsubscription ACK responses before mutating local subscription state.
2. Add explicit handling/logging for `type="error"` WebSocket frames and surface failure back to caller/retry logic.

P1:
1. Migrate Orion subscription payloads to canonical Gateway feed IDs (`stock_bars` for current usage) and add contract tests against `/catalog/feeds`.

## 43) Pass 36 Continuation (2026-02-06)

### 43.1 `backfill_exit_columns` Uses a Single Anchor Column Filter That Can Skip Partially-Missing Checkpoint Rows

Current selection logic:
- checkpoint backfill candidates are selected only where `price_at_15m IS NULL` (`src/orion/jobs/backfill_exit_columns.py:100` to `src/orion/jobs/backfill_exit_columns.py:101`),
- update routine fills many checkpoint columns (`price_at_30m`, `price_at_8h`, `price_at_1d`, `price_at_2d`, `price_at_3d`, `price_at_1w`) once selected (`src/orion/jobs/backfill_exit_columns.py:185` to `src/orion/jobs/backfill_exit_columns.py:200`).

Risk:
- rows with `price_at_15m` already populated but later checkpoints still null are never selected for remediation, leaving persistent partial feature gaps in `price_target_labels`.

### 43.2 Nightly Backfill Throughput Is Hard-Capped Per Run Without Deterministic Pagination

Current orchestrator/runtime behavior:
- nightly orchestrator invokes both backfills with fixed `limit=10000` (`src/orion/jobs/nightly_backfill.py:69`, `src/orion/jobs/nightly_backfill.py:74`),
- backfill selectors rely on `LIMIT :limit` without `ORDER BY` in candidate queries (`src/orion/jobs/backfill_ml_features.py:290` to `src/orion/jobs/backfill_ml_features.py:291`, `src/orion/jobs/backfill_exit_columns.py:82`, `src/orion/jobs/backfill_exit_columns.py:101`).

Risk:
- backlog processing order is non-deterministic and can repeatedly prioritize the same subset of rows, delaying catch-up and causing long-lived null-feature pockets.

### 43.3 Nightly Scheduler Intent/Config Drift: Declared “After Close (4:30pm ET)” vs Configured 4:00pm ET

Current module contract:
- module docstring states run “after market close (4:30pm ET)” (`src/orion/jobs/nightly_backfill.py:4`),
- configured schedule is `BACKFILL_HOUR_ET = 16`, `BACKFILL_MINUTE_ET = 0` (`src/orion/jobs/nightly_backfill.py:25` to `src/orion/jobs/nightly_backfill.py:26`).

Risk:
- operational expectations and runbook timing can diverge from actual execution window, increasing the chance of running before all intended end-of-day artifacts are finalized.

### 43.4 Updated Priorities

P0:
1. Update checkpoint-candidate query in `backfill_exit_columns` to target any missing checkpoint field (not only `price_at_15m`) and add regression tests for partial-row recovery.
2. Introduce deterministic pagination/checkpointing for nightly backfills (stable ordering + last-processed cursor) instead of fixed-limit best effort.

P1:
1. Align `nightly_backfill` scheduling docs/config to one explicit post-close time and validate it with timezone-aware scheduling rules.

## 44) Pass 37 Continuation (2026-02-06)

### 44.1 Option Quote Tracker Can Persist “Latest-Now” Prices as Historical Checkpoint Quotes

Current tracker behavior:
- selects any matured checkpoint where `minutes_since_entry >= checkpoint_minutes` (`src/orion/main_option_quote_tracker.py:203`, `src/orion/main_option_quote_tracker.py:212`),
- fetches current option snapshots via Alpaca snapshot endpoint (latest quote/trade only) (`src/orion/connectors/alpaca_option_greeks_connector.py:164` to `src/orion/connectors/alpaca_option_greeks_connector.py:171`, `src/orion/connectors/alpaca_option_greeks_connector.py:182` to `src/orion/connectors/alpaca_option_greeks_connector.py:199`),
- writes the fetched current quote into each overdue checkpoint while stamping `ts_utc` to historical checkpoint time (`src/orion/main_option_quote_tracker.py:242` to `src/orion/main_option_quote_tracker.py:252`).

Risk:
- if tracker is delayed/restarted or backlog exists, historical checkpoints (`15m`, `30m`, `1h`, etc.) can be populated with incorrect later prices, distorting return labels and downstream model training.

### 44.2 Quote Tracker Reconstructs OCC Symbols Instead of Using Canonical `option_chain` Field

Current query path:
- rebuilds `option_symbol` from ticker/expiry/put_call/strike math in SQL (`src/orion/main_option_quote_tracker.py:74` to `src/orion/main_option_quote_tracker.py:75`),
- ignores canonical source symbol already stored in flow schema (`src/orion/storage/models_silver.py:79`).

Risk:
- symbol reconstruction drift (format/rounding/root edge cases) can produce unmapped contracts and silent quote gaps even when canonical OCC symbols are available.

### 44.3 Updated Priorities

P0:
1. Enforce checkpoint integrity: only persist quote data when fetch time is within a strict tolerance window of target checkpoint timestamp; otherwise mark checkpoint as missed/stale (do not backfill with “latest-now” value).
2. Replace reconstructed OCC symbol logic with canonical `silver_uw_flow.option_chain` usage in quote tracking selection.

P1:
1. Add data-quality assertions for checkpoint monotonicity/timing provenance in `silver_option_quotes` (for example `abs(fetched_at - ts_utc)` bounds) and alert on violations.

## 45) Pass 38 Continuation (2026-02-06)

### 45.1 Tenacity Retry Configuration in UW Gateway Connectors Is Effectively Disabled

Current connector behavior:
- each UW connector fetch method is decorated with `@retry(...)` but catches broad exceptions and returns `None` instead of raising:
  - `src/orion/connectors/uw_greek_exposure_connector.py:30` to `src/orion/connectors/uw_greek_exposure_connector.py:40`
  - `src/orion/connectors/uw_market_tide_connector.py:30` to `src/orion/connectors/uw_market_tide_connector.py:44`
  - `src/orion/connectors/uw_iv_rank_connector.py:30` to `src/orion/connectors/uw_iv_rank_connector.py:40`
  - `src/orion/connectors/uw_max_pain_connector.py:30` to `src/orion/connectors/uw_max_pain_connector.py:40`
- feature loop logs stored counts and continues (`src/orion/main_feature_enrichment.py:261` to `src/orion/main_feature_enrichment.py:283`), so transient failures can look like normal low-volume cycles.

Risk:
- transient Gateway/network failures bypass intended retry/backoff behavior and silently degrade enrichment freshness.

### 45.2 `get_spy_cumulative_return` Computes Long-Horizon Return, Not the Intended “Past 20 Bars”

Current implementation:
- query uses window functions over full SPY history (`FIRST_VALUE`/`LAST_VALUE`) and then applies `ORDER BY ... DESC LIMIT 20` (`src/orion/main_feature_enrichment.py:168` to `src/orion/main_feature_enrichment.py:178`),
- function then reads a single row (`fetchone`) as the output (`src/orion/main_feature_enrichment.py:181` to `src/orion/main_feature_enrichment.py:182`),
- value is fed directly into regime detection (`src/orion/main_feature_enrichment.py:299` to `src/orion/main_feature_enrichment.py:303`).

Risk:
- trend input to `MultiAxisRegimeDetector` can be materially mis-scaled (anchored to very old history), skewing regime labels and downstream adaptive behavior.

### 45.3 Enrichment Silver Tables Are Written via Raw SQL but Not Represented in Canonical Schema Artifacts

Current write paths:
- connectors persist into `silver_greek_exposure`, `silver_market_tide`, `silver_max_pain`, and `silver_iv_rank` using raw SQL:
  - `src/orion/connectors/uw_greek_exposure_connector.py:118` to `src/orion/connectors/uw_greek_exposure_connector.py:129`
  - `src/orion/connectors/uw_market_tide_connector.py:86` to `src/orion/connectors/uw_market_tide_connector.py:92`
  - `src/orion/connectors/uw_max_pain_connector.py:115` to `src/orion/connectors/uw_max_pain_connector.py:124`
  - `src/orion/connectors/uw_iv_rank_connector.py:87` to `src/orion/connectors/uw_iv_rank_connector.py:94`
- silver ORM model file currently defines only flow/darkpool/bars/alerts/option-quotes tables and ends at line 213 (`src/orion/storage/models_silver.py:1` to `src/orion/storage/models_silver.py:213`).
- repo schema documentation also omits these enrichment silver tables from layer summary (`docs/DATABASE_SCHEMA.md:55` to `docs/DATABASE_SCHEMA.md:57`).

Risk:
- schema ownership is fragmented (runtime writes without schema-as-code parity), increasing migration and deployment break risk when table contracts evolve.

### 45.4 Updated Priorities

P0:
1. Make retry behavior real: re-raise request failures (or configure `retry_if_result` on `None`) in UW connectors and add failure-rate alerting in `main_feature_enrichment`.
2. Rewrite `get_spy_cumulative_return` with deterministic 20-bar semantics (explicit bounded subquery) and add a regression test for known bar sequences.

P1:
1. Bring enrichment silver tables under explicit schema governance (ORM + migration + docs), or retire Orion-local copies in favor of canonical Heber datasets.

## 46) Pass 39 Continuation (2026-02-06)

### 46.1 VIX Proxy “Daily” Metrics Are Computed from 1-Minute Bars

Current connector behavior:
- `VIXProxyConnector` claims to compute from recent daily bars (`src/orion/connectors/vix_proxy_connector.py:84` to `src/orion/connectors/vix_proxy_connector.py:85`),
- query reads `silver_alpaca_bars` for `ticker='VIXY'` with `ORDER BY ... DESC LIMIT 30` (`src/orion/connectors/vix_proxy_connector.py:90` to `src/orion/connectors/vix_proxy_connector.py:95`),
- underlying silver bars table is explicitly the 1-minute OHLCV store (`src/orion/storage/models_silver.py:126` to `src/orion/storage/models_silver.py:129`),
- connector then labels derived metrics as `vix_1d_change` and `vix_5d_ma` (`src/orion/connectors/vix_proxy_connector.py:53` to `src/orion/connectors/vix_proxy_connector.py:65`, `src/orion/connectors/vix_proxy_connector.py:74` to `src/orion/connectors/vix_proxy_connector.py:75`).

Risk:
- volatility regime inputs are semantically misnamed/misaligned (minute-scale calculations stored as daily-scale fields), which can distort downstream regime and feature interpretation.

### 46.2 Regime Detection Uses Hardcoded Realized Volatility Placeholder

Current runtime behavior:
- regime snapshots are generated in `main_feature_enrichment` (`src/orion/main_feature_enrichment.py:295`),
- detector call hardcodes `realized_vol=0.015` (`src/orion/main_feature_enrichment.py:304`) rather than deriving realized volatility from current bar history.

Risk:
- `silver_regime_history` can encode low-fidelity volatility-state labels that are weakly tied to live market conditions.

### 46.3 Duplicate VIX Ingestion Paths Exist, but Only Proxy Path Is Wired

Current code state:
- `main_feature_enrichment` wires `VIXProxyConnector` only (`src/orion/main_feature_enrichment.py:27`, `src/orion/main_feature_enrichment.py:245`),
- legacy direct Alpaca `VIXConnector` exists as standalone class (`src/orion/connectors/vix_connector.py:34`) with no in-repo runtime references (`src/orion/connectors/vix_connector.py` only hit for `VIXConnector` symbol).

Risk:
- dead/parallel connector paths increase maintenance overhead and ambiguity about canonical VIX source during Gateway/Heber migration.

### 46.4 Updated Priorities

P0:
1. Correct VIX proxy semantics: either aggregate to daily bars before deriving `vix_1d_change`/`vix_5d_ma`, or rename/store explicitly intraday metrics to avoid false daily semantics.
2. Replace hardcoded `realized_vol` with computed realized volatility from bounded recent SPY bar windows and add regression checks for known inputs.

P1:
1. Decide one canonical VIX ingestion path (`VIXProxyConnector` vs `VIXConnector`) and archive the non-canonical path after deployment confirmation.

## 47) Pass 40 Continuation (2026-02-06)

### 47.1 `main_labeler` “Unlabeled” Detection Can Misclassify Already-Labeled Backlog Rows

Current behavior:
- DB lookup only checks the first `max(limit * 4, limit)` event IDs (`src/orion/main_labeler.py:119` to `src/orion/main_labeler.py:124`),
- unlabeled filtering is then applied across the full record list (`src/orion/main_labeler.py:130`),
- records beyond the probed ID window are treated as unlabeled by default even if already present in `flow_labels`.

Risk:
- the labeler can repeatedly reprocess old/labeled rows under large backlogs, consuming cycles and delaying fresh-flow coverage.

### 47.2 Labeler Write Metrics Overstate Success Because Conflict-Noop Inserts Are Counted as New Labels

Current behavior:
- insert path uses `ON CONFLICT (event_id) DO NOTHING` (`src/orion/main_labeler.py:337`),
- `persist_labels` still returns `len(labels)` regardless of actual inserted row count (`src/orion/main_labeler.py:345`),
- loop aggregates this into `total_labeled` and success logs (`src/orion/main_labeler.py:354`, `src/orion/main_labeler.py:371` to `src/orion/main_labeler.py:380`).

Risk:
- operational telemetry can report successful labeling progress even when most writes are conflict no-ops, masking backlog/staleness.

### 47.3 Updated Priorities

P0:
1. Make unlabeled detection exact (query all candidate IDs used for batch selection, or switch to DB-driven anti-join pagination) before selecting the next batch.
2. Return and log real inserted-row counts from `persist_labels` (for example, via `RETURNING`/rowcount) instead of attempted-label counts.

P1:
1. Add backlog regression tests that simulate >`limit*4` historical rows with mixed labeled/unlabeled states and assert forward progress on newest unlabeled rows.

## 48) Pass 41 Continuation (2026-02-06)

### 48.1 Pattern-Miner Drift Baseline Uses Oldest-In-Window Importance, Not Latest

Current implementation:
- `get_last_week_importance` queries all rows ordered by newest first (`src/orion/ml/pattern_miner.py:552` to `src/orion/ml/pattern_miner.py:559`),
- then converts to a dict with duplicate feature keys (`src/orion/ml/pattern_miner.py:563`), which keeps the last occurrence per key (effectively oldest row in the 7-day window for each feature).

Risk:
- drift deltas in `run_pattern_mining` compare against stale baseline values instead of most recent production baseline, weakening alert quality for degrading features.

### 48.2 Pattern-Miner Training Query Does Not Gate on `ml_ready`

Current training query:
- filters `price_target_labels` by `entry_ts >= :cutoff` and `last_tracked_ts IS NOT NULL` (`src/orion/ml/pattern_miner.py:204` to `src/orion/ml/pattern_miner.py:217`),
- does not require `ml_ready = true` before model fitting.

Risk:
- model training can include rows that are track-complete but not fully feature-complete/validated under current Orion readiness semantics, increasing label/feature noise.

### 48.3 Updated Priorities

P0:
1. Fix `get_last_week_importance` to select one latest row per feature (for example `DISTINCT ON (feature_name) ... ORDER BY feature_name, created_at_utc DESC`) before drift delta calculation.
2. Add explicit `ml_ready` gating (or equivalent completeness predicate) in pattern-miner training queries and enforce with a regression test.

P1:
1. Add a data-quality metric that reports the fraction of fetched training rows passing readiness/completeness gates over time.

## 49) Pass 42 Continuation (2026-02-06)

### 49.1 Exit-Classifier Training Drops Losing Trades (Survivor Bias)

Current behavior:
- bucket training rows are fetched from `price_target_labels` (`src/orion/ml/exit_classifier.py:441` to `src/orion/ml/exit_classifier.py:463`),
- sample construction explicitly skips trades where `max_return_pct <= 0` (`src/orion/ml/exit_classifier.py:520` to `src/orion/ml/exit_classifier.py:522`).

Risk:
- model is trained primarily on winner trajectories and lacks explicit stop-loss/failed-trade exit patterns, which can bias live exit timing decisions.

### 49.2 Exit-Classifier Training Query Does Not Enforce Label Readiness Gate

Current training filter:
- query requires only `p.trade_type = ...` and `p.max_return_pct IS NOT NULL` (`src/orion/ml/exit_classifier.py:461` to `src/orion/ml/exit_classifier.py:463`),
- does not gate on `ml_ready` (or equivalent completeness predicate) before generating training samples.

Risk:
- checkpoint feature columns may be partially populated or stale while still entering model training.

### 49.3 Exit-Classifier Validation Uses Random Split Instead of Time-Aware Split

Current training path:
- `train_bucket_exit_classifier` uses `train_test_split(..., stratify=y)` (`src/orion/ml/exit_classifier.py:622`),
- no time-ordered or walk-forward validation path is used in this module.

Risk:
- reported AUC can be optimistic due temporal leakage, especially in non-stationary options-flow regimes.

### 49.4 Updated Priorities

P0:
1. Include losing/stop scenarios in exit-classifier training set (or train explicit “winner-only” and “risk-protect” models with clear runtime routing) to remove survivor bias.
2. Add `ml_ready` (or strict completeness equivalent) to exit-classifier training query filters.
3. Replace random split with time-aware validation (walk-forward or anchored split) and report both temporal holdout and training metrics.

P1:
1. Add per-bucket class-balance dashboards and fail training when class coverage is below minimum thresholds.

## 50) Pass 43 Continuation (2026-02-06)

### 50.1 Exit Orchestration Split-Brain: Two Independent Runtime Loops Can Close the Same Positions

Current runtime wiring:
- compose runs both `execution` (`python -m orion.main_execution`) and `position-monitor` (`python -m orion.main_position_monitor`) simultaneously (`docker-compose.yml:124`, `docker-compose.yml:144`),
- `main_execution` evaluates exit rules and executes closes via `execution_engine.close_position(...)` (`src/orion/main_execution.py:320` to `src/orion/main_execution.py:340`),
- `position_monitor` independently evaluates ML/heuristic exits and calls `connector.close_position(...)` (`src/orion/execution/position_monitor.py:242`, `src/orion/execution/position_monitor.py:315`).

Risk:
- duplicate/uncoordinated exit attempts can generate conflicting close orders and noisy execution telemetry.

### 50.2 `PositionMonitor` Uses Approximate Entry Time (`now`) Instead of Actual Entry Timestamp

Current behavior:
- new tracked positions set `entry_time=datetime.now(timezone.utc)` (`src/orion/execution/position_monitor.py:124`),
- time-held features are derived from this value for exit decisions (`src/orion/execution/position_monitor.py:222` to `src/orion/execution/position_monitor.py:223`),
- `_fetch_entry_context` query does not return entry timestamp from decision/candidate rows (`src/orion/execution/position_monitor.py:155` to `src/orion/execution/position_monitor.py:173`).

Risk:
- time-based exit logic (especially bucket urgency) is systematically wrong after monitor restarts or when positions predate process start.

### 50.3 `PositionMonitor` Entry-Context Lookup Uses Ticker Match, Not Option Symbol Match

Current lookup path:
- context query filters on `ct.ticker = :symbol` (`src/orion/execution/position_monitor.py:168`),
- but candidate model stores option contracts separately in `candidate_trades.option_symbol` (`src/orion/storage/models_gold.py:33`),
- if broker position symbol is an option contract, lookup can miss and default to generic `{"bucket": "SWING"}` (`src/orion/execution/position_monitor.py:209`).

Risk:
- option positions can be misbucketed and evaluated with weak/default context, reducing exit-model reliability.

### 50.4 Updated Priorities

P0:
1. Consolidate to one canonical exit executor (either `main_execution` exit-rule path or `position_monitor`) and disable the other in active compose runtime.
2. Populate monitor `entry_time` from authoritative execution timestamps (strategy decision / fill time), not process-time defaults.

P1:
1. Extend position-context lookup to match option positions by `option_symbol` and fall back to ticker only when appropriate.
2. Add idempotency guardrails around close-order submission (for example open-close intent table keyed by position + decision window).

## 51) Pass 44 Continuation (2026-02-06)

### 51.1 Admin Dashboard Endpoints Are Backed by In-Memory State, Not Runtime Execution Data

Current behavior:
- `/dashboard/*` endpoints read from `core.pnl_tracker` singleton (`src/orion/api/main.py:572` to `src/orion/api/main.py:667`),
- `PnLTracker` stores positions and P&L in process memory only (`src/orion/core/pnl_tracker.py:80` to `src/orion/core/pnl_tracker.py:89`),
- no in-repo caller updates tracker position state (`update_position` / `close_position`) outside the tracker itself (reference scan shows only definitions in `src/orion/core/pnl_tracker.py:91` and `src/orion/core/pnl_tracker.py:129`).

Risk:
- dashboard can report empty/stale portfolio state despite active trading, creating false operational confidence.

### 51.2 Dashboard Data Path Bypasses Persisted Execution Sources Already Present in Orion Schema

Current schema/runtime contrast:
- execution persistence tables exist for `orders`, `fills`, and `positions_snapshots` (`src/orion/storage/models_execution.py:13`, `src/orion/storage/models_execution.py:38`, `src/orion/storage/models_execution.py:65`),
- dashboard endpoints do not query these tables; they return ephemeral singleton state (`src/orion/api/main.py:562` to `src/orion/api/main.py:667`).

Risk:
- observability path is disconnected from durable execution truth and cannot be reliably reconciled across restarts/incidents.

### 51.3 Updated Priorities

P0:
1. Rebase dashboard endpoints onto durable execution sources (`fills`, `positions_snapshots`, and/or broker sync) rather than in-memory tracker state.
2. Add parity checks that compare dashboard open positions/P&L against broker or persisted fills snapshots and alert on divergence.

P1:
1. If in-memory tracker is retained for low-latency UI hints, clearly label it as transient and add periodic hydration from persisted execution tables.

## 52) Pass 45 Continuation (2026-02-06)

### 52.1 `PositionManager` Runtime Integration Is Incomplete (Write/Sync Paths Unused)

Current runtime behavior:
- `main_execution` initializes `PositionManager` and iterates `get_open_positions()` for exit rules (`src/orion/main_execution.py:223`, `src/orion/main_execution.py:322`),
- but `PositionManager.add_position(...)` has no in-repo call sites (`src/orion/execution/position_manager.py:126`; reference search shows no usage),
- `PositionManager.sync_with_broker(...)` is also not called (`src/orion/execution/position_manager.py:193`; no runtime usage found).

Risk:
- exit-rule evaluation set can become stale and miss newly opened positions during process lifetime.

### 52.2 `PositionManager` Keys Positions by Ticker, Collapsing Multi-Position/Options Cases

Current model/state shape:
- internal store uses `Dict[str, OpenPosition]` keyed by ticker (`src/orion/execution/position_manager.py:63`),
- `add_position` writes by `candidate.ticker` (`src/orion/execution/position_manager.py:159`),
- `OpenPosition` has optional `option_chain`, but keying does not include contract identity (`src/orion/execution/position_manager.py:37`).

Risk:
- multiple open positions on the same underlying (for example different option contracts/legs) can overwrite each other and lose exit context.

### 52.3 Exit Path in `ExecutionEngine.close_position` Uses Ticker-Only Symboling

Current exit execution behavior:
- `close_position` accepts `ticker` and uses it directly for price lookup and order submission (`src/orion/execution/execution_engine.py:428`, `src/orion/execution/execution_engine.py:451`, `src/orion/execution/execution_engine.py:464`, `src/orion/execution/execution_engine.py:487`),
- `main_execution` passes `position.ticker` from `PositionManager` into this method (`src/orion/main_execution.py:334` to `src/orion/main_execution.py:336`),
- no option-symbol branch is present in this exit path despite options fields existing in candidate/position models (`src/orion/storage/models_gold.py:33`, `src/orion/execution/position_manager.py:37`).

Risk:
- option exits in this path can target the wrong instrument identity (underlying ticker vs option contract), causing failed or incorrect close behavior.

### 52.4 Updated Priorities

P0:
1. Wire `PositionManager.add_position` on successful executions and invoke periodic `sync_with_broker` to keep tracked positions current.
2. Re-key tracked positions by stable instrument identity (`option_chain`/broker symbol + side) rather than underlying ticker only.
3. Add contract-aware close path in `ExecutionEngine.close_position` that uses option symbol when applicable.

P1:
1. Add integration tests covering simultaneous multi-contract positions on one ticker and correct exit targeting per contract.

## 53) Pass 46 Continuation (2026-02-06)

### 53.1 Exit Decisions Are Persisted Without `candidate_id` Linkage

Current persistence path:
- `ExecutionEngine._persist_exit_decision(...)` inserts `ExitDecision` rows with `ticker`, rule metadata, and broker IDs but does not populate `candidate_id` (`src/orion/execution/execution_engine.py:530` to `src/orion/execution/execution_engine.py:540`),
- `ExitDecision` model explicitly includes `candidate_id` as the link back to entry trade (`src/orion/storage/models_gold.py:62`).

Risk:
- exit records lose deterministic linkage to the originating candidate, weakening auditability and lifecycle reconstruction.

### 53.2 `PositionManager.initialize` Relies on Missing Linkage, So Closed Positions Can Reappear as Open

Current open-position reconstruction:
- startup query joins `StrategyDecision` to `ExitDecision` on `candidate_id` and keeps rows where no exit join exists (`src/orion/execution/position_manager.py:74` to `src/orion/execution/position_manager.py:78`),
- because exit rows from runtime close path omit `candidate_id`, this join can fail to match historical closes.

Risk:
- on restart, already-exited positions can be rehydrated as open and re-enter exit loops, causing noisy/double-close behavior.

### 53.3 Updated Priorities

P0:
1. Persist `candidate_id` on all `ExitDecision` writes from execution paths (thread through `close_position`/`_persist_exit_decision` call chain).
2. Add restart-resume integration test: execute open->close cycle, restart `PositionManager.initialize`, assert position is not rehydrated.

P1:
1. Backfill/repair recent `ExitDecision` rows with candidate linkage where deterministically resolvable (for example via broker order IDs + order/fill tables).

## 54) Pass 47 Continuation (2026-02-06)

### 54.1 Pending-Candidate Polling Lacks Concurrency-Safe Claiming

Current execution selection path:
- pending candidates are selected by anti-join on `strategy_decisions` (`src/orion/main_execution.py:59` to `src/orion/main_execution.py:63`),
- no row-level claiming/locking (`FOR UPDATE SKIP LOCKED`-style) or in-progress state exists before decision persistence.

Risk:
- concurrent execution workers (intentional scale-out or accidental duplicate processes) can fetch/process the same candidate simultaneously.

### 54.2 `strategy_decisions` Schema Does Not Enforce One Decision Per Candidate

Current schema shape:
- `StrategyDecision.candidate_id` is indexed but not unique (`src/orion/storage/models_gold.py:86`),
- duplicate decision rows for one candidate are therefore structurally allowed.

Risk:
- duplicate executions/skips for the same candidate can be persisted, complicating auditability and downstream lifecycle logic.

### 54.3 Updated Priorities

P0:
1. Implement atomic candidate claiming (for example transactional claim table/update or `SKIP LOCKED` polling pattern) before policy/execution steps.
2. Add uniqueness/idempotency guard (`UNIQUE` on decision candidate identity or deterministic upsert) aligned to intended one-decision-per-candidate contract.

P1:
1. Add multi-worker integration test that runs two execution loops against the same candidate set and asserts single decision/execution outcome per candidate.

## 55) Pass 48 Continuation (2026-02-06)

### 55.1 Fill Idempotency Is Process-Local; Restart Path Can Reprocess Recent Fills Into Risk State

Current runtime behavior:
- fill polling defaults to a 5-minute lookback when `last_fill_poll_ts` is absent (startup/restart path) (`src/orion/execution/execution_engine.py:618` to `src/orion/execution/execution_engine.py:621`),
- partial-fill dedupe uses in-memory `_partial_fill_tracker` only (`src/orion/execution/execution_engine.py:652` to `src/orion/execution/execution_engine.py:656`),
- `RiskManager.process_fill` idempotency uses in-memory `processed_fill_ids` set (`src/orion/execution/risk_manager.py:48`, `src/orion/execution/risk_manager.py:767` to `src/orion/execution/risk_manager.py:772`),
- broker sync explicitly clears processed fill history (`src/orion/execution/risk_manager.py:942` to `src/orion/execution/risk_manager.py:944`).

Risk:
- after restart, already-accounted fills within lookback can be reapplied to risk state (equity/daily-loss/exposure), causing drift from broker truth.

### 55.2 DB Fill Dedupe Happens After Risk Mutation

Current processing order:
- `_process_single_fill` mutates risk state first via `risk_manager.process_fill(...)` (`src/orion/execution/execution_engine.py:686` to `src/orion/execution/execution_engine.py:688`),
- fill DB write uses `ON CONFLICT DO NOTHING` on broker order id (`src/orion/execution/execution_engine.py:946` to `src/orion/execution/execution_engine.py:948`).

Risk:
- duplicate fill events can still alter risk state even when persistence layer correctly drops duplicate fill rows.

### 55.3 Durable Idempotency Helpers Exist But Are Unused

Current code state:
- `ExecutionEngine` defines `_is_fill_processed` and `_mark_fill_processed` backed by `ProcessedFill` table (`src/orion/execution/execution_engine.py:785`, `src/orion/execution/execution_engine.py:828`),
- no runtime call sites invoke these helpers in fill polling path (reference scan shows definitions only).

Risk:
- durable anti-duplication infrastructure is present but disconnected, leaving runtime behavior dependent on volatile in-memory caches.

### 55.4 Updated Priorities

P0:
1. Move fill idempotency to durable pre-checks (DB-backed `ProcessedFill` or equivalent) before mutating risk state.
2. Reorder processing so duplicate detection/persistence claim occurs before `risk_manager.process_fill`.

P1:
1. Add restart regression test: process fill, restart engine, replay same fill payload, assert risk state unchanged.

## 56) Pass 49 Continuation (2026-02-06)

### 56.1 Options Live Path Bypasses `RiskManager` Order Gates (Including Greeks Limits)

Current runtime behavior:
- options execution path (`_execute_options_order`) performs system-health/lag preflight, DTE check, quote lookup, and contract sizing, then submits order directly (`src/orion/execution/execution_engine.py:173` to `src/orion/execution/execution_engine.py:224`),
- no call to `risk_manager.check_order(...)` or `risk_manager.check_options_order(...)` exists in this path,
- `RiskManager.check_options_order(...)` exists and includes Greeks-limit checks (`src/orion/execution/risk_manager.py:119` to `src/orion/execution/risk_manager.py:156`),
- current references show `check_options_order` only in unit tests, not active runtime callsites (`tests/unit/test_risk_greeks_v2.py:152`, `tests/unit/test_risk_greeks_v2.py:165`, `tests/unit/test_risk_greeks_v2.py:185`).

Risk:
- options orders can bypass max-order/ticker exposure and options Greeks constraints in live execution flow, creating production behavior drift from risk-policy intent and tests.

### 56.2 Preflight Risk Sizing Contract Differs From Actual Options Order Sizing

Current flow split:
- preflight computes `qty` via `risk_manager.calculate_size(entry_price=limit_price, ...)` and validates with `check_order(candidate.ticker, qty, price, side, ...)` (`src/orion/execution/signal_preflight.py:93` to `src/orion/execution/signal_preflight.py:103`),
- actual options execution later derives contracts from `max_option_premium_pct` and `options_connector.calculate_option_contracts(...)` (`src/orion/execution/execution_engine.py:208` to `src/orion/execution/execution_engine.py:210`).

Risk:
- preflight approval is not bound to the real contracts submitted, so accepted signals can still produce materially different risk/exposure outcomes at execution time.

### 56.3 Updated Priorities

P0:
1. Add mandatory risk gate in `_execute_options_order` before submission:
- use `risk_manager.check_options_order(...)` (or equivalent unified options gate) with contract-aware cost and Greeks inputs.
2. Unify preflight/execution sizing contracts for options so preflight-validated quantity matches order-submission quantity semantics.

P1:
1. Add regression tests that fail if options execution submits without a `RiskManager` pass.
2. Add side-by-side preflight-vs-execution size parity assertions for option candidates.

## 57) Pass 50 Continuation (2026-02-06)

### 57.1 Options Notional Units Are Inconsistent in Risk Sizing Paths

Current implementation contracts:
- `RiskManager.calculate_size(...)` is share-based (`max_order_qty = floor(max_order_value / entry_price)`) (`src/orion/execution/risk_manager.py:523` to `src/orion/execution/risk_manager.py:556`),
- `RiskManager.check_order(...)` computes `estimated_cost = quantity * price` (`src/orion/execution/risk_manager.py:71` to `src/orion/execution/risk_manager.py:85`),
- options contract sizing uses `option_price * 100` in connector logic (`src/orion/connectors/alpaca_options_connector.py:240` to `src/orion/connectors/alpaca_options_connector.py:254`),
- execution logging also treats options premium as `contracts * option_price * 100` (`src/orion/execution/execution_engine.py:405`).

Risk:
- options risk checks using share-style `quantity * price` understate true contract notional by ~100x, making max-order/exposure safeguards materially weaker than configured intent.

### 57.2 `check_options_order` Reuses Share-Style `check_order` Cost Math

Current method behavior:
- `RiskManager.check_options_order(...)` delegates to `check_order(...)` before Greeks checks (`src/orion/execution/risk_manager.py:148` to `src/orion/execution/risk_manager.py:153`),
- delegated `check_order(...)` uses share-style notional math (`quantity * price`) with no contract multiplier (`src/orion/execution/risk_manager.py:84`).

Risk:
- even after wiring `check_options_order` into runtime, notional-based limits may still be non-binding for options unless cost semantics are fixed.

### 57.3 Updated Priorities

P0:
1. Introduce contract-aware cost normalization for options risk checks (for example `notional = contracts * premium * 100`) and apply it consistently in `check_order`/`check_options_order` paths.
2. Add explicit unit semantics to risk method contracts (`shares` vs `contracts`) to avoid silent misuse across preflight and execution.

P1:
1. Add risk regression tests that enforce options max-order-size and ticker-exposure rejections at realistic premium/contract sizes.
2. Add an audit assertion that `check_options_order` and execution premium accounting produce matching notional values for the same order payload.

## 58) Pass 51 Continuation (2026-02-06)

### 58.1 Rollup Consumers Still Depend on Orion-Local `gold_ticker_rollup`

Current rollup read paths:
- execution preflight resolves rollup evidence from `GoldTickerRollup` ORM rows (`src/orion/execution/signal_preflight.py:118` to `src/orion/execution/signal_preflight.py:124`),
- admin API `/rollups` and `/rollups/{ticker}/{period}/{timestamp_utc}` query `GoldTickerRollup` directly (`src/orion/api/main.py:392` to `src/orion/api/main.py:442`, `src/orion/api/main.py:445` to `src/orion/api/main.py:476`).

Risk:
- rollup integrity and observability remain tied to Orion-local SQL state instead of a centralized Gateway/Heber-backed contract, creating divergence risk when local rollup job lags or is not running.

### 58.2 Rollup Production Path Is Not a First-Class Deployed Service

Current production wiring:
- rollup generation starts only as a background task inside `IngestionService.initialize()` (`src/orion/ingestion/service.py:123` to `src/orion/ingestion/service.py:129`),
- current compose profile runs execution/label/enrichment services but does not run ingestion service (`docker-compose.yml:47` to `docker-compose.yml:144`),
- execution service explicitly disables rollup requirement (`docker-compose.yml:123`).

Risk:
- rollup consumers can operate with stale or absent rollups while still serving API responses and preflight traces, reducing confidence in decision evidence and debugging surfaces.

### 58.3 Updated Priorities

P0:
1. Decide canonical rollup ownership now:
- either run rollups as a dedicated always-on service in Orion,
- or consume rollups from Heber canonical datasets via one data-access facade.
2. Align execution preflight and admin `/rollups` endpoints to the same canonical source to prevent split-brain rollup views.

P1:
1. Add freshness SLO checks for rollup datasets used by preflight/API (max allowed staleness, explicit alerting).
2. Add integration tests that validate rollup availability/shape for execution preflight in the deployed compose profile.

## 59) Pass 52 Continuation (2026-02-06)

### 59.1 Compose Provisions Redpanda/MinIO, but Active Runtime Does Not Execute the Producer Path

Current deployment shape:
- compose includes `redpanda`, `minio`, and `createbuckets` services (`docker-compose.yml:21` to `docker-compose.yml:46`, `docker-compose.yml:241` to `docker-compose.yml:267`),
- compose does not run `orion.ingestion` (no ingestion service entry in active service list; execution stack starts at labelers/enrichment/execution) (`docker-compose.yml:47` to `docker-compose.yml:224`),
- `RedpandaProducer` is only used by `IngestionService` (`src/orion/ingestion/service.py:16`, `src/orion/ingestion/service.py:100`, `src/orion/ingestion/service.py:430`).

Risk:
- Kafka/lakehouse infrastructure appears “live” in deployment config but receives no Orion-produced event flow in active runtime, obscuring true data-path ownership and incident triage.

### 59.2 Orion Lakehouse Writer Is Config-Gated Off by Default in Current Compose Contract

Current write-path behavior:
- `IngestionService` initializes `LakehouseWriter` and calls it in cycle processing (`src/orion/ingestion/service.py:55`, `src/orion/ingestion/service.py:213`, `src/orion/ingestion/service.py:343`),
- `LakehouseWriter` disables itself unless all `ORION_LAKEHOUSE_*` vars are present (`src/orion/storage/lakehouse.py:30` to `src/orion/storage/lakehouse.py:40`),
- current compose service env blocks do not define `ORION_LAKEHOUSE_ENDPOINT_URL`, `ORION_LAKEHOUSE_ACCESS_KEY`, `ORION_LAKEHOUSE_SECRET_KEY`, or `ORION_LAKEHOUSE_BUCKET` (`docker-compose.yml:56` to `docker-compose.yml:223`).

Risk:
- even when ingestion is run ad hoc, lakehouse writes can silently no-op under missing env configuration, producing false confidence in bronze archival coverage.

### 59.3 Updated Priorities

P0:
1. Choose one canonical infra story for Orion runtime:
- either remove/disable unused local Redpanda/MinIO services from default compose profile,
- or restore ingestion as a first-class service and wire end-to-end producer/consumer/lakehouse health checks.
2. Add startup hard-fail (or prominent health-fail state) when lakehouse write path is expected but `ORION_LAKEHOUSE_*` config is incomplete.

P1:
1. Add compose-level integration check that verifies active event production volume to intended transport (DB-only vs Redpanda/lakehouse) and alerts on drift.

## 60) Pass 53 Continuation (2026-02-06)

### 60.1 Admin API Is Test-Covered but Not Deployed in Current Compose Runtime

Current state:
- `orion.api.main` defines active Admin endpoints (`/flows`, `/rollups`, `/dashboard/*`) (`src/orion/api/main.py:479` to `src/orion/api/main.py:669`),
- compose service list has no API/uvicorn service for this app (`docker-compose.yml:47` to `docker-compose.yml:286`),
- tests exercise API routes in-process via ASGI/TestClient imports of `app` (`tests/integration/test_api_endpoints.py:7`, `tests/api/test_flow_filters.py:5`, `tests/api/test_pointer_endpoints.py:5`).

Risk:
- endpoint contract tests can pass while the endpoint surface is unavailable in deployed runtime, creating false operational confidence for consumers and runbooks.

### 60.2 API Contract Still Anchored to Orion-Local Tables While Runtime Ownership Is Shifting

Current API data sources:
- `/flows` reads `SilverOptionFlow` SQL rows (`src/orion/api/main.py:495` to `src/orion/api/main.py:531`),
- `/rollups` reads `GoldTickerRollup` SQL rows (`src/orion/api/main.py:408` to `src/orion/api/main.py:427`),
- tests seed/read these same Orion-local tables for endpoint behavior (`tests/api/test_flow_filters.py:7` to `tests/api/test_flow_filters.py:8`, `tests/api/test_pointer_endpoints.py:8`).

Risk:
- even if API deployment is restored, endpoint semantics remain coupled to Orion-local storage rather than Gateway/Heber canonical data contracts.

### 60.3 Updated Priorities

P0:
1. Make an explicit product decision for Admin API:
- deploy it as a first-class service with health checks and auth wiring,
- or archive API surface/tests from default runtime expectations.
2. If API remains, route `/flows`/`/rollups` through the same canonical data-access facade used for Gateway/Heber parity work.

P1:
1. Add a deployment-level smoke test (outside in-process ASGI tests) that verifies API availability in active compose profile.

## 61) Pass 54 Continuation (2026-02-06)

### 61.1 `ExecutionEngine.close_position` Hardcodes Sell-Side Exit, Ignoring Position Direction

Current execution behavior:
- `ExecutionEngine.close_position(...)` always sets `side = OrderSide.SELL` before submitting exit order (`src/orion/execution/execution_engine.py:458`),
- `main_execution` calls this path with only `ticker` + `qty` (no direction passed) (`src/orion/main_execution.py:334` to `src/orion/main_execution.py:337`),
- `PositionManager` tracks `direction` on open positions (`LONG`/`SHORT`) but that field is not consumed in this close path (`src/orion/execution/position_manager.py:28`, `src/orion/execution/position_manager.py:145`).

Risk:
- if shorting is enabled (`src/orion/config.py:19`) and a short position is tracked, the exit path can submit an additional sell instead of buy-to-cover, increasing exposure instead of closing it.

### 61.2 Exit Semantics Diverge Across the Two Active Close Paths

Current split behavior:
- `position_monitor` closes via broker-level `connector.close_position(symbol)` which is position-side aware (`src/orion/execution/position_monitor.py:315`),
- `main_execution` close path uses explicit side selection and currently forces sell (`src/orion/execution/execution_engine.py:458`).

Risk:
- the same position can receive different close semantics depending on which runtime path handles it, compounding split-brain behavior with directional correctness drift.

### 61.3 Updated Priorities

P0:
1. Make `ExecutionEngine.close_position` direction-aware (`SELL` for long exits, `BUY` for short cover) and thread position side through call chain.
2. Add guardrails/tests that fail if close logic for short positions submits sell-side orders.

P1:
1. Unify close semantics by standardizing on one close primitive across `main_execution` and `position_monitor` (prefer broker-native close-by-symbol if contract identity is reliable).

## 62) Pass 55 Continuation (2026-02-06)

### 62.1 Exit-Rule Position Quantity Can Rehydrate as Zero in `PositionManager`

Current rehydration path:
- `PositionManager._create_position_from_decision` reconstructs `entry_price` from `decision.execution_params.limit_price` and does not read persisted order tables (`src/orion/execution/position_manager.py:106` to `src/orion/execution/position_manager.py:116`),
- `PositionManager.add_position` sets `qty` from `decision.execution_params["qty"]` defaulting to `0` (`src/orion/execution/position_manager.py:155`),
- `ExecutionEngine._submit_order` sets only `client_order_id` in `decision.execution_params` and does not persist `qty` there (`src/orion/execution/execution_engine.py:318` to `src/orion/execution/execution_engine.py:321`),
- actual executed quantity is persisted in order records (`src/orion/execution/execution_engine.py:873` to `src/orion/execution/execution_engine.py:880`).

Risk:
- positions tracked for exit-rule orchestration can carry `qty=0`, causing ineffective close attempts and stale “open” state loops despite real broker exposure.

### 62.2 `main_execution` Exit Path Uses Rehydrated `position.qty` Directly

Current close call:
- `main_execution` passes `position.qty` into `execution_engine.close_position(...)` (`src/orion/main_execution.py:334` to `src/orion/main_execution.py:337`).

Risk:
- zero/incorrect rehydrated quantity directly propagates into exit order submission path, degrading exit reliability.

### 62.3 Updated Priorities

P0:
1. Make position quantity source-of-truth explicit:
- persist executed qty on `StrategyDecision` (or linked execution snapshot),
- or reconstruct from `OrderRecord`/broker positions during `PositionManager.initialize`.
2. Add hard guard to block close submissions when tracked qty is non-positive and force broker-side quantity refresh.

P1:
1. Add restart regression test: execute order with nonzero qty, restart `PositionManager`, assert tracked qty matches persisted/broker qty and exit call uses that qty.

## 63) Pass 56 Continuation (2026-02-06)

### 63.1 Post-Execution Failure Reasons Are Mutated In-Memory but Not Persisted to `strategy_decisions`

Current lifecycle:
- `main_execution` persists the decision record before execution (`src/orion/main_execution.py:293` to `src/orion/main_execution.py:295`),
- `ExecutionEngine` mutates `decision.reason` for execution-time failures/rejections (for example broker error, rate limit, risk rejection) (`src/orion/execution/execution_engine.py:161`, `src/orion/execution/execution_engine.py:315`, `src/orion/execution/execution_engine.py:366`, `src/orion/execution/execution_engine.py:423`),
- post-execution DB update path only sets `executed_successfully` and does not persist updated `reason`/trace fields (`src/orion/main_execution.py:190` to `src/orion/main_execution.py:198`).

Risk:
- `strategy_decisions` can show final status (`FALSE`/`SKIPPED`) with stale pre-execution reasons, degrading auditability and operator debugging fidelity.

### 63.2 Updated Priorities

P0:
1. Replace status-only post-execution update with full decision-state persistence (at minimum `executed_successfully`, `reason`, and execution-trace deltas).
2. Add regression test asserting that broker/risk failures are reflected in persisted `strategy_decisions.reason`.

P1:
1. Normalize execution failure taxonomy across `strategy_decisions.reason` and `order_records.error_message` so dashboards/alerts can group by canonical failure codes.

## 64) Pass 57 Continuation (2026-02-06)

### 64.1 Option-Quote Checkpoint Selection Can Starve Older Eligible Events

Current selection logic:
- tracker pulls recent flows with fixed recency and newest-first cap (`ORDER BY f.flow_ts_utc DESC LIMIT 1000`) (`src/orion/main_option_quote_tracker.py:82` to `src/orion/main_option_quote_tracker.py:83`),
- checkpoint worklist is derived only from that bounded result set (`src/orion/main_option_quote_tracker.py:187` to `src/orion/main_option_quote_tracker.py:217`),
- no pagination/cursor over older candidate rows exists in this path.

Risk:
- in high-flow periods, older-but-still-within-window events can be consistently excluded from processing, leaving checkpoint coverage gaps in `silver_option_quotes` and downstream label features.

### 64.2 Tracking-Window Constant Drift (Config Says Variable, Query Is Hardcoded)

Current code state:
- module defines `MAX_TRACKING_AGE_HOURS = 24` (`src/orion/main_option_quote_tracker.py:37`),
- SQL filter is hardcoded `NOW() - INTERVAL '24 hours'` and does not use the constant (`src/orion/main_option_quote_tracker.py:79`).

Risk:
- maintainers may assume the constant governs behavior, but runtime window changes require manual SQL edits, increasing configuration drift and operational mistakes.

### 64.3 Updated Priorities

P0:
1. Replace newest-only fixed-limit polling with deterministic pagination/cursor (for example event-time ascending batches with last-processed checkpoint state) so all eligible rows are eventually processed.
2. Bind SQL recency filter to a single config source (`MAX_TRACKING_AGE_HOURS` or env setting), removing hardcoded interval literals.

P1:
1. Add coverage monitor: expected-vs-populated checkpoint counts by horizon/day and alert on sustained underfill.
2. Add regression/integration test with >1000 flow rows to assert no starvation of older eligible checkpoint events.

## 65) Pass 58 Continuation (2026-02-06)

### 65.1 Exit-Rule Evaluator Still Pulls Flow Context from Orion-Local `silver_uw_flow`

Current exit-context path:
- `main_execution` fetches recent flow from `SilverOptionFlow` SQL rows for each open position (`src/orion/main_execution.py:26` to `src/orion/main_execution.py:39`, `src/orion/main_execution.py:324`),
- flow-based exit rules use `recent_flow` as primary trigger input and return no signal when empty (`src/orion/processing/rules/exit_rules.py:148` to `src/orion/processing/rules/exit_rules.py:149`, `src/orion/processing/rules/exit_rules.py:212`, `src/orion/processing/rules/exit_rules.py:311`).

Risk:
- when local `silver_uw_flow` is stale/empty, exit-rule engine can silently degrade to “no exit” behavior even under adverse flow conditions.

### 65.2 Runtime Ownership Drift Increases Exit Blind-Spot Probability

Current ingestion ownership notes:
- ingestion service comments state UW flow/darkpool ingestion is centralized via Data-Gateway -> Heber and local UW polling was removed (`src/orion/ingestion/service.py:200` to `src/orion/ingestion/service.py:201`, `src/orion/ingestion/service.py:275`),
- active compose runtime does not run ingestion service in default profile (`docker-compose.yml:47` to `docker-compose.yml:224`).

Risk:
- exit rules tied to local SQL flow context are vulnerable to missing-input blind spots under current centralized data architecture.

### 65.3 Updated Priorities

P0:
1. Move exit-rule flow context reads behind the same Gateway/Heber-backed data-access facade used for ingestion parity work.
2. Add fail-loud behavior when exit context is unavailable (for example explicit warning state/metric rather than implicit empty-flow “no exit”).

P1:
1. Add integration test that simulates stale local `silver_uw_flow` with available Heber flow and asserts exit evaluator still receives non-empty context.

## 66) Pass 59 Continuation (2026-02-06)

### 66.1 `main_execution` Calls Exit Rules With Empty Context, Disabling Context-Dependent Rules

Current runtime invocation:
- exit-rule loop calls `rule.should_exit(position, recent_flow, context={})` for every rule (`src/orion/main_execution.py:327`),
- multiple rules require context keys and return `None` when absent:
  - `VolumeOIDivergenceExitRule` requires `current_oi` (`src/orion/processing/rules/exit_rules.py:270` to `src/orion/processing/rules/exit_rules.py:275`),
  - `IVContractionExitRule` requires `current_iv` for IV-drop logic and optional earnings context (`src/orion/processing/rules/exit_rules.py:370` to `src/orion/processing/rules/exit_rules.py:376`, `src/orion/processing/rules/exit_rules.py:388`),
  - `PriceTargetExitRule` requires `current_option_price` and entry option price (`src/orion/processing/rules/exit_rules.py:496` to `src/orion/processing/rules/exit_rules.py:503`).

Risk:
- configured exit policy surface appears active, but context-dependent rules are effectively inert in this runtime path, reducing exit coverage and increasing unintended hold risk.

### 66.2 Updated Priorities

P0:
1. Build and pass a populated exit context object in `main_execution` (at minimum `current_oi`, `current_iv`, `current_option_price`, and earnings proximity where supported).
2. Add guardrail logging/metrics when required context keys are missing so rule deactivation is explicit, not silent.

P1:
1. Add integration tests proving each context-dependent exit rule can fire in `main_execution` path with representative context payloads.

## 67) Pass 60 Continuation (2026-02-06)

### 67.1 Exit Rules Consume Underlying-Ticker Flow Streams Without Contract Scoping

Current data fetch:
- `main_execution` pulls recent flow by underlying ticker only (`SilverOptionFlow.ticker == ticker`) (`src/orion/main_execution.py:32` to `src/orion/main_execution.py:35`),
- this same `recent_flow` list is passed to all exit rules for the tracked position (`src/orion/main_execution.py:324` to `src/orion/main_execution.py:327`).

Rule-level scope behavior:
- `NetPremiumDeclineExitRule` aggregates bullish/bearish premium across all `recent_flow` rows with no option contract or DTE match filter (`src/orion/processing/rules/exit_rules.py:221` to `src/orion/processing/rules/exit_rules.py:236`),
- `WaningMomentumExitRule` counts sweeps across all rows in window with no contract scoping (`src/orion/processing/rules/exit_rules.py:323` to `src/orion/processing/rules/exit_rules.py:329`),
- `OpposingClusterExitRule` includes a note that strike/expiry filtering “could” be added but does not implement it (`src/orion/processing/rules/exit_rules.py:450`).

Risk:
- exits for one option position can be triggered by unrelated flow activity on different expiries/strikes of the same underlying, increasing false positives/negatives in contract-level strategy exits.

### 67.2 Updated Priorities

P0:
1. Scope exit-rule flow inputs to position identity (option contract or explicit strike/expiry family), not ticker-only streams.
2. Add deterministic filter contract in rule input layer (for example by `option_chain` exact match, then fallback to bucketed strike/expiry neighborhood only if explicitly intended).

P1:
1. Add regression tests with mixed-contract same-ticker flow proving one position’s exits are unaffected by unrelated contract flow.

## 68) Pass 61 Continuation (2026-02-06)

### 68.1 `PriceTargetExitRule` Is Enabled by Default but Lacks Required Position Field

Current wiring:
- default exit-rule set includes `PriceTargetExitRule` (`src/orion/processing/rules/exit_rules.py:551`),
- rule requires `entry_option_price` on position plus `current_option_price` in context (`src/orion/processing/rules/exit_rules.py:500` to `src/orion/processing/rules/exit_rules.py:503`),
- active `OpenPosition` model has `entry_price` but no `entry_option_price` field (`src/orion/execution/position_manager.py:33` to `src/orion/execution/position_manager.py:35`),
- `PositionManager` populates only `entry_price` from `execution_params.limit_price` (`src/orion/execution/position_manager.py:115`, `src/orion/execution/position_manager.py:149`).

Risk:
- `PriceTargetExitRule` can remain effectively inert in the `main_execution` exit path regardless of threshold configuration, creating false confidence that target/stop exits are active.

### 68.2 Updated Priorities

P0:
1. Align position contract with rule requirements:
- either add and persist `entry_option_price` on tracked positions,
- or refactor `PriceTargetExitRule` to use canonical available field(s) (`entry_price`) with explicit option/equity semantics.
2. Add boot-time validation that each enabled exit rule’s required fields are satisfiable by runtime position/context contracts.

P1:
1. Add regression tests asserting `PriceTargetExitRule` can trigger under controlled entry/current price conditions in active execution path.

## 69) Pass 62 Continuation (2026-02-06)

### 69.1 DTE-Bucket Filtering Depends on `position.option_chain`, But Position Construction Does Not Use Canonical `candidate.option_symbol`

Current model/contracts:
- `CandidateTrade` includes canonical `option_symbol` field (`src/orion/storage/models_gold.py:33`),
- `PositionManager` builds `OpenPosition.option_chain` from `candidate.evidence["option_chain"]` or `entry_context["option_chain"]`, not from `candidate.option_symbol` (`src/orion/execution/position_manager.py:116`, `src/orion/execution/position_manager.py:150`).

Rule dependency:
- exit-rule DTE bucketing reads `position.option_chain` and returns `"UNKNOWN"` when missing (`src/orion/processing/rules/exit_rules.py:92` to `src/orion/processing/rules/exit_rules.py:95`),
- when position bucket is `"UNKNOWN"`, flow DTE filter is effectively disabled (`src/orion/processing/rules/exit_rules.py:117` to `src/orion/processing/rules/exit_rules.py:118`).

Risk:
- DTE-alignment logic in flow-based exit rules can silently degrade to broad ticker-level matching, increasing mis-scoped exits and reducing strategy parity with intended contract-level behavior.

### 69.2 Updated Priorities

P0:
1. Populate `OpenPosition.option_chain` from canonical `candidate.option_symbol` by default, with evidence/context only as fallback.
2. Add validation warnings when option positions are tracked without a contract identifier (cannot enforce DTE/contract filters).

P1:
1. Add regression tests that assert DTE bucket is deterministically resolved for option positions created from `candidate.option_symbol`.

## 70) Pass 63 Continuation (2026-02-06)

### 70.1 Options-Specific Exit Policy Is Applied to All Open Positions (Including Equities)

Policy intent:
- exit-rule module is explicitly scoped to short-term options trades (`src/orion/processing/rules/exit_rules.py:2` to `src/orion/processing/rules/exit_rules.py:5`).

Runtime application:
- `main_execution` loads default exit rules and applies them to every tracked open position (`src/orion/main_execution.py:224`, `src/orion/main_execution.py:322` to `src/orion/main_execution.py:327`),
- `PositionManager.initialize` loads executed open positions without filtering to options-only candidates (`src/orion/execution/position_manager.py:72` to `src/orion/execution/position_manager.py:78`),
- execution supports both options and equities depending on `candidate.option_symbol` presence (`src/orion/execution/execution_engine.py:132` to `src/orion/execution/execution_engine.py:137`),
- `CandidateTrade.option_symbol` is nullable by model contract (`src/orion/storage/models_gold.py:33`).

Risk:
- options-flow-driven exit rules can be evaluated against equity positions, creating policy drift and potentially invalid exits for non-options trades.

### 70.2 Position Rehydration Caps Exit Monitoring to Latest 50 Open Decisions

Current startup query:
- `PositionManager.initialize` limits reconstructed open positions to 50 rows (`src/orion/execution/position_manager.py:80`),
- monitored set for exit evaluation is exactly `position_manager.get_open_positions()` (`src/orion/main_execution.py:322`).

Risk:
- when open executed positions exceed 50, older positions can be excluded from exit monitoring and remain unmanaged by rule-based close logic.

### 70.3 Updated Priorities

P0:
1. Enforce position-type gating for exit-rule families (apply options-flow rules only to option positions, or split policy sets by instrument type).
2. Remove fixed `LIMIT 50` from open-position reconstruction or replace with complete pagination to ensure full monitoring coverage.

P1:
1. Add integration tests covering mixed option/equity books and >50 open positions to verify correct rule applicability and monitoring completeness.

## 71) Pass 64 Continuation (2026-02-06)

### 71.1 Flow Premium Contract Drift Between Normalizer and Feature Aggregation

Current contract flow:
- `NormalizationEngine._normalize_uw_flow` writes flow premium to `premium_usd` (not `premium`) (`src/orion/processing/normalizer.py:106` to `src/orion/processing/normalizer.py:113`),
- `FeatureEngine.process_uw_flow` reads only `e.payload.get("premium")` when populating in-memory `flow_history` (`src/orion/processing/feature_engine.py:271`),
- rolling flow metrics (`call_premium_15m`, `put_premium_15m`, `flow_net_premium_15m`) sum this in-memory `premium` field (`src/orion/processing/feature_engine.py:374` to `src/orion/processing/feature_engine.py:380`).

Scope nuance:
- active ingestion cycle currently processes Alpaca bars only (`src/orion/ingestion/service.py:203` to `src/orion/ingestion/service.py:205`),
- however, the mismatch is live for UW-flow replay paths (`src/orion/jobs/dlq_consumer.py:167`) and is a latent parity break once UW flow is re-enabled in primary runtime.

Risk:
- normalized UW flow events carrying only `premium_usd` can be recorded as zero premium in in-memory aggregation, biasing flow-derived bar features and downstream decision/rule behavior.

### 71.2 Updated Priorities

P1:
1. Align `FeatureEngine.process_uw_flow` with canonical flow payload contract by reading `premium_usd` first (fallback to legacy `premium`).
2. Add contract test asserting normalized UW flow payloads contribute non-zero premium to `flow_history` and `flow_net_premium_15m`.

P2:
1. Define a single canonical premium-field accessor/helper for flow payloads to avoid repeated `premium` vs `premium_usd` drift across modules.

## 72) Pass 65 Continuation (2026-02-07)

### 72.1 Feature-Enrichment Ticker Universe Silently Degrades From Heber to Local SQL to Hardcoded Basket

Current behavior in `get_active_tickers`:
- attempts Heber flow-driven ticker discovery first (`src/orion/main_feature_enrichment.py:81` to `src/orion/main_feature_enrichment.py:86`),
- on any exception, silently falls back to Orion-local `silver_uw_flow` SQL (`src/orion/main_feature_enrichment.py:88` to `src/orion/main_feature_enrichment.py:107`),
- on SQL failure, silently falls back again to static hardcoded tickers (`SPY`, `QQQ`, `TSLA`, etc.) (`src/orion/main_feature_enrichment.py:109` to `src/orion/main_feature_enrichment.py:110`).

Scope/impact:
- this ticker set drives Greek exposure, max pain, and IV-rank enrichment fetch loops (`src/orion/main_feature_enrichment.py:269`, `src/orion/main_feature_enrichment.py:275`, `src/orion/main_feature_enrichment.py:281`),
- integration breaks in centralized Heber flow access can be masked by fallback behavior, while enrichment freshness narrows to local/stale/static universes.

Risk:
- feature coverage can drift away from actual active flow universe without a loud operational signal, reducing parity with centralized Data-Gateway/Heber contracts and weakening downstream model context quality.

### 72.2 Updated Priorities

P1:
1. Add explicit fallback-state telemetry (counter/alert) for each fallback tier (Heber -> local SQL -> static list), and promote to warning/error severity when static fallback is active.
2. Move ticker-universe ownership to one canonical source aligned with centralized architecture (Heber/Gateway-backed universe service) rather than local SQL + hardcoded symbols.

P2:
1. Add integration tests covering failure modes of Heber/local SQL paths and asserting deterministic fallback behavior plus alert emission.

## 73) Pass 66 Continuation (2026-02-07)

### 73.1 Feature-Enrichment Poll Cadence Advances Even on Empty/Failed Fetches

Current loop behavior:
- each enrichment branch updates its last-run timestamp unconditionally after `fetch_and_store` returns (`src/orion/main_feature_enrichment.py:262` to `src/orion/main_feature_enrichment.py:283`),
- connectors commonly signal “no data / failed fetch” by returning `0` instead of raising (for example `src/orion/connectors/uw_market_tide_connector.py:49` to `src/orion/connectors/uw_market_tide_connector.py:54`, `src/orion/connectors/uw_iv_rank_connector.py:49` to `src/orion/connectors/uw_iv_rank_connector.py:54`).

Risk:
- transient upstream failures can be treated as successful poll cycles, delaying retries until the next interval and masking sustained data starvation.

### 73.2 Regime Snapshot Uses Latest Stored Context Without Freshness Gates

Current snapshot inputs:
- `get_latest_vix_data()` and `get_latest_market_tide()` select latest rows with no max-age validation (`src/orion/main_feature_enrichment.py:116` to `src/orion/main_feature_enrichment.py:123`, `src/orion/main_feature_enrichment.py:145` to `src/orion/main_feature_enrichment.py:152`),
- regime detection consumes these values every snapshot cycle (`src/orion/main_feature_enrichment.py:297` to `src/orion/main_feature_enrichment.py:308`).

Risk:
- regime labels can be emitted using stale market context while appearing current, reducing trust in downstream policy/risk interpretation.

### 73.3 Updated Priorities

P1:
1. Gate `last_*` timestamp updates on successful ingestion (`count > 0`) or explicit “healthy empty” states, and emit warnings when zero rows persist across windows.
2. Add freshness checks (max age per source) for VIX/market-tide inputs before regime detection; mark snapshot degraded when inputs exceed SLA.

P2:
1. Add failure-mode tests that simulate repeated `fetch_and_store == 0` and assert retry cadence + degraded-state telemetry behavior.

## 74) Pass 67 Continuation (2026-02-07)

### 74.1 Max-Pain Distance Derivation Depends on Orion-Local Bar Table, Not Canonical Gateway/Heber Price Source

Current implementation:
- `UWMaxPainConnector` fetches max-pain payloads via Gateway but derives `current_price` from local `silver_alpaca_bars` (`src/orion/connectors/uw_max_pain_connector.py:56` to `src/orion/connectors/uw_max_pain_connector.py:63`, `src/orion/connectors/uw_max_pain_connector.py:93` to `src/orion/connectors/uw_max_pain_connector.py:101`),
- `distance_to_max_pain_pct` is computed from that local DB price (`src/orion/connectors/uw_max_pain_connector.py:72` to `src/orion/connectors/uw_max_pain_connector.py:75`).

Risk:
- if local bar ingestion is stale/disabled while centralized data remains healthy, stored max-pain distance features can be null or stale even when Gateway max-pain values are fresh.

### 74.2 Max-Pain Record Date Uses Host-Local `date.today()` Instead of Market-Date Semantics

Current implementation:
- connector stamps records with `today = date.today()` (`src/orion/connectors/uw_max_pain_connector.py:45`),
- this date participates in upsert identity `(ticker, expiry, date)` (`src/orion/connectors/uw_max_pain_connector.py:79`, `src/orion/connectors/uw_max_pain_connector.py:120`).

Risk:
- timezone/session boundary drift (for example UTC host past midnight before ET session rollover) can mis-bucket daily max-pain rows and produce duplicate/misaligned day-level records.

### 74.3 Updated Priorities

P1:
1. Source `current_price` from the same canonical data plane as max-pain pulls (Gateway/Heber market context), with explicit freshness requirements.
2. Replace `date.today()` with explicit market-date derivation (ET/session-aware) consistent with the rest of pipeline date semantics.

P2:
1. Add tests around session-boundary timestamps to ensure upsert keys remain stable across UTC/ET rollover windows.

## 75) Pass 68 Continuation (2026-02-07)

### 75.1 `is_sweep` Boolean Persistence Can Invert False Values After Normalization

Current contract path:
- normalizer stores `is_sweep` as string `"true"`/`"false"` in payload (`src/orion/processing/normalizer.py:86`),
- silver persistence coerces using `bool(p.get("is_sweep") or p.get("has_sweep"))` (`src/orion/processing/persistence.py:178`),
- target silver column is boolean (`src/orion/storage/models_silver.py:65`).

Risk mechanics:
- when payload contains `"false"` (string), `bool("false")` evaluates to `True`,
- non-sweep flow can therefore be persisted as sweep, corrupting sweep-dependent downstream analytics/rules.

Impact scope:
- affects normalized-ingestion and replay paths that persist `UW_FLOW` through `persist_silver_from_bronze`,
- can distort any logic keyed on sweep intensity or sweep ratio derived from `silver_uw_flow.is_sweep`.

### 75.2 Updated Priorities

P0:
1. Replace boolean coercion with explicit string-aware parsing (`"true"/"1"/"yes"` true; `"false"/"0"/"no"` false) before writing `silver_uw_flow.is_sweep`.
2. Add regression tests covering raw bool + normalized string inputs to prevent future coercion regressions.

P1:
1. Run one-time data quality check/backfill to identify suspicious sweep inflation windows after normalization rollout.

## 76) Pass 69 Continuation (2026-02-07)

### 76.1 UW Flow Normalizer Defaults Missing/Unknown `put_call` to Call (`"C"`)

Current normalization behavior:
- `put_call` is derived from `payload.put_call` or `payload.type`,
- if value is missing/unknown, normalizer falls back to `"C"` (`src/orion/processing/normalizer.py:64` to `src/orion/processing/normalizer.py:71`),
- silver schema requires non-null single-char `put_call` (`src/orion/storage/models_silver.py:49`).

Downstream dependency:
- multiple feature paths compute directional premium/flow using `put_call='C'` vs `'P'` splits (`src/orion/processing/feature_engine.py:270`, `src/orion/jobs/window_feature_job.py:97` to `src/orion/jobs/window_feature_job.py:99`).

Risk:
- malformed or side-missing flow rows are silently labeled bullish-call, biasing directional premium metrics instead of being quarantined or explicitly marked unknown.

### 76.2 Updated Priorities

P1:
1. Replace default-to-call fallback with explicit validation path:
- either drop/quarantine rows missing valid side,
- or map to explicit unknown state and exclude from directional aggregations.
2. Add normalization contract tests for invalid/missing `put_call` inputs to ensure they cannot silently become calls.

P2:
1. Add DQ monitor on unknown/invalid side-rate to surface upstream contract drift quickly.

## 77) Pass 70 Continuation (2026-02-07)

### 77.1 Flow Greeks Enrichment Truncates Coverage to First 100 Option Symbols Per Persist Batch

Current enrichment path:
- `persist_silver_from_bronze` runs `_enrich_flows_with_greeks(flow_rows)` before writing flow batch (`src/orion/processing/persistence.py:263` to `src/orion/processing/persistence.py:264`),
- `_enrich_flows_with_greeks` collects all option chains but calls `get_greeks_batch(symbols[:100])` (`src/orion/processing/persistence.py:118`, `src/orion/processing/persistence.py:123`),
- Alpaca batch connector itself also caps to first 100 symbols (`src/orion/connectors/alpaca_option_greeks_connector.py:159` to `src/orion/connectors/alpaca_option_greeks_connector.py:160`).

Risk:
- when a persist cycle has >100 option-chain rows, all rows after the first 100 are silently skipped for Greeks enrichment (`delta_alpaca`, `gamma_alpaca`, etc.), causing non-random feature sparsity tied to batch ordering.

Operational impact:
- downstream consumers can misinterpret missing Greeks as true data absence instead of enrichment truncation, reducing training/inference consistency.

### 77.2 Updated Priorities

P1:
1. Chunk symbols in `_enrich_flows_with_greeks` and merge results across all chunks (size <=100 each), rather than truncating the list.
2. Emit enrichment-coverage metrics (requested symbols vs enriched symbols) and alert when coverage drops below threshold.

P2:
1. Add regression test with >100 unique option chains asserting enrichment is applied to all rows, not only first page.

## 78) Pass 71 Continuation (2026-02-07)

### 78.1 Silver Flow/Darkpool Upsert Conflict Targets Do Not Match Declared Schema Constraints

Current persistence SQLAlchemy usage:
- flow writes use `ON CONFLICT DO NOTHING` on `["event_id", "flow_ts_utc"]` (`src/orion/processing/persistence.py:268`),
- darkpool writes use `ON CONFLICT DO NOTHING` on `["event_id", "dark_ts_utc"]` (`src/orion/processing/persistence.py:280`).

Declared schema contracts:
- ORM models define primary key only on `event_id` for both `silver_uw_flow` and `silver_uw_darkpool` (`src/orion/storage/models_silver.py:42`, `src/orion/storage/models_silver.py:112`),
- Alembic migration creates only `event_id` PK plus non-unique ticker/time indexes (`alembic/versions/0006_add_silver_ingest_envelope.py:29`, `alembic/versions/0006_add_silver_ingest_envelope.py:50`, `alembic/versions/0006_add_silver_ingest_envelope.py:61`, `alembic/versions/0006_add_silver_ingest_envelope.py:72`).

Risk:
- conflict targets that are not backed by matching unique/exclusion constraints can fail at runtime when flow/darkpool writes are exercised, causing batch write failures in replay or future re-enabled UW ingestion paths.

### 78.2 `is_sweep` Type Contract Drifts Between Alembic Schema and ORM/Persistence Expectations

Contract mismatch:
- Alembic creates `silver_uw_flow.is_sweep` as `String` (`alembic/versions/0006_add_silver_ingest_envelope.py:43`),
- ORM model defines it as `Boolean` (`src/orion/storage/models_silver.py:65`),
- persistence path currently writes boolean-coerced values (`src/orion/processing/persistence.py:178`).

Risk:
- schema/type drift increases migration uncertainty and can produce inconsistent query semantics across environments depending on which schema source was applied.

### 78.3 Updated Priorities

P0:
1. Align persistence conflict targets with real constraints (use `event_id` target, or add explicit unique constraints if composite keying is required).
2. Reconcile `is_sweep` type across Alembic + ORM + persistence and add migration to enforce one canonical type.

P1:
1. Add startup schema-contract checks that fail fast when runtime assumptions (conflict keys, column types) diverge from DB metadata.

## 79) Pass 72 Continuation (2026-02-07)

### 79.1 Labeler Uses Per-Flow, Per-Horizon Heber Bar Reads (N+1 Pattern) in Serial Loop

Current labeling path:
- each flow triggers four independent `get_price_at_time(...)` reads (15m/30m/1h/2h) (`src/orion/main_labeler.py:250` to `src/orion/main_labeler.py:253`),
- each read calls `_heber_reader.read_bars(...)` separately (`src/orion/main_labeler.py:153` to `src/orion/main_labeler.py:158`),
- batch loop processes flows one-by-one with `await label_flow(flow)` (`src/orion/main_labeler.py:364` to `src/orion/main_labeler.py:367`).

Risk:
- effective query count scales as `O(flows * horizons)` per poll cycle, creating avoidable lakehouse I/O pressure and slower backlog drain under elevated flow volumes.

Integration impact:
- centralized data architecture benefits from bulk/window reads, but current path repeatedly re-queries overlapping bar windows per ticker.

### 79.2 Updated Priorities

P1:
1. Replace per-horizon reads with batched per-ticker bar-window fetches reused across all horizons in the batch.
2. Parallelize label computation with bounded concurrency (ticker-aware) while preserving deterministic writes.

P2:
1. Add performance regression coverage for labeling throughput (flows/min) at representative backlog sizes.

## 80) Pass 73 Continuation (2026-02-07)

### 80.1 Silver Flow Persistence Validates Only `option_price` But Writes Multiple Non-Nullable Columns

Current write path:
- `persist_silver_from_bronze` skips `UW_FLOW` rows only when `option_price` is missing (`src/orion/processing/persistence.py:156` to `src/orion/processing/persistence.py:158`),
- same row still writes `put_call`, `expiry`, `strike`, `size_contracts`, `premium_usd` (`src/orion/processing/persistence.py:165` to `src/orion/processing/persistence.py:171`),
- target silver schema marks those fields non-nullable (`src/orion/storage/models_silver.py:49` to `src/orion/storage/models_silver.py:56`),
- normalizer can leave `expiry` unset when source payload omits it (`src/orion/processing/normalizer.py:78`).

Failure mode:
- one malformed flow row can trigger insert failure for the whole flow batch, since rows are inserted in bulk per batch (`src/orion/processing/persistence.py:265` to `src/orion/processing/persistence.py:269`).

### 80.2 Batch Failure Propagates to Full Ingestion Cycle

Current orchestration:
- `_run_cycle` executes `_persist_events(all_events)` before feature/rule processing (`src/orion/ingestion/service.py:211` to `src/orion/ingestion/service.py:213`),
- `_save_silver_data` re-raises silver write errors (`src/orion/ingestion/service.py:448` to `src/orion/ingestion/service.py:451`),
- cycle-level exception handler records crash and backs off (`src/orion/ingestion/service.py:167` to `src/orion/ingestion/service.py:171`).

Risk:
- single-row contract violations can repeatedly abort the cycle, starving downstream processing for otherwise valid events in the same batch.

### 80.3 Updated Priorities

P0:
1. Validate all non-null silver flow fields pre-insert and quarantine bad rows (DLQ with reason) instead of failing full batch.
2. Add per-row safe-write fallback for malformed rows so valid rows still persist and pipeline forward progress is maintained.

P1:
1. Add regression test with mixed valid/invalid `UW_FLOW` rows proving valid rows persist and invalid rows are isolated with explicit error telemetry.

## 81) Pass 74 Continuation (2026-02-07)

### 81.1 Labeler Timestamp Coercion Is Not Row-Isolated; One Bad Value Can Fail Whole Batch Build

Current parsing behavior:
- `_coerce_dt` calls `pd.Timestamp(value)` without local exception handling (`src/orion/main_labeler.py:55`),
- `_normalize_flow_df` invokes `_coerce_dt` inside row loop but does not catch parse errors per row (`src/orion/main_labeler.py:84` to `src/orion/main_labeler.py:88`),
- `get_unlabeled_flows` directly relies on `_normalize_flow_df` output (`src/orion/main_labeler.py:140` to `src/orion/main_labeler.py:145`).

Runtime impact:
- if one malformed timestamp appears in the fetched frame, normalization can raise before returning any rows for that cycle,
- loop-level handler logs generic labeling error and retries later (`src/orion/main_labeler.py:393` to `src/orion/main_labeler.py:396`), but problematic data can repeatedly stall progress.

Risk:
- backlog processing becomes brittle to single-record data quality defects; fresh valid rows may be delayed until offending records age out or data source is corrected.

### 81.2 Updated Priorities

P1:
1. Make timestamp parsing row-safe (catch parse errors per row, skip+log offending event IDs).
2. Add bad-row counters/telemetry for Heber input quality to surface recurring malformed timestamp sources.

P2:
1. Add regression test with mixed valid/invalid timestamp rows confirming valid rows still progress through labeling in same cycle.

## 82) Pass 75 Continuation (2026-02-07)

### 82.1 Labeler Checkpoint Price Reads Use `asof_time=now`, Allowing Historical Look-Ahead Leakage

Current behavior:
- unlabeled flow selection correctly uses `asof_time=now_utc` at batch start (`src/orion/main_labeler.py:140` to `src/orion/main_labeler.py:142`),
- each checkpoint price lookup calls `read_bars` with `asof_time=datetime.now(timezone.utc)` at lookup time (`src/orion/main_labeler.py:153` to `src/orion/main_labeler.py:156`),
- label returns (`return_15m/30m/1h/2h`) are then derived from those bars (`src/orion/main_labeler.py:256` to `src/orion/main_labeler.py:259`).

Risk:
- historical label generation can incorporate bars that became available after the intended decision horizon, violating strict as-of semantics and inflating backtest/train label fidelity.

Integration impact:
- this conflicts with centralized Heber contract intent around `ts_available`-aware, zero-leakage reads and weakens parity confidence between historical labeling and live-time information boundaries.

### 82.2 Updated Priorities

P1:
1. Use a deterministic as-of boundary for checkpoint reads (for example `asof_time=target_ts + tolerance`) rather than wall-clock now.
2. Add invariant checks that selected bars satisfy `ts_available <= chosen_asof_time` where available.

P2:
1. Add leakage regression tests that simulate late-arriving bars and assert labeler does not consume post-horizon data.

## 83) Pass 76 Continuation (2026-02-07)

### 83.1 Market-Tide Net-Premium Semantics Drift Across Modules

Current implementations use different formulas:
- `main_feature_enrichment.get_latest_market_tide` computes net as `net_call_premium - net_put_premium` (`src/orion/main_feature_enrichment.py:148` to `src/orion/main_feature_enrichment.py:149`),
- `main_price_target_labeler.get_market_tide_before_entry` computes net as `SUM(net_call_premium) + SUM(net_put_premium)` (`src/orion/main_price_target_labeler.py:498`, `src/orion/main_price_target_labeler.py:506`).

Risk:
- unless `net_put_premium` sign conventions are strictly guaranteed and documented, these paths can produce divergent market-tide direction labels from the same source table.

Impact:
- cross-service feature consistency degrades (regime context vs label enrichment), weakening parity and model interpretability.

### 83.2 Updated Priorities

P1:
1. Define one canonical market-tide net formula and encode it in a shared helper used by enrichment and labeler paths.
2. Add contract tests with controlled sample rows to assert consistent direction output across modules.

P2:
1. Backfill/validate recent windows for tide-direction divergence after formula standardization.

## 84) Pass 77 Continuation (2026-02-07)

### 84.1 Labeler Candidate Fetch Is Hard-Bounded to 72h History, Creating Permanent Label Gaps After Longer Downtime

Current bounds:
- labeler sets `FLOW_LOOKBACK_HOURS = 72` (`src/orion/main_labeler.py:32`),
- each poll reads flow from `start_time = cutoff - timedelta(hours=FLOW_LOOKBACK_HOURS)` (`src/orion/main_labeler.py:138`),
- only rows inside this moving window are considered for unlabeled processing (`src/orion/main_labeler.py:140` to `src/orion/main_labeler.py:145`).

Risk:
- if service downtime or backlog exceeds ~72h, older unlabeled flows fall outside scan window and can be permanently skipped by this runtime path.

Impact:
- label completeness can silently diverge from source history, reducing downstream training/evaluation parity and creating unrecoverable holes without separate backfill tooling.

### 84.2 Updated Priorities

P1:
1. Replace fixed lookback scanning with cursor/checkpoint pagination over unlabeled IDs/timestamps to guarantee eventual coverage regardless of downtime length.
2. Emit lag metrics for oldest-unlabeled age and alert when it exceeds expected SLA.

P2:
1. Add outage-recovery regression test simulating >72h gap and verify historical unlabeled rows are still processed.

## 85) Pass 78 Continuation (2026-02-07)

### 85.1 Labeler Silently Drops Flows With Missing/Invalid Underlying Entry Price

Current behavior:
- flow normalization coerces missing/invalid underlying price to `0.0` (`src/orion/main_labeler.py:93`),
- `label_flow` immediately returns `None` when `entry_price <= 0` (`src/orion/main_labeler.py:239` to `src/orion/main_labeler.py:242`),
- dropped rows are not surfaced with per-row reason telemetry.

Risk:
- label coverage can silently shrink when source flow rows miss `underlying_price` (or parse fails), biasing labeled dataset toward cleaner symbols/providers and masking data-quality regressions.

Integration implication:
- with centralized Heber/Gateway contracts, transient field sparsity can occur; silent drops reduce parity confidence unless fallback reconstruction is defined.

### 85.2 Updated Priorities

P1:
1. Add explicit drop-reason counters for label skips (`missing_underlying_price`, etc.) and include in batch logs/metrics.
2. Add fallback path to reconstruct entry underlying from bars near flow timestamp when flow payload lacks valid spot price.

P2:
1. Add regression tests with mixed valid/missing underlying prices to assert deterministic fallback or explicit counted drop behavior.

## 86) Pass 79 Continuation (2026-02-07)

### 86.1 Non-Flow Silver Writers Also Lack Full Required-Field Guards

Current persistence checks:
- bars path validates only `close` before append (`src/orion/processing/persistence.py:201` to `src/orion/processing/persistence.py:204`),
- darkpool path validates only `trade_price` (`src/orion/processing/persistence.py:220` to `src/orion/processing/persistence.py:223`),
- alert path has no explicit prevalidation before append (`src/orion/processing/persistence.py:238` to `src/orion/processing/persistence.py:257`).

Schema requirements:
- `silver_alpaca_bars` requires non-null `open/high/low/close/volume` (`src/orion/storage/models_silver.py:137` to `src/orion/storage/models_silver.py:141`),
- `silver_uw_darkpool` requires non-null `ticker/dark_ts_utc/trade_price/size_shares` (`src/orion/storage/models_silver.py:114` to `src/orion/storage/models_silver.py:119`),
- `silver_uw_alerts` requires non-null `ticker/alert_ts_utc` (`src/orion/storage/models_silver.py:157` to `src/orion/storage/models_silver.py:158`).

Risk:
- malformed rows in these feeds can still poison bulk inserts, causing avoidable batch failures and downstream starvation.

### 86.2 Updated Priorities

P0:
1. Apply uniform required-field validation + row quarantine across all silver event types (bars, darkpool, alerts, flow).
2. Ensure malformed rows are isolated with explicit DLQ reasons instead of aborting valid-row persistence.

P1:
1. Add mixed-validity regression tests per event type to prove valid rows persist when bad rows are present.

## 87) Pass 80 Continuation (2026-02-07)

### 87.1 Strict Timestamp Resolver Raises Pre-Insert and Is Not Row-Isolated

Current behavior:
- `_required_event_ts_utc` raises `ValueError` when both event timestamp and payload fallback are missing (`src/orion/processing/persistence.py:53` to `src/orion/processing/persistence.py:54`),
- it is called inline while building row dicts for flow/bar/darkpool/alert paths (`src/orion/processing/persistence.py:164`, `src/orion/processing/persistence.py:208`, `src/orion/processing/persistence.py:229`, `src/orion/processing/persistence.py:244`),
- no per-row `try/except` exists around those row-build operations.

Failure propagation:
- one malformed timestamp can abort `persist_silver_from_bronze` before batch insert loop executes,
- caller re-raises silver write errors (`src/orion/ingestion/service.py:448` to `src/orion/ingestion/service.py:451`), causing cycle-level failure handling.

Risk:
- single bad records can repeatedly block persistence and downstream processing for otherwise valid events in the same cycle.

### 87.2 Updated Priorities

P0:
1. Make timestamp parse failures row-local (skip/quarantine offending row with explicit reason and continue batch build).
2. Add malformed-timestamp counters by event type and alert on spikes.

P1:
1. Add regression tests with mixed valid/invalid timestamps for each event type to verify forward progress and explicit bad-row accounting.

## 88) Pass 81 Continuation (2026-02-07)

### 88.1 Bronze and Silver Persistence Are Split Across Separate Transactions

Current ingestion write order:
- `_persist_events` executes `_save_events_to_db(events)` then `_save_silver_data(events)` (`src/orion/ingestion/service.py:312` to `src/orion/ingestion/service.py:315`),
- each wrapper uses its own `db_write(...)` transaction (`src/orion/ingestion/service.py:443`, `src/orion/ingestion/service.py:453`).

Risk:
- bronze can commit successfully while silver fails, producing partial state for the same events.

### 88.2 Dedupe Uses Bronze Table Existence as Source of Truth

Current dedupe contract:
- deduper checks duplicates against `BronzeEvent.event_id` in DB (`src/orion/processing/deduper.py:29` to `src/orion/processing/deduper.py:31`, `src/orion/processing/deduper.py:63` to `src/orion/processing/deduper.py:65`),
- once bronze row exists, same event ID is filtered as duplicate on future ingest/replay path (`src/orion/processing/deduper.py:71` to `src/orion/processing/deduper.py:75`).

Failure implication:
- after bronze-success/silver-fail split, repeated delivery of same event ID is likely dropped at dedupe before normal silver processing path, creating persistent silver gaps unless explicitly repaired.

### 88.3 Updated Priorities

P0:
1. Persist bronze+silver atomically in one transaction (or introduce explicit recovery queue/state machine for bronze-committed/silver-pending events).
2. Add reconciliation job that detects bronze rows missing corresponding silver materialization and replays them through silver persistence safely.

P1:
1. Add integration test that simulates silver write failure after bronze commit and verifies automatic recovery path closes the gap without manual intervention.

## 89) Pass 82 Continuation (2026-02-07)

### 89.1 Event-Bus Publish Happens Before Bronze Commit and Uses Best-Effort Error Handling

Current write sequence in `_save_events_to_db`:
- each event is published to Redpanda first (`produce_event(...)`) (`src/orion/ingestion/service.py:424` to `src/orion/ingestion/service.py:431`),
- publish failures are logged and ignored (no retry/compensation) (`src/orion/ingestion/service.py:431` to `src/orion/ingestion/service.py:433`),
- bronze DB persistence happens afterward in a separate transactional call (`src/orion/ingestion/service.py:435` to `src/orion/ingestion/service.py:443`).

Risk modes:
- **phantom bus event**: publish succeeds, bronze commit later fails -> downstream consumers see event not durably recorded in bronze source-of-truth;
- **silent bus loss**: publish fails, bronze commit succeeds -> no replay/repair mechanism for missed bus fanout.

Operational impact:
- cross-sink consistency (stream vs DB) is not guaranteed, complicating replay, observability, and migration-parity validation.

### 89.2 Updated Priorities

P1:
1. Choose and enforce one ordering contract:
- transactional outbox (DB-first + async publish), or
- bus-first with durable publish-failure queue and reconciliation.
2. Add metrics for publish success/failure parity against bronze commit counts per run.

P2:
1. Add failure-injection tests for both modes (publish fail, bronze fail) and verify deterministic recovery/reconciliation behavior.

## 90) Pass 83 Continuation (2026-02-07)

### 90.1 DLQ Replay Duplicate-Path Swallows Normalization Errors and Proceeds With Raw Event

Current duplicate-bronze replay path:
- when dedupe returns empty (`already in bronze`), consumer attempts manual normalization (`src/orion/jobs/dlq_consumer.py:142` to `src/orion/jobs/dlq_consumer.py:149`),
- if normalization/temporal derivation fails, exception is swallowed (`except Exception: pass`) (`src/orion/jobs/dlq_consumer.py:156` to `src/orion/jobs/dlq_consumer.py:157`),
- consumer still forces `unique_events = [bronze]` and proceeds to silver persistence (`src/orion/jobs/dlq_consumer.py:158`, `src/orion/jobs/dlq_consumer.py:162`).

Risk:
- malformed duplicate events can repeatedly reach silver persistence in partially normalized shape, causing noisy replay failures without explicit root-cause telemetry.

Operational consequence:
- replay success/failure status can become opaque for duplicate-bronze cases, complicating incident recovery for poisoned events.

### 90.2 Updated Priorities

P1:
1. Replace `except Exception: pass` with explicit error classification and task failure reason update for replay observability.
2. Only proceed to downstream replay when normalization contract is satisfied; otherwise quarantine with structured reason.

P2:
1. Add duplicate-bronze replay regression tests covering normalization failure path and asserting deterministic quarantine (no silent fallthrough).

## 91) Pass 84 Continuation (2026-02-07)

### 91.1 Ingestion Runtime Never Hydrates `FeatureEngine` History Before Bar Processing

Current behavior:
- ingestion service constructs `FeatureEngine` (`src/orion/ingestion/service.py:53`) and processes bars via `process_alpaca_bars(...)` (`src/orion/ingestion/service.py:330`),
- `FeatureEngine` explicitly tracks hydration state and warns when processing occurs before hydration (`src/orion/processing/feature_engine.py:45`, `src/orion/processing/feature_engine.py:388` to `src/orion/processing/feature_engine.py:392`),
- ingestion initialization does not call `self.feature_engine.hydrate_history()` (`src/orion/ingestion/service.py:97` to `src/orion/ingestion/service.py:145`),
- parallel signal-engine path does hydrate explicitly (`src/orion/processing/signal_engine.py:44`).

Risk:
- after restarts/cold starts, indicator features can be computed with incomplete history in ingestion runtime, reducing early-cycle signal quality and creating path-to-path inconsistency with signal-engine semantics.

### 91.2 Updated Priorities

P1:
1. Hydrate `FeatureEngine` history during ingestion initialization (or load bounded rolling context per active ticker before first bar batch).
2. Add cold-start integration test asserting consistent indicator availability between ingestion and signal-engine runtimes.

P2:
1. Add startup telemetry for hydration completeness (tickers requested vs hydrated) and alert when below threshold.

## 92) Pass 85 Continuation (2026-02-07)

### 92.1 Feature Hydration Scope Uses Static Watchlist, Not Active Runtime Universe

Current behavior:
- `FeatureEngine.hydrate_history()` hydrates only `system_settings.static_watchlist` (`src/orion/processing/feature_engine.py:56`),
- ingestion/runtime bar processing uses dynamic active universe from `UniverseManager.get_active_universe()` (`src/orion/ingestion/service.py:224`),
- default static list is a fixed symbol set (`src/orion/config.py:96`).

Risk:
- tickers entering runtime universe outside the static watchlist can start with unhydrated indicator context, causing uneven cold-start feature quality across symbols.

### 92.2 Hydration Completion Flag Does Not Reflect Per-Ticker Coverage

Current state handling:
- `_hydrated` flips to true after watchlist loop completes (`src/orion/processing/feature_engine.py:64`),
- this flag is global and does not encode which tickers actually have sufficient history loaded.

Risk:
- runtime can treat feature engine as “hydrated” even when active-universe coverage is partial, masking cold-start indicator gaps.

### 92.3 Updated Priorities

P1:
1. Hydrate against active universe (or active-universe union static baseline), not static watchlist alone.
2. Track hydration readiness per ticker and gate indicator-dependent paths on ticker-level readiness.

P2:
1. Add regression test where active universe contains non-static ticker and assert hydration/readiness behavior is correct.

## 93) Pass 86 Continuation (2026-02-07)

### 93.1 `flow_count_15m` Mixes UW Flow and Darkpool Events in a Single Counter

Current aggregation path:
- `process_uw_flow` appends both `UW_FLOW` and `UW_DARKPOOL` into `flow_history` (`src/orion/processing/feature_engine.py:261` to `src/orion/processing/feature_engine.py:262`, `src/orion/processing/feature_engine.py:277` to `src/orion/processing/feature_engine.py:279`),
- `_compute_flow_features` filters premiums by `type == "UW_FLOW"` but sets `flow_count_15m = len(valid_events)` across all event types (`src/orion/processing/feature_engine.py:374` to `src/orion/processing/feature_engine.py:375`, `src/orion/processing/feature_engine.py:381`).

Risk:
- `flow_count_15m` can be inflated by darkpool prints, so feature semantics diverge from its implied “options flow count” meaning.

Downstream impact:
- drift monitoring consumes `flow_count_15m` directly (`src/orion/agents/eod_review_agent.py:652`), so distribution-shift alerts can be driven by darkpool mix changes rather than true flow-count changes.

### 93.2 Updated Priorities

P1:
1. Split counters by event family (`flow_count_15m`, `darkpool_count_15m`) or constrain `flow_count_15m` to `UW_FLOW` only.
2. Add feature-contract tests asserting count semantics by event type composition.

P2:
1. Rebaseline drift-monitor expectations once counter semantics are corrected.

## 94) Pass 87 Continuation (2026-02-07)

### 94.1 Dynamic Universe Lifecycle Methods Are Defined but Not Invoked

Current state:
- `UniverseManager` defines dynamic lifecycle controls (`update_from_event`, `update_from_positions`, `cleanup`) (`src/orion/core/universe_manager.py:99`, `src/orion/core/universe_manager.py:93`, `src/orion/core/universe_manager.py:159`),
- ingestion cycle reads active universe each loop (`src/orion/ingestion/service.py:224`) but does not call any of those lifecycle methods in the cycle path (`src/orion/ingestion/service.py:193` to `src/orion/ingestion/service.py:249`),
- repository-wide search shows no external call sites for these methods (outside `universe_manager.py`).

Risk:
- dynamic-universe behavior is effectively stale: event-driven promotions and TTL expiry logic are not active, so runtime symbol selection can diverge from intended PRD behavior.

### 94.2 Streaming Subscription Set Is Additive-Only and Not Pruned

Current behavior:
- stream drain path only subscribes newly added symbols (`src/orion/ingestion/service.py:238` to `src/orion/ingestion/service.py:240`),
- `AlpacaStreamConnector` has `unsubscribe()` support (`src/orion/connectors/alpaca_stream_connector.py:182`), but ingestion never calls it.

Risk:
- as active universe changes, stream subscription can retain stale symbols indefinitely, increasing unnecessary event load and allowing off-universe bars to continue through pipeline processing.

### 94.3 Updated Priorities

P1:
1. Wire universe lifecycle updates into ingestion loop (event-driven `update_from_event` where applicable and periodic `cleanup` execution).
2. Reconcile stream subscriptions each cycle: unsubscribe symbols not in current active universe and filter drained events against active set before processing.

P2:
1. Add integration test for universe churn (symbol enters/leaves active set) asserting subscription reconciliation and off-universe event suppression.

## 95) Pass 88 Continuation (2026-02-07)

### 95.1 SPY Cumulative-Return Query Uses Window Semantics That Do Not Match “Past 20 Bars”

Current implementation:
- `get_spy_cumulative_return()` computes:
  - `LAST_VALUE(close) OVER (ORDER BY bar_start_ts_utc)`
  - `FIRST_VALUE(close) OVER (ORDER BY bar_start_ts_utc)`
  then applies `ORDER BY ... DESC LIMIT 20` (`src/orion/main_feature_enrichment.py:170` to `src/orion/main_feature_enrichment.py:177`).

Why this is risky:
- window functions are evaluated before final `ORDER BY ... LIMIT`,
- default `LAST_VALUE` frame semantics are row-relative unless explicitly framed,
- result can represent cumulative behavior over broader history (or row-dependent values), not a strict “latest 20 bars” return.

Downstream impact:
- regime detector consumes this value as trend proxy (`src/orion/main_feature_enrichment.py:297`), so trend/risk regime classification can be biased by query-shape artifacts rather than intended recent-window return.

### 95.2 Updated Priorities

P1:
1. Rewrite cumulative-return logic to explicitly select last 20 bars first (CTE/subquery), then compute `(last_close - first_close) / first_close` on that bounded set.
2. Add unit/integration test with synthetic bar series verifying exact expected cumulative return for a fixed 20-bar window.

P2:
1. Add telemetry on computed trend proxy value distribution to catch future regressions in query semantics.

## 96) Pass 89 Continuation (2026-02-07)

### 96.1 Feature Enrichment Ticker Discovery Silently Degrades Away from Heber

Current behavior:
- `get_active_tickers()` attempts Heber flow-based discovery first (`src/orion/main_feature_enrichment.py:81` to `src/orion/main_feature_enrichment.py:87`),
- on Heber failure it logs only at debug and falls back to Orion-local `silver_uw_flow` SQL (`src/orion/main_feature_enrichment.py:89`, `src/orion/main_feature_enrichment.py:95`),
- if local query also fails it falls back again to hardcoded tickers (`src/orion/main_feature_enrichment.py:109` to `src/orion/main_feature_enrichment.py:110`).

Why this is risky in current architecture:
- ingestion runtime explicitly no longer polls UW directly (`src/orion/ingestion/service.py:200` to `src/orion/ingestion/service.py:201`), so reliance on Orion-local `silver_uw_flow` as fallback can be stale or empty relative to Heber-first truth,
- silent fallback chain can hide Heber integration regressions while enrichment continues with degraded symbol selection quality.

### 96.2 Updated Priorities

P1:
1. Promote Heber discovery failure to warning/error with explicit metric (e.g., `active_ticker_source=heber|local_db|static_fallback`).
2. Make fallback policy explicit and gated: only permit local/static fallback under configured degrade mode, otherwise fail fast.

P2:
1. Add regression tests for each fallback tier and assert alerting/telemetry behavior when Heber discovery path fails.

## 97) Pass 90 Continuation (2026-02-07)

### 97.1 Streaming Event Buffers Are Unbounded While Drain Is Batch-Limited

Current behavior:
- `AlpacaStreamConnector` and `GatewayStreamClient` both initialize default unbounded queues (`asyncio.Queue()` with no `maxsize`) (`src/orion/connectors/alpaca_stream_connector.py:60`, `src/orion/connectors/gateway_stream_client.py:67`),
- drain path consumes at most 1000 events per cycle by default (`src/orion/connectors/alpaca_stream_connector.py:251`, `src/orion/connectors/gateway_stream_client.py:424`),
- ingestion loop runs on a 60-second cycle (`src/orion/ingestion/service.py:161` to `src/orion/ingestion/service.py:163`) and drains stream events once per cycle (`src/orion/ingestion/service.py:229` to `src/orion/ingestion/service.py:230`).

Risk:
- during ingest slowdowns or bursty streams, backlog can grow without hard cap, increasing memory pressure and event-latency drift.

### 97.2 Queue Full Handling Is Effectively Inert with Current Queue Construction

Current path:
- gateway callback path catches `asyncio.QueueFull` on `put_nowait` (`src/orion/connectors/alpaca_stream_connector.py:102` to `src/orion/connectors/alpaca_stream_connector.py:104`),
- but queue has no max size, so `QueueFull` will not trigger under default implementation.

Risk:
- intended overload protection does not activate; system behavior under burst load is implicit/unbounded rather than explicit/drop-or-backpressure controlled.

### 97.3 Updated Priorities

P1:
1. Introduce bounded queue sizes and explicit overflow policy (drop oldest, drop newest, or backpressure) for both stream clients.
2. Replace fixed `max_events=1000` drain strategy with adaptive draining budget tied to queue depth/processing time budget.

P2:
1. Add load-test scenario asserting bounded memory and lag recovery under temporary ingestion stalls.

## 98) Pass 91 Continuation (2026-02-07)

### 98.1 UW Greek Exposure Connector Parses a Different Contract Than Gateway Spot-Exposure Endpoint

Current integration path:
- Orion connector calls `GET /api/v1/uw/{ticker}/spot-exposures` (`src/orion/connectors/uw_greek_exposure_connector.py:33`),
- Gateway routes that path to flow-analytics strike endpoint (`../Data-Gateway/gateway/api/uw/flow_analytics.py:21` to `../Data-Gateway/gateway/api/uw/flow_analytics.py:37`),
- provider normalization for that endpoint emits keys like `gamma_exposure`, `call_volume`, `put_volume`, `call_oi`, `put_oi` (`../Data-Gateway/gateway/providers/uw.py:2563` to `../Data-Gateway/gateway/providers/uw.py:2575`).

Mismatch in Orion parser:
- connector expects keys such as `call_gamma`, `put_gamma`, `call_vanna`, `put_vanna`, `call_charm`, `put_charm` (`src/orion/connectors/uw_greek_exposure_connector.py:57` to `src/orion/connectors/uw_greek_exposure_connector.py:88`),
- these fields are not part of the normalized strike response contract above, so parsed aggregate values can silently collapse to zeros/defaults.

Downstream impact:
- connector persists `gex_oi` / `vex_oi` / `cex_oi` into `silver_greek_exposure` (`src/orion/connectors/uw_greek_exposure_connector.py:93` to `src/orion/connectors/uw_greek_exposure_connector.py:120`),
- labeling/enrichment consumers read those values directly (`src/orion/main_price_target_labeler.py:478`, `src/orion/ml/flow_enricher.py:228`), so feature quality can degrade without explicit failure signals.

### 98.2 Updated Priorities

P1:
1. Align connector to a single canonical endpoint+schema (either parse Gateway’s strike `gamma_exposure` contract or switch to an endpoint that returns call/put gamma/vanna/charm fields).
2. Add strict response-contract validation with explicit error telemetry when expected keys are missing.

P2:
1. Add integration test with mocked Gateway payloads proving non-zero GEX/VEX extraction under the chosen canonical schema.

## 99) Pass 92 Continuation (2026-02-07)

### 99.1 Ingestion Heber Integration Is Declared in Code/Docs but Not Executed in Runtime Path

Current state:
- ingestion service constructs `HeberReader` and documents it as the UW source (`src/orion/ingestion/service.py:58` to `src/orion/ingestion/service.py:60`),
- cycle comments claim UW flow/darkpool comes from Heber (`src/orion/ingestion/service.py:200` to `src/orion/ingestion/service.py:201`),
- module comments still direct use of `self.heber.read_flow()` / `read_darkpool()` (`src/orion/ingestion/service.py:275` to `src/orion/ingestion/service.py:276`),
- but ingestion entrypoint/runtime currently only polls Alpaca in-cycle (`src/orion/ingestion/service.py:203` to `src/orion/ingestion/service.py:205`), and no `self.heber.*` calls exist in service code.

Additional drift signal:
- entrypoint docstring states ingestion “Reads flow/darkpool from Heber” (`src/orion/ingestion/__main__.py:8`) despite missing active runtime call path.

Risk:
- integration status is ambiguous for operators and future maintainers; dead wiring + stale docs can mask that live UW-driven signal generation is absent in this runtime.

### 99.2 Updated Priorities

P1:
1. Either wire explicit Heber read/merge path into ingestion cycle or remove dead `HeberReader` wiring and correct runtime docs/comments to match actual behavior.
2. Add startup capability log/health field that explicitly reports enabled data sources (Alpaca-only vs Alpaca+Heber flow).

P2:
1. Add regression check that fails when documented ingestion sources diverge from active runtime source wiring.

## 100) Pass 93 Continuation (2026-02-07)

### 100.1 IV-Rank Connector Field Mapping Lags Gateway Normalized Contract

Current path:
- Orion fetches `GET /api/v1/uw/{symbol}/iv-rank` and parses payload into `silver_iv_rank` records (`src/orion/connectors/uw_iv_rank_connector.py:48` to `src/orion/connectors/uw_iv_rank_connector.py:73`),
- Gateway provider normalizes IV-rank response with fields including `iv_rank`, `iv_percentile`, `current_iv`, `one_year_high`, `one_year_low` (`../Data-Gateway/gateway/providers/uw.py:1546` to `../Data-Gateway/gateway/providers/uw.py:1560`).

Mismatch:
- Orion connector maps `iv_52w_high` from `iv_high` and `iv_52w_low` from `iv_low` (`src/orion/connectors/uw_iv_rank_connector.py:70` to `src/orion/connectors/uw_iv_rank_connector.py:71`),
- those key names are not present in the current Gateway normalized object, so 52-week high/low fields can silently default to zero.

Risk:
- persisted IV feature completeness degrades silently (especially `iv_52w_high`/`iv_52w_low`, and `iv_30d` when absent),
- downstream consumers treating these columns as populated may operate on defaulted values rather than real IV-context features.

### 100.2 Updated Priorities

P1:
1. Align connector mapping to Gateway normalized keys (`one_year_high`, `one_year_low`) with backward-compatible aliases as needed.
2. Enforce key-presence validation and emit warning/error metrics when expected IV context fields are missing.

P2:
1. Add integration tests with canonical Gateway `iv-rank` payload verifying non-default persistence of 52-week high/low fields.

## 101) Pass 94 Continuation (2026-02-07)

### 101.1 Max-Pain Connector Expects Raw Key While Gateway Returns Normalized Key

Current integration:
- Orion connector reads `max_pain = exp_data.get("max_pain")` and skips row when `max_pain is None` (`src/orion/connectors/uw_max_pain_connector.py:61` to `src/orion/connectors/uw_max_pain_connector.py:65`),
- Gateway provider normalizes max-pain payload into `max_pain_strike` field (`../Data-Gateway/gateway/providers/uw.py:1492` to `../Data-Gateway/gateway/providers/uw.py:1494`).

Mismatch effect:
- when Gateway returns normalized objects (common path), `max_pain` key is absent and connector drops otherwise valid rows before persistence.

Downstream impact:
- `silver_max_pain` can remain sparse/stale (`src/orion/connectors/uw_max_pain_connector.py:115`),
- feature consumers pulling `distance_to_max_pain_pct` from this table can operate with missing data (`src/orion/main_price_target_labeler.py:522`, `src/orion/ml/flow_enricher.py:315`).

### 101.2 Updated Priorities

P1:
1. Update connector mapping to accept `max_pain_strike` first (with `max_pain` as backward-compatible alias).
2. Emit explicit metric/log when payload rows are skipped due to missing strike key.

P2:
1. Add contract test using Gateway normalized max-pain payload to verify non-zero row persistence into `silver_max_pain`.

## 102) Pass 95 Continuation (2026-02-07)

### 102.1 Daily Earnings Sync Persists Batch Date Instead of Record-Level Report Date

Current implementation:
- `sync_todays_earnings()` computes `today = date.today()` and passes it through batch calls (`src/orion/jobs/sync_earnings.py:29`, `src/orion/jobs/sync_earnings.py:32` to `src/orion/jobs/sync_earnings.py:33`),
- per-row sync path writes each record via `_upsert_earnings(e, today, announce_time)` (`src/orion/jobs/sync_earnings.py:50`),
- `_upsert_earnings()` forwards that `report_date` directly into storage (`src/orion/jobs/sync_earnings.py:179` to `src/orion/jobs/sync_earnings.py:181`).

Risk:
- if API payload includes record-level dates that differ from local batch date (timezone boundaries, schedule shifts, upstream response semantics), `silver_earnings_calendar` can be written with incorrect `report_date`.

Contrast within same module:
- historical backfill path explicitly parses record-level `e.report_date` before upsert (`src/orion/jobs/sync_earnings.py:80` to `src/orion/jobs/sync_earnings.py:91`),
- daily sync path does not mirror this per-record date handling.

### 102.2 Updated Priorities

P1:
1. In daily sync path, derive `report_date` from each earnings record (with controlled fallback to batch date only when missing).
2. Add telemetry counting rows where fallback date was used.

P2:
1. Add regression test with mixed record dates to ensure per-record persistence keys `(ticker, report_date)` are correct.

## 103) Pass 96 Continuation (2026-02-07)

### 103.1 Earnings Backfill Universe Is Seeded Only from Local Label Table

Current behavior:
- `backfill_all_earnings()` selects symbols exclusively from `price_target_labels` (`src/orion/jobs/sync_earnings.py:147`),
- no union with active universe, watchlist, positions, or centralized catalog-driven symbol sources is applied in this backfill path.

Risk:
- earnings coverage depends on prior local labeling history instead of current runtime/centralized symbol scope,
- newly relevant symbols without prior label presence can be omitted from backfill, producing uneven earnings-feature availability.

Context signal:
- this module otherwise fetches earnings through Data Gateway (`src/orion/jobs/sync_earnings.py:140` to `src/orion/jobs/sync_earnings.py:143`), but symbol-seeding remains local-table coupled.

### 103.2 Updated Priorities

P1:
1. Build backfill symbol universe from canonical active sources (e.g., current runtime universe + configured watchlist + optional label history union) instead of `price_target_labels` alone.
2. Add coverage metrics showing symbol-source composition for each backfill run.

P2:
1. Add regression test confirming symbols outside local label history are still included when present in canonical universe inputs.

## 104) Pass 97 Continuation (2026-02-07)

### 104.1 “Today” Earnings Sync Does Not Pass Date Filter to Gateway Fetch Calls

Current path:
- `sync_todays_earnings()` defines `today = date.today()` and describes syncing “today’s” premarket/afterhours earnings (`src/orion/jobs/sync_earnings.py:21` to `src/orion/jobs/sync_earnings.py:33`),
- `_fetch_and_sync_earnings()` invokes SDK fetch functions without a `date` argument (`src/orion/jobs/sync_earnings.py:46`).

Why this is risky:
- UW/Gateway earnings endpoints support optional `date` and default to current/last market day behavior when omitted,
- runtime “today sync” semantics become dependent on upstream defaults rather than explicit date targeting.

Operational consequence:
- on weekends/holidays or date-boundary conditions, fetched set can drift from intended run date while downstream persistence logic still treats the batch as the current sync cycle.

### 104.2 Updated Priorities

P1:
1. Pass explicit `date=today.isoformat()` in daily premarket/afterhours fetch calls.
2. Log requested date and returned record-date distribution for each sync run.

P2:
1. Add regression test asserting date argument propagation in `_fetch_and_sync_earnings` path.

## 105) Pass 98 Continuation (2026-02-07)

### 105.1 Earnings Backfill Uses Single-Page Fetch Against Paginated Gateway Endpoint

Current behavior:
- backfill loop calls `get_ticker_earnings.sync(ticker=..., client=...)` once per symbol (`src/orion/jobs/sync_earnings.py:68`),
- Gateway ticker-earnings endpoint is paginated with `limit` (default 50) (`../Data-Gateway/gateway/api/uw/earnings.py:64`, `../Data-Gateway/gateway/api/uw/earnings.py:77`),
- Orion backfill path does not iterate pages/cursors.

Risk:
- historical earnings coverage per ticker can be truncated to first page window,
- long-history symbols may have incomplete earnings rows in `silver_earnings_calendar`.

Downstream implication:
- derived earnings features (`days_to_earnings`, `is_post_earnings`) can be based on incomplete history set, reducing reliability of date-relative feature logic.

### 105.2 Updated Priorities

P1:
1. Implement pagination loop for ticker-earnings backfill (consume all pages/cursors from Gateway response).
2. Add per-ticker completeness telemetry (rows fetched, pages consumed, oldest/newest report dates).

P2:
1. Add regression test with multi-page mocked earnings response ensuring full-history ingestion for a ticker.

## 106) Pass 99 Continuation (2026-02-07)

### 106.1 Daily Earnings Sync Uses Batch-Level `announce_time` Instead of Record-Level Timing Fields

Current behavior:
- daily sync invokes `_fetch_and_sync_earnings(..., announce_time=\"premarket\"|\"afterhours\", ...)` (`src/orion/jobs/sync_earnings.py:32` to `src/orion/jobs/sync_earnings.py:33`),
- each row is persisted via `_upsert_earnings(e, today, announce_time)` using that batch label (`src/orion/jobs/sync_earnings.py:50`),
- in contrast, historical backfill path extracts per-record timing (`report_time`) when available (`src/orion/jobs/sync_earnings.py:87`, `src/orion/jobs/sync_earnings.py:113` to `src/orion/jobs/sync_earnings.py:121`).

Risk:
- daily path can flatten record-level timing nuance to endpoint-level labels,
- inconsistent timing semantics between daily sync and historical backfill can introduce drift in `silver_earnings_calendar.announce_time`.

### 106.2 Updated Priorities

P1:
1. Harmonize daily and backfill logic: prefer record-level timing field extraction, with endpoint label as fallback only when missing.
2. Add telemetry on fallback usage rate for `announce_time`.

P2:
1. Add regression test with mixed `report_time` availability to verify consistent announce-time persistence rules across daily and backfill paths.

## 107) Pass 100 Continuation (2026-02-07)

### 107.1 Earnings Backfill Error Accounting Masks Per-Ticker and Per-Record Failures

Current behavior:
- `backfill_ticker_earnings()` catches fetch errors, logs at debug, and returns `count` (often `0`) instead of surfacing failure (`src/orion/jobs/sync_earnings.py:67` to `src/orion/jobs/sync_earnings.py:74`),
- `_process_single_earnings_record()` catches parse/upsert errors per row, logs at debug, and returns `0` (`src/orion/jobs/sync_earnings.py:84` to `src/orion/jobs/sync_earnings.py:101`),
- `backfill_all_earnings()` increments `results["errors"]` only when outer ticker loop catches exceptions (`src/orion/jobs/sync_earnings.py:154` to `src/orion/jobs/sync_earnings.py:166`), but inner catches prevent most exceptions from bubbling.

Risk:
- run-level success metrics can overstate data quality (`errors` stays low while rows are dropped/skipped),
- Gateway/contract regressions can hide behind normal-looking completion logs with reduced earnings coverage.

### 107.2 Updated Priorities

P1:
1. Replace silent `count=0` failure path with structured ticker result accounting (`fetched`, `inserted`, `skipped`, `errors`) and aggregate it in `backfill_all_earnings()`.
2. Promote row-level parse/upsert exceptions to counted error metrics (with bounded logging) so data-loss modes are visible.

P2:
1. Add regression tests asserting that simulated fetch and row-parse failures increment `results["errors"]` and produce deterministic failure counters.

## 108) Pass 101 Continuation (2026-02-07)

### 108.1 Daily Earnings Sync Imports Non-Exported UW Endpoint Symbols

Current behavior:
- `sync_todays_earnings()` imports `get_afterhours_earnings` and `get_premarket_earnings` from `orion.unusualwhales.api.earnings` (`src/orion/jobs/sync_earnings.py:23`),
- the earnings package exports module names `get_afterhours`, `get_premarket`, and `get_ticker_earnings` (plus `EarningsEndpoints` helpers), not `*_earnings` aliases (`src/orion/unusualwhales/api/earnings/__init__.py:5`, `src/orion/unusualwhales/api/earnings/__init__.py:8` to `src/orion/unusualwhales/api/earnings/__init__.py:42`),
- this mismatch raises `ImportError` before any daily fetch call is attempted.

Runtime consequence:
- ingestion startup wraps `sync_todays_earnings()` in broad exception handling and logs warning on failure (`src/orion/ingestion/service.py:115` to `src/orion/ingestion/service.py:121`),
- service continues running, but earnings calendar sync is skipped.

Risk:
- earnings synchronization can be effectively disabled while runtime appears healthy,
- downstream earnings-dependent features remain stale/incomplete without explicit hard failure.

### 108.2 Updated Priorities

P1:
1. Replace imports with canonical module names (`get_premarket`, `get_afterhours`) and invoke `.sync` on those modules.
2. Promote startup earnings-sync failure to explicit health-degraded state/metric (not warning-only best effort).

P2:
1. Add import-path smoke test and a mocked startup integration test asserting earnings sync executes successfully with expected function bindings.

## 109) Pass 102 Continuation (2026-02-07)

### 109.1 HeberReader Time/As-Of Filters Are Applied Post-Load (Not as Parquet Predicates)

Current behavior:
- `_read_silver_dataset()` builds parquet filters only for `instrument_key` when provided (`src/orion/clients/heber_reader.py:210` to `src/orion/clients/heber_reader.py:214`),
- `start_time`/`end_time` and `asof_time` are applied afterward in pandas (`src/orion/clients/heber_reader.py:218` to `src/orion/clients/heber_reader.py:221`, `src/orion/clients/heber_reader.py:245` to `src/orion/clients/heber_reader.py:277`),
- one hot path (`get_active_tickers`) calls `read_flow(...)` without symbol filters (`src/orion/main_feature_enrichment.py:81` to `src/orion/main_feature_enrichment.py:84`), so this path can read broad feed data before trimming.

Risk:
- as Heber Silver grows, active-ticker discovery can degrade toward repeated full-feed scans,
- latency/memory pressure in feature enrichment can increase and trigger fallback behavior that masks root-cause read-path inefficiency.

### 109.2 Updated Priorities

P1:
1. Add predicate pushdown for time bounds (and partition-aware pruning where available) inside `_read_silver_dataset()` instead of post-load filtering only.
2. Bound active-ticker discovery reads with explicit lookback partition filters and row caps before dataframe materialization.

P2:
1. Add performance regression test/benchmark for `read_flow` on large partition sets to enforce bounded read latency and memory footprint.

## 110) Pass 103 Continuation (2026-02-07)

### 110.1 Daily Earnings Sync Uses Attribute Names Not Present on Current `Earnings` Model

Current behavior:
- daily upsert path reads `eps_estimate`, `eps_actual`, `revenue_estimate`, `revenue_actual` directly via `getattr(...)` on each earnings object (`src/orion/jobs/sync_earnings.py:183` to `src/orion/jobs/sync_earnings.py:186`),
- current generated `Earnings` model defines fields such as `eps_mean_est` and `street_mean_est` (plus other legacy fields), not those normalized names (`src/orion/unusualwhales/models/earnings.py:57`, `src/orion/unusualwhales/models/earnings.py:68`),
- backfill path already uses an alternate extraction (`street_mean_est`) via `_extract_eps_estimate(...)` (`src/orion/jobs/sync_earnings.py:124` to `src/orion/jobs/sync_earnings.py:131`).

Cross-service contract signal:
- Gateway normalized earnings responses use `eps_estimate`, `eps_actual`, `revenue_estimate`, `revenue_actual` (`../Data-Gateway/gateway/providers/uw.py:872` to `../Data-Gateway/gateway/providers/uw.py:887`),
- without explicit mapping from model/additional-properties to persisted fields, daily sync can miss available fundamentals.

Risk:
- `silver_earnings_calendar` can receive incomplete EPS/revenue data on daily sync runs,
- semantics drift remains between daily sync and historical backfill enrichment quality.

### 110.2 Updated Priorities

P1:
1. Implement one canonical earnings-field extractor shared by daily and backfill paths that resolves both generated-model fields and normalized Gateway keys.
2. Add counters for field-presence rates (`eps_estimate`, `eps_actual`, `revenue_estimate`, `revenue_actual`) per run to detect silent mapping regressions.

P2:
1. Add regression tests with mocked earnings payloads covering both model-native (`street_mean_est`) and normalized (`eps_estimate`) shapes to verify persisted parity.

## 111) Pass 104 Continuation (2026-02-07)

### 111.1 HeberReader Health Path Depends on Relative URL Escapes

Current behavior:
- `HeberReader.health_check()` tries `"/health"` first, then `"../../health"` as fallback (`src/orion/clients/heber_reader.py:69`),
- default Orion config sets `heber_catalog_url` to `http://localhost:8085/api/v1` (`src/orion/config.py:81` to `src/orion/config.py:83`),
- Heber catalog exposes health at `/health` and datasets at `/api/v1/datasets` (`../Heber/heber/catalog/api.py:129`, `../Heber/heber/catalog/api.py:135`).

Observed URL-shape coupling:
- with base URL ending in `/api/v1`, request `"/health"` resolves to `/api/v1/health` (not canonical),
- fallback `"../../health"` is relied upon to escape path prefix and hit root health endpoint.

Risk:
- behavior depends on subtle path-join semantics and exact base URL shape,
- configuration drift (for example changing base URL to root vs `/api/v1`) can make one path family work while another breaks, increasing diagnosis time.

### 111.2 Updated Priorities

P1:
1. Replace relative traversal fallback with explicit canonical endpoint composition (derive root URL once, then call `/health` and `/api/v1/datasets` deterministically).
2. Add startup validation that asserts `heber_catalog_url` contract and logs a single actionable misconfiguration error.

P2:
1. Add unit tests for both supported base URL shapes (`.../api/v1` and root host) to verify deterministic health and dataset endpoint resolution.

## 112) Pass 105 Continuation (2026-02-07)

### 112.1 Earnings Proximity Logic Ignores `announce_time` and Uses Date-Only Boundaries

Current behavior:
- `get_earnings_for_ticker(ticker, as_of_date)` accepts only a `date` (no time-of-day context) (`src/orion/jobs/sync_earnings.py:247`),
- next/last earnings lookups filter solely on `report_date` comparisons (`>= :as_of` / `< :as_of`) (`src/orion/jobs/sync_earnings.py:263` to `src/orion/jobs/sync_earnings.py:277`),
- `announce_time` is selected in both queries but not used in output logic (`src/orion/jobs/sync_earnings.py:263`, `src/orion/jobs/sync_earnings.py:274`, `src/orion/jobs/sync_earnings.py:289` to `src/orion/jobs/sync_earnings.py:297`).

Risk:
- same-day premarket and afterhours earnings scenarios can collapse to the same `days_to_earnings` / `is_post_earnings` values,
- intraday feature semantics can drift from intended event-timing behavior despite storing announce-time metadata.

### 112.2 Updated Priorities

P1:
1. Add announce-time-aware proximity logic (at minimum for same-day boundaries) using entry timestamp + `announce_time` classification.
2. Keep a deterministic fallback when `announce_time` is missing and emit fallback-rate metrics.

P2:
1. Add regression tests for same-day premarket vs afterhours scenarios to ensure distinct post/pre earnings classification outcomes.

## 113) Pass 106 Continuation (2026-02-07)

### 113.1 Ingestion Can Quietly Bypass Gateway Streaming and Revert to Direct Alpaca Polling

Current behavior:
- ingestion initializes `AlpacaStreamConnector` when streaming is enabled (`src/orion/ingestion/service.py:74` to `src/orion/ingestion/service.py:82`),
- Gateway mode is default in connector (`src/orion/connectors/alpaca_stream_connector.py:44`, `src/orion/connectors/alpaca_stream_connector.py:66` to `src/orion/connectors/alpaca_stream_connector.py:67`),
- Gateway stream client creation hard-fails when `DATA_GATEWAY_API_KEY` is missing (`src/orion/connectors/gateway_stream_client.py:449` to `src/orion/connectors/gateway_stream_client.py:454`),
- ingestion startup catches stream startup errors and falls back to polling (`src/orion/ingestion/service.py:133` to `src/orion/ingestion/service.py:143`),
- cycle path then uses polling fallback whenever stream is absent/not running (`src/orion/ingestion/service.py:229` to `src/orion/ingestion/service.py:233`).

Risk:
- runtime can silently run outside intended centralized Gateway stream path,
- source parity can drift (Gateway multiplexer/auth/contracts bypassed) while process appears healthy.

### 113.2 Updated Priorities

P1:
1. In Gateway-enabled mode, treat missing Gateway key/startup failure as explicit degraded-state (or fail fast by policy), not warning-only fallback.
2. Emit runtime source telemetry (`stream_source=gateway|direct_polling`) and alert when fallback persists.

P2:
1. Add startup integration test for Gateway-mode + missing-key scenario asserting deterministic degraded/fail-fast behavior.

## 114) Pass 107 Continuation (2026-02-07)

### 114.1 Backfill Announce-Time Extraction Targets `report_time`, Not Gateway-Normalized `time`

Current behavior:
- ticker-earnings backfill derives announce timing via `_extract_announce_time(...)` (`src/orion/jobs/sync_earnings.py:86`, `src/orion/jobs/sync_earnings.py:113`),
- extractor checks `additional_properties["report_time"]` and `e.report_time` only (`src/orion/jobs/sync_earnings.py:116` to `src/orion/jobs/sync_earnings.py:121`),
- Gateway normalized ticker-earnings shape sets timing under `time` (or `timing` upstream alias) (`../Data-Gateway/gateway/providers/uw.py:983`).

Risk:
- when Orion consumes Gateway-normalized ticker earnings, announce-time can be dropped during backfill despite upstream availability,
- `silver_earnings_calendar.announce_time` completeness drifts and undermines downstream timing-aware feature logic.

### 114.2 Updated Priorities

P1:
1. Extend `_extract_announce_time(...)` to read normalized timing keys (`time`, `timing`) in addition to legacy `report_time`.
2. Add run-level timing-field coverage metrics (records with timing present upstream vs persisted) to detect silent mapping regressions.

P2:
1. Add regression tests with mocked ticker-earnings payloads covering `report_time` and `time` shapes to enforce announce-time parity.

## 115) Pass 108 Continuation (2026-02-07)

### 115.1 Options Execution Quote Lookup Bypasses Gateway and Calls Alpaca Directly

Current behavior:
- execution options path fetches live quote via `self.options_connector.get_option_quote(...)` before sizing (`src/orion/execution/execution_engine.py:198` to `src/orion/execution/execution_engine.py:209`),
- `AlpacaOptionsConnector.get_option_quote()` issues direct HTTP request to `https://data.alpaca.markets/v1beta1/options/snapshots` with Alpaca keys (`src/orion/connectors/alpaca_options_connector.py:194` to `src/orion/connectors/alpaca_options_connector.py:199`),
- Gateway already exposes canonical option quote endpoint `GET /api/v1/alpaca/options/{contract}/quotes` with centralized auth/contracts (`../Data-Gateway/gateway/api/alpaca/options.py:157` to `../Data-Gateway/gateway/api/alpaca/options.py:171`).

Risk:
- options execution runs outside centralized Gateway control plane (auth policy, rate limits, contract normalization),
- parity and observability drift can emerge between execution-time quotes and other Orion/Heber paths that route through Gateway.

### 115.2 Updated Priorities

P1:
1. Route options execution quote discovery through Gateway endpoint(s) rather than direct Alpaca HTTP snapshots.
2. Add explicit degraded/fallback telemetry if direct-provider fallback is retained for break-glass operation.

P2:
1. Add integration test covering execution quote lookup against Gateway contract to verify schema compatibility with sizing logic.

## 116) Pass 109 Continuation (2026-02-07)

### 116.1 Earnings Backfill Expects `report_date` but Gateway-Normalized Records Provide `date`

Current behavior:
- backfill record processor requires `e.report_date` and skips row when missing/UNSET (`src/orion/jobs/sync_earnings.py:80` to `src/orion/jobs/sync_earnings.py:82`),
- generated `Earnings` model only maps `report_date` as a first-class field; unknown keys remain in `additional_properties` (`src/orion/unusualwhales/models/earnings.py:65`, `src/orion/unusualwhales/models/earnings.py:186` to `src/orion/unusualwhales/models/earnings.py:194`),
- Gateway normalized ticker earnings emit `date` as canonical date key (`../Data-Gateway/gateway/providers/uw.py:982`).

Risk:
- when ticker earnings payloads follow Gateway normalized schema, valid rows can be skipped in Orion backfill,
- `silver_earnings_calendar` historical completeness can degrade without explicit failure signals.

### 116.2 Updated Priorities

P1:
1. Add normalized date extraction fallback (`date`, `earnings_date`) in backfill processor when `report_date` is absent.
2. Track row-skip reasons (missing date key, parse failure, upsert failure) in run metrics to surface coverage regressions.

P2:
1. Add regression tests for ticker-earnings shapes carrying `report_date` vs `date` to guarantee consistent ingestion.

## 117) Pass 110 Continuation (2026-02-07)

### 117.1 Dataset Discovery Endpoint Depends on `HEBER_CATALOG_URL` Shape

Current behavior:
- `HeberReader.list_datasets()` calls `self.client.get("/datasets")` (`src/orion/clients/heber_reader.py:87`),
- default Orion config sets `heber_catalog_url` to `http://localhost:8085/api/v1` (`src/orion/config.py:81` to `src/orion/config.py:83`),
- Heber catalog route is exposed as `/api/v1/datasets` (`../Heber/heber/catalog/api.py:135`).

Risk:
- when `HEBER_CATALOG_URL` is configured as root host (for example `http://localhost:8085`), list-datasets requests can 404 while health checks still appear healthy,
- metadata/discovery readiness can diverge by environment despite equivalent endpoint intent.

### 117.2 Updated Priorities

P1:
1. Construct catalog dataset routes deterministically (explicit `/api/v1/datasets`) independent of base URL shape.
2. Add startup URL-contract validation that rejects incompatible `HEBER_CATALOG_URL` values with actionable config guidance.

P2:
1. Add unit tests for root and `/api/v1` base URL variants ensuring `list_datasets()` resolves to the same canonical endpoint.

## 118) Pass 111 Continuation (2026-02-07)

### 118.1 Direct Alpaca Polling Fallback Uses Single Request with Fixed Row Cap

Current behavior:
- polling connector builds `StockBarsRequest(..., limit=10000, ...)` (`src/orion/connectors/alpaca_market_connector.py:51` to `src/orion/connectors/alpaca_market_connector.py:57`),
- fetch path performs one `get_stock_bars(req)` call and iterates returned data without page-token loop (`src/orion/connectors/alpaca_market_connector.py:63` to `src/orion/connectors/alpaca_market_connector.py:76`),
- ingestion falls back to this polling path whenever stream mode is unavailable (`src/orion/ingestion/service.py:232` to `src/orion/ingestion/service.py:233`).

Risk:
- high-cardinality fallback windows (many symbols / broad lookback overlap) can exceed fixed request cap and drop bars silently,
- bronze/silver completeness during fallback periods can diverge from intended stream parity.

### 118.2 Updated Priorities

P1:
1. Implement paginated bar retrieval in `AlpacaMarketConnector.fetch_bars()` (consume all pages within bounded window/timeout policy).
2. Emit fallback completeness metrics (requested symbols, returned bars, pagination depth, truncation indicators) for operational visibility.

P2:
1. Add integration test simulating >10k fallback bar response to ensure pagination drains full result set.

## 119) Pass 112 Continuation (2026-02-07)

### 119.1 Gold Reads Are Unscoped by Project/Version in Orion HeberReader

Current behavior:
- Orion `read_gold_features(dataset, asof_time, symbols)` loads from `gold/dataset={dataset}` only (`src/orion/clients/heber_reader.py:183`),
- no `project` or `version` constraint is accepted/applied in this read path (`src/orion/clients/heber_reader.py:176` to `src/orion/clients/heber_reader.py:196`),
- Heber gold write contract partitions data by `dataset`, `project`, and `version` (`../Heber/heber/sdk/client.py:449` to `../Heber/heber/sdk/client.py:454`).

Risk:
- reads can blend multiple project/version partitions for the same dataset,
- model-training/evaluation reproducibility and parity checks can degrade when feature rows are not version-scoped.

### 119.2 Updated Priorities

P1:
1. Extend Orion gold-read facade to require (or explicitly default) `project` and `version` selectors in path/filter logic.
2. Emit read-scope telemetry (dataset/project/version cardinality seen per query) to catch accidental cross-version mixing.

P2:
1. Add tests with multi-project/multi-version fixture partitions ensuring read path returns only the requested scope.

## 120) Pass 113 Continuation (2026-02-07)

### 120.1 As-Of Filter Becomes No-Op When `ts_available` Is Missing

Current behavior:
- `read_gold_features(...)` always applies `_apply_asof_filter(...)` after parquet load (`src/orion/clients/heber_reader.py:192` to `src/orion/clients/heber_reader.py:196`),
- `_apply_asof_filter(...)` returns rows unchanged when `ts_available` is absent (`src/orion/clients/heber_reader.py:245` to `src/orion/clients/heber_reader.py:247`),
- this path does not emit warnings/errors when as-of semantics are bypassed.

Risk:
- datasets that drift from Heber column contracts can silently lose point-in-time guarantees,
- downstream consumers may assume anti-leakage behavior is enforced when it is not.

### 120.2 Updated Priorities

P1:
1. Enforce fail-closed behavior for as-of reads when `ts_available` is missing (or require explicit override flag with warning telemetry).
2. Add schema/column-contract validation before read returns for datasets expected to be point-in-time safe.

P2:
1. Add regression tests asserting as-of reads fail or explicitly flag degraded mode when `ts_available` is absent.

## 121) Pass 114 Continuation (2026-02-07)

### 121.1 Gateway Stream Client Does Not Explicitly Close/Reset Socket on Failed Handshake

Current behavior:
- `connect()` establishes websocket, sends auth, and inspects first response (`src/orion/connectors/gateway_stream_client.py:86` to `src/orion/connectors/gateway_stream_client.py:110`),
- on auth failure (`status != "ok"`), method returns `False` without explicit `await self._websocket.close()` / handle reset (`src/orion/connectors/gateway_stream_client.py:114` to `src/orion/connectors/gateway_stream_client.py:117`),
- exception path also returns `False` without deterministic socket cleanup (`src/orion/connectors/gateway_stream_client.py:119` to `src/orion/connectors/gateway_stream_client.py:121`).

Gateway side behavior:
- server returns auth error payload and then closes unauthenticated connections (`../Data-Gateway/gateway/api/websocket.py:190` to `../Data-Gateway/gateway/api/websocket.py:200`, `../Data-Gateway/gateway/api/websocket.py:50` to `../Data-Gateway/gateway/api/websocket.py:52`),
- but client-side lifecycle still relies on remote close timing rather than explicit local cleanup.

Risk:
- reconnect/failure loops can carry stale websocket handles/state longer than intended,
- troubleshooting auth/key issues becomes noisier when connection-state transitions are implicit.

### 121.2 Updated Priorities

P1:
1. On any failed handshake path, explicitly close websocket (if open), clear `_websocket`, and reset `_authenticated` before returning.
2. Add structured metrics/log fields for handshake outcome categories (auth_failed, timeout, transport_error) to improve diagnostics.

P2:
1. Add unit/integration tests simulating auth-fail and timeout paths to assert deterministic client-side cleanup and stable reconnect behavior.

## 122) Pass 115 Continuation (2026-02-07)

### 122.1 WebSocket URL Builder Does Not Normalize API-Prefixed Gateway Base URLs

Current behavior:
- stream client constructs websocket URL by appending `"/ws"` to configured `gateway_url` (`src/orion/connectors/gateway_stream_client.py:48` to `src/orion/connectors/gateway_stream_client.py:55`),
- Gateway websocket endpoint is mounted at root path `/ws` (`../Data-Gateway/gateway/api/websocket.py:28`),
- if `DATA_GATEWAY_URL` is set with API prefix (for example `http://host:8080/api/v1`), computed websocket URL becomes `ws://host:8080/api/v1/ws`, which does not match router contract.

Risk:
- environment-specific base URL settings can break streaming even when HTTP endpoints appear reachable,
- Orion can fall back to polling mode, reducing parity with intended centralized Gateway stream path.

### 122.2 Updated Priorities

P1:
1. Normalize Gateway base URL before WS composition (strip API path prefixes, then append `/ws` deterministically).
2. Add startup validation that rejects/rewrites API-prefixed `DATA_GATEWAY_URL` values for stream mode.

P2:
1. Add unit tests for gateway URL variants (`host`, `host/api/v1`, `ws://host`) to assert stable `ws_url` derivation.

## 123) Pass 116 Continuation (2026-02-07)

### 123.1 Earnings Sync Uses UW SDK Contract That Does Not Match Gateway Auth/Route Shape

Current behavior:
- `sync_earnings` constructs `UnusualWhalesClient(base_url=f"{gateway_url}/api/v1/uw", token="gateway")` (`src/orion/jobs/sync_earnings.py:27`, `src/orion/jobs/sync_earnings.py:142`),
- UW SDK client defaults to `Authorization: Bearer <token>` rather than Gateway `X-Gateway-Key` (`src/orion/unusualwhales/client.py:56`, `src/orion/unusualwhales/client.py:98`),
- earnings SDK endpoints call `/api/earnings/*` paths (`src/orion/unusualwhales/api/earnings/get_premarket.py:26`, `src/orion/unusualwhales/api/earnings/get_ticker_earnings.py:18`),
- Gateway requires `X-Gateway-Key` (`../Data-Gateway/gateway/api/deps.py:103` to `../Data-Gateway/gateway/api/deps.py:115`) and exposes earnings routes as `/api/v1/uw/earnings/*` (`../Data-Gateway/gateway/api/uw/earnings.py:21` to `../Data-Gateway/gateway/api/uw/earnings.py:62`).

Risk:
- earnings sync/backfill can fail against Gateway due to both header-contract and path-contract mismatch (`/api/v1/uw/api/earnings/*`),
- failure visibility is reduced because ticker-level backfill failures are debug-logged and continue (`src/orion/jobs/sync_earnings.py:72` to `src/orion/jobs/sync_earnings.py:74`), which can leave `silver_earnings_calendar` stale without strong alerts.

### 123.2 Updated Priorities

P1:
1. Replace `sync_earnings` UW SDK calls with a Gateway-native client path that uses canonical routes (`/api/v1/uw/earnings/*`) and `X-Gateway-Key` auth.
2. Add fail-fast observability for earnings sync completeness (expected tickers/requested endpoints vs successful upserts), and elevate repeated fetch failures to warning/error with actionable context.

P2:
1. Add contract tests that verify earnings sync request headers/path shapes against Gateway (premarket, afterhours, historical ticker earnings).
2. Add integration test coverage for API-prefixed and root Gateway base URL variants to prevent future route-composition regressions.

## 124) Pass 117 Continuation (2026-02-07)

### 124.1 UW Feature-Enrichment Connectors Re-Append `/api/v1` Without Base URL Canonicalization

Current behavior:
- feature loop injects raw `system_settings.data_gateway_url` into UW connectors (`src/orion/main_feature_enrichment.py:237` to `src/orion/main_feature_enrichment.py:243`),
- each connector hardcodes endpoint composition as `"{gateway_url}/api/v1/uw/..."` (`src/orion/connectors/uw_market_tide_connector.py:33`, `src/orion/connectors/uw_greek_exposure_connector.py:33`, `src/orion/connectors/uw_max_pain_connector.py:33`, `src/orion/connectors/uw_iv_rank_connector.py:33`),
- Gateway UW router is already mounted at `/api/v1/uw` (`../Data-Gateway/gateway/api/uw/__init__.py:32`), so API-prefixed base URLs produce doubled paths (for example `/api/v1/api/v1/uw/...`),
- fetch failures are converted to `None` and loop continues (`src/orion/connectors/uw_market_tide_connector.py:42` to `src/orion/connectors/uw_market_tide_connector.py:44`, `src/orion/connectors/uw_greek_exposure_connector.py:38` to `src/orion/connectors/uw_greek_exposure_connector.py:40`, `src/orion/connectors/uw_max_pain_connector.py:38` to `src/orion/connectors/uw_max_pain_connector.py:40`, `src/orion/connectors/uw_iv_rank_connector.py:38` to `src/orion/connectors/uw_iv_rank_connector.py:40`).

Risk:
- environment-dependent URL shape can break all four enrichment feeds (market tide, exposure, max pain, IV rank) simultaneously,
- because connector failures degrade to empty results, enrichment can appear “healthy” while writing zero/partial updates, delaying detection of stale feature tables.

### 124.2 Updated Priorities

P1:
1. Centralize Gateway HTTP base URL normalization (single helper shared across UW connectors and jobs) so `/api/v1` is appended exactly once.
2. Add fail-fast degraded-mode signaling when repeated connector fetches return zero records across configured tickers/intervals.

P2:
1. Add unit tests for URL construction across root and API-prefixed `DATA_GATEWAY_URL` values for all UW enrichment connectors.
2. Emit per-feed freshness telemetry (last successful fetch timestamp + rows written) to support parity SLO checks against Heber datasets.

## 125) Pass 118 Continuation (2026-02-07)

### 125.1 HeberReader Filter Fallback Drops Symbol Constraints and Reads Full Dataset

Current behavior:
- Silver reads rely on parquet filter pushdown for instrument scoping (`instrument_key in [...]`) (`src/orion/clients/heber_reader.py:210` to `src/orion/clients/heber_reader.py:214`),
- if filtered `pq.read_table(...)` raises, `_read_parquet(...)` logs `heber_reader_filter_fallback` and retries without filters (`src/orion/clients/heber_reader.py:285` to `src/orion/clients/heber_reader.py:296`),
- caller does not re-apply instrument filter after fallback (`src/orion/clients/heber_reader.py:214` to `src/orion/clients/heber_reader.py:223`),
- `main_labeler.get_price_at_time(...)` assumes symbol-scoped bars from `read_bars(...)` and does not enforce ticker filtering post-read (`src/orion/main_labeler.py:153` to `src/orion/main_labeler.py:182`).

Risk:
- schema/partition drift that breaks filter pushdown can silently convert targeted reads into full-table scans,
- correctness can degrade (wrong-symbol bar chosen for label pricing) and runtime cost can spike due to unbounded dataset loads.

### 125.2 Updated Priorities

P1:
1. Change filter-fallback behavior to fail-closed for symbol-scoped reads (or explicitly re-apply `instrument_key` filter in-memory before returning).
2. Emit elevated alerts when filter pushdown fails (dataset/feed, filter keys, row counts before/after fallback) and block downstream label writes on unresolved scope mismatch.

P2:
1. Add regression tests that simulate missing/renamed filter columns and assert no cross-symbol rows can escape from `read_bars(...)`/`read_flow(...)`.
2. Add bounded-read safeguards (max rows per scoped read + explicit override) to prevent accidental full-dataset loads in live services.

## 126) Pass 119 Continuation (2026-02-07)

### 126.1 Regime Trend Input Uses Incorrect SQL Window Semantics for “20-Bar Cumulative Return”

Current behavior:
- `get_spy_cumulative_return()` computes trend input with `LAST_VALUE(close) OVER (...)` and `FIRST_VALUE(close) OVER (...)` (`src/orion/main_feature_enrichment.py:171` to `src/orion/main_feature_enrichment.py:173`),
- query applies `ORDER BY bar_start_ts_utc DESC LIMIT 20` after window expression (`src/orion/main_feature_enrichment.py:176` to `src/orion/main_feature_enrichment.py:177`) and then reads a single row (`src/orion/main_feature_enrichment.py:181`),
- that value is fed directly into regime detection every cycle (`src/orion/main_feature_enrichment.py:299` to `src/orion/main_feature_enrichment.py:304`).

Inference and risk:
- with default SQL window frames, `LAST_VALUE(...)` is row-frame sensitive (not automatically “last of final 20-bar slice”); combined with post-window `LIMIT 20` + single-row fetch, this can produce a value that is not the intended recent-20-bar return,
- regime classification may drift because trend input is derived from inconsistent historical scope, weakening downstream feature and policy decisions.

### 126.2 Updated Priorities

P1:
1. Replace the query with an explicit two-point calculation on a deterministic 20-bar slice (for example: CTE selecting last 20 SPY bars, then `latest_close` vs `oldest_close`).
2. Add validation guardrails that log/alert when insufficient bars are available for the intended lookback window.

P2:
1. Add unit/integration tests for `get_spy_cumulative_return()` covering monotonic-up, monotonic-down, and flat synthetic bar windows.
2. Add telemetry comparing computed `cum_ret` against a reference in-Python implementation to detect SQL regressions.

## 127) Pass 120 Continuation (2026-02-07)

### 127.1 Feature-Enrichment Loop Treats Connector Failures and “No Data” as the Same Outcome

Current behavior:
- loop records connector output only as `stored {count}` and advances schedule timers regardless of result (`src/orion/main_feature_enrichment.py:263` to `src/orion/main_feature_enrichment.py:283`),
- UW connector fetch paths convert request failures into empty returns (`src/orion/connectors/uw_market_tide_connector.py:49` to `src/orion/connectors/uw_market_tide_connector.py:54`, `src/orion/connectors/uw_greek_exposure_connector.py:48` to `src/orion/connectors/uw_greek_exposure_connector.py:54`, `src/orion/connectors/uw_max_pain_connector.py:48` to `src/orion/connectors/uw_max_pain_connector.py:54`, `src/orion/connectors/uw_iv_rank_connector.py:48` to `src/orion/connectors/uw_iv_rank_connector.py:54`),
- upstream request exceptions are logged in connector scope but the loop-level signal remains an informational zero-count message.

Risk:
- persistent Gateway contract/auth outages can be mistaken for legitimate low-activity periods,
- stale enrichment tables can continue without explicit degraded-state alarms, reducing trust in downstream models/features.

### 127.2 Updated Priorities

P1:
1. Distinguish connector result states (`success_with_rows`, `success_empty`, `request_failed`, `parse_failed`) and propagate them to loop-level logs/metrics.
2. Trigger degraded-mode alerts when consecutive failure states exceed threshold per feed.

P2:
1. Add tests that force HTTP/auth failures and verify loop emits failure-classified telemetry rather than generic `stored 0`.
2. Add per-feed freshness SLO checks tied to last successful write timestamp, not loop heartbeat alone.

## 128) Pass 121 Continuation (2026-02-07)

### 128.1 `price_target_labeler` Metadata Features Depend on Direct UW API Credentials Not Wired in Runtime

Current behavior:
- compose service `price_target_labeler` sets `GATEWAY_URL` but does not provide `UW_API_KEY` (`docker-compose.yml:61` to `docker-compose.yml:74`),
- labeler metadata fetch path builds direct `UnusualWhalesClient` from `UW_API_KEY` / `UW_BASE_URL` (`src/orion/main_price_target_labeler.py:1624` to `src/orion/main_price_target_labeler.py:1629`),
- when key is missing, client initialization returns `None` and ticker info falls back to empty cache values (`src/orion/main_price_target_labeler.py:1666` to `src/orion/main_price_target_labeler.py:1668`),
- those values feed sector/earnings feature columns during label assembly (`src/orion/main_price_target_labeler.py:1772` to `src/orion/main_price_target_labeler.py:1798`, `src/orion/main_price_target_labeler.py:2648` to `src/orion/main_price_target_labeler.py:2654`).

Risk:
- production label generation can silently emit incomplete sector/earnings feature coverage for non-static-mapped tickers,
- centralization objective is weakened because this path bypasses Gateway/Heber contracts and depends on undeclared direct-provider credentials.

### 128.2 Updated Priorities

P1:
1. Replace direct UW client usage in `main_price_target_labeler` with Gateway/Heber-backed metadata source (or a local canonical cache populated from centralized ingestion).
2. Add explicit feature-completeness telemetry for `sector`, `days_to_earnings`, and `is_post_earnings` with alert thresholds.

P2:
1. Add startup config validation that fails fast when selected metadata mode requires unavailable credentials.
2. Add regression tests for label generation on unmapped tickers to assert deterministic fallback behavior and completeness accounting.

## 129) Pass 122 Continuation (2026-02-07)

### 129.1 `backfill_ml_features` Recomputes and Overwrites `entry_session` with a Different Taxonomy Than Live Labeling

Current behavior:
- live label generation uses session buckets `OPEN/MID/CLOSE` (`src/orion/main_price_target_labeler.py:671` to `src/orion/main_price_target_labeler.py:681`),
- backfill job defines different buckets (`early/midday/afternoon/late`) in its own `get_entry_time_features` (`src/orion/jobs/backfill_ml_features.py:122` to `src/orion/jobs/backfill_ml_features.py:134`),
- any row selected for missing non-time features (for example `oi_change_1d IS NULL`) is still updated with backfill time features (`src/orion/jobs/backfill_ml_features.py:288` to `src/orion/jobs/backfill_ml_features.py:290`, `src/orion/jobs/backfill_ml_features.py:309` to `src/orion/jobs/backfill_ml_features.py:310`),
- update query writes all recomputed fields back to `price_target_labels` (`src/orion/jobs/backfill_ml_features.py:461` to `src/orion/jobs/backfill_ml_features.py:465`).

Risk:
- identical records can receive different `entry_session` values depending on whether/when backfill touched them,
- training and analysis consistency degrades because the same feature column mixes incompatible ontologies over time.

### 129.2 Updated Priorities

P1:
1. Centralize time-feature derivation in one shared helper used by live labeler and backfill jobs.
2. Prevent backfill from overwriting already-populated time features unless explicitly running a controlled re-derivation migration.

P2:
1. Add regression tests asserting labeler and backfill produce identical `entry_session` for the same timestamps.
2. Add data-quality checks to flag mixed session vocabularies in `price_target_labels`.

## 130) Pass 123 Continuation (2026-02-07)

### 130.1 Backfill Record Selection Lacks Deterministic Ordering/Cursor and Can Recycle Failing Rows

Current behavior:
- `get_records_to_backfill()` uses `LIMIT :limit` without `ORDER BY` or cursor fields (`src/orion/jobs/backfill_ml_features.py:283` to `src/orion/jobs/backfill_ml_features.py:291`),
- main loop repeatedly fetches the same shape query each cycle (`src/orion/jobs/backfill_ml_features.py:480` to `src/orion/jobs/backfill_ml_features.py:483`),
- per-record failures are logged but not quarantined/marked (`src/orion/jobs/backfill_ml_features.py:489` to `src/orion/jobs/backfill_ml_features.py:490`),
- loop termination is tied to processed-attempt count limit, not successful advancement through the backlog (`src/orion/jobs/backfill_ml_features.py:494` to `src/orion/jobs/backfill_ml_features.py:503`).

Risk:
- problematic rows can be retried repeatedly while other eligible rows remain untouched,
- nightly runs can report processing activity while making limited forward progress on true feature-completion coverage.

### 130.2 Updated Priorities

P1:
1. Add deterministic ordering + pagination cursor (for example `ORDER BY entry_ts, event_id`) so each run advances predictably through backlog.
2. Introduce failure quarantine metadata (retry count / last_error / next_retry_at) to avoid hot-looping the same broken rows.

P2:
1. Add completion metrics that separate `attempted`, `updated`, and `newly_completed` records per run.
2. Add regression tests with one synthetic failing row to assert surrounding rows still progress.

## 131) Pass 124 Continuation (2026-02-07)

### 131.1 `main_price_target_labeler` Shadows Shared `orion.labeler` Logic with Divergent Local Implementations

Current behavior:
- file imports shared constants/helpers from `orion.labeler` (`src/orion/main_price_target_labeler.py:22` to `src/orion/main_price_target_labeler.py:34`),
- same symbol names are redefined locally in the same module (`src/orion/main_price_target_labeler.py:40`, `src/orion/main_price_target_labeler.py:224`, `src/orion/main_price_target_labeler.py:252`, `src/orion/main_price_target_labeler.py:274`, `src/orion/main_price_target_labeler.py:1831`, `src/orion/main_price_target_labeler.py:1845`, `src/orion/main_price_target_labeler.py:1859`, `src/orion/main_price_target_labeler.py:1873`, `src/orion/main_price_target_labeler.py:1888`),
- runtime calls resolve to local redefinitions (for example Greeks/checkpoint usage at `src/orion/main_price_target_labeler.py:855` to `src/orion/main_price_target_labeler.py:856`, `src/orion/main_price_target_labeler.py:2314` to `src/orion/main_price_target_labeler.py:2355`),
- local behavior diverges from shared `orion/labeler` package:
  - checkpoint tolerances differ (`5m`/`4h`/`30m` windows locally vs tighter shared windows in `src/orion/labeler/checkpoints.py:45` to `src/orion/labeler/checkpoints.py:100`),
  - volatility calc uses `np.std` locally vs `statistics.stdev` in shared (`src/orion/main_price_target_labeler.py:1873` to `src/orion/main_price_target_labeler.py:1882`, `src/orion/labeler/checkpoints.py:121` to `src/orion/labeler/checkpoints.py:139`),
  - sector map contents drift (example: `TXN`/`LOW`/`NXPI` present in shared constants, absent in local map; `BMNR` present only locally) (`src/orion/labeler/constants.py:58`, `src/orion/labeler/constants.py:66`, `src/orion/labeler/constants.py:94`, `src/orion/main_price_target_labeler.py:185`).

Risk:
- consolidation effort into shared labeler modules is effectively bypassed for production label generation,
- feature outputs can drift between components that rely on shared helpers versus this local shadowed copy, reducing reproducibility and parity with Heber migration work.

### 131.2 Updated Priorities

P1:
1. Remove shadowed local definitions in `main_price_target_labeler` and consume shared `orion.labeler` functions/constants as single source of truth.
2. Add startup self-check that asserts no local symbol shadowing for imported labeler helpers/constants.

P2:
1. Add regression tests that compare `main_price_target_labeler` outputs against direct `orion.labeler` helper outputs on fixed fixtures.
2. Add a sector-map parity check test to detect drift between shared constants and runtime labeler mapping.

## 132) Pass 125 Continuation (2026-02-07)

### 132.1 `main_option_quote_tracker` Still Depends on Local `silver_uw_flow` and Truncates Candidate Coverage

Current behavior:
- option quote tracker candidate discovery reads only local DB table `silver_uw_flow` (`src/orion/main_option_quote_tracker.py:78`),
- selection is hard-capped at the most recent 1000 rows from the last 24h (`src/orion/main_option_quote_tracker.py:82` to `src/orion/main_option_quote_tracker.py:83`),
- service has no Heber/Gateway reader path or centralized config usage (`src/orion/main_option_quote_tracker.py:1` to `src/orion/main_option_quote_tracker.py:260`),
- runtime still launches this tracker as part of compose stack (`docker-compose.yml:92` to `docker-compose.yml:106`),
- meanwhile main labeler flow/bars ingestion is already Heber-backed (`src/orion/main_labeler.py:21`, `src/orion/main_labeler.py:140`, `src/orion/main_labeler.py:153`).

Risk:
- centralized ingestion migration can leave quote tracker with missing/empty candidate flow universe when local `silver_uw_flow` is stale or no longer authoritative,
- high-volume days can silently skip quote tracking beyond the newest 1000 events, creating checkpoint-label coverage bias.

### 132.2 Updated Priorities

P1:
1. Migrate candidate-event discovery in `main_option_quote_tracker` to centralized Heber/Gateway flow reads (same source contract as `main_labeler`).
2. Replace fixed `LIMIT 1000` selection with deterministic pagination/watermark progression over the full eligible window.

P2:
1. Add checkpoint coverage telemetry: eligible events vs quoted events per checkpoint and per polling cycle.
2. Add regression/integration tests with >1000 synthetic flow events to assert complete progression rather than newest-only truncation.

## 133) Pass 126 Continuation (2026-02-07)

### 133.1 `gold_feature_windows` Producer Is Not Wired into Runtime, While Consumers Remain Active

Current behavior:
- `gold_feature_windows` write path exists only in `WindowFeatureJob` (`src/orion/jobs/window_feature_job.py:189` to `src/orion/jobs/window_feature_job.py:209`),
- repository references show `WindowFeatureJob` usage only in its own module/`__main__` entrypoint (`src/orion/jobs/window_feature_job.py:32`, `src/orion/jobs/window_feature_job.py:241`),
- ingestion runtime starts `RollupJob`, not `WindowFeatureJob` (`src/orion/ingestion/service.py:125` to `src/orion/ingestion/service.py:128`),
- compose does not declare a `window_feature_job` service (`docker-compose.yml`),
- downstream readers still query `gold_feature_windows` in scoring/training paths (`src/orion/ml/flow_enricher.py:1031` to `src/orion/ml/flow_enricher.py:1036`, `src/orion/ml/exit_classifier.py:444` to `src/orion/ml/exit_classifier.py:456`).

Risk:
- window-level context features can be stale or absent in production without explicit failure signal,
- model feature completeness and parity checks can silently degrade because consumers continue operating against outdated `gold_feature_windows` state.

### 133.2 Updated Priorities

P1:
1. Decide and enforce one producer path for `gold_feature_windows`: wire `WindowFeatureJob` into runtime orchestration or migrate generation into Heber Gold and switch consumers.
2. Add mandatory freshness checks (`max(window_end_ts_utc)` age threshold) before consumers trust window features.

P2:
1. Add startup/runtime telemetry that reports producer status for `gold_feature_windows` (enabled source + last successful build).
2. Add integration tests that fail when consumers run with stale/missing window features beyond allowed SLO.

## 134) Pass 127 Continuation (2026-02-07)

### 134.1 Zero-Flow Windows Are Not Materialized, So Consumers Can Reuse Stale Historical Context

Current behavior:
- window aggregation returns `None` when a ticker-period window has zero flow rows (`src/orion/jobs/window_feature_job.py:133` to `src/orion/jobs/window_feature_job.py:135`),
- when `None`, persistence is skipped for that window (`src/orion/jobs/window_feature_job.py:67` to `src/orion/jobs/window_feature_job.py:72`),
- scoring/training consumers query latest row `<= entry_ts` per period (`src/orion/ml/flow_enricher.py:1031` to `src/orion/ml/flow_enricher.py:1036`, `src/orion/ml/exit_classifier.py:444` to `src/orion/ml/exit_classifier.py:460`),
- no max-age freshness condition is applied in those lookups.

Risk:
- during quiet regimes (or ingestion gaps), models can consume old window context as if current, rather than explicit zero-activity context,
- feature semantics become time-skewed: “latest known” is treated as “current state,” which can bias signals and training labels.

### 134.2 Updated Priorities

P1:
1. Materialize explicit zero-activity rows per ticker/period window (for example `flow_count=0`, `total_premium=0`, `sweep_ratio=0`) instead of skipping persistence.
2. Add freshness gates in consumers so rows older than a period-specific threshold are treated as missing/degraded.

P2:
1. Add regression tests for no-flow windows that assert consumers receive explicit zero features (or controlled nulls), not arbitrarily old historical rows.
2. Add telemetry comparing window end-time age against entry-time to detect stale carry-forward usage.

## 135) Pass 128 Continuation (2026-02-07)

### 135.1 Window Feature Coverage Is Constrained to Static Watchlist, Not Runtime Active Universe

Current behavior:
- `WindowFeatureJob` defaults ticker scope to `system_settings.static_watchlist` (`src/orion/jobs/window_feature_job.py:49`),
- job loops only over that fixed list (`src/orion/jobs/window_feature_job.py:58`),
- flow enrichment reads window features using the event ticker (dynamic by incoming flow universe) (`src/orion/ml/flow_enricher.py:1017`, `src/orion/ml/flow_enricher.py:1032`),
- ingestion runtime already maintains a broader active universe lifecycle outside this static list (`src/orion/ingestion/service.py`, `src/orion/core/universe_manager.py`).

Risk:
- non-watchlist tickers can have valid flow events but never receive `gold_feature_windows` context features,
- model feature completeness drifts by ticker cohort (watchlist vs non-watchlist), creating silent bias and parity gaps against centralized Gateway/Heber universes.

### 135.2 Updated Priorities

P1:
1. Source `WindowFeatureJob` ticker scope from canonical active universe (or active-universe + configured baseline union), not static watchlist alone.
2. Add completeness checks that track per-ticker window-feature availability for tickers seen in recent flow events.

P2:
1. Add regression tests with flow events on off-watchlist symbols asserting window rows are generated once symbols become active.
2. Add telemetry dimension (`ticker_in_watchlist`) on window-feature misses to expose structural coverage bias.

## 136) Pass 129 Continuation (2026-02-07)

### 136.1 Window-Feature Consumers Do Not Scope by `feature_set_id` Despite Versioned Table Contract

Current behavior:
- `gold_feature_windows` schema uses composite identity including `feature_set_id` (`src/orion/storage/models_gold.py:231` to `src/orion/storage/models_gold.py:235`),
- producer writes versioned rows with `FEATURE_SET_ID = "v1"` and upserts on `(ticker, window_end_ts_utc, period, feature_set_id)` (`src/orion/jobs/window_feature_job.py:29`, `src/orion/jobs/window_feature_job.py:193` to `src/orion/jobs/window_feature_job.py:199`, `src/orion/jobs/window_feature_job.py:208`),
- `flow_enricher` window lookup omits `feature_set_id` filter and selects only by `(ticker, period, window_end_ts_utc <= entry_ts)` (`src/orion/ml/flow_enricher.py:1031` to `src/orion/ml/flow_enricher.py:1036`),
- `exit_classifier` training joins similarly omit `feature_set_id` across 1h/1d/1w lateral lookups (`src/orion/ml/exit_classifier.py:444` to `src/orion/ml/exit_classifier.py:460`).

Risk:
- once multiple feature-set versions coexist (planned by schema design), consumers can select rows from unintended versions at identical timestamps,
- training/scoring reproducibility degrades because feature semantics become version-ambiguous and query outcomes can be nondeterministic.

### 136.2 Updated Priorities

P1:
1. Add explicit `feature_set_id` selection in all `gold_feature_windows` consumers (flow enrichment, classifier training, and any API consumers).
2. Define one canonical configured feature-set version in runtime settings and enforce it at query boundaries.

P2:
1. Add regression tests with mixed-version fixtures (`v1`, `v2`) proving consumers pull only the configured feature set.
2. Add observability metric for version mismatches/absence (expected feature set missing for ticker-period-entry tuple).

## 137) Pass 130 Continuation (2026-02-07)

### 137.1 Window Features Use Processing-Time Sliding Windows Instead of Period-Aligned Buckets

Current behavior:
- each run computes `window_end = now` and `window_start = now - window_size` for every period (`src/orion/jobs/window_feature_job.py:55` to `src/orion/jobs/window_feature_job.py:63`),
- job default cadence is 60 seconds (`src/orion/jobs/window_feature_job.py:47`),
- persisted key includes exact `window_end_ts_utc` (`src/orion/jobs/window_feature_job.py:194`, `src/orion/storage/models_gold.py:232`),
- consumers then pick the latest row `<= entry_ts` rather than a boundary-aligned bucket (`src/orion/ml/flow_enricher.py:1034` to `src/orion/ml/flow_enricher.py:1036`, `src/orion/ml/exit_classifier.py:446` to `src/orion/ml/exit_classifier.py:459`).

Risk:
- 1h/1d/1w “window features” become moving processing-time snapshots rather than deterministic time-bucket features,
- two nearby events can get materially different context based on job run timing instead of underlying market interval boundaries, reducing reproducibility and cross-system parity.

### 137.2 Updated Priorities

P1:
1. Align `window_end` to canonical period boundaries (for example floor-to-5m, top-of-hour, trading-day close boundary) before persistence.
2. Query/serve window features by boundary key semantics rather than arbitrary latest processing-time snapshot.

P2:
1. Add regression tests asserting stable feature retrieval for events within the same canonical bucket regardless of job runtime second.
2. Add migration/backfill plan to normalize existing `gold_feature_windows` rows into boundary-aligned windows.

## 138) Pass 131 Continuation (2026-02-07)

### 138.1 `flow_enricher` Fetches Window Context via Per-Period Query Loop (3 Round-Trips per Event)

Current behavior:
- scoring enrichment executes `_get_window_features(ticker, entry_ts)` for every event (`src/orion/ml/flow_enricher.py:101`),
- `_get_window_features` loops through periods `["1h", "1d", "1w"]` and runs one SQL query per period (`src/orion/ml/flow_enricher.py:1027` to `src/orion/ml/flow_enricher.py:1042`),
- each query independently scans/sorts latest rows from `gold_feature_windows` (`src/orion/ml/flow_enricher.py:1030` to `src/orion/ml/flow_enricher.py:1036`).

Risk:
- high-throughput scoring paths incur avoidable query amplification and extra DB latency per event,
- under load, this pattern can create contention on `gold_feature_windows` reads and increase end-to-end scoring jitter.

### 138.2 Updated Priorities

P1:
1. Replace per-period loop with one set-based query (for example `period IN (...)` + `DISTINCT ON (period)` / window function) to fetch all required period rows in one round-trip.
2. Add request-level timeout and fallback handling that degrades window features as a unit rather than period-by-period partial results.

P2:
1. Add perf regression benchmark for enrichment latency before/after query consolidation.
2. Add metrics for window-feature query count and latency percentiles per scored event.

## 139) Pass 132 Continuation (2026-02-07)

### 139.1 `gold_feature_windows` Producer Emits `5m` Period Rows That No Active Consumer Reads

Current behavior:
- window producer defines periods `5m`, `1h`, `1d`, `1w` (`src/orion/jobs/window_feature_job.py:22` to `src/orion/jobs/window_feature_job.py:26`),
- default runtime period set includes all configured periods (`src/orion/jobs/window_feature_job.py:50`),
- flow enrichment window consumer fetches only `["1h", "1d", "1w"]` (`src/orion/ml/flow_enricher.py:1027`),
- exit classifier training joins only `period='1h'`, `period='1d'`, `period='1w'` (`src/orion/ml/exit_classifier.py:445` to `src/orion/ml/exit_classifier.py:458`),
- no in-repo consumer currently reads `gold_feature_windows` with `period='5m'`.

Risk:
- unnecessary compute/write/storage overhead persists for an unconsumed feature slice,
- operators may assume 5m context is influencing models when it is effectively dead data for current scoring/training paths.

### 139.2 Updated Priorities

P1:
1. Decide whether `5m` window context is required; if yes, wire explicit consumer usage, otherwise remove `5m` from `WindowFeatureJob` default periods.
2. Add producer-consumer contract test ensuring each emitted period has at least one active consumer path.

P2:
1. Add table-level usage telemetry (rows written per period vs rows read per period) to detect dead feature slices.
2. If retained, document exact downstream usage of `5m` period in training/inference specs.

## 140) Pass 133 Continuation (2026-02-07)

### 140.1 `ORION_USE_GATEWAY` Flag Controls Only Alpaca Streaming Path, Not Overall Gateway/Heber Integration

Current behavior:
- global setting exists as `orion_use_gateway` / `ORION_USE_GATEWAY` (`src/orion/config.py:70`),
- repository usage of this setting appears only in Alpaca stream connector (`src/orion/connectors/alpaca_stream_connector.py:23`, `src/orion/connectors/alpaca_stream_connector.py:44`),
- connector docs imply toggling gateway mode for that stream path (`src/orion/connectors/alpaca_stream_connector.py:33`),
- UW enrichment connectors still hardwire Data Gateway URL/key regardless of this flag (`src/orion/connectors/uw_market_tide_connector.py:26` to `src/orion/connectors/uw_market_tide_connector.py:27`, `src/orion/connectors/uw_greek_exposure_connector.py:26` to `src/orion/connectors/uw_greek_exposure_connector.py:27`),
- Heber-backed readers in labeling/enrichment are instantiated unconditionally (`src/orion/main_labeler.py:34`, `src/orion/main_feature_enrichment.py:42`).

Risk:
- operators can reasonably expect `ORION_USE_GATEWAY=false` to disable or bypass centralized Gateway/Heber dependencies system-wide, but only Alpaca stream transport changes,
- partial toggle semantics increase migration/cutover confusion and can produce inconsistent mixed-mode runtime behavior.

### 140.2 Updated Priorities

P1:
1. Rename/scope the existing flag to explicit intent (for example `ORION_USE_GATEWAY_FOR_ALPACA_STREAM`) or implement true system-wide gateway toggle behavior.
2. Add startup diagnostics that print effective mode per subsystem (Alpaca stream, UW enrichment, Heber readers) to remove ambiguity.

P2:
1. Add integration tests for configuration mode matrix ensuring documented toggle semantics match runtime behavior.
2. Update runbooks/env docs to distinguish transport toggles from full data-plane source toggles.

## 141) Pass 134 Continuation (2026-02-07)

### 141.1 Gateway-Mode Toggle for Alpaca Stream Is Bound at Import Time via Module Constant

Current behavior:
- module defines `USE_GATEWAY = system_settings.orion_use_gateway` at import time (`src/orion/connectors/alpaca_stream_connector.py:23`),
- constructor default uses that frozen module constant when `use_gateway` arg is omitted (`src/orion/connectors/alpaca_stream_connector.py:44`),
- factory helper does not pass explicit `use_gateway`, so it inherits import-time value (`src/orion/connectors/alpaca_stream_connector.py:281` to `src/orion/connectors/alpaca_stream_connector.py:283`).

Risk:
- configuration changes made after module import (runtime config reloads, tests, shell env changes in long-lived process) do not alter connector mode unless explicitly overridden,
- behavior can diverge from operator expectation that `ORION_USE_GATEWAY` is read at service start time for each connector instance.

### 141.2 Updated Priorities

P1:
1. Resolve mode dynamically in constructor from `system_settings.orion_use_gateway` (or pass explicit mode from caller), not via import-time module constant.
2. Add startup logging on connector instantiation showing effective mode source (explicit arg vs config default).

P2:
1. Add regression tests proving mode changes are respected per-instance without module reload hacks.
2. Audit other connectors for import-time config snapshots that can freeze runtime behavior unexpectedly.

## 142) Pass 135 Continuation (2026-02-07)

### 142.1 Data-Quality Checker Is Not Wired into Runtime Scheduling/Compose

Current behavior:
- `data_quality_checker` provides `run_quality_checks()` entrypoint and is intended as a scheduled quality job (`src/orion/jobs/data_quality_checker.py:433`, `src/orion/jobs/data_quality_checker.py:564`),
- in-repo references are limited to that module itself (no orchestration import/callers),
- compose defines a `nightly-backfill` service (`docker-compose.yml:210` to `docker-compose.yml:224`) but no `data_quality_checker` service,
- nightly orchestrator runs only ML/backfill tasks (`src/orion/jobs/nightly_backfill.py:66` to `src/orion/jobs/nightly_backfill.py:71`).

Risk:
- critical freshness/quality regressions can go undetected in normal runtime unless operators manually invoke the checker,
- migration assurance weakens because parity/data-quality guardrails are not continuously enforced.

### 142.2 Updated Priorities

P1:
1. Add scheduled runtime wiring for `data_quality_checker` (dedicated compose service/cron profile or integrated periodic task with explicit cadence).
2. Emit structured quality-check outcomes to the same observability channel as ingestion/enrichment health alerts.

P2:
1. Add integration test that validates checker execution in deployed compose profile (not just module-level CLI).
2. Define escalation thresholds (for example stale bars/flow/darkpool) and ensure non-zero exit/alert behavior on breach.

## 143) Pass 136 Continuation (2026-02-07)

### 143.1 `validate_features` Drift-Detection Script Is Not Wired into CI or Scheduled Runtime

Current behavior:
- `validate_features` provides CLI validation entrypoints (`src/orion/jobs/validate_features.py`),
- repository references to this module are self-contained/manual; no orchestration path invokes it in normal runtime,
- compose has no `validate_features` service and nightly orchestration does not call it (`docker-compose.yml`, `src/orion/jobs/nightly_backfill.py:66` to `src/orion/jobs/nightly_backfill.py:71`).

Risk:
- feature-contract drift (source lineage, ranges, checkpoint consistency) can accumulate without automated detection,
- migration parity confidence depends on ad-hoc manual runs instead of repeatable enforcement.

### 143.2 Updated Priorities

P1:
1. Wire `validate_features` into automated cadence (CI gate and/or scheduled post-backfill task) with machine-readable pass/fail output.
2. Fail pipeline/report degraded status when critical validations regress beyond configured thresholds.

P2:
1. Split validations into fast smoke checks (every run) and deep audits (scheduled) to keep signal timely and actionable.
2. Persist validation snapshots for trend analysis of feature-quality drift over time.

## 144) Pass 137 Continuation (2026-02-07)

### 144.1 Nightly Backfill Scheduler Uses Weekday-Only Trading-Day Check (No Exchange Holiday Calendar)

Current behavior:
- scheduler treats “trading day” as `weekday <= 4` only (`src/orion/jobs/nightly_backfill.py:29` to `src/orion/jobs/nightly_backfill.py:31`),
- next-run logic skips weekends but does not consult exchange holiday calendars (`src/orion/jobs/nightly_backfill.py:54` to `src/orion/jobs/nightly_backfill.py:56`),
- backfill service is wired as always-on scheduled runtime (`docker-compose.yml:210` to `docker-compose.yml:224`).

Risk:
- backfill can run on market holidays and partial/irregular sessions as if normal trading days,
- this wastes runtime capacity and can produce misleading “successful nightly run” signals on days with atypical or absent market data.

### 144.2 Updated Priorities

P1:
1. Replace weekday-only check with exchange-calendar based session checks (for example NYSE calendar) for run scheduling.
2. Add explicit holiday/half-day handling policy and runtime logging for skipped vs executed backfill cycles.

P2:
1. Add scheduler regression tests covering U.S. market holidays and early-close sessions.
2. Surface next scheduled run in both UTC and ET with exchange-session metadata for operator visibility.

## 145) Pass 138 Continuation (2026-02-07)

### 145.1 `HeberReader.read_bars` Accepts `timeframe` but Silently Ignores It

Current behavior:
- `read_bars(...)` API exposes `timeframe: str = "1m"` (`src/orion/clients/heber_reader.py:95` to `src/orion/clients/heber_reader.py:102`),
- implementation explicitly discards the argument (`_ = timeframe`) and always reads the same `bars` dataset (`src/orion/clients/heber_reader.py:105` to `src/orion/clients/heber_reader.py:113`),
- comments describe this as “interface compatibility” without enforcement or warning when non-default timeframe is requested (`src/orion/clients/heber_reader.py:105` to `src/orion/clients/heber_reader.py:108`).

Risk:
- callers can request non-1m bars and still receive 1m data with no explicit failure, causing silent feature/label distortions,
- interface contract is misleading and can hide granularity mismatches during future Gateway/Heber migration steps.

### 145.2 Updated Priorities

P1:
1. Either implement timeframe-aware routing (for example distinct Heber bar datasets/resampling policy) or fail fast when unsupported timeframe is requested.
2. Add explicit validation/logging so non-default timeframe requests cannot silently degrade to default behavior.

P2:
1. Add contract tests that assert requested timeframe and returned bar granularity are consistent.
2. Document supported timeframe values and fallback semantics in runtime integration docs.

## 146) Pass 139 Continuation (2026-02-07)

### 146.1 Reconciliation Job Is Alpaca-Only and Not Wired into Runtime Orchestration

Current behavior:
- `run_reconciliation` compares Bronze vs Silver counts only for `ALPACA_BAR_1M` and `SilverAlpacaBar` (`src/orion/jobs/reconcile_backfill.py:40`, `src/orion/jobs/reconcile_backfill.py:47` to `src/orion/jobs/reconcile_backfill.py:53`),
- UW/Heber datasets are not included in reconciliation scope (no flow/darkpool/silver feature tables in this job),
- repository references show usage only in module main and a unit test (`src/orion/jobs/reconcile_backfill.py:92`, `tests/unit/test_remediation_rules.py:5`),
- no compose/runtime service wiring invokes reconciliation automatically.

Risk:
- data-gap detection focuses on one local Alpaca path while centralized Gateway/Heber migration risk concentrates in flow/darkpool/feature datasets,
- reconciliation can appear present in codebase but remain operationally inert for live parity assurance.

### 146.2 Updated Priorities

P1:
1. Expand reconciliation scope to include migration-critical datasets (flow, darkpool, and derived silver/gold tables) with contract-aware comparisons.
2. Wire reconciliation into scheduled runtime/ops workflow with alerting on detected gaps.

P2:
1. Add per-dataset discrepancy metrics and severity thresholds (missing rows, orphan rows, lag windows).
2. Add integration tests for reconciliation over both Alpaca and Gateway/Heber dataset families.

## 147) Pass 140 Continuation (2026-02-07)

### 147.1 SignalEngine ML Prefilter Uses a Raw Scoring Contract That Does Not Match Candidate Field Semantics

Current behavior:
- `SignalEngine` prefilter builds `flow_dict` with `premium_usd` from `candidate.premium`, `put_call` from `candidate.option_type`, and `strike_price` key (`src/orion/processing/signal_engine.py:107` to `src/orion/processing/signal_engine.py:123`),
- `MLScorer` raw extraction expects `put_call` in `C/P` form and reads strike from `strike` (not `strike_price`) (`src/orion/ml/scorer.py:151`, `src/orion/ml/scorer.py:178`, `src/orion/ml/scorer.py:267`),
- ML candidate construction stores `option_type` as `CALL/PUT` and stores per-contract `option_price` into `candidate.premium` while `premium_usd` is kept separately in evidence (`src/orion/ml/flow_processor.py:197`, `src/orion/ml/flow_processor.py:218`, `src/orion/ml/flow_processor.py:221`),
- `score_enriched` explicitly exists for training/inference parity, but prefilter path uses `score(...)` directly (`src/orion/ml/scorer.py:345` to `src/orion/ml/scorer.py:352`, `src/orion/processing/signal_engine.py:123`).

Risk:
- ML prefilter can under/over-score candidates due to categorical/value-shape mismatches (`CALL/PUT` vs `C/P`, strike key mismatch, premium scale mismatch),
- valid candidates can be falsely skipped before solver evaluation, with behavior that diverges from intended enriched-parity scoring.

### 147.2 Updated Priorities

P1:
1. Replace prefilter scoring call with parity-safe input mapping (or `score_enriched`) so field semantics match scorer expectations.
2. Add explicit normalization for `put_call`, strike field, and premium scale before any prefilter scoring decision.

P2:
1. Add contract tests for prefilter inputs using both rule-generated and ML-generated `CandidateTrade` objects.
2. Emit structured diagnostics for prefilter input completeness/normalization to support production debugging.

## 148) Pass 141 Continuation (2026-02-07)

### 148.1 Ensemble Consensus Threshold Is Hardcoded to `0.5` Despite “Configurable” Intent

Current behavior:
- decision gate uses `if consensus_score >= 0.5` with hardcoded reject reason text `< 0.5` (`src/orion/processing/signal_engine.py:281`, `src/orion/processing/signal_engine.py:351`),
- in-code comment says the threshold is configurable, but no runtime read occurs for this value in `SignalEngine` (`src/orion/processing/signal_engine.py:280` to `src/orion/processing/signal_engine.py:282`),
- centralized `SystemSettings` has no `ensemble_consensus_threshold` field (`src/orion/config.py:58` to `src/orion/config.py:99`).

Risk:
- operators cannot tune consensus strictness by environment/stage without code edits,
- policy drift risk increases because comments/docs can imply configurability that runtime does not actually implement.

### 148.2 Updated Priorities

P1:
1. Add typed config for ensemble consensus threshold in centralized settings and consume it in `SignalEngine`.
2. Keep decision reason/trace values derived from the resolved threshold to prevent stale hardcoded messaging.

P2:
1. Add stage-level integration tests that assert threshold overrides affect EXECUTE/SKIP behavior.
2. Document threshold defaults and allowed ranges alongside other live risk controls.

## 149) Pass 142 Continuation (2026-02-07)

### 149.1 ML Prefilter Threshold Is Managed as an Isolated Env Lookup, Not a Centralized Runtime Setting

Current behavior:
- prefilter threshold is read via `os.getenv("ORION_ML_PREFILTER_THRESHOLD", "0.5")` inline inside decision logic (`src/orion/processing/signal_engine.py:126` to `src/orion/processing/signal_engine.py:129`),
- threshold is not modeled in centralized settings (`src/orion/config.py:58` to `src/orion/config.py:99`),
- no typed validation/range enforcement is applied before converting to float in runtime path (`src/orion/processing/signal_engine.py:128`).

Risk:
- invalid or out-of-range threshold values can cause runtime surprises at decision time,
- configuration governance is fragmented (part in centralized settings, part in ad-hoc env lookups), complicating migration-safe operations.

### 149.2 Updated Priorities

P1:
1. Move ML prefilter threshold into centralized typed settings with bounds validation and single-source ownership.
2. Remove ad-hoc env parsing from decision logic and read only from validated runtime config.

P2:
1. Add startup logging of resolved prefilter threshold by stage/profile.
2. Add negative tests for malformed threshold env values to verify fail-fast behavior.

## 150) Pass 143 Continuation (2026-02-07)

### 150.1 Audit Closure Snapshot for Migration-Critical Scope

Completed audit coverage for active migration-critical surfaces:
- ingestion/runtime wiring and compose-orchestration paths,
- Gateway stream contract and Heber read contract usage paths,
- label/enrichment/feature jobs that drive model and execution context,
- scoring/decisioning paths (`flow_enricher`, `scorer`, `signal_engine`),
- reconciliation/validation/scheduling guardrail jobs.

Remaining audit scope before implementation work is low priority:
- deeper review of non-runtime experimental/research modules not in active deployment path,
- final archive/delete decisions after fix rollout confirms no residual dependencies.

Conclusion:
- for Gateway/Heber integration parity plus active-path technical debt, audit scope is now sufficiently complete to begin remediation planning and implementation.

## 151) Pass 144 Continuation (2026-02-07)

### 151.1 Post-Remediation Revalidation Snapshot

Revalidated previously raised migration-critical findings against current code:

- Resolved: Alpaca stream gateway mode now resolves at connector initialization time (no import-time frozen flag) (`src/orion/connectors/alpaca_stream_connector.py:41`).
- Resolved: Heber bar reader now fails fast on unsupported timeframes instead of silently ignoring `timeframe` (`src/orion/clients/heber_reader.py:109` to `src/orion/clients/heber_reader.py:114`).
- Resolved: Reconciliation now covers `ALPACA_BAR_1M`, `UW_FLOW`, and `UW_DARKPOOL` (`src/orion/jobs/reconcile_backfill.py:25` to `src/orion/jobs/reconcile_backfill.py:47`).
- Resolved: Runtime scheduler wiring now exists for reconciliation, data-quality checks, and feature validation (`src/orion/jobs/quality_guardrails.py:18` to `src/orion/jobs/quality_guardrails.py:21`, `docker-compose.yml:226` to `docker-compose.yml:245`).
- Resolved: Nightly backfill scheduling now uses exchange-session close times via `MarketSchedule` instead of weekday-only checks (`src/orion/jobs/nightly_backfill.py:29` to `src/orion/jobs/nightly_backfill.py:68`).

### 151.2 Updated Priorities

P1:
1. Keep these revalidated fixes under regression coverage while completing remaining Gateway/Heber contract hardening.
2. Shift audit focus to unresolved runtime contracts still blocking full migration parity.

P2:
1. Add one integration smoke test that boots compose services and asserts guardrail job loop execution at least once.
2. Add release checklist section that explicitly verifies resolved pass-137 to pass-142 items before deploy.

## 152) Pass 145 Continuation (2026-02-07)

### 152.1 `sync_earnings` Still Uses Bearer-Token UW SDK Path Instead of Data Gateway `X-Gateway-Key` Contract

Current behavior:
- earnings sync/backfill creates `UnusualWhalesClient(base_url=f"{gateway_url}/api/v1/uw", token="gateway")` (`src/orion/jobs/sync_earnings.py:27`, `src/orion/jobs/sync_earnings.py:142`),
- UW SDK client injects `Authorization: Bearer <token>` by default (`src/orion/unusualwhales/client.py:55` to `src/orion/unusualwhales/client.py:56`, `src/orion/unusualwhales/client.py:98`),
- Data Gateway auth dependency explicitly requires `X-Gateway-Key` (`../Data-gateway/gateway/api/deps.py:103` to `../Data-gateway/gateway/api/deps.py:115`),
- ingestion startup still calls daily earnings sync (`src/orion/ingestion/service.py:114` to `src/orion/ingestion/service.py:121`), so this contract mismatch affects runtime initialization.

Risk:
- earnings sync can fail auth against Gateway-protected routes or depend on permissive behavior that is not contractually guaranteed,
- startup-path failure degrades calendar freshness and downstream earnings-aware features/labels.

### 152.2 Updated Priorities

P1:
1. Replace UW-SDK-through-gateway usage in `sync_earnings` with a Gateway-native client path that sends `X-Gateway-Key`.
2. Add explicit fail-fast logging/alerting when earnings sync auth fails at startup.

P2:
1. Add integration tests for daily + backfill earnings paths using Gateway auth contract.
2. Decouple startup from earnings sync success (retry queue or scheduled job) so ingestion boot is not coupled to external earnings fetch health.

## 153) Pass 146 Continuation (2026-02-07)

### 153.1 Ingestion Heber Usage Is Still Declared in Docs/Comments but Not Executed in Cycle Logic

Current behavior:
- ingestion module entrypoint text claims it “Reads flow/darkpool from Heber” (`src/orion/ingestion/__main__.py:8`),
- ingestion service comments still indicate Heber as UW source and instantiate `HeberReader` (`src/orion/ingestion/service.py:18` to `src/orion/ingestion/service.py:20`, `src/orion/ingestion/service.py:60`),
- code path has no active Heber read invocation; `_run_cycle` only appends Alpaca events (`src/orion/ingestion/service.py:200` to `src/orion/ingestion/service.py:205`),
- file-level note explicitly leaves Heber polling as a comment-only placeholder (`src/orion/ingestion/service.py:275` to `src/orion/ingestion/service.py:277`).

Risk:
- runtime behavior remains Alpaca-centric while documentation implies UW flow/darkpool ingestion is active,
- operators can overestimate migration parity and miss that `UW_FLOW`/`UW_DARKPOOL` event production still depends on alternative paths.

### 153.2 Updated Priorities

P1:
1. Either implement explicit Heber flow/darkpool reads in ingestion cycle or remove implied Heber-ingest claims from runtime entrypoint/docs.
2. Add startup diagnostics that report actual active event-source families for the running ingestion process.

P2:
1. Add contract test asserting expected event-type mix (`ALPACA_*` vs `UW_*`) for configured ingestion mode.
2. Align runbooks with real ingestion behavior until Heber-driven UW ingestion is fully implemented.

## 154) Pass 147 Continuation (2026-02-07)

### 154.1 Active Migration-Scope Audit Completion Status

For Orion runtime paths that matter for Gateway/Heber parity, audit coverage is now complete and revalidated through pass 147.

Remaining high-priority unresolved items before “migration-complete” status:
1. `sync_earnings` Gateway auth/path contract hardening (`src/orion/jobs/sync_earnings.py`, `src/orion/unusualwhales/client.py`).
2. Ingestion-source truth alignment (implement Heber UW ingestion or remove misleading Heber-ingest claims) (`src/orion/ingestion/service.py`, `src/orion/ingestion/__main__.py`).
3. Final canonical ownership decision for label/feature production (`main_price_target_labeler`/`main_option_quote_tracker` vs Heber watch/Gold outputs).
4. Runtime decommission/archive wave for remaining Orion-local SQL dependency surfaces after parity signoff.

Conclusion:
- migration-critical audit work is finished; remaining work is implementation and controlled decommission, not additional discovery.

## 155) Pass 148 Continuation (2026-02-07)

### 155.1 `sync_earnings` Gateway Auth Contract Remediated

Implemented:
- `sync_earnings` no longer uses UW SDK Bearer-token calls routed through Gateway.
- Orion now calls Data Gateway earnings endpoints directly with `X-Gateway-Key` in request headers.
- Daily sync now persists record-level earnings dates from Gateway payloads instead of overriding all rows to `date.today()`.
- Added unit coverage for header wiring, response parsing, daily date semantics, and ticker backfill row handling.

References:
- `src/orion/jobs/sync_earnings.py`
- `tests/unit/test_sync_earnings_gateway.py`

## 156) Pass 149 Continuation (2026-02-07)

### 156.1 Ingestion Source-Truth Drift Partially Remediated (Runtime Disclosure + Docs Alignment)

Implemented:
- Removed misleading ingestion-entrypoint claim that this service reads Heber flow/darkpool directly.
- Added explicit ingestion source-profile reporting at startup, including produced event types and externalized UW flow/darkpool ownership.
- Updated ingestion-service inline comments to reflect current behavior: this process emits Alpaca bar events; UW flow/darkpool ingestion remains external.

References:
- `src/orion/ingestion/__main__.py`
- `src/orion/ingestion/service.py`
- `tests/unit/test_ingestion_source_profile.py`

Residual:
- Full Heber-driven UW event ingestion inside `IngestionService` is still not implemented; this pass resolves contract clarity and operator visibility, not source unification.

## 157) Pass 150 Continuation (2026-02-07)

### 157.1 Label/Feature Canonical Ownership Is Still Split Across Orion and Heber Runtime Paths

Current behavior:
- Orion compose still runs local label/quote services:
  - `orion.main_labeler` (`docker-compose.yml:59`)
  - `orion.main_price_target_labeler` (`docker-compose.yml:74`)
  - `orion.main_option_quote_tracker` (`docker-compose.yml:106`)
- Orion `main_option_quote_tracker` reads `silver_uw_flow` and writes `silver_option_quotes` using `AlpacaOptionGreeksConnector` (direct provider path, not Heber watch path) (`src/orion/main_option_quote_tracker.py:78`, `src/orion/main_option_quote_tracker.py:129`, `src/orion/main_option_quote_tracker.py:172`).
- Orion `main_price_target_labeler` depends on local silver tables plus direct UW SDK client calls (`src/orion/main_price_target_labeler.py:347`, `src/orion/main_price_target_labeler.py:899`, `src/orion/main_price_target_labeler.py:1629`).
- Heber watch stack already performs centralized watch outcomes + features:
  - Gateway quote polling for active watches (`../Heber/heber/watch/poller.py:157`)
  - Gold label writes to `labels_alert_barriers` (`../Heber/heber/watch/writer.py:30`)
  - Gold feature writes under `meta_label_features` (`../Heber/heber/ml/datasets.py:24`, `../Heber/heber/watch/features.py:579`)

Surface-area comparison (feature/label parity snapshot):
- Orion price-target labeler writes a broad local schema (67 distinct `label[...]` keys found in code path).
- Heber watch label row currently exposes 26 outcome keys (`outcome_to_label_row`).
- Heber meta-label training feature vector currently exposes 29 numeric features (`AlertFeatures.numeric_feature_names`).

Risk:
- dual-producer ambiguity (Orion local DB tables vs Heber Gold datasets) creates schema drift and training/inference mismatch risk,
- backfill collision risk remains if Orion local backfills and Heber pipelines target overlapping business outcomes,
- migration cannot be declared complete while ownership of “source of truth” for labels/features is undecided.

### 157.2 Updated Priorities

P1:
1. Decide canonical owner for each outcome domain:
   - contract outcome labels,
   - alert meta-label features,
   - price-target/extended checkpoint labels.
2. Produce field-level mapping: `Orion price_target_labels` -> `Heber labels_alert_barriers/meta_label_features` with status `equivalent`, `derive`, `drop`.
3. Freeze net-new Orion-only label columns until mapping and ownership are signed off.

P2:
1. Add one parity dataset export check that compares row counts and key null-rates between Orion local outputs and Heber Gold outputs over the same date window.
2. Promote shared feature definitions into one canonical schema artifact to prevent parallel drift.

## 158) Pass 151 Continuation (2026-02-07)

### 158.1 Decommission/Archive Scope Is Identified but Not Yet Executed for Remaining Local-SQL Labeling Stack

Current behavior:
- The following Orion modules still hard-depend on local silver/gold SQL tables tied to pre-centralization flow:
  - `src/orion/main_option_quote_tracker.py`
  - `src/orion/main_price_target_labeler.py`
  - `src/orion/main_labeler.py`
  - `src/orion/jobs/backfill_ml_features.py`
  - `src/orion/jobs/backfill_exit_columns.py`
  - `src/orion/jobs/window_feature_job.py`
  - `src/orion/jobs/validate_features.py`
  - `src/orion/jobs/data_quality_checker.py`
  - `src/orion/ml/flow_enricher.py`
  - `src/orion/ml/exit_classifier.py`
  - `src/orion/ml/pattern_miner.py`
- Local-table ownership remains partly implicit: repository search shows writes/reads to `price_target_labels` and `flow_labels` in runtime scripts, but no clear table-creation migration in current Alembic history (schema lifecycle governance gap).

Risk:
- migration debt remains operational (not just documentary), with continued runtime dependence on local legacy tables,
- decommission later becomes higher-risk if local paths keep evolving while Heber/Gateway contracts evolve independently.

### 158.2 Updated Priorities

P1:
1. Publish a decommission matrix for each module: `keep`, `migrate-to-heber`, `archive-now`, `archive-after-parity`.
2. Stop adding new dependencies on `price_target_labels`, `flow_labels`, and `silver_option_quotes` outside explicitly approved migration work.
3. Add runtime startup warning when deprecated local-label pipelines are enabled, including replacement Heber dataset names.

P2:
1. Add archive-ready criteria per module (owner, replacement path, rollback plan, parity check passed).
2. Execute archive wave in small PRs grouped by bounded blast radius (quote tracking first, then label backfills, then consumers).

## 159) Pass 152 Continuation (2026-02-07)

### 159.1 Proposed Keep/Migrate/Archive Matrix for Remaining Label-Stack Modules

Audit scope for this pass:
- 11 local SQL-coupled modules identified in active code paths still tied to `silver_uw_*`, `silver_option_quotes`, `flow_labels`, `price_target_labels`, or `gold_feature_windows`.
- goal is to convert prior “inventory” into an actionable decommission sequence.

Decision matrix (proposed):

| Module | Current Role | Centralized Replacement Path | Recommendation |
|---|---|---|---|
| `src/orion/main_option_quote_tracker.py` | Local checkpoint quote collector into `silver_option_quotes` via direct Alpaca connector | Heber watch `SnapshotPoller` + Gateway quotes (`/alpaca/options/quotes`) + Gold outcomes/features | **Migrate, then archive early** |
| `src/orion/main_labeler.py` | Local flow outcome labeling loop (15m/30m/1h/2h) using Heber flow/bars reads + local `flow_labels` writes | Heber watch label writer (`labels_alert_barriers`) | **Migrate, then archive** |
| `src/orion/main_price_target_labeler.py` | Extended checkpoint + enrichment label production into `price_target_labels` | Heber watch/meta-label datasets with explicit feature-gap port plan | **Keep temporarily, split & migrate in phases, archive last** |
| `src/orion/jobs/backfill_exit_columns.py` | Historical backfill for `price_target_labels` exit/checkpoint columns | One-time Heber backfill pipeline after schema mapping | **Archive after parity backfill** |
| `src/orion/jobs/backfill_ml_features.py` | Historical ML feature backfill on `price_target_labels` | Heber feature recompute/backfill job | **Archive after parity backfill** |
| `src/orion/jobs/window_feature_job.py` | Builds `gold_feature_windows` from local silver tables | Heber Gold feature materialization | **Migrate, then archive** |
| `src/orion/jobs/validate_features.py` | Local feature sanity/spot-check tooling for `price_target_labels` | Heber quality checks + dataset parity checks | **Port checks, then archive local-only script** |
| `src/orion/jobs/data_quality_checker.py` | Local SQL quality checks for flow/darkpool/features | Shared Gateway/Heber quality monitors | **Keep short-term guardrail; retire after monitor parity** |
| `src/orion/ml/flow_enricher.py` | Feature assembly for scorer from local silver/gold tables | Read normalized features from Heber Gold views | **Migrate read path; keep API surface** |
| `src/orion/ml/exit_classifier.py` | Trains/uses models against `price_target_labels` | Retrain from Heber canonical outcome/feature datasets | **Migrate training source; keep model logic** |
| `src/orion/ml/pattern_miner.py` | Pattern mining from `price_target_labels` | Re-point to Heber canonical label/feature datasets | **Migrate data source; keep mining logic** |

### 159.2 Recommended Archive Execution Order

P1 (lowest coupling first):
1. `main_option_quote_tracker` -> archive after Heber watch quote parity and row-count parity checks pass.
2. `main_labeler` + `window_feature_job` -> archive after output parity checks on agreed horizons/dimensions.
3. `backfill_exit_columns` + `backfill_ml_features` -> archive once one-time migration backfills are complete and signed off.

P2 (consumer/source retargeting):
1. Repoint `flow_enricher`, `exit_classifier`, and `pattern_miner` to Heber canonical datasets.
2. Decommission local validation scripts (`validate_features`, `data_quality_checker`) once equivalent checks run in centralized pipeline.
3. Archive `main_price_target_labeler` only after field-level mapping decisions from pass 157 are complete and accepted.

### 159.3 Blocking Decisions Before Archive Wave Can Start

1. Confirm whether Orion’s extended `price_target_labels` enrichment set is strategic and should be ported to Heber, or intentionally reduced.
2. Approve canonical dataset names/contracts for migrated training inputs (labels + features).
3. Define acceptance gates for each archive PR:
   - parity window and metrics,
   - rollback procedure,
   - ownership handoff.

## 160) Pass 153 Continuation (2026-02-07)

### 160.1 Silent Fallback Paths Mask Feature-Calculation Failures in Active ML Enrichment Paths

Current behavior:
- `main_price_target_labeler` has multiple broad `except Exception` fallbacks that silently skip feature computations (for example sector-flow/correlation and DB sector-cache checks) (`src/orion/main_price_target_labeler.py:1498`, `src/orion/main_price_target_labeler.py:1531`, `src/orion/main_price_target_labeler.py:1656`).
- `ml/flow_enricher` suppresses feature-calculation failures without structured failure output in key derivations (darkpool windows, IV-vs-HV, OI change) (`src/orion/ml/flow_enricher.py:402`, `src/orion/ml/flow_enricher.py:502`, `src/orion/ml/flow_enricher.py:533`).

Risk:
- missing features can silently degrade model quality and parity checks without explicit operational signals,
- troubleshooting becomes reactive because failures are converted to `None` values without consistent alertable telemetry.

### 160.2 Dynamic Label Insert Guard Claims Schema Safety but Does Not Validate Column Existence

Current behavior:
- `main_price_target_labeler.persist_labels()` comment states it avoids inserting non-existent columns, but column selection only checks `key is not None` (`src/orion/main_price_target_labeler.py:2696` to `src/orion/main_price_target_labeler.py:2699`), which does not validate DB schema membership.

Risk:
- schema evolution in label dict keys can break runtime writes (`undefined column`) unexpectedly,
- mismatch between code intent and enforcement increases migration fragility during rapid feature iteration.

### 160.3 Updated Priorities

P1:
1. Replace silent `pass`/null fallbacks in feature-critical paths with structured error events and per-feature failure counters.
2. Add explicit schema guard before dynamic inserts (derive allowed columns from DB metadata at startup and reject unknown keys deterministically).
3. Add runtime feature-fill telemetry (non-null rates for high-impact columns) to detect degradation early.

P2:
1. Add DLQ/audit sink for failed enrichment records to support replay and root-cause analysis.
2. Add regression tests that assert failure paths are observable (log/metric) rather than silently ignored.

## 161) Pass 154 Continuation (2026-02-07)

### 161.1 Audit Closure Snapshot (Post Pass 153)

Migration-critical audit coverage status:
- complete for active runtime integration paths (ingestion, feature enrichment, label stack, reconciliation/guardrails),
- complete for current Gateway/Heber parity blockers at the contract/ownership level,
- complete for decommission candidate identification and archive-wave ordering.

Open implementation blockers (audited, not yet remediated):
1. Canonical ownership signoff for label/feature outputs (`price_target_labels` vs Heber watch Gold datasets).
2. Field-level mapping and parity acceptance criteria for migration of Orion-only enriched features.
3. Runtime observability hardening for silent enrichment fallbacks.
4. Deterministic schema guard for dynamic label inserts.
5. Execution of archive waves from approved matrix (quote tracker -> label loops/backfills -> downstream consumers).

Residual audit scope (non-blocking, lower priority):
- additional deep review of non-runtime experimental modules not in deployed compose paths,
- post-remediation verification passes after each archive wave.

Conclusion:
- the audit itself is no longer the blocker; migration completion now depends on implementation decisions and staged remediation execution.

## 162) Pass 155 Continuation (2026-02-07)

### 162.1 Dynamic Label Insert Now Uses Deterministic Schema Guard

Implemented:
- Added shared schema-guard utility for runtime insert payload validation:
  - `fetch_table_columns(...)` reads authoritative table columns from `information_schema`,
  - `resolve_insert_columns(...)` enforces required keys and rejects unknown columns.
- `main_price_target_labeler.persist_labels()` now validates each label payload against live `price_target_labels` schema before executing INSERT.
- Unknown/missing columns now fail fast with structured error context instead of relying on implicit DB exceptions.

References:
- `src/orion/labeler/schema_guard.py`
- `src/orion/main_price_target_labeler.py`
- `tests/unit/test_label_schema_guard.py`

### 162.2 Silent Feature Fallback Paths Now Emit Structured Observability Signals

Implemented:
- Added fallback counters and structured warning events in:
  - `main_price_target_labeler` (checkpoint quote lookup, sector/correlation feature fallback paths, sector info lookup),
  - `ml/flow_enricher` (task-level gather failures, darkpool window fetch fallback, IV-vs-HV fallback, OI-change fallback).
- Existing behavior still degrades gracefully, but fallback events are now visible for operators and guardrail dashboards.

References:
- `src/orion/main_price_target_labeler.py`
- `src/orion/ml/flow_enricher.py`

Residual:
- fallback logging is now present, but parity SLO thresholds/alerts and DLQ replay flow are still pending.

## 163) Pass 156 Continuation (2026-02-07)

### 163.1 Deprecated Local Label Pipelines Now Emit Startup Warnings with Centralized Replacement Paths

Implemented:
- Added explicit startup deprecation warnings for legacy local-label runtime services:
  - `orion.main_option_quote_tracker`
  - `orion.main_labeler`
  - `orion.main_price_target_labeler`
- Each warning includes an explicit centralized replacement target in Heber datasets/pipelines to reduce operator ambiguity during migration window.

References:
- `src/orion/main_option_quote_tracker.py`
- `src/orion/main_labeler.py`
- `src/orion/main_price_target_labeler.py`

Residual:
- warnings are now present, but runtime disable switches / staged shutdown orchestration for these services are still pending the archive-wave implementation.

## 164) Pass 157 Continuation (2026-02-07)

### 164.1 Legacy Label Pipelines Now Support Runtime Disable Control for Staged Decommission

Implemented:
- Added runtime gate `ORION_ENABLE_LEGACY_LABEL_PIPELINES` (default enabled) to:
  - `orion.main_option_quote_tracker`
  - `orion.main_labeler`
  - `orion.main_price_target_labeler`
- When set to false, service logs `DEPRECATED_PIPELINE_DISABLED` and exits before processing loop.
- This enables environment-level staged shutdown without code edits during archive waves.

References:
- `src/orion/main_option_quote_tracker.py`
- `src/orion/main_labeler.py`
- `src/orion/main_price_target_labeler.py`

Residual:
- runtime gate is global (shared across all legacy label services); per-service kill switches may still be desirable for finer rollout control.

## 165) Pass 158 Continuation (2026-02-07)

### 165.1 Per-Service Legacy Label Pipeline Kill Switches Are Now Implemented (TDD-Backed)

Implemented:
- Added per-service override gates with global fallback:
  - `ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER` (`src/orion/main_option_quote_tracker.py:50`)
  - `ORION_ENABLE_LEGACY_FLOW_LABELER` (`src/orion/main_labeler.py:40`)
  - `ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER` (`src/orion/main_price_target_labeler.py:57`)
  - fallback remains `ORION_ENABLE_LEGACY_LABEL_PIPELINES` in each service (`src/orion/main_option_quote_tracker.py:53`, `src/orion/main_labeler.py:43`, `src/orion/main_price_target_labeler.py:60`).
- Added focused unit tests to validate override precedence and disabled early-return behavior:
  - `tests/unit/test_legacy_label_pipeline_gates.py`.

Result:
- staged decommission can now disable one legacy service at a time without disabling all legacy label services globally.

### 165.2 New Audit Finding: Compose-Level Rollout Controls Are Not Yet Wired for the New Per-Service Gates

Current behavior:
- Compose still defines legacy services (`labeler`, `price_target_labeler`, `option_quote_tracker`) without explicit per-service gate env wiring (`docker-compose.yml:47`, `docker-compose.yml:61`, `docker-compose.yml:92`).
- Disabled log payloads still point to the global control key (`"control": "ORION_ENABLE_LEGACY_LABEL_PIPELINES=false"`) even when a service-specific gate is the effective control (`src/orion/main_option_quote_tracker.py:200`, `src/orion/main_labeler.py:375`, `src/orion/main_price_target_labeler.py:2784`).

Risk:
- operators can still miss fine-grained rollout intent in default compose deployments,
- troubleshooting disabled-service behavior is slower because logs do not identify the exact effective control variable.

### 165.3 Updated Priorities

P1:
1. Add per-service gate env variables to `docker-compose.yml` for `labeler`, `price_target_labeler`, and `option_quote_tracker` (default to enabled for backward compatibility).
2. Update `DEPRECATED_PIPELINE_DISABLED` log payloads to report the effective control key/value (specific override vs global fallback).

P2:
1. Add a startup info log for each legacy service summarizing resolved gate values (`global`, `service_specific`, `effective`) to simplify operational cutovers.

## 166) Pass 159 Continuation (2026-02-07)

### 166.1 New Audit Finding: Compose Restart Policy Causes Disable-Loop for Gated Legacy Services

Current behavior:
- legacy services run with `restart: unless-stopped` in compose (`docker-compose.yml:50`, `docker-compose.yml:64`, `docker-compose.yml:95`),
- each service now exits early when its legacy gate is disabled:
  - option quote tracker (`src/orion/main_option_quote_tracker.py:193`, `src/orion/main_option_quote_tracker.py:203`),
  - flow labeler (`src/orion/main_labeler.py:371`, `src/orion/main_labeler.py:453`),
  - price-target labeler (`src/orion/main_price_target_labeler.py:2779`, `src/orion/main_price_target_labeler.py:3010`).

Operational implication:
- in docker-compose deployments, disabling a legacy service via env gate can trigger repeated restart loops (clean exit + `unless-stopped` policy),
- this creates noisy logs/churn and undermines the intent of “disable service” as a stable rollout state.

Risk:
- decommission cutovers become noisy and harder to verify,
- service-disabled state may be misread as a crash/recovery issue by operators.

### 166.2 Updated Priorities

P1:
1. For legacy services under decommission, move runtime control from “exit early” to compose-level inclusion control (profiles/overrides) so disabled means not launched.
2. If env-gate disable must remain, avoid restart loops by adjusting restart policy for these services during migration waves.

P2:
1. Add an operations runbook note for legacy gate usage in compose, including expected container behavior in each rollout mode.
2. Add a lightweight smoke check in CI/dev scripts asserting disabled legacy services do not churn restart counts under compose defaults.

## 167) Pass 160 Continuation (2026-02-07)

### 167.1 New Audit Finding: Legacy Gate Config Is Not Centralized in Typed Settings

Current behavior:
- newly introduced legacy-gate env vars are parsed ad hoc inside runtime modules:
  - option quote tracker (`src/orion/main_option_quote_tracker.py:49` to `src/orion/main_option_quote_tracker.py:52`),
  - flow labeler (`src/orion/main_labeler.py:40` to `src/orion/main_labeler.py:43`),
  - price-target labeler (`src/orion/main_price_target_labeler.py:57` to `src/orion/main_price_target_labeler.py:60`).
- centralized typed settings (`SystemSettings`) do not define these controls (`src/orion/config.py:58` to `src/orion/config.py:110`).

Risk:
- config-governance drift: new operational controls bypass typed validation and centralized discoverability,
- behavior can diverge across services if parsing/default semantics evolve independently.

### 167.2 Updated Priorities

P1:
1. Move legacy gate definitions into `SystemSettings` with explicit names/defaults and environment aliases.
2. Replace duplicated ad hoc parsing with a shared helper (or direct `system_settings` fields) used by all legacy label services.

P2:
1. Add unit tests at config layer verifying precedence semantics (service-specific override > global fallback).
2. Include these gates in operator docs so rollout controls are discoverable in one place.

## 168) Pass 161 Continuation (2026-02-07)

### 168.1 Disable-Gate Ordering Fixed: No DB Initialization in Disabled Legacy Labeler Modes

Implemented (TDD-backed):
- Added failing tests that assert disabled services do not call `init_db()`:
  - `test_flow_labeler_does_not_init_db_when_specific_gate_disabled`
  - `test_price_target_labeler_does_not_init_db_when_specific_gate_disabled`
  - file: `tests/unit/test_legacy_label_pipeline_gates.py`.
- Moved gate checks ahead of DB initialization in:
  - `src/orion/main_labeler.py:361` to `src/orion/main_labeler.py:379`,
  - `src/orion/main_price_target_labeler.py:2769` to `src/orion/main_price_target_labeler.py:2788`.

Result:
- when legacy labelers are disabled via gate, they now return before DB initialization and do not require live DB availability.

Residual:
- compose restart-loop risk from pass 159 remains until service inclusion/restart-policy handling is updated.

## 169) Pass 162 Continuation (2026-02-08)

### 169.1 Rollout Operability Remediation: Effective Control Attribution + Compose Gate Wiring

Implemented (TDD-backed):
- Added explicit effective-control helpers for legacy gate resolution in each service:
  - `src/orion/main_option_quote_tracker.py`
  - `src/orion/main_labeler.py`
  - `src/orion/main_price_target_labeler.py`
- `DEPRECATED_PIPELINE_DISABLED` log payload now reports the actual control key/value that disabled the service (specific override or global fallback), instead of hardcoding the global key.
- Added targeted tests validating control-key precedence/fallback:
  - `tests/unit/test_legacy_label_pipeline_gates.py`
- Wired per-service gate env vars in compose for legacy services:
  - `ORION_ENABLE_LEGACY_FLOW_LABELER`
  - `ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER`
  - `ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER`
  - plus global fallback `ORION_ENABLE_LEGACY_LABEL_PIPELINES`
  - file: `docker-compose.yml`
- Added compose wiring test:
  - `tests/unit/test_compose_legacy_gate_wiring.py`

Result:
- operators can disable legacy services with explicit per-service env controls in standard compose workflow,
- disabled-service logs now identify the exact effective gate source for faster cutover diagnostics.

Residual:
- restart-loop behavior under `restart: unless-stopped` (pass 159) is still open and requires lifecycle-policy handling beyond env wiring.

## 170) Pass 163 Continuation (2026-02-08)

### 170.1 Config-Governance Remediation: Legacy Gate Controls Centralized in Typed Settings

Implemented (TDD-backed):
- Added typed `SystemSettings` fields for legacy-gate controls:
  - `legacy_label_pipelines_enabled` (`ORION_ENABLE_LEGACY_LABEL_PIPELINES`)
  - `legacy_flow_labeler_enabled` (`ORION_ENABLE_LEGACY_FLOW_LABELER`)
  - `legacy_option_quote_tracker_enabled` (`ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER`)
  - `legacy_price_target_labeler_enabled` (`ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER`)
  - file: `src/orion/config.py`.
- Updated legacy service gate-resolution helpers to derive effective control from `SystemSettings` instead of ad hoc env parsing:
  - `src/orion/main_option_quote_tracker.py`
  - `src/orion/main_labeler.py`
  - `src/orion/main_price_target_labeler.py`
- Added centralized config mapping test:
  - `tests/unit/test_config_centralization.py::test_legacy_label_gate_settings_env_mapping`

Result:
- gate ownership/typing is now centralized in config semantics rather than duplicated across service modules,
- rollout control behavior remains aligned with service-specific override > global fallback while using typed settings.

Residual:
- compose restart-loop behavior for intentionally disabled services (pass 159) is still unresolved.

## 171) Pass 164 Continuation (2026-02-08)

### 171.1 Compose Restart-Loop Remediation for Disabled Legacy Label Services

Implemented (TDD-backed):
- Added compose test enforcing restart policy for legacy label services:
  - `tests/unit/test_compose_legacy_gate_wiring.py::test_legacy_label_services_use_on_failure_restart_policy`.
- Updated compose restart policy for:
  - `labeler`,
  - `price_target_labeler`,
  - `option_quote_tracker`,
  from `unless-stopped` to `on-failure` (`docker-compose.yml`).

Result:
- when legacy service gates intentionally disable a service (clean exit), compose no longer auto-restarts these containers in a churn loop,
- crash recovery for non-zero failures remains enabled via `on-failure`.

Residual:
- longer-term decommission path (profiles/inclusion control vs runtime gating) remains an architectural choice, but the immediate restart-loop operational risk is mitigated.

## 172) Pass 165 Continuation (2026-02-08)

### 172.1 Compose Inclusion Control Enabled: Legacy Label Stack Is Now Opt-In via Profile

Implemented (TDD-backed):
- Added compose test enforcing profile-based inclusion for legacy label stack:
  - `tests/unit/test_compose_legacy_gate_wiring.py::test_legacy_label_stack_services_are_profiled_for_opt_in`.
- Added `profiles: [ "legacy-labels" ]` to:
  - `labeler`,
  - `price_target_labeler`,
  - `option_quote_tracker`,
  - `nightly-backfill`,
  - `quality-guardrails`,
  in `docker-compose.yml`.

Result:
- default compose startup no longer launches legacy label/backfill/guardrail services unless explicitly requested with `--profile legacy-labels`,
- runtime disable gates remain available, but compose inclusion is now the primary decommission control plane for this stack.

Residual:
- operator docs still need explicit runbook examples for profile usage in each rollout mode (default runtime vs legacy-label maintenance runs).

## 173) Pass 166 Continuation (2026-02-08)

### 173.1 New Audit Finding: Pattern-Miner Runtime Is No Longer Source-Aligned After Legacy Profile Opt-In

Current behavior:
- `pattern-miner` remains in default compose runtime without a profile gate (`docker-compose.yml:205` to `docker-compose.yml:216`).
- Pattern miner training still reads from Orion-local `price_target_labels` (`src/orion/ml/pattern_miner.py:181`, `src/orion/ml/pattern_miner.py:216`), which is maintained by services now gated behind `legacy-labels` profile.
- When samples are absent, miner emits no-data/insufficient-sample warnings and skips model updates (`src/orion/ml/pattern_miner.py:227`, `src/orion/ml/pattern_miner.py:646`).

Risk:
- default deployments can run pattern-miner on stale or empty label data with no hard failure,
- ML refresh expectations become ambiguous because the model-training service is active but its source pipeline is opt-in/off by default.

Recommended next remediation:
1. Short-term: profile-gate `pattern-miner` with the same `legacy-labels` runtime until training inputs are centralized.
2. Mid-term: migrate pattern-miner training source from Orion-local `price_target_labels` to Heber canonical label/features datasets, then remove legacy dependency.

## 174) Pass 167 Continuation (2026-02-08)

### 174.1 New Audit Finding: Feature-Enrichment Gateway Auth Is Optional in Code but Missing in Compose, Enabling Silent No-Data Loops

Current behavior:
- `feature_enrichment` compose wiring provides `GATEWAY_URL` but not `GATEWAY_API_KEY`/`DATA_GATEWAY_API_KEY` (`docker-compose.yml:91` to `docker-compose.yml:96`).
- Gateway UW connectors build auth headers only when key is present (`self.headers = {"X-Gateway-Key": ...} if self.gateway_key else {}`):
  - `src/orion/connectors/uw_greek_exposure_connector.py:28`
  - `src/orion/connectors/uw_market_tide_connector.py:28`
  - same pattern in max-pain and IV-rank connectors.
- On request failure, connectors log warning and return `None`/`0` (instead of fail-fast), and the main loop continues:
  - `src/orion/connectors/uw_greek_exposure_connector.py:38` to `src/orion/connectors/uw_greek_exposure_connector.py:40`
  - `src/orion/connectors/uw_market_tide_connector.py:42` to `src/orion/connectors/uw_market_tide_connector.py:44`
  - `src/orion/main_feature_enrichment.py:256` to `src/orion/main_feature_enrichment.py:322`.

Risk:
- when Gateway auth is required, default compose can run indefinitely with mostly empty enrichment writes and no startup hard failure,
- operators may misread “service up” as “data fresh” while enrichment features silently degrade.

Recommended next remediation:
1. Enforce startup contract: fail fast in feature-enrichment if Gateway URL is set but no Gateway API key is configured.
2. Wire `DATA_GATEWAY_API_KEY` (or alias) in compose for `feature_enrichment`.
3. Add an integration smoke test that asserts non-zero enrichment writes (or explicit auth error) under configured Gateway mode.

## 175) Pass 168 Continuation (2026-02-08)

### 175.1 Remaining Hotspot Snapshot (Post Legacy-Profile Remediation)

Repo scan snapshot (`src/orion`, tokenized local-table dependencies):

| Token | Approx refs |
| --- | --- |
| `silver_uw_flow` | 65 |
| `silver_market_tide` | 11 |
| `silver_greek_exposure` | 11 |
| `silver_max_pain` | 8 |
| `silver_iv_rank` | 3 |
| `price_target_labels` | 28 |

Top file concentration:
- `src/orion/jobs/validate_features.py` (53 refs)
- `src/orion/main_price_target_labeler.py` (27 refs)
- `src/orion/ml/flow_enricher.py` (11 refs)
- `src/orion/jobs/backfill_exit_columns.py` (6 refs)
- `src/orion/jobs/backfill_ml_features.py` (5 refs)
- `src/orion/jobs/data_quality_checker.py` (5 refs)

Scope interpretation:
- Core runtime decommission controls have been substantially audited and partially remediated (legacy label stack gating, restart policy, compose profile inclusion).
- Remaining high-volume SQL-coupled debt is now concentrated in validation/backfill/training support jobs plus `main_price_target_labeler`.
- Audit can now transition from broad discovery to targeted closeout on these concentrated hotspots.

## 176) Pass 169 Continuation (2026-02-08)

### 176.1 New Audit Finding: Guardrail Sanity Checks Ignore Incomplete Label Rows (`ml_ready = false`)

Current behavior:
- `quality_guardrails` invokes feature sanity validation on schedule (`src/orion/jobs/quality_guardrails.py:90` to `src/orion/jobs/quality_guardrails.py:92`).
- `run_sanity_checks()` in `validate_features` filters to `WHERE ml_ready` (`src/orion/jobs/validate_features.py:248` to `src/orion/jobs/validate_features.py:249`).

Risk:
- if backfill/enrichment pipelines stall and records remain `ml_ready = false`, scheduled sanity checks can report green while the pipeline is actually incomplete,
- this creates a blind spot in the primary operational guardrail loop.

### 176.2 Supporting Consistency Drift: `minutes_to_close` Validation Bounds Diverge Inside Same Module

Observed drift:
- spot-check path enforces `minutes_to_close` in `[0, 390]` (`src/orion/jobs/validate_features.py:104` to `src/orion/jobs/validate_features.py:109`),
- batch sanity path enforces `[0, 500]` (`src/orion/jobs/validate_features.py:244`, `src/orion/jobs/validate_features.py:261`).

Risk:
- contradictory bounds can produce inconsistent pass/fail outcomes across guardrail modes, reducing trust in validation signals.

Recommended next remediation:
1. Add explicit coverage metrics for `ml_ready = false` rows in guardrail output (count + age + top missing fields).
2. Align `minutes_to_close` bounds to one canonical market-session contract and enforce it across spot-check and batch sanity paths.

## 177) Pass 170 Continuation (2026-02-08)

### 177.1 Feature-Enrichment Gateway Auth Contract Hardened (TDD-Backed)

Implemented:
- Added startup contract helper `main_feature_enrichment._gateway_runtime_contract()` that:
  - requires configured Gateway URL (`DATA_GATEWAY_URL/GATEWAY_URL`),
  - requires configured Gateway API key (`DATA_GATEWAY_API_KEY/GATEWAY_API_KEY`),
  - normalizes trailing slash from base URL.
- Updated `run_feature_loop()` to enforce the contract before connector initialization and pass resolved key explicitly into all UW Gateway connectors.
- Wired Gateway API key env for `feature_enrichment` in compose:
  - `GATEWAY_API_KEY=${GATEWAY_API_KEY}`.
- Added tests:
  - `tests/unit/test_feature_enrichment_gateway_contract.py`
  - `tests/unit/test_compose_legacy_gate_wiring.py::test_feature_enrichment_wires_gateway_api_key_env`

Result:
- feature enrichment now fails fast at startup when Gateway auth is missing instead of silently looping with no/empty enrichment writes.

Residual:
- connector-level behavior still logs-and-continues on per-request failures; this is acceptable for transient runtime errors, but alerting/SLO policy is still needed for sustained zero-write conditions.

## 178) Pass 171 Continuation (2026-02-08)

### 178.1 Guardrail Sanity Blind Spot Remediated in `validate_features` (TDD-Backed)

Implemented:
- Added canonical validation constant `MINUTES_TO_CLOSE_MAX = 390` and aligned both:
  - spot-check time validation,
  - batch sanity query bounds,
  in `src/orion/jobs/validate_features.py`.
- Updated sanity SQL to evaluate bad-feature checks on `ml_ready` rows while also measuring incomplete coverage:
  - added `not_ready` count for `ml_ready = false` rows.
- Added explicit sanity failure when incomplete rows are present:
  - emits issue `ml_ready = false rows present: N` and increments failed-check count.
- Added tests:
  - `tests/unit/test_validate_features_guardrails.py::test_run_sanity_checks_query_uses_consistent_minutes_to_close_bound`
  - `tests/unit/test_validate_features_guardrails.py::test_run_sanity_checks_flags_unready_rows`

Result:
- scheduled guardrails no longer silently report green while label population is incomplete,
- time-bound validation semantics are now internally consistent for `minutes_to_close`.

Residual:
- this fix improves detection/reporting; operational response (auto-remediation/escalation) for sustained incomplete-row states is still a follow-up.

## 179) Pass 172 Continuation (2026-02-08)

### 179.1 Runtime Source-Alignment Remediation: `pattern-miner` Now Opt-In with Legacy Label Stack (TDD-Backed)

Implemented:
- Added compose guardrail test:
  - `tests/unit/test_compose_legacy_gate_wiring.py::test_pattern_miner_is_profiled_with_legacy_label_stack`.
- Added `profiles: [ "legacy-labels" ]` to `pattern-miner` service in `docker-compose.yml`.

Result:
- default compose startup no longer runs pattern mining against potentially stale/empty local `price_target_labels` inputs when legacy label stack is disabled,
- `pattern-miner` lifecycle now matches its current source dependency boundary.

Residual:
- this is an alignment stopgap; long-term fix remains migration of pattern-miner training sources to Heber canonical datasets so it can rejoin non-legacy runtime profiles.

## 180) Pass 173 Continuation (2026-02-08)

### 180.1 Guardrail Orchestrator No Longer Masks Structured Validation Failures (TDD-Backed)

Implemented:
- Added `tests/unit/test_quality_guardrails_results.py` coverage for:
  - structured job result parsing (`failed`/`issues`),
  - error-path logging when guardrail jobs report failed checks,
  - completion logging only for clean results.
- Added `_result_failure_summary()` in `src/orion/jobs/quality_guardrails.py` to normalize and summarize structured failure results.
- Updated `_run_job()` to:
  - log an explicit error (`Guardrail job reported failed checks`) when a job returns non-zero failures,
  - avoid logging misleading `Completed guardrail job` for failed-result payloads.

Result:
- scheduled guardrail orchestration now surfaces contract-level check failures loudly in logs instead of reporting false-green completion messages.

Residual:
- orchestrator behavior still prefers non-crashing loop continuity over process exit on failed checks; escalation policy (exit vs alert-only by job type) remains a follow-up design decision.

## 181) Pass 174 Continuation (2026-02-08)

### 181.1 Guardrail Failure Escalation Policy Added (TDD-Backed)

Implemented:
- Extended `tests/unit/test_quality_guardrails_results.py` to assert:
  - `_run_job()` boolean success contract (`True` clean, `False` failed checks),
  - opt-in fail-fast escalation on structured check failures.
- Added `quality_guardrails._env_flag()` and wired new runtime policy flag:
  - `ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES`.
- Updated `quality_guardrails._run_job()` behavior:
  - returns `False` for job exceptions and structured failed-check results,
  - returns `True` only on clean completion,
  - raises `RuntimeError` for structured failed-check results when fail-fast flag is enabled.

Result:
- guardrail scheduler now supports explicit operational mode selection:
  - alert-only mode (default) for resilience,
  - fail-fast mode for strict CI/ops environments that must stop on validation failures.

Residual:
- current fail-fast policy is global; per-job escalation granularity (e.g., fail-fast only for reconciliation or feature sanity) remains a follow-up enhancement.

## 182) Pass 175 Continuation (2026-02-08)

### 182.1 Guardrail Scheduler Retry Semantics Corrected (TDD-Backed)

Finding:
- scheduler timestamps were advanced after each guardrail invocation regardless of success/failure, which delays retries on failing guardrail jobs by a full interval window.

Implemented:
- Added test coverage in `tests/unit/test_quality_guardrails.py` for `_next_last_run()` timestamp behavior.
- Added `_next_last_run(last_run, succeeded, now)` helper in `src/orion/jobs/quality_guardrails.py`.
- Updated `run_guardrail_loop()` to update `last_*` markers only when `_run_job()` succeeds.

Result:
- failed guardrail jobs are now retried on the next scheduler cycle (subject to loop sleep) instead of being deferred behind interval-based cooldown from false-success timestamp updates.

Residual:
- with persistent failures, retries will occur every loop tick; optional failure backoff/jitter may be worth adding if guardrail jobs are noisy under prolonged outages.

## 183) Pass 176 Continuation (2026-02-08)

### 183.1 Backfill Session-Taxonomy Drift Removed (TDD-Backed)

Finding:
- `backfill_ml_features.get_entry_time_features()` used a local bucket contract (`early/midday/afternoon/late`) that diverged from live label generation (`OPEN/MID/CLOSE`).

Implemented:
- Added regression coverage:
  - `tests/unit/test_backfill_ml_features_time_alignment.py` validates parity against `main_price_target_labeler.get_entry_time_features()` across OPEN/MID/CLOSE boundary timestamps.
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - removed local session bucketing logic,
  - delegated time-feature generation directly to labeler’s canonical `get_entry_time_features`.

Result:
- historical backfill writes now preserve the same entry-session ontology as live label generation, eliminating silent label-feature drift introduced by backfill rewrites.

Residual:
- this resolves taxonomy drift only; broader backfill candidate selection semantics (`LIMIT` without deterministic ordering) remain open in the backlog.

## 184) Pass 177 Continuation (2026-02-08)

### 184.1 Backfill Candidate Selection Made Deterministic (TDD-Backed)

Finding:
- `get_records_to_backfill()` used `LIMIT :limit` without stable ordering, creating non-deterministic batch composition across runs/retries.

Implemented:
- Added regression coverage:
  - `tests/unit/test_backfill_ml_features_selection.py` asserts ordered query contract and limit parameter flow.
- Updated `src/orion/jobs/backfill_ml_features.py` candidate query to include:
  - `ORDER BY p.entry_ts ASC, p.event_id ASC` before `LIMIT`.

Result:
- backfill batch iteration is now stable and repeatable, reducing missed/duplicated candidate churn when processing in slices.

Residual:
- full resumability still depends on explicit progress watermarking/mark-state strategy; deterministic ordering is a prerequisite hardening step, not the final idempotency model.

## 185) Pass 178 Continuation (2026-02-08)

### 185.1 Backfill Pagination Watermark Added for Forward Progress (TDD-Backed)

Finding:
- even with deterministic ordering, repeated `LIMIT`-window fetches could still revisit earlier rows without explicit cursor progression, especially under partial-update/error scenarios.

Implemented:
- Added regression coverage in `tests/unit/test_backfill_ml_features_selection.py` for:
  - cursor predicate SQL contract in `get_records_to_backfill(...)`,
  - run-loop pagination behavior (`run_backfill`) that advances cursor arguments between fetches.
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - `get_records_to_backfill` now accepts optional cursor args (`after_entry_ts`, `after_event_id`) and applies keyset predicate:
    - `p.entry_ts > :after_entry_ts OR (p.entry_ts = :after_entry_ts AND p.event_id > :after_event_id)`.
  - `run_backfill` now carries a per-run cursor based on last row of each fetched page to ensure monotonic traversal through the candidate set.

Result:
- backfill now has stable forward traversal within a run, reducing re-fetch churn and improving progress guarantees under large candidate sets.

Residual:
- this remains an in-memory run cursor; crash-safe resumability still requires persisted watermark/checkpoint state if strict exactly-once backfill semantics are needed.

## 186) Pass 179 Continuation (2026-02-08)

### 186.1 Backfill Fetch-Budget Contraction Added to Eliminate Truncation Overshoot (TDD-Backed)

Finding:
- `run_backfill` requested full `batch_size` on every page fetch even when only a smaller process budget remained in the current run (`limit - total_processed`),
- this allows page over-fetch relative to remaining budget and couples cursor state to rows that may never need to be processed in that run slice.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_selection.py` with:
  - `test_run_backfill_requests_only_remaining_budget` (red/green) to enforce per-iteration fetch-limit contraction.
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - computes `remaining = limit - total_processed` each loop,
  - requests `get_records_to_backfill(limit=min(batch_size, remaining), ...)`,
  - advances cursor from each processed row instead of pre-setting from fetched-page tail.

Result:
- backfill pagination now aligns fetch volume with the exact remaining process budget for the run,
- removes page over-fetch/truncation risk and hardens cursor semantics around processed progress.

Residual:
- restart continuity is still in-memory for a single run; persisted checkpoints/watermarks remain the next required step for crash-safe resumability across process restarts.

## 187) Pass 180 Continuation (2026-02-08)

### 187.1 Backfill Crash-Resume Watermarking Added (TDD-Backed)

Finding:
- backfill traversal was monotonic within a single process, but restart continuity was still in-memory only, requiring reruns from the beginning after process interruptions.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_selection.py` with:
  - `test_get_records_to_backfill_supports_timestamp_only_cursor_filter`
  - `test_run_backfill_resumes_from_watermark_and_persists_progress`
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - added persisted watermark helpers using existing `ingest_watermarks` utilities,
  - loads startup watermark for resume cursor initialization,
  - supports timestamp-only cursor predicate (`p.entry_ts >= :after_entry_ts`) when event-id cursor is unavailable,
  - persists watermark progression during row processing.

Result:
- backfill now resumes from the latest persisted entry timestamp after restarts, reducing full-run replay and improving operational continuity.

Residual:
- persisted state is timestamp-only; strict no-duplicate cursor continuity across identical `entry_ts` cohorts still requires durable keyset state (`entry_ts` + `event_id`).

## 188) Pass 181 Continuation (2026-02-08)

### 188.1 `backfill_exit_columns` Partial-Row Selection Gaps Remediated (TDD-Backed)

Finding:
- velocity backfill candidate selection only targeted `time_to_75_pct_seconds`, which could miss rows where 75% velocity was present but 100%/150% remained null,
- checkpoint backfill candidate selection only targeted `price_at_15m`, which could miss partially-filled rows in other checkpoint columns.

Implemented:
- Added `tests/unit/test_backfill_exit_columns_selection.py` with coverage for:
  - velocity candidate selector including all three velocity fields,
  - checkpoint selector including all checkpoint price/return fields,
  - deterministic ordering contract on both queries.
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - widened velocity selector predicate to include null checks for 75/100/150 targets with corresponding hit timestamps,
  - widened checkpoint selector predicate to include all price/return checkpoint null checks,
  - added `ORDER BY entry_ts ASC, event_id ASC` before `LIMIT` in both selectors.

Result:
- backfill now recovers partially-populated velocity/checkpoint rows instead of silently skipping them due single-anchor filters.

Residual:
- selector correctness is fixed, but the job still fetches a single capped page (`LIMIT`) per phase without iterative pagination/watermark progression; very large backlogs can require repeated runs.

## 189) Pass 182 Continuation (2026-02-08)

### 189.1 `backfill_exit_columns` Single-Page Phase Limits Replaced with Keyset Pagination (TDD-Backed)

Finding:
- both velocity and checkpoint phases in `run_backfill` performed a single selector call with `LIMIT`, then processed only that one page,
- this created truncation behavior for large backlogs and required repeated job reruns for complete processing.

Implemented:
- Extended `tests/unit/test_backfill_exit_columns_selection.py` with:
  - cursor-filter SQL contract tests for both selectors,
  - run-loop pagination behavior test validating:
    - per-iteration `limit=min(batch_size, remaining)`,
    - cursor advancement across pages for both phases.
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - selectors now accept optional keyset cursor args (`after_entry_ts`, `after_event_id`),
  - selectors apply cursor predicates and preserve deterministic ordering (`entry_ts`, `event_id`),
  - run loop now paginates both phases until exhaustion or phase limit.

Result:
- exit-column backfills now traverse candidate sets deterministically across multiple pages within a single run, instead of stopping after one capped query page.

Residual:
- unlike ML backfill, exit-column phases still do not persist phase watermarks across process restarts; crash-safe resume remains an open follow-up.

## 189) Pass 182 Continuation (2026-02-08)

### 189.1 Guardrail Fail-Fast Escalation Granularity Added (TDD-Backed)

Finding:
- fail-fast escalation policy for structured guardrail check failures was global-only (`ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES`), so operators could not enforce strict stop behavior for high-criticality jobs while leaving others in alert-only mode.

Implemented:
- Extended `tests/unit/test_quality_guardrails_results.py` with:
  - `test_run_job_raises_when_job_is_listed_for_fail_fast`
  - `test_run_job_does_not_raise_when_job_not_listed_for_fail_fast`
- Updated `src/orion/jobs/quality_guardrails.py`:
  - added `_env_csv()` parser for comma-separated env values,
  - added `_fail_fast_enabled_for_job(name)` policy helper,
  - wired new env contract `ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES_JOBS`,
  - updated `_run_job()` to apply per-job fail-fast escalation while preserving global override behavior from `ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES`.

Result:
- guardrail escalation can now be targeted by job class (for example fail-fast only on `feature_sanity_validation` while keeping reconciliation/data-quality in alert-only mode),
- operational rollout can be stricter where required without globally forcing process exits for all guardrail failures.

Residual:
- failure retry cadence remains loop-driven without adaptive per-job backoff/jitter controls under prolonged outage conditions.

## 190) Pass 183 Continuation (2026-02-08)

### 190.1 Guardrail Failure Backoff Window Added (TDD-Backed)

Finding:
- after retry-semantics correction and per-job fail-fast controls, failed guardrail jobs could still rerun immediately on every loop tick during prolonged dependency outages, producing noisy repeated failures.

Implemented:
- Extended `tests/unit/test_quality_guardrails.py` with:
  - `test_failure_backoff_elapsed_true_without_failure_timestamp`
  - `test_failure_backoff_elapsed_respects_backoff_window`
- Updated `src/orion/jobs/quality_guardrails.py`:
  - added `_env_nonneg_int()` for non-negative scheduler env parsing,
  - added `_failure_backoff_elapsed(last_failure, backoff_seconds, now)` helper,
  - added `ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS` env contract (default `0`),
  - tracked per-job failure timestamps and gated reruns until the failure backoff window elapses.

Result:
- guardrail scheduler now throttles repeated failed runs under outage conditions, reducing alert/log storm behavior while preserving normal interval-driven execution when healthy.

Residual:
- backoff policy is currently global across guardrail jobs; per-job backoff tuning and jitter remain future hardening options if differentiated retry pacing is required.

## 191) Pass 184 Continuation (2026-02-08)

### 191.1 Guardrail Per-Job Backoff Overrides Added (TDD-Backed)

Finding:
- failure backoff support was global-only, preventing differentiated retry pacing across guardrail classes with different operational criticality and dependency profiles.

Implemented:
- Extended `tests/unit/test_quality_guardrails.py` with:
  - `test_job_failure_backoff_seconds_uses_global_default_when_not_configured`
  - `test_job_failure_backoff_seconds_uses_job_specific_override`
- Updated `src/orion/jobs/quality_guardrails.py`:
  - added `_env_job_nonneg_int_map()` parser for `job=seconds` env entries,
  - added `_job_failure_backoff_seconds(name, default_seconds)` resolver,
  - wired per-job backoff resolution for `reconciliation`, `data_quality_checker`, and `feature_sanity_validation`,
  - added env contract `ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS`.

Result:
- operators can now retain a global default backoff while overriding retry windows per guardrail job, reducing alert noise where needed without slowing all checks uniformly.

Residual:
- scheduler currently evaluates a static per-process backoff map from env at startup; runtime hot-reload for backoff policy changes remains future operational hardening.

## 191) Pass 184 Continuation (2026-02-08)

### 191.1 `backfill_exit_columns` Crash-Resume Watermarking Added (TDD-Backed)

Finding:
- `backfill_exit_columns` had deterministic keyset pagination, but restart continuity was still in-memory only for both phases; process restarts resumed from phase start and could re-scan large historical ranges.

Implemented:
- Extended `tests/unit/test_backfill_exit_columns_selection.py` with:
  - `test_get_records_to_backfill_supports_timestamp_only_cursor_filter`
  - `test_get_all_records_for_checkpoints_supports_timestamp_only_cursor_filter`
  - `test_run_backfill_resumes_from_phase_watermarks_and_persists_progress`
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - added per-phase watermark keys for velocity and checkpoint phases,
  - added load/save helpers backed by existing `ingest_watermarks` storage utilities,
  - initialized both phase cursors from persisted watermarks at startup,
  - persisted watermark progression during per-record processing in each phase.

Result:
- exit-column backfills now resume from the latest persisted phase timestamps after restarts, reducing repeated full-range scans and improving operational continuity for large backlogs.

Residual:
- resume state is timestamp-only; strict duplicate-free continuity across shared `entry_ts` cohorts still requires durable keyset state (`entry_ts` + `event_id`) per phase.

## 192) Pass 186 Continuation (2026-02-09)

### 192.1 `backfill_exit_columns` Durable Keyset Resume State Added (TDD-Backed)

Finding:
- resume continuity for `backfill_exit_columns` was timestamp-only, so restarts during shared `entry_ts` cohorts could replay same-timestamp rows and produce duplicate processing churn.

Implemented:
- Extended `tests/unit/test_backfill_exit_columns_selection.py` with:
  - `test_run_backfill_resumes_with_keyset_cursor_when_available`
- Added durable cursor state primitives:
  - `src/orion/storage/models.py`: new `job_cursor_state` table (`key`, `last_seen_ts_utc`, `last_seen_id`)
  - `src/orion/storage/watermarks.py`: `get_cursor_state(...)`, `upsert_cursor_state(...)`
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - per-phase cursor keys for velocity and checkpoint phases,
  - startup resume now prefers keyset cursor (`entry_ts` + `event_id`) when available,
  - per-record progress now persists keyset cursor state,
  - retained backward-compatible fallback to legacy timestamp-only watermarks.

Result:
- exit backfill restart behavior now supports strict keyset continuation, reducing duplicate replay risk across same-timestamp candidate cohorts.

Residual:
- after rollout stabilization, legacy timestamp-only watermark keys can be cleaned up in a controlled migration to simplify cursor-state ownership.

## 193) Pass 187 Continuation (2026-02-09)

### 193.1 Guardrail Backoff Policy Runtime Hot-Reload Added (TDD-Backed)

Finding:
- per-job backoff overrides existed, but scheduler resolved them only once at startup, so env changes required a process restart to take effect.

Implemented:
- Extended `tests/unit/test_quality_guardrails.py` with:
  - `test_resolve_job_failure_backoff_policy_uses_global_default`
  - `test_resolve_job_failure_backoff_policy_reloads_env_each_call`
- Updated `src/orion/jobs/quality_guardrails.py`:
  - added `_resolve_job_failure_backoff_policy(default_seconds)` helper,
  - moved per-job backoff-policy resolution into the main scheduler loop per iteration,
  - preserved existing global default + per-job override semantics.

Result:
- runtime edits to `ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS` are now observed on subsequent loop ticks without restarting `quality_guardrails`.

Residual:
- env parsing now occurs each loop iteration; if loop cadence is reduced significantly, consider caching + change-detection to avoid unnecessary parse work.

## 194) Pass 188 Continuation (2026-02-09)

### 194.1 Guardrail Backoff Policy Cache + Change Detection Added (TDD-Backed)

Finding:
- runtime hot-reload remediated restart dependency, but scheduler still reparsed backoff env config on every loop iteration even when unchanged.

Implemented:
- Extended `tests/unit/test_quality_guardrails.py` with:
  - `test_resolve_job_failure_backoff_policy_cached_reuses_policy_when_env_unchanged`
  - `test_resolve_job_failure_backoff_policy_cached_rebuilds_on_env_change`
- Updated `src/orion/jobs/quality_guardrails.py`:
  - added `_parse_job_nonneg_int_map(raw, env_name)` parser helper,
  - added `_resolve_job_failure_backoff_policy_cached(default_seconds, cached_raw, cached_policy)` cache-aware resolver,
  - updated `run_guardrail_loop()` to hold cached raw/policy state and only rebuild policy when env input changes.

Result:
- scheduler now preserves runtime hot-reload behavior while avoiding redundant parse work when policy env remains unchanged across ticks.

Residual:
- configuration still relies on polling env state each loop; if stronger dynamic config controls are needed, migrate policy source to a centralized runtime config table/watch channel.

## 197) Pass 191 Continuation (2026-02-09)

### 197.1 Guardrail Backoff Policy Moved to Centralized Runtime Config Table (TDD-Backed)

Finding:
- guardrail backoff policy remained env-driven even after hot-reload + cache controls, limiting centralized runtime control and requiring env mutation as the operator interface.

Implemented:
- Added `runtime_config` model in `src/orion/storage/models.py` for centralized key/value JSON runtime settings.
- Extended `tests/unit/test_quality_guardrails.py` with:
  - `test_runtime_backoff_policy_from_value_parses_and_clamps`
  - `test_runtime_backoff_policy_from_value_returns_none_for_unusable_payload`
  - `test_resolve_runtime_backoff_policy_cached_reuses_when_updated_ts_unchanged`
- Updated `src/orion/jobs/quality_guardrails.py`:
  - added DB-backed loaders/resolvers for key `quality_guardrails.backoff_seconds_jobs`,
  - added runtime-config payload normalization for per-job backoff policy,
  - scheduler now prefers valid DB runtime policy and falls back to env-based policy when DB config is absent/invalid.

Result:
- backoff policy can now be centrally controlled through a durable DB table entry, while preserving safe env fallback behavior.

Residual:
- runtime policy is still poll-based (loop tick + DB read); move to push/watch invalidation for lower latency and lower steady-state query overhead if needed.

## 195) Pass 189 Continuation (2026-02-09)

### 195.1 `backfill_ml_features` Durable Keyset Resume State Added (TDD-Backed)

Finding:
- `backfill_ml_features` resume continuity persisted only timestamp watermarks; restart during shared `entry_ts` cohorts could replay same-timestamp rows because `event_id` ordering state was not durable.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_selection.py` with:
  - `test_run_backfill_resumes_with_keyset_cursor_when_available`
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - added `BACKFILL_CURSOR_KEY` and durable cursor load/save helpers using `job_cursor_state`,
  - startup resume now loads `entry_ts` + `event_id` keyset cursor when available,
  - retained fallback to legacy timestamp watermark for backward compatibility,
  - per-record progress now persists both keyset cursor state and legacy timestamp watermark.

Result:
- ML feature backfill restart behavior now supports strict keyset continuation, reducing duplicate replay risk across same-timestamp candidate cohorts.

Residual:
- after rollout stabilization, timestamp-only watermark fallback for this job can be retired to simplify resume-state ownership.

## 196) Pass 190 Continuation (2026-02-09)

### 196.1 `main_price_target_labeler` Underlying-Price Lookup Started on Heber Bars (TDD-Backed)

Finding:
- core underlying-price context in price-target labeling was still sourced directly from Orion-local `silver_alpaca_bars`, increasing migration friction and local SQL dependency.

Implemented:
- Added `tests/unit/test_price_target_labeler_heber_bars.py` with coverage for:
  - Heber-first lookup in `get_underlying_price_at_entry(...)`,
  - SQL fallback when Heber bars are unavailable,
  - Heber-first lookup in `get_underlying_price_at_offset(...)`.
- Updated `src/orion/main_price_target_labeler.py`:
  - introduced Heber reader wiring and `_get_heber_close_at_or_before(...)` helper,
  - routed entry/offset underlying-price reads through Heber-first lookup with existing SQL fallback preserved.

Result:
- the price-target labeler now has an active Heber-backed read path for underlying-price context, reducing direct reliance on local bar tables for two frequently used accessors.

Residual:
- broader `main_price_target_labeler` feature and label calculations still query multiple Orion-local silver tables; remaining functions require phased migration to Heber datasets/access facades.

## 197) Pass 191 Continuation (2026-02-09)

### 197.1 `main_price_target_labeler` Heber-First Flow Candidate + Price Reads Added (TDD-Backed)

Finding:
- `main_price_target_labeler` still depended directly on `silver_uw_flow` for both:
  - unlabeled entry candidate discovery (`get_entry_signals`), and
  - subsequent option price series lookup (`get_subsequent_prices`).

Implemented:
- Added `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_flow.py` with coverage for:
  - Heber-first candidate sourcing in `get_entry_signals(...)`,
  - SQL fallback when Heber flow is empty,
  - Heber-first subsequent price sourcing in `get_subsequent_prices(...)`,
  - SQL fallback when Heber flow schema is incompatible.
- Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
  - introduced Heber flow normalization helpers for candidate extraction and eligibility gating,
  - split SQL logic into explicit fallback helpers (`_get_entry_signals_sql`, `_get_subsequent_prices_sql`),
  - routed both read paths through Heber-first logic with fallback retention.

Result:
- two high-frequency read paths in price-target labeling now consume Heber flow data first, reducing direct coupling to Orion-local `silver_uw_flow` while preserving operational safety through SQL fallbacks.

Residual:
- large portions of feature enrichment in `main_price_target_labeler` still query local silver context tables (`silver_market_tide`, `silver_greek_exposure`, `silver_max_pain`, `silver_iv_rank`) and require further staged migration.

## 198) Pass 192 Continuation (2026-02-09)

### 198.1 Combined Backfill Legacy Watermark Fallback Retirement (TDD-Backed)

Finding:
- both `backfill_ml_features` and `backfill_exit_columns` had already adopted durable keyset cursor state, but still retained legacy timestamp-watermark fallback reads/writes, creating dual-state complexity and unnecessary writes.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_selection.py` with:
  - `test_load_backfill_cursor_does_not_fallback_to_legacy_watermark`
  - `test_save_backfill_cursor_does_not_write_legacy_watermark`
- Extended `tests/unit/test_backfill_exit_columns_selection.py` with:
  - `test_load_phase_cursors_do_not_fallback_to_legacy_watermarks`
  - `test_save_phase_cursors_do_not_write_legacy_watermarks`
- Updated both jobs:
  - `src/orion/jobs/backfill_ml_features.py`
  - `src/orion/jobs/backfill_exit_columns.py`
  - removed watermark fallback on cursor load and watermark writes on cursor save,
  - now rely exclusively on `job_cursor_state` keyset cursor (`entry_ts` + `event_id`) for resume continuity.

Result:
- resume-state ownership is simplified to a single durable cursor mechanism across both jobs, reducing state divergence risk and maintenance complexity.

Residual:
- run a one-time cleanup migration to remove obsolete legacy watermark keys from `ingest_watermarks` for retired backfill paths.

## 199) Pass 193 Continuation (2026-02-09)

### 199.1 One-Time Legacy Backfill Watermark Cleanup Path Implemented (TDD-Backed)

Finding:
- after retiring legacy timestamp-watermark fallback reads/writes for `backfill_ml_features` and `backfill_exit_columns`, obsolete key rows in `ingest_watermarks` remained as cleanup debt.

Implemented:
- Added `tests/unit/test_storage_watermarks_cleanup.py` covering delete-helper behavior for:
  - empty-key no-op,
  - no-match count-only path,
  - matching-row delete path.
- Added `tests/unit/test_cleanup_legacy_backfill_watermarks.py` covering:
  - cleanup delete path for known legacy keys,
  - dry-run count path with delete suppression.
- Added `src/orion/jobs/cleanup_legacy_backfill_watermarks.py`:
  - defines `LEGACY_BACKFILL_WATERMARK_KEYS`,
  - exposes `cleanup_legacy_backfill_watermarks(dry_run=...)`,
  - supports direct execution (`python -m orion.jobs.cleanup_legacy_backfill_watermarks [--dry-run]`).
- Updated `src/orion/storage/watermarks.py`:
  - added `delete_watermarks(session, keys)` helper for targeted multi-key cleanup,
  - tightened `upsert_watermark(...)` timezone typing/validation guard.

Result:
- obsolete backfill watermark rows can now be audited (`--dry-run`) and removed deterministically via a dedicated, test-covered cleanup path.

Residual:
- once executed in each environment, document completion evidence (row counts before/after) in operations runbook and retire any now-obsolete manual SQL cleanup notes.

## 200) Pass 194 Continuation (2026-02-09)

### 200.1 Legacy Watermark Cleanup Operational Evidence Runbook Added

Finding:
- cleanup execution path existed, but operator evidence capture (pre/post row snapshots + dry-run count logging) was not yet formalized in runbooks.

Implemented:
- Updated `docs/runbooks/database_ops.md` with:
  - explicit SQL for legacy-key verification in `ingest_watermarks`,
  - dry-run and execution commands for `python -m orion.jobs.cleanup_legacy_backfill_watermarks`,
  - evidence checklist for before/after capture in ops logs.
- corrected watermark table references in the same runbook to `ingest_watermarks` for consistency with actual storage model.

Result:
- operations now have a deterministic, documented procedure to execute and prove completion of legacy watermark cleanup across environments.

Residual:
- execute the documented procedure in each active environment and archive evidence artifacts in the team’s runbook/change-management system.

## 201) Pass 195 Continuation (2026-02-09)

### 201.1 Combined `main_price_target_labeler` Heber-First Context Migration (GEX + Market Tide)

Finding:
- after prior Heber-first migration of entry candidates and underlying prices, `main_price_target_labeler` still relied on local SQL context reads for:
  - `silver_greek_exposure` (`get_gex_at_entry`),
  - `silver_market_tide` (`get_market_tide_before_entry`).

Implemented:
- Added `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_price_target_labeler_heber_context.py` to enforce:
  - Heber-first GEX retrieval with SQL fallback,
  - Heber-first market-tide aggregation with SQL fallback.
- Extended `/Users/jacobmcmillan/Empire/Orion/src/orion/clients/heber_reader.py` with:
  - `read_greek_exposure(...)`,
  - `read_market_tide(...)`,
  - generic time-range filtering support for `ts_utc`.
- Updated `/Users/jacobmcmillan/Empire/Orion/src/orion/main_price_target_labeler.py`:
  - introduced Heber-first helpers for both context lookups,
  - split legacy SQL logic into explicit fallback helpers,
  - preserved fallback behavior to avoid regression during phased rollout.
- Extended `/Users/jacobmcmillan/Empire/Orion/tests/unit/test_heber_reader.py` with coverage for new Heber reader methods.

Result:
- four high-use labeler read paths now run Heber-first (`entry_signals`, `subsequent_prices`, `underlying_bars`, `gex`, `market_tide`), materially reducing direct coupling to Orion-local silver SQL tables.

Residual:
- remaining major SQL-coupled context in `main_price_target_labeler` includes `silver_max_pain` and `silver_iv_rank`; these should be the next combined migration slice.

## 202) Pass 196 Continuation (2026-02-09)

### 202.1 Market-Tide Heber Path Hardened with Flow-Derived Fallback + Regime Reuse (TDD-Backed)

Finding:
- `market_tide` Heber-first path existed, but relied on a single aggregate dataset read.
- when aggregate parquet shape is incompatible/missing, labeler still fell through to SQL and `get_regime_at_entry(...)` independently queried local `silver_market_tide`, preserving migration coupling and reducing resilience.

Implemented:
- Added `tests/unit/test_price_target_labeler_heber_market_tide.py` covering:
  - aggregate market-tide net reconstruction from Heber rows,
  - flow-derived net reconstruction fallback from Heber flow (`premium_usd` + `put_call`) when aggregate data path is unavailable,
  - Heber-first market-tide behavior for `get_market_tide_before_entry(...)`,
  - Heber-first tide injection into `get_regime_at_entry(...)` before SQL fallback.
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_sum_market_tide_from_dataframe(...)` as canonical tide-net aggregator,
  - added `_get_heber_market_tide_net_premium(...)` with two-step strategy:
    - attempt `read_market_tide(...)` first,
    - fallback to `read_flow(...)` net reconstruction when needed,
  - routed both `get_market_tide_before_entry(...)` and `get_regime_at_entry(...)` through the same Heber tide helper before invoking SQL fallback.

Result:
- market-tide context is now more resilient to aggregate-dataset availability issues and shared across both feature extraction and regime detection, reducing steady-state dependence on local `silver_market_tide`.

Residual:
- remaining SQL-coupled labeler context still includes `silver_max_pain` and `silver_iv_rank`; these remain the next combined migration target.

## 203) Pass 197 Continuation (2026-02-09)

### 203.1 `main_price_target_labeler` Heber-First Max-Pain + IV-Rank Paths Added (TDD-Backed)

Finding:
- `main_price_target_labeler` still queried local SQL tables for:
  - max-pain distance (`silver_max_pain`) via `get_max_pain_distance(...)`,
  - IV-rank offset lookups (`silver_iv_rank`) via `get_iv_at_offset(...)`.

Implemented:
- Added `tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py` to validate:
  - Heber-first max-pain lookup with SQL fallback behavior,
  - Heber-first IV-rank offset lookup with SQL fallback behavior.
- Extended `tests/unit/test_heber_reader.py` with Heber dataset read coverage for:
  - `read_max_pain(...)`,
  - `read_iv_rank(...)`.
- Updated `src/orion/clients/heber_reader.py`:
  - added silver dataset readers for `max_pain` and `iv_rank`,
  - tightened `_read_parquet(...)` return typing via DataFrame casts for mypy compliance.
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_get_max_pain_distance_from_heber(...)` and SQL fallback split helper,
  - added `_get_iv_rank_from_heber(...)` and SQL fallback split helper,
  - routed `get_max_pain_distance(...)` and `get_iv_at_offset(...)` through Heber-first access.

Result:
- previously residual SQL-coupled max-pain and IV-rank context reads now execute Heber-first with explicit compatibility fallback, reducing local table coupling in feature construction paths.

Residual:
- broader labeler SQL dependencies still remain (for example `silver_vix_data`, portions of ticker/market context joins), and should continue to be migrated in incremental, test-backed slices.

## 204) Pass 198 Continuation (2026-02-09)

### 204.1 Combined Execution Exit-Policy Contract Remediation (Options Scope + Position Rehydration)

Findings addressed in this pass:
- `PriceTargetExitRule` contract expected `entry_option_price`, but tracked positions only persisted `entry_price` (`src/orion/processing/rules/exit_rules.py`, `src/orion/execution/position_manager.py`).
- option-position identity was sourced from legacy evidence/context instead of canonical `candidate.option_symbol`, weakening DTE/contract-scoped rule behavior (`src/orion/execution/position_manager.py`).
- `PositionManager.initialize()` limited reconstructed open positions to latest 50 rows, leaving older open positions unmanaged by exit loops (`src/orion/execution/position_manager.py`).
- options-only exit policy was applied to all open positions, including non-option equities (`src/orion/main_execution.py`, `src/orion/processing/rules/exit_rules.py`).

Implemented (TDD-backed):
- Added `tests/unit/test_position_manager_execution_contracts.py` enforcing:
  - canonical option-chain propagation from `candidate.option_symbol`,
  - `entry_option_price` persistence on tracked option positions,
  - startup reconstruction of books larger than 50 open positions.
- Added `tests/unit/test_main_execution_exit_scope.py` enforcing:
  - options-only exit-rule applicability guard behavior.
- Updated `src/orion/execution/position_manager.py`:
  - added `OpenPosition.entry_option_price`,
  - introduced canonical option-chain resolver precedence (`candidate.option_symbol` -> runtime context -> evidence),
  - removed fixed `.limit(50)` from open-position rehydration query.
- Updated `src/orion/main_execution.py`:
  - added `_should_apply_options_exit_rules(...)`,
  - now skips options-rule evaluation for non-option positions.

Result:
- exit-rule prerequisites for option-price target logic are now satisfied in tracked position state,
- contract identity for options is canonicalized at position creation/rehydration,
- startup monitoring scope is no longer hard-capped to 50 positions,
- options-only rule family no longer runs against equity positions by default.

Residual:
- flow-level contract scoping inside individual exit rules remains broader than strict contract matching (ticker-level flow still feeds rule evaluation), and should be addressed in a dedicated follow-up slice.

## 205) Pass 199 Continuation (2026-02-09)

### 205.1 Exit-Rule Input Flow Is Now Contract-Scoped for Option Positions

Finding:
- even after options-only position gating, `main_execution` still passed ticker-wide `recent_flow` directly into every exit rule.
- this allowed same-underlying, different-contract flow to influence rule outcomes for a tracked option position.

Implemented (TDD-backed):
- Extended `tests/unit/test_main_execution_exit_scope.py` with:
  - contract-matching flow filter behavior for option positions,
  - pass-through behavior for non-option positions.
- Updated `src/orion/main_execution.py`:
  - added `_scope_recent_flow_for_position(position, recent_flow)` helper,
  - for option positions, keeps only rows whose `flow.option_chain` equals `position.option_chain`,
  - feeds scoped flow into `rule.should_exit(...)` loop.

Result:
- contract-level exit decisions are less exposed to unrelated same-ticker flow noise,
- execution behavior now aligns better with options-contract intent in dynamic exit policy.

Residual:
- rule internals still primarily use flow-side heuristics (aggressor/put_call/premium) and do not yet enforce explicit expiry/strike neighborhood policies when `option_chain` is missing on flow rows.

## 206) Pass 200 Continuation (2026-02-09)

### 206.1 Exit-Flow Contract Scoping Hardened for Missing `option_chain` Rows

Finding:
- prior pass enforced exact `flow.option_chain == position.option_chain`, but dropped rows where `option_chain` was absent even when equivalent contract metadata (`expiry`, `strike`, `put_call`) existed.
- this could undercount relevant flow in partial-normalization windows and weaken exit signal fidelity.

Implemented (TDD-backed):
- Extended `tests/unit/test_main_execution_exit_scope.py` with:
  - component-match include case (`expiry/strike/put_call` exact contract),
  - mismatch rejection cases across expiry, strike, and option side,
  - strike-string tolerance case (e.g. `"200"`).
- Updated `src/orion/main_execution.py`:
  - added `_parse_option_chain_contract(...)` for OCC decomposition to `(expiry, put_call, strike)`,
  - added `_flow_matches_contract_components(...)`,
  - enhanced `_scope_recent_flow_for_position(...)` to apply component fallback matching when flow-side `option_chain` is missing.

Result:
- contract scoping now remains effective under partial flow payloads where `option_chain` is absent but core contract fields are present,
- reduces both false excludes (relevant contract flow dropped) and false includes (unrelated same-ticker flow retained).

Residual:
- rows missing both `option_chain` and contract components are still excluded for option positions (intentional conservative behavior); upstream normalization quality remains a dependency.

## 207) Pass 201 Continuation (2026-02-09)

### 207.1 Gateway WebSocket URL Canonicalization + Failed-Handshake Cleanup Remediated

Finding:
- `GatewayStreamClient` previously built websocket URL by appending `"/ws"` to provided base URL, which broke when `DATA_GATEWAY_URL` included `/api/v1` (yielding `/api/v1/ws` instead of `/ws`).
- failed auth path returned `False` without explicit websocket close/handle reset, leaving stale state risk in reconnect loops.

Implemented (TDD-backed):
- Added `tests/unit/test_gateway_stream_client_contract.py` validating:
  - stable `ws_url` derivation for URL variants (`host`, `http(s)://host`, `ws(s)://host`, and `/api/v1`-suffixed forms),
  - failed-auth cleanup semantics (`close()` called, `_websocket` cleared, `_authenticated` reset).
- Updated `src/orion/connectors/gateway_stream_client.py`:
  - added `_normalize_ws_url(...)` with scheme mapping and `/api/v1` suffix stripping before websocket path composition,
  - added `_cleanup_failed_connection(...)`,
  - invoked cleanup in both auth-failure and exception paths of `connect()`.

Result:
- websocket endpoint construction is now consistent with Gateway router contract (`/ws`) across common deployment URL shapes,
- failed connection handshakes no longer retain stale websocket/auth state.

Residual:
- this pass hardens client-side contract handling; end-to-end reconnect soak under real Gateway load is still recommended to validate operational behavior at scale.

## 204) Pass 198 Continuation (2026-02-09)

### 204.1 `main_price_target_labeler` Heber-First VIX Proxy Regime Path Added (TDD-Backed)

Finding:
- `get_regime_at_entry(...)` still sourced VIX context from SQL (`silver_vix_data` with `silver_alpaca_bars` fallback), keeping regime detection coupled to local Orion tables.

Implemented:
- Added `tests/unit/test_price_target_labeler_heber_vix_proxy.py` to validate:
  - Heber VIX proxy snapshot derivation from VIXY bars at-or-before entry,
  - Heber-first regime path behavior in `get_regime_at_entry(...)`,
  - SQL fallback behavior when Heber VIX proxy data is unavailable.
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_map_vix_proxy_to_regime(...)`,
  - added `_get_heber_vix_proxy_snapshot_at_or_before(...)`,
  - routed `get_regime_at_entry(...)` to use Heber VIX proxy first, preserving existing SQL fallback for compatibility.

Result:
- regime feature construction now has an active Heber-first VIX path, reducing steady-state dependence on `silver_vix_data`/`silver_alpaca_bars` SQL reads while keeping migration safety through fallback.

Residual:
- SQL fallback remains enabled and should be retired after Heber VIX data completeness validation in production-like soak runs.
- next audit/remediation slice should target contract-level validation under load (Gateway e2e schema/retry behavior + SQLite durability/contention).

## 208) Pass 202 Continuation (2026-02-09)

### 208.1 SQLite Lock-Contention Retry + Soak Harness Remediation (TDD-Backed)

Finding:
- `db_transaction(...)` failed fast on transient SQLite lock contention (`database is locked`/`SQLITE_BUSY`) with no bounded retry behavior.
- there was no dedicated contract-level harness to validate write-attempt accounting and successful-write persistence under concurrent SQLite contention.

Implemented:
- Added `tests/unit/test_db_utils_sqlite_retry.py` covering:
  - retry-and-succeed behavior on transient SQLite lock contention,
  - non-retry behavior for non-lock errors,
  - non-retry behavior for non-SQLite dialects,
  - retry-budget exhaustion behavior.
- Updated `src/orion/shared/db_utils.py`:
  - added bounded SQLite lock retry support in `db_transaction(...)`,
  - added retry config env vars:
    - `ORION_SQLITE_LOCK_RETRY_ATTEMPTS`,
    - `ORION_SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS`,
    - `ORION_SQLITE_LOCK_RETRY_MAX_DELAY_SECONDS`,
  - implemented exponential backoff with max-delay clamp and retryability guards scoped to SQLite lock signatures.
- Added `src/orion/jobs/sqlite_contention_soak.py`:
  - `run_sqlite_contention_soak(...)` concurrent write harness with summary metrics (`attempted_writes`, `successful_writes`, `failed_writes`, `final_counter_value`, elapsed),
  - CLI entrypoint (`python -m orion.jobs.sqlite_contention_soak`),
  - uses `orion_soak_counter` table (avoids reserved `sqlite_*` internal namespace).
- Added `tests/unit/test_sqlite_contention_soak.py` validating consistency between attempted/success/failure totals and persisted counter value.

Verification:
- `uv run pytest -q tests/unit/test_db_utils_sqlite_retry.py tests/unit/test_sqlite_contention_soak.py` passed.
- `uv run ruff check src/orion/shared/db_utils.py src/orion/jobs/sqlite_contention_soak.py tests/unit/test_db_utils_sqlite_retry.py tests/unit/test_sqlite_contention_soak.py` passed.

Result:
- Orion now has bounded, test-covered resilience for transient SQLite lock contention in shared transaction helpers.
- a deterministic soak harness is available to quantify contention behavior and validate retry outcomes before and after config tuning.

Residual:
- execute longer-duration soak runs in production-like conditions and record retry/failure ratios for baseline thresholds.
- next contract-under-load slice remains Gateway end-to-end schema/error/retry validation against a live Data-Gateway instance.

## 209) Pass 203 Continuation (2026-02-09)

### 209.1 Heber Catalog URL-Shape Contract Hardening (TDD-Backed)

Finding:
- `HeberReader` catalog requests were sensitive to `httpx.Client(base_url=...)` path shape.
- with `/api/v1`-suffixed client base URLs, path-join behavior could produce incorrect requests (for example, duplicate `/api/v1` segments) and failed health/dataset checks.

Implemented:
- Extended `tests/unit/test_heber_reader.py` to assert canonical endpoint behavior for both:
  - `base_url=http://host`
  - `base_url=http://host/api/v1`
- Updated `src/orion/clients/heber_reader.py`:
  - added catalog-origin URL composition helpers,
  - switched `health_check()` to explicit origin `/health` plus API fallback,
  - switched `list_datasets()` to explicit origin `/api/v1/datasets`.

Verification:
- `pytest -q tests/unit/test_heber_reader.py tests/unit/test_db_utils_sqlite_retry.py tests/unit/test_sqlite_contention_soak.py` passed.

Result:
- Heber catalog health and dataset discovery now behave consistently regardless of caller `httpx` base URL shape.
- this removes another migration footgun during mixed environment rollout where some callers configure host-root URLs and others include `/api/v1`.

Residual:
- continue auditing remaining Heber adoption gaps in `main_price_target_labeler`/`flow_enricher`/backfill jobs where local SQL tables are still used as primary source of truth.

## 210) Pass 204 Continuation (2026-02-09)

### 210.1 Price-Target Labeler Darkpool Context Heber-First Path (TDD-Backed)

Finding:
- `get_darkpool_volume(...)` in `src/orion/main_price_target_labeler.py` was still SQL-only (`silver_uw_darkpool`), unlike other context features already migrated to Heber-first reads.

Implemented:
- Extended `tests/unit/test_price_target_labeler_heber_context.py` with:
  - `test_get_darkpool_volume_prefers_heber_when_available`
  - `test_get_darkpool_volume_falls_back_to_sql_when_heber_empty`
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_get_darkpool_volume_from_heber(...)` with symbol/time-window filtering and robust column mapping (`dark_ts_utc|ts_utc|ts_event`, `size_shares|size|shares|volume`),
  - extracted SQL path into `_get_darkpool_volume_sql(...)`,
  - updated `get_darkpool_volume(...)` to use Heber-first lookup with SQL fallback.

Verification:
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_heber_reader.py` passed.

Result:
- darkpool volume context in price-target labeling is now aligned with the Heber-first migration pattern already used for GEX, market tide, max pain, and IV-rank.
- this reduces direct reliance on Orion-local `silver_uw_darkpool` while preserving backward-compatible fallback behavior.

Residual:
- `main_price_target_labeler` still has other SQL-coupled reads (for example RVOL and sector-correlation sourcing) that need similar Heber-first migration passes.

## 211) Pass 205 Continuation (2026-02-09)

### 211.1 Gateway End-to-End Contract Probe Added + Live Validation Executed (TDD-Backed)

Finding:
- after websocket URL/auth hardening, Orion still lacked a repeatable contract probe to validate Gateway behavior under real runtime conditions (HTTP health, websocket auth/subscription contract, error-code mapping, and data-event schema presence).

Implemented:
- Added `src/orion/jobs/gateway_contract_probe.py`:
  - `run_gateway_contract_probe(...)` for end-to-end checks against a live Gateway instance,
  - health probe with bounded retry (`/health`),
  - websocket auth + subscription contract checks,
  - explicit unknown-action error mapping check (`GW-E3001`),
  - best-effort data-event envelope/schema validation,
  - CLI entrypoint (`python -m orion.jobs.gateway_contract_probe`).
- Added `tests/unit/test_gateway_contract_probe.py` covering:
  - gateway URL normalization behavior,
  - happy-path auth/subscription/error/data-flow contract,
  - health retry behavior,
  - auth-failure summary behavior.
- Added `tests/integration/test_gateway_live_contract_probe.py` (env-gated) for repeatable live checks when `ORION_GATEWAY_LIVE_API_KEY` is present.

Verification:
- `uv run pytest -q tests/unit/test_gateway_contract_probe.py tests/unit/test_gateway_stream_client_contract.py tests/connectors/test_gateway_stream_client.py` passed.
- `ORION_GATEWAY_LIVE_API_KEY=... PYTHONPATH=src uv run pytest -q tests/integration/test_gateway_live_contract_probe.py` passed locally.
- Live probe execution against local Gateway (`http://localhost:8080`) returned:
  - `health_ok=true`,
  - `auth_ok=true`,
  - `subscription_ok=true`,
  - `unknown_action_error_code=GW-E3001`,
  - `data_event_seen=false` (no stream payload observed during short 2s capture window).

Result:
- Orion now has a reusable, test-covered contract probe for Gateway integration that can be run in CI-like environments and during staged migration checks.
- error-code mapping and core websocket contract are now verified against a real Data-Gateway instance, not only mocked unit paths.

Residual:
- to close full stream schema/load parity, run the probe during an active market-data flow window (or with seeded replay feed) and capture at least one `type=data` envelope.
- extend probe to repeated-loop/soak mode for reconnect + transient error behavior under sustained load.

## 211) Pass 205 Continuation (2026-02-09)

### 211.1 Price-Target Labeler RVOL Context Heber-First Path (TDD-Backed)

Finding:
- `get_rvol_metrics(...)` in `src/orion/main_price_target_labeler.py` remained SQL-only (`silver_alpaca_bars`) and therefore outside the Heber-first migration pattern used by other context features.

Implemented:
- Extended `tests/unit/test_price_target_labeler_heber_context.py` with:
  - `test_get_rvol_metrics_prefers_heber_when_available`
  - `test_get_rvol_metrics_falls_back_to_sql_when_heber_empty`
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_get_rvol_metrics_from_heber(...)` to compute hourly/daily/weekly RVOL aggregates from Heber bars,
  - extracted existing SQL logic into `_get_rvol_metrics_sql(...)`,
  - updated `get_rvol_metrics(...)` to use Heber-first lookup with SQL fallback.

Verification:
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k rvol` passed.
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py` passed.

Result:
- RVOL context now follows the same migration pattern as GEX/market-tide/max-pain/IV-rank/darkpool: Heber-first with backward-compatible SQL fallback.

Residual:
- remaining SQL-coupled feature families in `main_price_target_labeler` (notably sector-correlation and some flow-derived context) still require equivalent Heber-first migration slices.

## 212) Pass 206 Continuation (2026-02-09)

### 212.1 Price-Target Labeler Sector/Correlation Context Heber-First Path (TDD-Backed)

Finding:
- `get_sector_correlation_features(...)` in `src/orion/main_price_target_labeler.py` was still SQL-only (sector lookup + SPY return/correlation), leaving a major context feature family outside the Heber-first migration pattern.

Implemented:
- Extended `tests/unit/test_price_target_labeler_heber_context.py` with:
  - `test_get_sector_correlation_features_prefers_heber_when_available`
  - `test_get_sector_correlation_features_falls_back_to_sql_when_heber_empty`
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_get_sector_correlation_features_from_heber(...)` for:
    - 1h sector net premium + direction from Heber flow (with schema-flexible column mapping),
    - 1h SPY return from Heber bars,
    - 5-day ticker/SPY correlation from Heber daily closes,
  - extracted existing SQL behavior into `_get_sector_correlation_features_sql(...)`,
  - updated `get_sector_correlation_features(...)` to run Heber-first and fallback to SQL only when Heber yields no usable features.

Verification:
- `uv run pytest -q tests/unit/test_price_target_labeler_heber_context.py -k sector` passed.
- `uv run pytest -q tests/unit/test_price_target_labeler_heber_context.py` passed.

Result:
- Sector/correlation context now matches the same Heber-first + SQL-fallback migration contract already used for GEX, market tide, max pain, IV-rank, darkpool, and RVOL.

Residual:
- Full completion of `main_price_target_labeler` migration still requires auditing remaining SQL-coupled reads outside context helpers (for example entry sourcing and legacy lookup paths used in some label backfill/update flows).

## 213) Pass 207 Continuation (2026-02-09)

### 213.1 Price-Target Labeler Opposing-Flow Context Heber-First Path (TDD-Backed)

Finding:
- `get_opposing_flow(...)` in `src/orion/main_price_target_labeler.py` was SQL-only against `silver_uw_flow`, leaving an active context feature outside Heber-first parity.

Implemented:
- Extended `tests/unit/test_price_target_labeler_heber_context.py` with:
  - `test_get_opposing_flow_prefers_heber_when_available`
  - `test_get_opposing_flow_falls_back_to_sql_when_heber_empty`
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_get_opposing_flow_from_heber(...)` with robust column mapping and strict filter parity:
    - same ticker,
    - opposing `put_call`,
    - `(entry_ts, end_ts]` window,
    - sweep-only,
    - `ASK` aggressor-only,
  - extracted SQL path into `_get_opposing_flow_sql(...)`,
  - updated `get_opposing_flow(...)` to use Heber-first with SQL fallback.

Verification:
- `uv run pytest -q tests/unit/test_price_target_labeler_heber_context.py -k opposing_flow` passed.
- `uv run pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "sector or opposing_flow"` passed.

Result:
- Opposing-flow context now aligns with the ongoing Heber-first migration contract in `main_price_target_labeler`.

Residual:
- Additional SQL-coupled labeler helpers still remain (notably flow-aggression and institutional-flow lookups already captured by existing failing tests in this suite) and should be migrated in the next slices.

## 214) Pass 208 Continuation (2026-02-09)

### 214.1 Phase-1 Bucket Market Context Heber-First Path (TDD-Backed)

Finding:
- `get_phase1_bucket_features(...)` still sourced overnight gap / VWAP distance / 5-day momentum from SQL-only bar queries.

Implemented:
- Extended and normalized `tests/unit/test_price_target_labeler_heber_context.py` coverage for phase-1 bucket behavior:
  - `test_get_phase1_bucket_features_prefers_heber_when_available`
  - `test_get_phase1_bucket_features_falls_back_to_sql_when_heber_empty`
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_get_phase1_bucket_features_from_heber(...)` for bar-derived market context,
  - extracted SQL path into `_get_phase1_bucket_features_sql(...)`,
  - updated `get_phase1_bucket_features(...)` to use Heber-first market context with SQL fallback,
  - retained existing `minutes_to_close` and earnings-window logic unchanged.

Verification:
- `uv run pytest -q tests/unit/test_price_target_labeler_heber_context.py -k phase1_bucket_features` passed.
- `uv run pytest -q tests/unit/test_price_target_labeler_heber_context.py` passed (`18 passed`).

Result:
- Phase-1 bucket market context now follows the same migration contract as other labeler context families: Heber-first with SQL fallback.

Residual:
- Remaining SQL-heavy areas are concentrated in deeper option-level feature blocks (`get_p2_features` / `get_p3_features`) and label backfill/update routines that still depend on local silver tables.

## 212) Pass 206 Continuation (2026-02-09)

### 212.1 Flow Aggression + Institutional 1W Context Heber-First Migration (TDD-Backed)

Finding:
- `get_flow_aggression(...)` and `get_institutional_flow_1w(...)` in `src/orion/main_price_target_labeler.py` were still SQL-only against `silver_uw_flow`.
- existing Heber-context tests for these paths were red, confirming migration drift.

Implemented:
- Updated `src/orion/main_price_target_labeler.py`:
  - `get_flow_aggression(...)` now routes Heber-first via `_get_flow_aggression_from_heber(...)` and falls back via `_get_flow_aggression_sql(...)`.
  - `get_institutional_flow_1w(...)` now routes Heber-first via `_get_institutional_flow_1w_from_heber(...)` and falls back via `_get_institutional_flow_1w_sql(...)`.
- Heber implementations normalize ticker forms (`ticker`/`symbol`/`underlying`/`instrument_key`), apply UTC window filters, and preserve previous output contracts.

Verification:
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "flow_aggression or institutional_flow_1w"` passed.
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py` passed.

Result:
- two additional context feature families now follow the same migration pattern (Heber-first with SQL fallback), reducing direct dependence on Orion-local UW SQL tables.

Residual:
- additional SQL-coupled context remains in `main_price_target_labeler` and adjacent enrichment jobs; continue staged Heber-first migration by feature family.

## 215) Pass 213 Continuation (2026-02-09)

### 215.1 Price-Target Labeler P2 Option Features Heber-First Path (TDD-Backed)

Finding:
- `get_p2_features(...)` in `src/orion/main_price_target_labeler.py` remained SQL-primary (`silver_uw_flow` + `silver_alpaca_bars`), leaving core option-level feature construction outside the Heber-first migration contract used by context helpers.

Implemented:
- Extended `tests/unit/test_price_target_labeler_heber_context.py` with:
  - `test_get_p2_features_prefers_heber_when_available`
  - `test_get_p2_features_falls_back_to_sql_when_heber_empty`
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_get_p2_features_from_heber(...)` to compute:
    - OI change (`oi_change_1d`, `oi_change_pct`) from Heber flow scoped to `option_chain`,
    - 30-day HV (`hv_30d`) from Heber bars,
    - IV/HV ratio (`iv_vs_hv_ratio`) from Heber IV + derived HV,
  - extracted existing SQL logic into `_get_p2_features_sql(...)`,
  - updated `get_p2_features(...)` to run Heber-first and fallback to SQL when Heber yields no usable data.

Verification:
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k get_p2_features` passed.
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py` passed.
- `pytest -q tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- P2 option-level feature construction now follows the same Heber-first with compatibility fallback pattern as the migrated context feature families, reducing direct dependence on Orion-local silver tables.

Residual:
- next high-value migration target in this area is `get_p3_features(...)`, which still reads 52w/high + same-expiry activity from local SQL tables as its primary source.

## 216) Pass 209 Continuation (2026-02-09)

### 216.1 Price-Target Labeler P3 Option Features Heber-First Path (TDD-Backed)

Finding:
- `get_p3_features(...)` in `src/orion/main_price_target_labeler.py` was still SQL-primary for:
  - 52-week high distance,
  - same-expiry 1h activity,
  - spread-leg heuristic.

Implemented:
- Extended `tests/unit/test_price_target_labeler_heber_context.py` with:
  - `test_get_p3_features_prefers_heber_when_available`
  - `test_get_p3_features_falls_back_to_sql_when_heber_empty`
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_get_p3_features_from_heber(...)` to derive:
    - `high_52w_distance_pct` from Heber bars,
    - `same_expiry_trades_1h` and `is_spread_leg` from Heber flow scoped by expiry + time window,
  - extracted SQL implementation into `_get_p3_features_sql(...)`,
  - updated `get_p3_features(...)` to run Heber-first with SQL fallback.

Verification:
- `uv run pytest -q tests/unit/test_price_target_labeler_heber_context.py` passed (`18 passed`).

Result:
- P3 option-level feature construction now follows the same Heber-first with compatibility fallback model used by the other migrated labeler feature families.

Residual:
- Primary remaining SQL-heavy work is now in downstream backfill/update routines and any non-Heberized legacy labeling/reporting paths outside the core per-trade feature helpers.

## 216) Pass 214 Continuation (2026-02-09)

### 216.1 Price-Target Labeler P3 Option Features Heber-First Path (TDD-Backed)

Finding:
- `get_p3_features(...)` in `src/orion/main_price_target_labeler.py` remained SQL-primary for:
  - 52-week high distance (`silver_alpaca_bars`),
  - same-expiry activity and spread-leg detection (`silver_uw_flow`).
- This left the remaining deep option-feature family outside the Heber-first migration contract.

Implemented:
- Extended `tests/unit/test_price_target_labeler_heber_context.py` with:
  - `test_get_p3_features_prefers_heber_when_available`
  - `test_get_p3_features_falls_back_to_sql_when_heber_empty`
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_get_p3_features_from_heber(...)` for:
    - 52w-high distance from Heber bars,
    - same-expiry 1h trade count and spread-leg heuristic from Heber flow,
  - extracted SQL path into `_get_p3_features_sql(...)`,
  - updated `get_p3_features(...)` to run Heber-first and fallback to SQL when Heber yields no usable data.

Verification:
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k get_p3_features` passed.
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- both deep option-level feature builders (`get_p2_features` and `get_p3_features`) now follow the same Heber-first + compatibility fallback contract as the migrated context helpers.

Residual:
- primary remaining parity debt in `main_price_target_labeler` is now concentrated in broader label backfill/update routines and remaining local-table primary reads outside feature helpers.

## 217) Pass 215 Continuation (2026-02-09)

### 217.1 Price-Target Labeler `get_iv_rank_at_entry(...)` Heber-First Path (TDD-Backed)

Finding:
- `get_iv_rank_at_entry(...)` in `src/orion/main_price_target_labeler.py` remained SQL-primary (flow-history percentile query) with no direct Heber-first lookup.
- this left a high-frequency entry enrichment read outside the migration contract used by other labeler feature families.

Implemented:
- Extended `tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py` with:
  - `test_get_iv_rank_at_entry_prefers_heber_when_available`
  - `test_get_iv_rank_at_entry_falls_back_to_sql_when_heber_empty`
- Updated `src/orion/main_price_target_labeler.py`:
  - `get_iv_rank_at_entry(...)` now checks `_get_iv_rank_from_heber(ticker, entry_ts)` first,
  - retains existing SQL percentile calculation path as fallback when Heber lookup is unavailable/unusable.

Verification:
- `uv run pytest -q tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py -k iv_rank_at_entry` passed.
- `uv run pytest -q tests/unit/test_price_target_labeler_heber_max_pain_iv_rank.py` passed.

Result:
- `iv_rank_at_entry` now follows the same Heber-first + compatibility fallback pattern as other migrated labeler context/feature reads.

Residual:
- remaining high-volume technical debt is still concentrated in downstream backfill/update/reporting routines and other local-table primary reads outside these entry-time helper migrations.

## 218) Pass 216 Continuation (2026-02-09)

### 218.1 Backfill ML Features Underlying-Price Source Alignment to Shared Heber-First Path (TDD-Backed)

Finding:
- `src/orion/jobs/backfill_ml_features.py` still had local SQL-only implementations for:
  - `get_underlying_price_at_entry(...)`
  - `get_underlying_price_at_offset(...)`
- live label generation already routes these lookups through shared helpers with Heber-first behavior, so backfill and live labeling could diverge in source semantics.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_signature.py` with:
  - `test_get_underlying_price_at_entry_delegates_to_labeler`
  - `test_get_underlying_price_at_offset_delegates_to_labeler`
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - imported shared labeler helpers as:
    - `get_labeler_underlying_price_at_entry`
    - `get_labeler_underlying_price_at_offset`
  - replaced local SQL implementations with wrapper delegation to those shared helpers.

Verification:
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "underlying_price_at_entry_delegates or underlying_price_at_offset_delegates"` passed.
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- backfill now inherits the same underlying-price source contract as live labeling, reducing local SQL divergence and tightening parity for reprocessed records.

Residual:
- additional backfill/update routines still contain local-table primary reads (for example local flow-greeks derivation) and remain candidates for the next TDD migration slice.

## 219) Pass 217 Continuation (2026-02-09)

### 219.1 Backfill ML Features Flow-Greeks Source Alignment to Shared Labeler Path (TDD-Backed)

Finding:
- `src/orion/jobs/backfill_ml_features.py` had a local SQL-only `get_flow_greeks(...)` implementation against `silver_uw_flow`.
- live label generation already uses `main_price_target_labeler.get_flow_greeks(...)`, so backfill could drift in feature semantics and fallback behavior.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_signature.py` with:
  - `test_get_flow_greeks_delegates_to_labeler`
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - imported shared helper as `get_labeler_flow_greeks`,
  - replaced local SQL implementation of `get_flow_greeks(...)` with wrapper delegation.

Verification:
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k flow_greeks_delegates` passed.
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- backfill now uses the same flow-greeks contract as live labeling, reducing local SQL duplication and tightening parity.

Residual:
- remaining backfill/update debt is now concentrated in other locally implemented enrichment routines (for example direct ticker metadata/earnings and any remaining local-only derivations) that should be migrated in the next slices.

## 220) Pass 218 Continuation (2026-02-09)

### 220.1 Backfill Ticker-Metadata Source Alignment to Shared Labeler Helper (TDD-Backed)

Finding:
- `src/orion/jobs/backfill_ml_features.py` maintained its own UW-client implementation of `get_ticker_info(...)`, including separate cache and endpoint handling.
- this duplicated logic already present in `main_price_target_labeler.get_ticker_info(...)` and could drift from live labeling semantics.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_signature.py` with:
  - `test_get_ticker_info_delegates_to_labeler`
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - imported shared helper as `get_labeler_ticker_info`,
  - removed local direct UW client path for ticker metadata lookup,
  - changed `get_ticker_info(...)` to delegate to shared helper and keep a lightweight local cache envelope.

Verification:
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k ticker_info_delegates` passed.
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- backfill now shares ticker metadata/earnings-date source behavior with live label generation, reducing divergence and removing one direct local UW implementation path.

Residual:
- remaining backfill-local enrichment debt is now mostly concentrated in feature update orchestration and any still-local derivations not yet delegated to shared helpers.

## 221) Pass 219 Continuation (2026-02-09)

### 221.1 Backfill Earnings-Proximity Alignment to Shared Labeler Helper (TDD-Backed)

Finding:
- `src/orion/jobs/backfill_ml_features.py` still computed `days_to_earnings` / `is_post_earnings` inline inside `update_ml_features(...)` using local date arithmetic over ticker metadata.
- live labeling already centralizes this in `main_price_target_labeler.get_earnings_proximity(...)`, so backfill could diverge from live behavior on boundary/date-handling cases.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_signature.py` with:
  - `test_get_earnings_proximity_delegates_to_labeler`
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - imported shared helper as `get_labeler_earnings_proximity`,
  - added wrapper `get_earnings_proximity(...)` delegating to shared helper,
  - replaced inline `days_to_earnings` / `is_post_earnings` computation in `update_ml_features(...)` with delegated helper output.
- Updated existing orchestration signature test to stub the new helper call path:
  - `test_update_ml_features_calls_sector_corr_with_two_args`.

Verification:
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k earnings_proximity_delegates` passed.
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- backfill and live labeling now share one earnings-proximity derivation contract, reducing duplicated date logic and closing another parity gap in feature recomputation.

Residual:
- remaining backfill parity debt is now mostly in orchestration breadth (large update surface and runtime integration behavior), rather than local helper-level source divergence.

## 222) Pass 220 Continuation (2026-02-09)

### 222.1 Price-Target Labeler Flow-Greeks Event Lookup Heber-First Path (TDD-Backed)

Finding:
- `get_flow_greeks(event_id)` in `src/orion/main_price_target_labeler.py` was still SQL-primary against `silver_uw_flow`.
- this left an event-level option-context helper outside the Heber-first migration pattern used by other labeler helpers.

Implemented:
- Extended `tests/unit/test_price_target_labeler_heber_context.py` with:
  - `test_get_flow_greeks_prefers_heber_when_available`
  - `test_get_flow_greeks_falls_back_to_sql_when_heber_missing`
- Updated `src/orion/main_price_target_labeler.py`:
  - added `_get_flow_greeks_from_heber(event_id)` to resolve event rows from Heber flow data and map required fields,
  - extracted existing SQL behavior into `_get_flow_greeks_sql(event_id)`,
  - updated `get_flow_greeks(...)` to run Heber-first with SQL fallback when Heber has no usable event row.
- preserved downstream computation order for returned Greeks (`stored` -> Alpaca contract lookup -> Black-Scholes derivation), changing only the source lookup strategy.

Verification:
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k flow_greeks` passed.

Result:
- flow-greeks event context now follows the same Heber-first + compatibility fallback contract as other migrated labeler feature/context helpers.

Residual:
- remaining parity debt is concentrated in non-helper orchestration/backfill paths and any still-local feature derivations not yet routed through shared Heber-first helpers.

## 223) Pass 221 Continuation (2026-02-09)

### 223.1 Repository-Wide SQL Coupling Inventory (Post-Helper Migration Sweep)

Finding:
- after the latest helper migrations, the largest remaining direct `silver_*` table coupling is no longer concentrated only in per-trade labeler helpers.
- current highest-density modules by `silver_*` reference count:
  - `src/orion/jobs/validate_features.py` (~87 references; validation/audit SQL surface),
  - `src/orion/main_price_target_labeler.py` (~58 references; many now fallback paths, but still broad),
  - `src/orion/ml/flow_enricher.py` (~28 references; heavy feature derivation path),
  - `src/orion/jobs/reconcile_backfill.py` (~20 references; reconciliation path).

Implemented:
- ran a repository sweep to inventory direct `FROM/JOIN/INSERT/UPDATE silver_*` usage across `main_*`, `jobs/*`, `ml/*`, and ingestion paths.
- produced a concrete residual target map to sequence next migration slices by operational impact.

Verification:
- command used:
  - `rg -n "silver_[a-z0-9_]+" src/orion | cut -d: -f1 | sort | uniq -c | sort -nr`
  - `rg -n "FROM silver_|JOIN silver_|INSERT INTO silver_|UPDATE silver_" src/orion/main_*.py src/orion/jobs/*.py src/orion/ml/*.py src/orion/ingestion/*.py`

Result:
- helper-level parity is materially improved; remaining debt is now primarily in:
  - bulk validation/reporting jobs,
  - flow enricher SQL feature assembly,
  - reconciliation/batch orchestration surfaces.

Residual / Next Slices:
- prioritize next TDD migration work in this order:
  1. `src/orion/ml/flow_enricher.py` (highest runtime feature impact).
  2. `src/orion/jobs/reconcile_backfill.py` + `src/orion/jobs/backfill_exit_columns.py` (batch/backfill parity).
  3. `src/orion/main_option_quote_tracker.py` and remaining quote/event local dependencies.
- keep `validate_features.py` for late-stage parity hardening (it is primarily an audit tool and lower live-path risk).

## 223) Pass 221 Continuation (2026-02-09)

### 223.1 Backfill Phase-1 Feature Delegation + Dead Local SQL Removal (TDD-Backed)

Finding:
- `src/orion/jobs/backfill_ml_features.py` still carried a local `get_phase1_features(...)` SQL implementation even though `update_ml_features(...)` was already using `main_price_target_labeler.get_phase1_bucket_features(...)`.
- this left dead code and an unnecessary alternate path for phase-1 feature derivation inside backfill.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_signature.py` with:
  - `test_get_phase1_bucket_features_delegates_to_labeler`
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - imported shared helper as `get_labeler_phase1_bucket_features`,
  - added wrapper `get_phase1_bucket_features(...)` delegating to shared helper,
  - removed unused local `get_phase1_features(...)` SQL code path,
  - updated `update_ml_features(...)` to use the wrapper directly.
- Updated orchestration signature test to stub the wrapper path:
  - `test_update_ml_features_calls_sector_corr_with_two_args`.

Verification:
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "phase1_bucket_features_delegates or update_ml_features_calls_sector_corr_with_two_args"` passed.
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- backfill phase-1 feature enrichment now has one delegated source path, and dead local SQL phase-1 logic has been removed.

Residual:
- remaining backfill debt is now mostly in large orchestration/runtime behavior concerns rather than helper-level source divergence or duplicate local implementations.

## 224) Pass 222 Continuation (2026-02-09)

### 224.1 `flow_enricher` Flow-Greeks Path Delegated to Shared Labeler Helpers (TDD-Backed)

Finding:
- `src/orion/ml/flow_enricher.py::_get_flow_greeks(...)` was still a local SQL-heavy implementation over `silver_uw_flow` and `silver_alpaca_bars`.
- this duplicated logic already centralized in labeler helpers and preserved another high-frequency local-source divergence path.

Implemented:
- Added `tests/unit/test_flow_enricher_delegation.py` with:
  - `test_get_flow_greeks_delegates_to_labeler_and_p2_when_option_chain_present`
  - `test_get_flow_greeks_skips_p2_when_option_chain_missing`
- Updated `src/orion/ml/flow_enricher.py`:
  - `_get_flow_greeks(...)` now delegates base greeks (`delta/gamma/theta/vega/iv/volume/open_interest`) to `get_labeler_flow_greeks(event_id)`,
  - when `ticker + option_chain + entry_ts` are available, enriches `iv_vs_hv_ratio`, `oi_change_1d`, `oi_change_pct` via `get_labeler_p2_features(...)`,
  - removed the previous local SQL-heavy flow-greeks derivation path.
- Updated `enrich_flow_for_scoring(...)` call-site to pass `ticker`, `entry_ts`, and `option_chain` into `_get_flow_greeks(...)` for shared P2 feature enrichment.

Verification:
- `pytest -q tests/unit/test_flow_enricher_delegation.py` passed.

Result:
- flow-enricher now shares one core flow-greeks + option-feature contract with label generation paths, reducing SQL duplication and tightening parity.

Residual:
- additional `flow_enricher` helpers still read local tables directly (for example market context/window aggregation); continue staged delegation/Heber-first migration by helper family.

## 224) Pass 222 Continuation (2026-02-09)

### 224.1 Backfill Sector-Correlation Wrapper Alignment (TDD-Backed)

Finding:
- `update_ml_features(...)` still imported and called `get_sector_correlation_features(...)` directly from `main_price_target_labeler` inside the method body.
- this kept one orchestration branch outside the wrapper-delegation pattern used by other migrated backfill helpers and made test stubbing less uniform.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_signature.py` with:
  - `test_get_sector_correlation_features_delegates_to_labeler`
- Updated existing orchestration regression test:
  - `test_update_ml_features_calls_sector_corr_with_two_args`
  - now stubs `backfill.get_sector_correlation_features(...)` directly.
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - imported shared helper alias `get_labeler_sector_correlation_features`,
  - added wrapper `get_sector_correlation_features(...)`,
  - removed direct inline import of `get_sector_correlation_features` inside `update_ml_features(...)`,
  - routed sector-correlation enrichment through the wrapper.

Verification:
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "sector_corr_with_two_args or sector_correlation_features_delegates"` passed.
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- sector-correlation enrichment now follows the same backfill wrapper-delegation contract as the other migrated helper paths.

Residual:
- remaining backfill orchestration debt is primarily the broader set of inline helper imports/calls and runtime integration behavior under full backfill load.

## 225) Pass 223 Continuation (2026-02-09)

### 225.1 Backfill IV-Rank Wrapper Alignment (TDD-Backed)

Finding:
- `update_ml_features(...)` still imported `get_iv_rank_at_entry(...)` directly inside the method body from `main_price_target_labeler`.
- this left IV-rank enrichment outside the wrapper delegation pattern now used by the rest of migrated backfill helpers.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_signature.py` with:
  - `test_get_iv_rank_at_entry_delegates_to_labeler`
- Updated `test_update_ml_features_calls_sector_corr_with_two_args` to stub `backfill.get_iv_rank_at_entry(...)`.
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - imported shared helper alias `get_labeler_iv_rank_at_entry`,
  - added wrapper `get_iv_rank_at_entry(...)`,
  - removed inline import call and routed IV-rank enrichment through the wrapper.

Verification:
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "iv_rank_at_entry_delegates or sector_corr_with_two_args"` passed.
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- IV-rank enrichment now follows the same wrapper-delegation contract as other migrated backfill helper calls.

Residual:
- remaining backfill technical debt is now dominated by broader orchestration complexity and additional inline helper call paths not yet wrapped.

## 226) Pass 224 Continuation (2026-02-09)

### 226.1 Backfill P2/P3 Wrapper Alignment (TDD-Backed)

Finding:
- `update_ml_features(...)` still imported `get_p2_features(...)` / `get_p3_features(...)` inline from `main_price_target_labeler`.
- this left option-feature enrichment partially outside the wrapper delegation pattern used by other migrated backfill helper paths.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_signature.py` with:
  - `test_get_p2_features_delegates_to_labeler`
  - `test_get_p3_features_delegates_to_labeler`
- Updated `test_update_ml_features_calls_sector_corr_with_two_args` to stub:
  - `backfill.get_p2_features(...)`
  - `backfill.get_p3_features(...)`
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - imported aliases `get_labeler_p2_features` and `get_labeler_p3_features`,
  - added wrappers `get_p2_features(...)` and `get_p3_features(...)`,
  - removed inline `p2/p3` import path in `update_ml_features(...)`,
  - routed P2/P3 enrichment through wrappers.

Verification:
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "get_p2_features_delegates or get_p3_features_delegates or sector_corr_with_two_args"` passed.
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- P2/P3 option-feature enrichment now follows the same wrapper-delegation contract as the other migrated backfill helper calls.

Residual:
- remaining backfill orchestration debt is now primarily concentrated in the remaining inline helper import block for darkpool/rvol/flow-aggression/tide/regime context and runtime behavior under full-load backfill execution.

## 227) Pass 225 Continuation (2026-02-09)

### 227.1 Backfill Context Helper Wrapper Alignment (TDD-Backed)

Finding:
- `update_ml_features(...)` still had a remaining inline helper import block for:
  - darkpool metrics,
  - RVOL metrics,
  - flow aggression,
  - institutional flow (1w),
  - market tide context,
  - regime context.
- this left a final set of backfill orchestration paths outside the wrapper-delegation pattern and kept stubbing behavior inconsistent across helper families.

Implemented:
- Extended `tests/unit/test_backfill_ml_features_signature.py` with:
  - `test_get_darkpool_metrics_delegates_to_labeler`
  - `test_get_rvol_metrics_delegates_to_labeler`
  - `test_get_flow_aggression_delegates_to_labeler`
  - `test_get_institutional_flow_1w_delegates_to_labeler`
  - `test_get_market_tide_before_entry_delegates_to_labeler`
  - `test_get_regime_at_entry_delegates_to_labeler`
- Updated orchestration regression test `test_update_ml_features_calls_sector_corr_with_two_args` to stub:
  - `backfill.get_darkpool_metrics(...)`,
  - `backfill.get_rvol_metrics(...)`,
  - `backfill.get_flow_aggression(...)`,
  - `backfill.get_institutional_flow_1w(...)`,
  - `backfill.get_market_tide_before_entry(...)`,
  - `backfill.get_regime_at_entry(...)`.
- Updated `src/orion/jobs/backfill_ml_features.py`:
  - imported shared helper aliases for the six context helpers above,
  - added wrapper delegates for each helper,
  - removed the remaining inline direct-import block in `update_ml_features(...)`,
  - routed context enrichment through wrappers.

Verification:
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py -k "get_darkpool_metrics_delegates or get_rvol_metrics_delegates or get_flow_aggression_delegates or get_institutional_flow_1w_delegates or get_market_tide_before_entry_delegates or get_regime_at_entry_delegates or sector_corr_with_two_args"` passed.
- `uv run pytest -q tests/unit/test_backfill_ml_features_signature.py` passed.

Result:
- backfill context enrichment is now consistently wrapper-delegated across darkpool/RVOL/flow/tide/regime helper families, aligning orchestration with the shared labeler contract.

Residual:
- major helper-level wrapper alignment in `backfill_ml_features.py` is now largely complete.
- remaining debt in this file is primarily runtime behavior and operational concerns (full-load backfill execution characteristics, integration/soak behavior, and broader orchestration complexity rather than helper-source divergence).

## 228) Pass 226 Continuation (2026-02-09)

### 228.1 `flow_enricher` Context Helper Delegation (TDD-Backed)

Finding:
- after the prior flow-greeks delegation slice, `src/orion/ml/flow_enricher.py` still had direct local SQL paths for:
  - market tide (`_get_market_tide`),
  - IV-rank (`_get_iv_rank`),
  - darkpool windows (`_get_darkpool_volumes`),
  - regime snapshot (`_get_regime`).
- this kept inference-side context derivation partly divergent from shared labeler logic and preserved avoidable local table coupling in a high-frequency enrichment path.

Implemented:
- Extended `tests/unit/test_flow_enricher_delegation.py` with:
  - `test_get_market_tide_delegates_to_labeler`
  - `test_get_iv_rank_delegates_to_labeler`
  - `test_get_darkpool_volumes_delegates_to_labeler_and_maps_windows`
  - `test_get_regime_delegates_to_labeler`
- Updated `src/orion/ml/flow_enricher.py` to delegate:
  - `_get_market_tide(...)` -> `get_labeler_market_tide_before_entry(...)`,
  - `_get_iv_rank(...)` -> `get_labeler_iv_rank_at_entry(...)`,
  - `_get_darkpool_volumes(...)` -> `get_labeler_darkpool_metrics(...)` (with explicit mapping to expected `30m/1h/4h/1d` keys),
  - `_get_regime(...)` -> `get_labeler_regime_at_entry(...)`.
- preserved existing flow-enricher return contracts while removing these local SQL implementations.

Verification:
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py -k "market_tide_delegates or iv_rank_delegates or darkpool_volumes_delegates or get_regime_delegates"` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py` passed.

Result:
- flow-enricher now shares one source contract with labeler helpers across market tide, IV-rank, darkpool windows, and regime context, reducing parity drift risk in live inference enrichment.

Residual:
- `flow_enricher` still contains local SQL for some context families (for example GEX rolling averages, broader flow metrics, market context, and window aggregations).
- next recommended `flow_enricher` slice: isolate and delegate GEX base snapshot + retain explicit rolling-average contract as a separate helper-level migration.

## 229) Pass 227 Continuation (2026-02-09)

### 229.1 `backfill_exit_columns` Subsequent-Price Lookup Delegation (TDD-Backed)

Finding:
- `src/orion/jobs/backfill_exit_columns.py::get_subsequent_prices(...)` still queried local `silver_uw_flow` directly.
- this preserved an extra local-source lookup path in backfill processing even though equivalent subsequent-price lookup logic already exists in shared labeler helpers.

Implemented:
- Extended `tests/unit/test_backfill_exit_columns_selection.py` with:
  - `test_get_subsequent_prices_delegates_to_labeler`
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - imported `get_subsequent_prices` from `main_price_target_labeler` as `get_labeler_subsequent_prices`,
  - replaced local SQL query implementation with direct delegation to shared helper.

Verification:
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py -k subsequent_prices` passed.
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- backfill exit-column subsequent-price retrieval now shares a single helper contract with label generation paths, reducing duplicated SQL and parity drift risk.

Residual:
- `backfill_exit_columns` still has remaining local SQL in checkpoint/velocity update and candidate selection paths; continue staged delegation where a canonical shared helper exists and retain local SQL only for true backfill-only orchestration concerns.

## 230) Pass 228 Continuation (2026-02-09)

### 230.1 `flow_enricher` GEX Snapshot Delegation (TDD-Backed)

Finding:
- `src/orion/ml/flow_enricher.py::_get_gex_at_entry(...)` still performed both base snapshot and rolling-average calculations from local `silver_greek_exposure`.
- this duplicated base snapshot logic already centralized in shared labeler helper `get_gex_at_entry(...)` and kept another live inference path partially divergent.

Implemented:
- Extended `tests/unit/test_flow_enricher_delegation.py` with:
  - `test_get_gex_at_entry_delegates_base_to_labeler_and_adds_rolling_avg`
  - `test_get_gex_at_entry_skips_sql_avg_when_labeler_has_no_snapshot`
- Updated `src/orion/ml/flow_enricher.py`:
  - imported shared helper alias `get_labeler_gex_at_entry`,
  - updated `_get_gex_at_entry(...)` to source base `gex/vex` from shared helper,
  - extracted rolling-average SQL into `_get_gex_rolling_averages(...)`,
  - short-circuits and skips rolling-average SQL when shared base snapshot is unavailable.

Verification:
- `pytest -q tests/unit/test_flow_enricher_delegation.py -k gex_at_entry` passed.
- `pytest -q tests/unit/test_flow_enricher_delegation.py` passed.

Result:
- flow-enricher now shares one base GEX/VEX source contract with labeler paths while preserving existing rolling-average semantics required by confidence rules.

Residual:
- `flow_enricher` still has local SQL in other context families (for example max-pain distance and broader window aggregations); continue helper-by-helper delegation where shared canonical contracts exist.

## 231) Pass 229 Continuation (2026-02-09)

### 231.1 `flow_enricher` Max-Pain Distance Delegation (TDD-Backed)

Finding:
- `src/orion/ml/flow_enricher.py::_get_max_pain_distance(...)` still queried local `silver_max_pain` directly.
- a shared labeler helper already provides Heber-first + fallback max-pain distance lookup, so the local flow-enricher query was duplicate source logic.

Implemented:
- Extended `tests/unit/test_flow_enricher_delegation.py` with:
  - `test_get_max_pain_distance_delegates_to_labeler`
  - `test_get_max_pain_distance_returns_none_without_dte`
- Updated `src/orion/ml/flow_enricher.py`:
  - imported `get_max_pain_distance` from labeler as `get_labeler_max_pain_distance`,
  - rewired `_get_max_pain_distance(...)` to delegate to shared helper when DTE is provided,
  - preserved existing `None` return behavior when DTE is not available.

Verification:
- `pytest -q tests/unit/test_flow_enricher_delegation.py -k max_pain_distance` passed.
- `pytest -q tests/unit/test_flow_enricher_delegation.py` passed.

Result:
- max-pain context in flow-enricher now uses the same canonical lookup path as label generation, reducing direct SQL duplication and parity drift.

Residual:
- remaining `flow_enricher` local SQL debt is concentrated in broader market/window aggregation helpers and should be migrated in the same helper-level TDD pattern.

## 232) Pass 230 Continuation (2026-02-09)

### 232.1 `flow_enricher` Combined Flow-Context + VIX Delegation (TDD-Backed)

Finding:
- after GEX and max-pain delegation, `flow_enricher` still had:
  - local SQL VIX lookup in `_get_vix(...)`,
  - local SQL flow-context assembly in `_get_flow_metrics(...)` (flow aggression, sector premium direction, SPY return, earnings proximity).
- these paths were good candidates to combine in one pass because they produce related context features for the same enrichment payload.

Implemented:
- Extended `tests/unit/test_flow_enricher_delegation.py` with:
  - `test_get_vix_delegates_to_labeler_regime`
  - `test_get_flow_metrics_delegates_context_to_labeler_helpers`
- Updated `src/orion/ml/flow_enricher.py`:
  - `_get_vix(...)` now delegates to `get_labeler_regime_at_entry(...)` and returns `vix_at_entry`,
  - `_get_flow_metrics(...)` now delegates:
    - flow aggression metrics to `get_labeler_flow_aggression(...)`,
    - sector/spy metrics to `get_labeler_sector_correlation_features(...)`,
    - earnings proximity to `get_labeler_earnings_proximity(...)`,
  - retained output contract and DTE window derivation (`earnings_in_dte_window` from `days_to_earnings` + `dte`).

Verification:
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py -k "get_vix_delegates_to_labeler_regime or get_flow_metrics_delegates_context_to_labeler_helpers"` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py` passed.

Result:
- one combined pass removed another chunk of local SQL coupling in live enrichment and tightened parity by reusing shared labeler contracts for VIX + flow context fields.

Residual:
- `flow_enricher` still has local SQL in broader market/window feature families (`_get_market_context(...)`, `_get_window_features(...)`, and GEX rolling averages).
- next combined pass recommendation: delegate RVOL/overnight-gap/52w-high context to shared helpers where available, then isolate remaining window aggregation SQL as explicit backfill-only/local-derivation contracts.

## 233) Pass 231 Continuation (2026-02-09)

### 233.1 `flow_enricher` Window-Feature Retrieval Delegation (TDD-Backed)

Finding:
- `src/orion/ml/flow_enricher.py::_get_window_features(...)` still queried `gold_feature_windows` directly.
- even though this data source is valid, the lookup contract was local to flow-enricher and not reusable by other parity-sensitive paths.

Implemented:
- Added shared helper in `src/orion/main_price_target_labeler.py`:
  - `get_window_features_at_entry(ticker, entry_ts)` returning latest `gold_feature_windows` payloads for `1h/1d/1w`.
- Extended `tests/unit/test_flow_enricher_delegation.py` with:
  - `test_get_window_features_delegates_to_labeler_and_maps_period_values`
- Updated `src/orion/ml/flow_enricher.py`:
  - `_get_window_features(...)` now delegates data retrieval to `get_labeler_window_features_at_entry(...)`,
  - preserves existing feature mapping/output contract for all period-specific fields.

Verification:
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py -k "get_window_features_delegates_to_labeler_and_maps_period_values"` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py` passed.

Result:
- window-feature retrieval is now centralized behind a shared helper contract, reducing direct query duplication in live enrichment and enabling reuse in future parity refactors.

Residual:
- remaining notable local SQL in flow-enricher is now mostly intentional/derived aggregation logic rather than raw source lookup duplication.
- next meaningful combined pass should target `backfill_exit_columns` remaining checkpoint/candidate-selection orchestration debt.

## 234) Pass 232 Continuation (2026-02-09)

### 234.1 `flow_enricher` + Labeler Shared Window-Feature Contract (TDD-Backed)

Finding:
- window-feature retrieval for `1h/1d/1w` context still needed explicit shared-helper coverage so `flow_enricher` could consume one canonical contract without local query ownership.

Implemented:
- Added/confirmed shared helper in `src/orion/main_price_target_labeler.py`:
  - `get_window_features_at_entry(ticker, entry_ts)` for latest `gold_feature_windows` payloads by period.
- Extended `tests/unit/test_flow_enricher_delegation.py` with:
  - `test_get_window_features_delegates_to_labeler_and_maps_period_values`
- Updated `src/orion/ml/flow_enricher.py`:
  - `_get_window_features(...)` now relies on `get_labeler_window_features_at_entry(...)` for retrieval,
  - retained existing period-to-feature mapping shape,
  - removed the now-unused local DB import path for this feature family.

Verification:
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py -k "get_window_features_delegates_to_labeler_and_maps_period_values"` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py` passed.

Result:
- window-feature lookup now has an explicit shared contract boundary between labeler and flow-enricher, reducing direct query duplication and tightening parity behavior.

Residual:
- remaining high-value remediation area is still `backfill_exit_columns` checkpoint/candidate-selection orchestration logic.

## 233) Pass 231 Continuation (2026-02-09)

### 233.1 `flow_enricher` GEX Rolling-Average Delegation (TDD-Backed)

Finding:
- after delegating base GEX snapshot, `src/orion/ml/flow_enricher.py::_get_gex_at_entry(...)` still computed rolling averages through a local `silver_greek_exposure` query.
- this left one direct `silver_*` dependency in flow enricher and duplicated logic outside the shared labeler contract.

Implemented:
- Updated `tests/unit/test_flow_enricher_delegation.py`:
  - `test_get_gex_at_entry_delegates_base_to_labeler_and_adds_rolling_avg`
  - `test_get_gex_at_entry_skips_sql_avg_when_labeler_has_no_snapshot`
  - both now enforce delegated rolling-average lookup.
- Added shared helper in `src/orion/main_price_target_labeler.py`:
  - `get_gex_rolling_averages(...)` with Heber-first + SQL fallback internals.
- Updated `src/orion/ml/flow_enricher.py`:
  - `_get_gex_at_entry(...)` now calls `get_labeler_gex_rolling_averages(...)`,
  - removed local `_get_gex_rolling_averages(...)` SQL helper.

Verification:
- `pytest -q tests/unit/test_flow_enricher_delegation.py -k gex_at_entry` passed.
- `pytest -q tests/unit/test_flow_enricher_delegation.py` passed.

Result:
- `flow_enricher` no longer directly queries high-priority `silver_*` tables for core context helpers.
- remaining local table coupling is concentrated in `gold_feature_windows` consumption (`_get_window_features(...)`), which should be handled in the next batch.

## 235) Pass 233 Continuation (2026-02-09)

### 235.1 `backfill_exit_columns` Candidate-Selection Delegation (TDD-Backed)

Finding:
- `src/orion/jobs/backfill_exit_columns.py` still owned local SQL for candidate-selection paths:
  - `get_records_to_backfill(...)` (velocity phase),
  - `get_all_records_for_checkpoints(...)` (checkpoint phase).
- this left orchestration-level SQL duplicated outside shared labeler contracts and made backfill selection behavior harder to reuse or validate centrally.

Implemented:
- Added shared helpers in `src/orion/main_price_target_labeler.py`:
  - `get_velocity_backfill_candidates(...)`,
  - `get_checkpoint_backfill_candidates(...)`,
  - plus `_build_backfill_cursor_clause(...)` to centralize keyset cursor predicate construction.
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - `get_records_to_backfill(...)` now delegates to `get_labeler_velocity_backfill_candidates(...)`,
  - `get_all_records_for_checkpoints(...)` now delegates to `get_labeler_checkpoint_backfill_candidates(...)`.
- Extended tests:
  - `tests/unit/test_backfill_exit_columns_selection.py`
    - `test_get_records_to_backfill_delegates_to_labeler`
    - `test_get_all_records_for_checkpoints_delegates_to_labeler`
    - updated cursor/timestamp-only tests to assert delegated argument pass-through.
  - `tests/unit/test_price_target_labeler_heber_context.py`
    - `test_get_velocity_backfill_candidates_queries_expected_shape`
    - `test_get_checkpoint_backfill_candidates_queries_expected_shape`

Verification:
- `uv run pytest -q tests/unit/test_backfill_exit_columns_selection.py` passed.
- `uv run pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "window_features_at_entry or velocity_backfill_candidates or checkpoint_backfill_candidates"` passed.

Result:
- backfill candidate-selection SQL is now centralized behind shared labeler helpers.
- checkpoint/velocity orchestration in `backfill_exit_columns` now consumes a single canonical candidate contract, reducing drift risk and improving testability.

Residual:
- remaining debt in `backfill_exit_columns` is primarily runtime orchestration behavior (batching/retry/operational controls) rather than duplicated candidate SQL ownership.
- next high-value remediation target remains classifier/window-query consolidation and broader end-to-end gateway contract behavior under load.

## 236) Pass 234 Continuation (2026-02-09)

### 236.1 Shared Window Query Consolidation in Labeler + Exit Classifier (TDD-Backed)

Finding:
- window-feature lookup for `1h/1d/1w` context still performed repetitive multi-query patterns in key training/inference paths:
  - `main_price_target_labeler.get_window_features_at_entry(...)` fetched each period separately,
  - `ml/exit_classifier.build_bucket_training_data(...)` used three lateral joins (`w1h/w1d/w1w`) per row.
- this increased query round-trips and widened parity/performance drift risk between training and shared helper behavior.

Implemented:
- Updated `src/orion/main_price_target_labeler.py`:
  - `get_window_features_at_entry(...)` now issues one SQL call using `DISTINCT ON (period)` and maps periods from a single result set.
- Updated `src/orion/ml/exit_classifier.py`:
  - replaced three per-period lateral joins with one lateral subquery that builds `features_by_period` via `jsonb_object_agg(period, features)`,
  - preserved existing feature extraction output contract (`1h/1d/1w` field paths).
- Extended tests:
  - `tests/unit/test_price_target_labeler_heber_context.py`
    - `test_get_window_features_at_entry_uses_single_query_and_maps_periods`
    - `test_get_window_features_at_entry_returns_empty_dict_on_query_error`
  - `tests/unit/test_exit_classifier_window_query.py`
    - `test_build_bucket_training_data_uses_single_lateral_window_lookup`

Verification:
- `pytest -q tests/unit/test_price_target_labeler_heber_context.py -k "window_features_at_entry or velocity_backfill_candidates or checkpoint_backfill_candidates"` passed.
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py` passed.
- `pytest -q tests/unit/test_exit_classifier_window_query.py` passed.

Result:
- shared window retrieval now uses one query path in labeler helper usage.
- classifier training query now uses one lateral window lookup instead of three, tightening parity and reducing repeated table scans.

Residual:
- further parity work should focus on remaining high-cardinality training joins and on extracting additional local SQL paths in model prep into shared helper contracts where stable.

## 237) Pass 235 Continuation (2026-02-09)

### 237.1 Exit Classifier Trade-Type Binding Hardening (TDD-Backed)

Finding:
- `src/orion/ml/exit_classifier.py::build_bucket_training_data(...)` still interpolated `trade_type` directly into SQL text.
- while current source values are controlled, this kept query safety/consistency below project standard and made parameterized contract behavior untested.

Implemented:
- Extended `tests/unit/test_exit_classifier_window_query.py` with:
  - `test_build_bucket_training_data_binds_trade_type_parameter`.
- Updated `src/orion/ml/exit_classifier.py`:
  - changed `WHERE p.trade_type = '{trade_type}'` to `WHERE p.trade_type = :trade_type`,
  - now executes with bound params: `{"trade_type": trade_type}`.

Verification:
- `uv run pytest -q tests/unit/test_exit_classifier_window_query.py` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- exit-classifier training query now uses explicit bind parameters for bucket trade type.
- this reduces SQL interpolation risk and aligns training query behavior with hardened DB access patterns used elsewhere in remediation.

Residual:
- next combined pass should target broader classifier training contract validation (feature null-handling, large-sample query performance, and cross-bucket schema drift checks).

## 238) Pass 236 Continuation (2026-02-09)

### 238.1 Exit Classifier Training-Data Contract Hardening (TDD-Backed)

Finding:
- `src/orion/ml/exit_classifier.py::build_bucket_training_data(...)` still relied on direct `float(...)` casts and direct key lookup for some fields:
  - non-numeric checkpoint return values raised `ValueError`,
  - missing `max_return_pct` key raised `KeyError`.
- this made training brittle to schema/value drift in large historical datasets and reduced reliability under backfill/contract evolution.

Implemented:
- Updated `src/orion/ml/exit_classifier.py`:
  - switched max-return extraction to safe lookup/conversion (`_safe_float(row.get("max_return_pct"))`),
  - switched checkpoint return conversion to safe numeric parsing with explicit skip for non-numeric values,
  - replaced remaining direct numeric casts in checkpoint/entry feature assembly with `_safe_float(...)` defaults.
- Extended `tests/unit/test_exit_classifier_window_query.py`:
  - `test_build_bucket_training_data_skips_non_numeric_checkpoint_returns`
  - `test_build_bucket_training_data_handles_missing_max_return_pct_key`

Verification:
- `pytest -q tests/unit/test_exit_classifier_window_query.py` passed.
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_price_target_labeler_heber_context.py -k "velocity_backfill_candidates or checkpoint_backfill_candidates or window_features_at_entry"` passed.

Result:
- exit-classifier training-data build now degrades gracefully on malformed row values and minor schema drift.
- this reduces training interruptions and makes bucket model generation more robust during parity migration and backfill phases.

Residual:
- next combined classifier pass should target explicit cross-bucket column-contract assertions and optional query-time feature null normalization for higher-volume training runs.

## 238) Pass 236 Continuation (2026-02-09)

### 238.1 Exit Classifier Training Robustness (Sweep Normalization + Sample Guard, TDD-Backed)

Finding:
- in `src/orion/ml/exit_classifier.py::build_bucket_training_data(...)`, `is_sweep` was encoded via Python truthiness (`1 if row.get("is_sweep") else 0`).
- string payloads like `"false"` were therefore incorrectly encoded as `1`, creating silent label/feature corruption risk during model training.
- sample construction also lacked an explicit guard for feature-vector length mismatch vs `feature_names`.

Implemented:
- Extended `tests/unit/test_exit_classifier_window_query.py` with:
  - `test_build_bucket_training_data_unknown_bucket_short_circuits_without_query`
  - `test_build_bucket_training_data_normalizes_is_sweep_string_false_and_shapes_features`
- Updated `src/orion/ml/exit_classifier.py`:
  - added `_is_truthy(...)` helper to normalize bool-like DB payloads,
  - switched training sample sweep encoding to normalized boolean conversion,
  - added feature-count guard that skips malformed samples and logs structured warning metadata.

Verification:
- `uv run pytest -q tests/unit/test_exit_classifier_window_query.py` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- sweep flag handling is now deterministic for string/integer/boolean payload variants, removing a concrete source of training-data drift.
- training loop now fails-safe on malformed feature vectors instead of silently emitting inconsistent samples.

Residual:
- next high-value classifier pass should add explicit schema-drift tests around checkpoint column availability and null-heavy row distributions under larger sample sets.

## 240) Pass 238 Continuation (2026-02-09)

### 240.1 Exit-Classifier Cross-Bucket Query Contracts + SQL Null Normalization (TDD-Backed)

Finding:
- residual classifier debt called out in pass 239 was still open in audit narrative: explicit cross-bucket checkpoint column contract checks and query-time null normalization validation.
- these checks are important because bucket schema drift or nullable window payloads can silently degrade training quality while still returning rows.

Implemented:
- Extended `tests/unit/test_exit_classifier_window_query.py` with:
  - `test_build_bucket_training_data_query_contract_per_bucket`
  - `test_build_bucket_training_data_query_coalesces_entry_and_window_fields`
- Validated query behavior in `src/orion/ml/exit_classifier.py::build_bucket_training_data(...)`:
  - bucket-specific checkpoint columns are asserted for `0DTE`, `SHORT_SWING`, `SWING`, `POSITION`,
  - SQL-side defaults are asserted via `COALESCE(...)` for entry fields and `gold_feature_windows` JSON window fields (`1h/1d/1w`).

Verification:
- `pytest -q tests/unit/test_exit_classifier_window_query.py` passed.
- `pytest -q tests/unit/test_exit_classifier_window_query.py tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_price_target_labeler_heber_context.py -k "window_features_at_entry or velocity_backfill_candidates or checkpoint_backfill_candidates or query_contract_per_bucket or query_coalesces_entry_and_window_fields"` passed.

Result:
- classifier training query contract is now explicitly guarded across all trade buckets.
- null-heavy entry/window rows are normalized at SQL projection time, reducing downstream training-data instability and drift risk.

Residual:
- next high-value classifier pass should cover large-sample performance checks and optional query plan/index verification for `price_target_labels` + `gold_feature_windows` joins.

## 239) Pass 237 Continuation (2026-02-09)

### 239.1 Exit Classifier Label-Distribution Guarding (TDD-Backed)

Finding:
- `train_bucket_exit_classifier(...)` could still reach stratified train/test split with problematic label distributions (single-class or too-few minority samples), causing avoidable training-time failures.
- dataset building also needed explicit coverage for mixed malformed/valid numeric rows to ensure partial-data salvage behavior remains stable.

Implemented:
- Updated `src/orion/ml/exit_classifier.py`:
  - added `_can_train_with_labels(...)` to validate sample count and class distribution before model fitting,
  - integrated guard into `train_bucket_exit_classifier(...)` prior to `LightGBM` training path.
- Extended `tests/unit/test_exit_classifier_window_query.py`:
  - `test_can_train_with_labels_rejects_single_class_and_sparse_classes`
  - `test_build_bucket_training_data_skips_malformed_numeric_rows`

Verification:
- `uv run pytest -q tests/unit/test_exit_classifier_window_query.py` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- classifier training now short-circuits cleanly for invalid label distributions instead of failing mid-fit/split.
- training-data assembly remains resilient by skipping malformed numeric rows while retaining valid samples in the same batch.

Residual:
- next combined pass should target explicit cross-bucket schema drift checks (checkpoint column availability contracts and large-window query behavior under sparse/null-heavy datasets).

## 240) Pass 238 Continuation (2026-02-09)

### 240.1 Exit Classifier Empty-Batch Shape/Dtype Stability (TDD-Backed)

Finding:
- `build_bucket_training_data(...)` previously returned `np.array([])` when all candidate rows were filtered.
- this produced shape `(0,)` instead of a stable feature-matrix contract shape, which can cause downstream ambiguity in training/pipeline consumers that expect 2D `X`.

Implemented:
- Updated `src/orion/ml/exit_classifier.py`:
  - when no samples survive filtering, now returns:
    - `X`: `np.empty((0, len(feature_names)), dtype=float)`
    - `y`: `np.empty((0,), dtype=int)`
  - non-empty outputs are now explicitly cast to `float` (`X`) and `int` (`y`) for consistent downstream behavior.
- Extended tests in `tests/unit/test_exit_classifier_window_query.py`:
  - `test_build_bucket_training_data_returns_stable_empty_matrix_shape_when_rows_filtered`
  - strengthened missing-`max_return_pct` scenario with explicit shape assertions.

Verification:
- `uv run pytest -q tests/unit/test_exit_classifier_window_query.py` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- dataset output contract is now stable for empty training batches and explicitly typed for numeric model input/output handling.
- this reduces shape surprises in classifier training orchestration and future vectorized consumers.

Residual:
- next high-value pass remains schema-drift guarding for checkpoint-column availability and larger-volume performance profiling for the classifier training query.

## 241) Pass 239 Continuation (2026-02-09)

### 241.1 `backfill_exit_columns` Runtime Hardening (TDD-Backed, Combined Pass)

Finding:
- `src/orion/jobs/backfill_exit_columns.py::run_backfill(...)` previously called record updaters directly inside phase loops.
- any per-record exception could abort the full job, preventing later records from being processed and creating brittle recovery behavior during long backfill runs.
- phase progress logs also lacked explicit failure/retry counters, making operational visibility weaker than neighboring backfill jobs.

Implemented:
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - added `_update_record_with_retry(...)` with bounded retry behavior for per-record update failures,
  - introduced retry controls:
    - `MAX_RECORD_RETRIES = 2`
    - `RETRY_SLEEP_SECONDS = 0.25`
  - routed both velocity/checkpoint phase updates through retry helper,
  - preserved cursor advancement semantics while adding per-phase `failed` and `retried` counters,
  - added richer progress/final logs for both phases including processed/updated/failed/retried totals.
- Extended `tests/unit/test_backfill_exit_columns_selection.py`:
  - `test_update_record_with_retry_retries_then_succeeds`
  - `test_update_record_with_retry_marks_failure_after_max_retries`
  - `test_run_backfill_continues_when_velocity_update_raises`

Verification:
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py` passed.
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_exit_classifier_window_query.py` passed.

Result:
- backfill now degrades gracefully under transient/per-record failures instead of stopping the entire run.
- retry/failure telemetry is explicit, which improves operations visibility and post-run triage quality.

Residual:
- next high-value backfill pass should add a configurable dead-letter sink for repeated per-record failures and optional phase-level summary return payload for orchestration callers.

## 242) Pass 240 Continuation (2026-02-09)

### 242.1 Exit Classifier Query-Failure Fallback Contract (TDD-Backed)

Finding:
- `build_bucket_training_data(...)` still propagated DB/query exceptions directly.
- during schema drift events (for example missing checkpoint columns) this caused hard failures instead of safe degradation, even though downstream training orchestration can tolerate empty datasets.

Implemented:
- Updated `src/orion/ml/exit_classifier.py`:
  - added `_empty_training_arrays(feature_count)` helper for consistent empty output contracts,
  - wrapped `db_query(run_query)` in guarded fallback:
    - logs structured warning event `exit_training_query_failed`,
    - returns empty typed arrays with preserved `feature_names` schema instead of raising.
- Extended `tests/unit/test_exit_classifier_window_query.py`:
  - `test_build_bucket_training_data_returns_empty_with_feature_schema_on_query_error` (parametrized across `0DTE`, `SHORT_SWING`, `SWING`, `POSITION`),
  - `test_build_bucket_training_data_returns_stable_empty_matrix_shape_when_rows_filtered` (explicit shape/dtype contract).

Verification:
- `uv run pytest -q tests/unit/test_exit_classifier_window_query.py` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- classifier training-data preparation now degrades safely under query/schema errors while preserving feature-schema contract.
- this reduces operational interruption risk during migrations and partial schema rollout states.

Residual:
- next high-value pass should add explicit checkpoint-column presence preflight checks so failures can be categorized as “schema missing” before query execution, with actionable remediation metadata.

## 243) Pass 241 Continuation (2026-02-09)

### 243.1 Exit Classifier Schema-Preflight + Missing-Column Degradation (TDD-Backed)

Finding:
- `build_bucket_training_data(...)` still relied on query-time behavior to reveal schema issues.
- if checkpoint columns were missing for a bucket, failures occurred late and were not categorized as explicit schema-preflight misses.

Implemented:
- Updated `src/orion/ml/exit_classifier.py`:
  - added `_required_price_target_columns_for_bucket(...)` to compute bucket-specific required column set,
  - added `_load_price_target_label_columns(...)` metadata probe (`information_schema.columns`),
  - added preflight short-circuit path when required columns are missing:
    - logs `exit_training_schema_missing_columns`,
    - returns stable empty arrays with preserved feature schema.
  - retained query-error fallback path (`exit_training_query_failed`) for runtime query exceptions.
- Extended tests in `tests/unit/test_exit_classifier_window_query.py`:
  - `test_required_price_target_columns_for_bucket_includes_checkpoint_families`
  - `test_build_bucket_training_data_short_circuits_when_required_columns_missing`
  - `test_build_bucket_training_data_returns_empty_with_feature_schema_on_query_error`

Verification:
- `uv run pytest -q tests/unit/test_exit_classifier_window_query.py` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- classifier training now differentiates schema-missing conditions from generic query failures and degrades safely without interrupting broader remediation workflows.

Residual:
- next pass should focus on actionable diagnostics payloads for missing-column groups (by checkpoint family) and optional metadata caching to reduce repeated schema probes in high-frequency training loops.

## 244) Pass 242 Continuation (2026-02-09)

### 244.1 `backfill_exit_columns` Orchestration Contract Expansion (TDD-Backed, Combined Pass)

Finding:
- pass 241 left two explicit residuals open:
  - no dead-letter sink for exhausted retries,
  - no structured run summary return for orchestration callers.
- this limited batch post-processing observability and made repeated-failure triage harder in long runs.

Implemented:
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - expanded `_update_record_with_retry(...)` to return terminal error message metadata,
  - added optional dead-letter JSONL output for exhausted retries:
    - function arg `dead_letter_path`
    - env default `ORION_BACKFILL_EXIT_DEAD_LETTER_PATH`
    - helper `_write_dead_letter_record(...)`,
  - added configurable retry knobs to function + CLI:
    - `max_retries`
    - `retry_sleep_seconds`
    - `--max-retries`
    - `--retry-sleep-seconds`
    - `--dead-letter-path`,
  - `run_backfill(...)` now returns structured summary payload:
    - phase counters (`processed`, `updated`, `failed`, `retried`, `dead_lettered`)
    - aggregate totals.
- Extended `tests/unit/test_backfill_exit_columns_selection.py`:
  - updated retry tests for 4-tuple return contract (`error_message`),
  - `test_run_backfill_writes_dead_letter_for_exhausted_retry`,
  - strengthened continuation test to assert summary counters.

Verification:
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py -k "update_record_with_retry or dead_letter or continues_when_velocity_update_raises"` passed.
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- backfill orchestration now exposes machine-readable run outcomes and provides an explicit failure capture lane for repeated per-record errors.
- this closes both pass-241 residuals and improves operational handoff for nightly/managed backfill runs.

Residual:
- next pass should add optional dead-letter payload redaction controls and a max-file-size rotation policy for prolonged high-error periods.

## 245) Pass 243 Continuation (2026-02-09)

### 245.1 Exit Classifier Empty-Contract + Diagnostics Bundling (TDD-Backed)

Finding:
- exit-classifier training data had mixed empty-output contracts:
  - unknown bucket and no-row paths returned empty arrays without feature schema,
  - schema/query failure paths returned stable empty arrays with feature schema.
- this made orchestrator behavior and downstream validation inconsistent.
- schema-preflight logs had full missing-column lists but lacked grouped families for fast triage.

Implemented:
- Updated `src/orion/ml/exit_classifier.py`:
  - promoted canonical training feature names to `EXIT_FEATURE_NAMES`,
  - normalized `build_bucket_training_data(...)` to always return stable empty arrays + schema for:
    - unknown bucket names,
    - valid buckets with no rows,
    - schema preflight failures,
    - query exceptions,
  - added schema metadata TTL caching for column probes:
    - `SCHEMA_CACHE_TTL_SECONDS`
    - `_clear_price_target_label_schema_cache()` test/support reset helper,
  - added `_group_missing_columns_by_family(...)` and attached grouped diagnostics to `exit_training_schema_missing_columns` logs.
- Extended `tests/unit/test_exit_classifier_window_query.py`:
  - `test_group_missing_columns_by_family_assigns_expected_buckets`
  - `test_load_price_target_label_columns_uses_ttl_cache`
  - strengthened unknown-bucket/no-row contract tests to enforce stable empty shape + feature schema.

Verification:
- `uv run pytest -q tests/unit/test_exit_classifier_window_query.py` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- training-data builders now expose one consistent empty-dataset contract regardless of failure/empty mode.
- schema-preflight diagnostics are now triage-friendly and cheaper under repeated execution due to metadata caching.

Residual:
- add optional `force_schema_refresh` control for long-lived workers during active migrations.
- add a lightweight metric counter for each `missing_by_family` group to improve runbook-driven alerting.

## 246) Pass 244 Continuation (2026-02-09)

### 246.1 Exit Classifier Schema Refresh Control + Family Metric Counters (TDD-Backed)

Finding:
- pass 245 identified two remaining operational gaps:
  - no explicit cache-bypass control for schema metadata in long-lived processes,
  - grouped missing-column diagnostics had no compact per-family counters for alert runbooks.

Implemented:
- Updated `src/orion/ml/exit_classifier.py`:
  - `_load_price_target_label_columns(force_refresh: bool = False)` now supports explicit cache bypass when set to `True`,
  - added `_group_count_map(...)` to derive `missing_by_family_counts`,
  - enriched `exit_training_schema_missing_columns` structured logs with:
    - `missing_by_family`
    - `missing_by_family_counts`.
- Extended `tests/unit/test_exit_classifier_window_query.py`:
  - `test_load_price_target_label_columns_force_refresh_bypasses_cache`
  - `test_build_bucket_training_data_logs_missing_family_counts`.

Verification:
- `uv run pytest -q tests/unit/test_exit_classifier_window_query.py` passed.
- `uv run pytest -q tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- long-lived workers can force schema re-probe during active DB migrations.
- alerting/runbook flows can now key off compact family counters instead of parsing full missing-column arrays.

Residual:
- wire `force_refresh=True` into selected orchestration paths where schema rollout windows are expected.

## 247) Pass 245 Continuation (2026-02-09)

### 247.1 Exit Classifier All-Bucket Schema Refresh Strategy (TDD-Backed)

Finding:
- orchestration only supported a one-time pre-refresh model, with no explicit option to force schema refresh per bucket in migration windows where checkpoint columns can change mid-run.

Implemented:
- Updated `src/orion/ml/exit_classifier.py`:
  - `train_all_exit_classifiers(...)` now accepts `refresh_each_bucket: bool = False`,
  - strategy behavior:
    - `force_schema_refresh=True`, `refresh_each_bucket=False`:
      - one-time schema pre-refresh (cache warming) before bucket loop,
      - logs `refresh_strategy="prefetch_once"`,
    - `force_schema_refresh=True`, `refresh_each_bucket=True`:
      - no pre-refresh call,
      - each bucket training invocation receives `force_schema_refresh=True`.
- Extended `tests/unit/test_exit_classifier_window_query.py`:
  - `test_train_all_exit_classifiers_refresh_each_bucket_forces_bucket_refresh`,
  - retained one-time pre-refresh coverage:
    - `test_train_all_exit_classifiers_force_refreshes_schema_once`.

Verification:
- `uv run pytest -q tests/unit/test_exit_classifier_window_query.py -k "train_all_exit_classifiers or train_bucket_exit_classifier_passes_force_schema_refresh"` passed.
- `uv run pytest -q tests/unit/test_exit_classifier_window_query.py tests/unit/test_flow_enricher_delegation.py tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- orchestrator now supports both low-overhead cache-preload mode and high-safety per-bucket refresh mode, improving resilience during rolling schema migrations.

## 247) Pass 245 Continuation (2026-02-09)

### 247.1 Backfill Dead-Letter Redaction + Rotation Policy (TDD-Backed, Combined Pass)

Finding:
- pass 244 left one explicit residual open:
  - dead-letter records had no payload redaction controls,
  - dead-letter sink had no size-based rotation policy for sustained error periods.
- this created avoidable PII/log-risk exposure and operational storage risk during prolonged retry exhaustion events.

Implemented:
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - added dead-letter redaction helper `_apply_dead_letter_redaction(...)`,
  - added dead-letter rotation helper `_rotate_dead_letter_file_if_needed(...)`,
  - expanded `_write_dead_letter_record(...)` to:
    - accept `max_bytes` and `redact_fields`,
    - apply field redaction before write,
    - rotate file when threshold reached,
    - return `bool` flag indicating rotation occurred,
  - introduced new defaults:
    - `ORION_BACKFILL_EXIT_DEAD_LETTER_MAX_BYTES`
    - `ORION_BACKFILL_EXIT_DEAD_LETTER_REDACT_FIELDS`,
  - expanded `run_backfill(...)` contract with:
    - `dead_letter_max_bytes`
    - `dead_letter_redact_fields`,
    - phase and total `dead_letter_rotated` counters in summary payload,
  - expanded CLI with:
    - `--dead-letter-max-bytes`
    - `--dead-letter-redact-fields`.
- Extended `tests/unit/test_backfill_exit_columns_selection.py`:
  - `test_write_dead_letter_record_applies_redaction_and_rotation`
  - `test_run_backfill_dead_letter_redaction_and_rotation`
  - updated retry helper assertions for terminal `error_message` metadata.

Verification:
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py` passed.
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_exit_classifier_window_query.py` passed.

Result:
- exhausted-retry backfill failures now flow through a safer and operationally bounded dead-letter channel.
- this closes the pass-244 residual and improves parity with production-grade ingestion/backfill handling requirements.

Residual:
- add optional gzip compression for rotated dead-letter files if sustained error rates make long-lived archives large.

## 248) Pass 246 Continuation (2026-02-09)

### 248.1 Exit Classifier Forced Schema-Refresh Wiring for Orchestration (TDD-Backed, Combined Pass)

Finding:
- pass 246 left one operational residual:
  - force-refresh support existed at the schema probe helper layer, but orchestration-level training entrypoints could not request it explicitly.
- this limited safe rollout behavior during active schema migrations where workers should re-read metadata before training loops.

Implemented:
- Updated `src/orion/ml/exit_classifier.py`:
  - `build_bucket_training_data(...)` now accepts `force_schema_refresh: bool = False`,
  - `train_bucket_exit_classifier(...)` now accepts and forwards `force_schema_refresh`,
  - `train_all_exit_classifiers(...)` now accepts `force_schema_refresh`; when enabled:
    - it performs a one-time forced schema refresh via `_load_price_target_label_columns(force_refresh=True)`,
    - logs `exit_training_schema_forced_refresh` with refreshed column count,
    - proceeds through bucket training using the refreshed cached metadata.
- Extended `tests/unit/test_exit_classifier_window_query.py`:
  - `test_train_bucket_exit_classifier_passes_force_schema_refresh`
  - `test_train_all_exit_classifiers_force_refreshes_schema_once`

Verification:
- `pytest -q tests/unit/test_exit_classifier_window_query.py -k "force_schema_refresh or force_refreshes_schema_once"` passed.
- `pytest -q tests/unit/test_exit_classifier_window_query.py tests/unit/test_backfill_exit_columns_selection.py` passed.

Result:
- orchestration callers now have an explicit migration-safe switch to refresh schema metadata before exit-model training starts.
- this closes the pass-246 residual while keeping normal-mode training behavior unchanged.

Residual:
- evaluate whether nightly automation should set `force_schema_refresh=True` only inside schema rollout windows (feature flag or schedule guard).

## 249) Pass 247 Continuation (2026-02-09)

### 249.1 Combined Pass: Dead-Letter Gzip Compression + Exit-Training Env Refresh Wiring (TDD-Backed)

Finding:
- pass 247 residual remained open on dead-letter archive bloat for sustained error windows.
- pass 248 residual required rollout-window control for exit-classifier schema refresh behavior in scheduled orchestration paths.

Implemented:
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - added optional gzip compression for rotated dead-letter files:
    - env default: `ORION_BACKFILL_EXIT_DEAD_LETTER_COMPRESS_ROTATED`,
    - function arg: `dead_letter_compress_rotated`,
    - CLI flags:
      - `--dead-letter-compress-rotated`
      - `--no-dead-letter-compress-rotated`,
  - rotation helper now optionally compresses `.jsonl.N` to `.jsonl.N.gz`,
  - summary now includes:
    - per-phase `dead_letter_compressed`
    - `total_dead_letter_compressed`
    - `dead_letter_compress_rotated`.
- Updated `src/orion/ml/pattern_miner.py`:
  - added `_exit_classifier_schema_refresh_config_from_env()` to read rollout controls:
    - `ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH`
    - `ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET`,
  - wired `run_all_pattern_mining()` to forward those settings into `train_all_exit_classifiers(...)`,
  - added guardrail:
    - if per-bucket refresh is enabled without force refresh, it is disabled and logged as config-invalid.
- Added test coverage:
  - `tests/unit/test_backfill_exit_columns_selection.py`
    - `test_write_dead_letter_record_rotates_and_gzips_when_enabled`
    - `test_run_backfill_dead_letter_rotation_tracks_compressed_files`
  - `tests/unit/test_pattern_miner_exit_refresh_config.py`
    - env defaults
    - invalid config guard behavior
    - orchestration pass-through to exit-classifier trainer.

Verification:
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py -k "dead_letter and (rotation or gzip or compressed)" tests/unit/test_pattern_miner_exit_refresh_config.py` passed.
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_pattern_miner_exit_refresh_config.py` passed.

Result:
- dead-letter archives are now operationally bounded with optional compressed rotation.
- exit-classifier schema-refresh behavior is now configurable at orchestration time, closing rollout-window control gaps.

Residual:
- add a runbook note defining when to use `refresh_each_bucket=true` versus one-time prefetch in schema rollout playbooks.

## 250) Pass 248 Continuation (2026-02-09)

### 250.1 Pattern Miner Strategy Env Consolidation for Exit-Refresh Controls (TDD-Backed)

Finding:
- exit-refresh orchestration controls in pattern miner required two boolean env vars, which increased operator misconfiguration risk and did not provide a single declarative strategy switch.

Implemented:
- Updated `src/orion/ml/pattern_miner.py`:
  - added strategy env support:
    - `ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY`
  - supported modes:
    - `off|disabled|none|false` => no forced refresh,
    - `prefetch_once|once` => one-time prefetch refresh,
    - `per_bucket|each_bucket|each` => force refresh per bucket,
  - precedence:
    - valid strategy env overrides legacy flags,
    - invalid strategy env logs `exit_training_schema_refresh_strategy_invalid` and falls back to legacy flags.
- Updated tests in `tests/unit/test_pattern_miner_exit_refresh_config.py`:
  - `test_exit_classifier_schema_refresh_strategy_per_bucket_overrides_legacy`
  - `test_exit_classifier_schema_refresh_strategy_invalid_falls_back_to_legacy`.

Verification:
- `uv run pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py` passed.
- `uv run pytest -q tests/unit/test_pattern_miner_exit_refresh_config.py tests/unit/test_exit_classifier_window_query.py` passed.

Result:
- orchestration now supports one declarative refresh strategy switch with backward-compatible fallback behavior, reducing rollout configuration mistakes.

## 251) Pass 249 Continuation (2026-02-09)

### 251.1 Combined Remediation: Dead-Letter Rotation Retention Cap + Refresh Strategy Runbook Note (TDD-Backed)

Finding:
- pass 249 left two operational gaps:
  - rotated dead-letter files could still grow unbounded in file count during sustained failure periods,
  - strategy guidance for `prefetch_once` vs `per_bucket` refresh mode was not captured in runbooks.

Implemented:
- Updated `src/orion/jobs/backfill_exit_columns.py`:
  - added `ORION_BACKFILL_EXIT_DEAD_LETTER_MAX_ROTATED_FILES`,
  - added `dead_letter_max_rotated_files` through function/runtime summary/CLI surfaces,
  - added pruning helper behavior that deletes oldest rotated files (`.jsonl.N` and `.jsonl.N.gz`) before creating a new rotation when cap is reached.
- Extended `tests/unit/test_backfill_exit_columns_selection.py`:
  - `test_write_dead_letter_record_prunes_oldest_rotation_when_cap_reached`
  - `test_write_dead_letter_record_prunes_oldest_gzip_rotation_when_cap_reached`
- Added runbook guidance:
  - `docs/runbooks/schema_rollout.md` with explicit strategy selection criteria for:
    - `ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY=prefetch_once`
    - `ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY=per_bucket`
  - linked from `docs/runbooks/README.md`.

Verification:
- `pytest -q tests/unit/test_backfill_exit_columns_selection.py tests/unit/test_exit_classifier_window_query.py tests/unit/test_pattern_miner_exit_refresh_config.py` passed.

Result:
- dead-letter rotation is now bounded by both file size and retained-file count.
- rollout operators now have an explicit runbook for schema-refresh strategy selection during migration windows.
