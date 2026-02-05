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

2. Gateway WebSocket payload mismatch in Orion client
- Data Gateway sends stream payloads as `{type: "data", envelope: {...}, data: {...}}` (`../Data-Gateway/gateway/main.py`, `_on_stream_data`).
- Orion `GatewayStreamClient` only processes message types `ALPACA_BAR_1M` or `bar` and expects payload at top-level or `payload` field (`src/orion/connectors/gateway_stream_client.py`).
- Impact: Stream events can be silently ignored.

3. Test suite is structurally broken by removed modules
- Removed modules (`orion.main_ingest`, `orion.connectors.uw_flow_connector`) are still imported in many tests.
- Reproduced failures:
  - `pytest -o addopts='-q' tests/connectors/test_uw_flow.py` -> `ModuleNotFoundError: orion.connectors.uw_flow_connector`
  - `pytest -o addopts='-q' tests/unit/test_eod_wrapper.py` -> `ModuleNotFoundError: orion.main_ingest`
- Impact: migration regressions are harder to detect due test noise.
- Status in this pass: addressed by archiving those legacy tests into `archive/2026-02-05_gateway-heber-migration/legacy_tests/`; replacement tests are still required.

## Medium

4. Environment variable contract drift
- Orion uses multiple naming families: `GATEWAY_URL`, `DATA_GATEWAY_URL`, `GATEWAY_API_KEY`, `DATA_GATEWAY_API_KEY`, plus legacy UW vars.
- `src/orion/config.py` does not centrally define Gateway/Heber settings, leaving direct `os.getenv` spread across connectors.
- Impact: deploy misconfiguration risk and hidden behavior divergence.

5. Mixed data ownership model (SQL-local vs lakehouse)
- Orion still writes and depends on local SQL silver tables for UW-derived context while migration intent is Heber ownership.
- Impact: duplicate sources of truth and schema drift.

6. Hardcoded default gateway key in several connectors
- Example defaults like `gw_orion_trading_key_55555` in UW connectors.
- Impact: security and operational hygiene concern.

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

P0 (Do next):
1. Fix Gateway stream client message parsing to consume `type=data` + `envelope` payload shape.
2. Introduce central config for Gateway/Heber URLs and keys in `src/orion/config.py`; remove scattered hardcoded defaults.

P1:
4. Refactor label/enrichment jobs to read from Heber datasets (or a single sanctioned data-access layer) instead of local UW silver SQL tables.
5. Rebuild tests around `orion.ingestion.service` and new integration contracts.
6. Update README/docs to match new architecture and command paths.

P2:
7. Define canonical feature/label schema ownership between Orion and Heber (single source of truth per dataset family).
8. Remove stale generated artifacts/docs that keep reintroducing deprecated paths.

## 7) Recommended Migration Sequence

1. Runtime contract hardening
- Fix Gateway stream parsing.
- Fix Heber read client contract.

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

This audit now includes pass-2 column parity mapping. Next pass should implement the remaining P0 runtime fix (Gateway stream payload parsing) and start executing the migration decisions above.
