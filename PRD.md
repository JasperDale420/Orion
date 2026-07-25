# PRD: Real-Time UW + Alpaca Data Lake + Signal Engine + Live Trading + Daily Self-Improvement (RAG-first)

**Status:** Draft v2 (adds signal generation, robust validation, live trading, and an LLM improvement loop)
**Primary mode (v1):** UW polling + Alpaca market data (extended hours). Upgrade path to UW websocket streaming.

> **This PRD's "RAG search", "LLM review agent", and "Meta-Search Orchestrator"
> sections describe the original design — that machinery (the RAG stack,
> EOD-review LLM agent, MetaSearchAgent, and the automated
> generate/backtest/promote loop, ~15.7k LOC) was subsequently deleted; see
> `CHANGELOG.md` ("Delete the LLM solver-evolution machinery"). None of it ever
> influenced a live trading decision. The measurement loop that replaces it is
> mechanical: nightly per-bucket P&L reconciliation
> (`realize_expired_journal_rows` + `reconcile_pnl`) feeds
> `jobs/bucket_metrics.py`, which posts advisory-only sizing-up/halting
> verdicts to Discord — nothing here promotes a solver automatically; that
> still requires a human hitting `POST /promotions/{id}/approve`. The rest of
> this document (ingestion, signal engine, execution, risk) still describes
> current behavior; treat the LLM-agent sections as historical context, not a
> statement of what runs today. See `docs/project-overview-pdr.md` and
> `docs/system-architecture.md` for the current picture.

---

## 1) Objective

Build a system that continuously ingests and stores **real-time Unusual Whales (UW)** + **Alpaca** data during **PRE/REG/POST** sessions, then:

1) Produces **trading signals** from historically validated patterns (rule-first, ML-second).
2) Executes signals live via **Alpaca trading API** once promotion criteria are met.
3) Runs a daily **LLM review agent** that inspects trades/outcomes, proposes improvements (rules/features/models/risk/config/code), and creates auditable change proposals using a controlled workflow.
4) Provides a **RAG search layer** so agents can query the dataset and the research outcomes with natural language + filters, with pointers back to raw evidence.

This is a “massive dataset” system: **durable lakehouse** + **hot store**, with strong observability and statistical hygiene (no lookahead, no leakage, time-series validation, multiple-testing controls).

---

## 2) Guiding Principles (Anti-Overfitting Doctrine)

1) **Rule-first vertical slice:** start with explicit hypotheses encoded as rules + engineered features; backtest brutally with costs.
2) **ML as a meta-layer:** once rules have some edge, train ML to decide when to follow a rule (meta-labeling).
3) **Robust stats before dopamine:** walk-forward testing, purged/embargoed CV, bootstrap/permutation checks, and multiple-hypothesis discipline.
4) **RAG is retrieval, not truth:** RAG returns **evidence + pointers**; execution logic is deterministic and fully logged.
5) **LLM can suggest, not silently deploy:** improvements become PRs/config proposals with tests + human/automated gates.

---

## 3) Scope

### 3.1 In Scope (v1)
- Real-time ingestion (UW polling) for:
  - Options Flow
  - Dark Pool
  - Flow Alerts (if available via your UW API plan)
  - Optional UW aggregates (GEX/Whale Feed) as “nice-to-have” feature sources (toggleable)
- Real-time ingestion from Alpaca for:
  - 1-minute bars (baseline)
  - Optional trades/quotes for a managed subset (v2; can explode data volume)
- Storage:
  - Hot store for fast queries
  - Lakehouse for long-term massive storage (Parquet + table format)
- Processing:
  - Normalization, dedupe, rollups, feature store
  - Backtests and statistical validation harness
  - Signal generation
- Execution:
  - Paper trading first, then live trading via Alpaca
  - Trade & PnL tracking using Alpaca fills + price series
- RAG:
  - Index rollups, “event cards,” daily recaps, strategy/test reports, and trade journals
  - Hybrid retrieval (keyword + vector + metadata filters)
- Daily self-improvement agent:
  - Generates an “Improvement Report”
  - Produces PRs/config change proposals (not auto-merge)

### 3.2 Out of Scope (v1)
- High-frequency microstructure execution optimization
- Complex multi-leg options execution (v2+)
- “Fully autonomous” self-modifying production code without gates (explicitly forbidden)

---

## 4) High-Level Architecture

### 4.1 Services (logical)
1) **Connectors (Ingestion)**
   - `uw_flow_connector` (poll now; websocket later)
   - `uw_darkpool_connector`
   - `uw_alerts_connector`
   - `uw_gex_connector` (optional)
   - `alpaca_market_connector` (1m bars)
   - `alpaca_trading_connector` (orders/fills/positions snapshots; required once trading enabled)

2) **Event Bus**
   - Kafka/Redpanda recommended (durable, replayable).
   - Minimal mode: persistent queue + idempotent writers (acceptable but less robust).

3) **Storage**
   - **Hot Store:** TimescaleDB/Postgres (or ClickHouse), optimized for time-range queries
   - **Lakehouse:** S3/MinIO + Parquet + Iceberg (recommended) for massive, cheap history
   - **Vector DB:** Qdrant or pgvector for embeddings + metadata filters

4) **Processors**
   - `normalizer` (provider payload → canonical schema)
   - `deduper_validator` (idempotency + schema checks + quarantine)
   - `rollup_builder` (5m/1h/daily rollups)
   - `feature_engine` (event/ticker-window → feature rows)
   - `label_engine` (feature rows → forward returns + triple-barrier labels)
   - `backtest_engine` (rules + meta-model → trades → metrics)
   - `signal_engine` (live features → signals)
   - `performance_tracker` (live trades → realized metrics)

5) **RAG Layer**
   - `rag_indexer` (build docs + embed + index)
   - `query_api` (hybrid vector+keyword search + pointer-based fetch)

6) **Daily Improvement Agent**
   - `eod_review_agent` (LLM)
   - `proposal_builder` (turns suggestions -> PR/config patch + test plan)

7) **Observability**
   - structured logs, metrics, DLQ, alerts, dashboards

---

## 5) Sessions / Timekeeping (Extended Hours)

All timestamps stored as **UTC**. Add derived fields:
- `trading_date` (US equities calendar day in ET)
- `session` ∈ `PRE|REG|POST|CLOSED`

Default ET bands (configurable):
- PRE: 04:00–09:30
- REG: 09:30–16:00
- POST: 16:00–20:00

Use an exchange calendar library to avoid holiday/weekend mistakes.

---

## 6) Data Contracts (Bronze/Silver/Gold)

### 6.1 Canonical Event Envelope (All events)
Each event stored in bronze/silver must include:

```json
{
  "event_id": "string",                 // deterministic idempotency key
  "source": "UW|ALPACA",
  "source_event_id": "string|null",
  "event_type": "UW_FLOW|UW_DARKPOOL|UW_ALERT|UW_GEX|ALPACA_BAR_1M|ALPACA_ORDER|ALPACA_FILL|...",
  "event_ts_utc": "ISO-8601",
  "received_ts_utc": "ISO-8601",
  "trading_date": "YYYY-MM-DD",
  "session": "PRE|REG|POST|CLOSED",
  "ticker": "string|null",
  "schema_version": "v1",
  "payload": { ... },                   // raw (bronze) or normalized (silver)
  "ingest": { "connector": "...", "run_id": "...", "trace_id": "...", "attempt": 1 }
}

event_id rule:
	•	If provider gives unique IDs: sha256(source + source_event_id)
	•	Else: sha256(source + event_type + ticker + event_ts_utc + stable_payload_subset)

6.2 Silver Schemas (Normalized)

UW Options Flow (minimum)
	•	ticker, flow_ts_utc
	•	call_put (C/P)
	•	expiry, strike
	•	option_price, size_contracts
	•	premium_usd (compute option_price * size * 100 if missing)
	•	bid, ask, underlying_price
	•	aggressor (ASK/BID/MID/UNK)
	•	flags: is_sweep, is_block, is_multi_leg
	•	volume_contract, open_interest
	•	optional: iv, delta, gamma, vega, theta

UW Dark Pool (minimum)
	•	ticker, dark_ts_utc
	•	trade_price, size_shares
	•	venue, conditions

UW Flow Alerts (minimum)
	•	ticker, alert_ts_utc
	•	contract fields + premium_usd, size_contracts
	•	volume_contract, open_interest
	•	flags + alert_tags[]

Alpaca Bars 1m (required)
	•	ticker, bar_start_ts_utc
	•	open, high, low, close, volume
	•	optional: trade_count, vwap

Alpaca Trading (required once live)
	•	Orders, fills, positions snapshots
	•	Must store Alpaca order_id and fill price/qty/timestamp

6.3 Gold Tables (Rollups + Feature Store + Labels)

Rollups (examples)
	•	gold_ticker_5m_rollup
	•	gold_ticker_hour_rollup
	•	gold_ticker_daily_rollup

Each includes:
	•	flow aggregates (call/put premium, sweeps/blocks, DTE/moneyness concentration)
	•	dark pool aggregates (dp volume, %ADV, dp vwap vs price)
	•	price context (returns, range, vol)

Feature Store Tables
Two “levels” are supported (both are useful):
	1.	Window-level features: (ticker, window_start, window_size)
	2.	Event-level features: one row per “candidate event” (e.g., large sweep, dark cluster)

Tables:
	•	features_window
	•	features_event

Labels
	•	labels_window
	•	labels_event

Labels include:
	•	forward returns (1m/5m/1h/1d/3d etc; configurable)
	•	triple-barrier class labels (see Section 8)

⸻

7) Ingestion Requirements (Polling now, Websocket later)

7.1 UW Polling (v1)

Each UW connector must implement watermark polling:
	•	poll every P seconds (config; default 5s)
	•	request events after (last_seen_ts - overlap_margin) (default overlap = 120s)
	•	dedupe via event_id
	•	handle rate limits/backoff
	•	write failures to DLQ with payload+context

7.2 UW Websocket Upgrade (v2)

Design connectors with a common interface:
	•	fetch_since(ts) for polling
	•	stream() for websocket
Same normalization + idempotency layer; switching transport should not change downstream code.

7.3 Alpaca Market Data

Baseline:
	•	ingest 1-minute bars for managed universe during PRE/REG/POST

Optional v2:
	•	trades/quotes for a smaller subset (very high volume)

7.4 Universe Manager (Required)

To keep “massive” but not “infinite”:
	•	Manage tickers to stream from Alpaca based on:
	•	config watchlists
	•	top UW activity in last X minutes
	•	active alerts
	•	positions currently held
	•	TTL demotion when quiet

⸻

8) Targets & Labels (Robust, Configurable)

8.1 Prediction Targets

Configurable by strategy:
	•	Intraday: 5–60 minute horizons
	•	Swing: 1–3 day horizons

8.2 Triple-Barrier Labeling (recommended default)

For each candidate trade time t:
	•	upper barrier: +U (e.g., +1.5% or k * ATR)
	•	lower barrier: -L (e.g., -1.0% or k * ATR)
	•	time barrier: T (e.g., 60m, same day close, 3D)

Label:
	•	+1 if upper hit first
	•	-1 if lower hit first
	•	0 if neither hit by time barrier (optional)

Store:
	•	label, time-to-hit, max favorable excursion, max adverse excursion.

8.3 Leakage Rules (hard)
	•	Features must only use data at or before t
	•	No “end of day” fields in intraday features unless trading after close
	•	Use purging/embargo in CV (Section 10)

⸻

9) Signal Generation Stack (Rule → ML Meta → Optional Advanced)

9.1 Step 1: Rule-First (Python)

Implement 3–10 initial hypothesis rules (configurable), e.g.:

Bullish Sweep + Confirming Dark
	•	large call sweep (premium ≥ X)
	•	aggressor=ASK (or price ≥ mid)
	•	DTE 7–30d
	•	delta in [0.3, 0.6] (if available)
	•	concurrent dark pool prints clustered within Y minutes
	•	dp notional ≥ Z and dp_vwap near/above vwap

Bearish Put Pressure
	•	put premium burst, aggressor=ASK on puts, short DTE
	•	negative call/put imbalance
	•	dark pool prints at discount + immediate price weakness

Rules should output:
	•	candidate_trade rows with:
	•	entry timestamp
	•	direction (LONG/SHORT)
	•	rule_id + explanation
	•	evidence pointers (event_ids, rollup_ids, segments)

Table: candidates

9.2 Step 2: ML Meta-Labeling (Light ML)

Once candidates exist:
	•	label each candidate: 1 if profitable net of costs, 0 otherwise (or use triple-barrier outcome)
	•	train classifier to predict whether to take the candidate

Recommended models (in order):
	•	Logistic regression baseline (calibrated)
	•	Gradient-boosted trees (LightGBM/XGBoost/CatBoost)
	•	Optional: random forest

ML reads engineered features (not raw text) and outputs:
	•	p_take (probability rule instance is good)
	•	optional size modifier

Decision policy:
	•	rule fires → ML decides: TRADE / SKIP / SIZE_DOWN
	•	keep this policy deterministic and fully logged

9.3 Step 3: Heavier ML (only if rule+meta stack shows stable edge)

Optional research lane:
	•	regime clustering (HDBSCAN/k-means) + policy per regime
	•	sequence models (TCN/transformer) on event sequences

Not required for v1.

⸻

10) Robust Statistical Validation (Required)

10.1 Time-Series Splits

All evaluation must be time-aware:
	•	walk-forward splits (train on past → test on next period)
	•	no random shuffles

10.2 Purged & Embargoed CV (required for event-based labels)

When labels overlap in time, use:
	•	purging: remove samples whose label window overlaps test intervals
	•	embargo: exclude a buffer after test window to prevent leakage

10.3 Multiple Hypothesis Control (required if mining many rules)

When searching many patterns:
	•	enforce minimum support (N trades)
	•	keep an untouched “final test” period
	•	use bootstrap/permutation tests for rule significance
	•	track false discovery risk; don’t promote “one-hit wonders”

10.4 Cost/Slippage Modeling (harsh by default)

Backtests must include:
	•	commissions/fees (config)
	•	slippage (bps) and/or spread-based model
	•	latency assumptions (polling delay)
	•	market impact approximation (optional)

10.5 Promotion Gates (Offline → Paper → Live)

Hard gates (configurable, but explicit):
	•	minimum dataset size (e.g., ≥ X candidates and ≥ Y days)
	•	out-of-sample results:
	•	positive expectancy after costs
	•	drawdown ≤ threshold
	•	stability across at least K walk-forward folds
	•	paper trading period with live fills and reconciliation
	•	kill-switch rules in place (Section 12)

All promotions must be logged with version ids.

⸻

11) Live Signal Engine (Production)

11.1 Runtime Steps
	1.	Ingest live UW + Alpaca data
	2.	Build near-real-time rollups (e.g., 5m window)
	3.	Generate candidates via rules
	4.	Score candidates via ML meta-model (if enabled)
	5.	Apply risk filters and portfolio constraints
	6.	Emit signals_live rows (with evidence pointers)
	7.	Execute via Alpaca (paper/live depending on mode)

11.2 Signal Table

signals_live must include:
	•	timestamp, ticker, direction
	•	entry logic (market/limit, time-in-force)
	•	horizon/exit rules
	•	rule_id, model_version
	•	expected_return, p_take, risk_score
	•	evidence pointers (event_ids/rollup_ids)
	•	decision_trace_json (all computed inputs used to decide)

⸻

12) Execution & Risk Layer (Alpaca)

12.1 Modes
	•	OFF (research only)
	•	PAPER
	•	LIVE

12.2 Risk Controls (Required)
	•	max concurrent positions
	•	max exposure per ticker
	•	max daily loss / max drawdown kill switch
	•	volatility regime filter (optional)
	•	time-of-day bans (optional)
	•	circuit breakers if data lagging or model drift detected

12.3 Exits

Support:
	•	time-based exit (T bars/days)
	•	triple-barrier exits (TP/SL + time)
	•	end-of-session forced exit (optional)

12.4 Trade Tracking (Alpaca as source of truth)
	•	Realized PnL uses Alpaca fills
	•	Mark-to-market uses Alpaca bars
	•	Store:
	•	orders, fills, positions
	•	trade journal entries linking back to the signal and evidence

⸻

13) Daily LLM Review Agent (Recursive Improvement Loop)

13.1 Trigger

Run after POST session close (e.g., 20:05 ET) or configurable.

13.2 Inputs (must be machine-readable)
	•	trades (fills, timestamps, slippage vs model)
	•	signals_live (decision traces)
	•	performance metrics:
	•	by rule, by model, by ticker, by session, by regime
	•	drift metrics:
	•	feature distribution shift (PSI/KL)
	•	degradation vs rolling baseline
	•	backtest vs live deltas:
	•	model calibration drift
	•	execution slippage drift
	•	errors/incidents:
	•	ingestion gaps, lag, DLQ events

13.3 Outputs (must be auditable)

The agent produces an Improvement Report with:
	•	what worked / what didn’t (stats + confidence)
	•	suggested actions categorized by impact:
	1.	Config changes (thresholds, disable rules, tighten filters)
	2.	New rule hypotheses (clearly stated)
	3.	Feature changes (add/remove, fix computation)
	4.	Model changes (retrain schedule, calibration, hyperparameters)
	5.	Risk changes (position sizing, kill-switch thresholds)
	6.	Engineering changes (bug fixes, data quality fixes)

13.4 Change Proposal Workflow (No silent self-modification)

Agent must NOT deploy directly.
It must create one of:
	•	a config patch (YAML diff) + expected effect + rollback plan
	•	a PR with code changes + tests + benchmark report
	•	a “do not trade” recommendation (kill-switch) if conditions are unsafe

All proposals must include:
	•	rationale
	•	exact files/parameters to change
	•	evidence pointers (queries and ids)
	•	test plan (unit + integration + backtest rerun)

13.5 Safety Gates
	•	Auto-run CI:
	•	unit tests
	•	pipeline integration test (ingest → features → signal)
	•	backtest regression suite
	•	Require human approval (or explicit “auto-approve config-only within bounds” policy)

⸻

14) RAG Layer (Searchable by Agents)

14.1 What gets indexed

Index summaries + structured docs, not every raw event:
	•	ticker_hour_summary (primary)
	•	major_event_card (large sweeps/blocks/dark prints)
	•	daily_recap_ticker and daily_recap_portfolio
	•	strategy_backtest_report (per strategy/model version)
	•	trade_journal_doc (per trade: thesis, evidence, outcome)
	•	dataset_catalog_docs (schemas, query examples, glossary)

14.2 Retrieval Requirements (Hybrid)

Query API supports:
	•	vector similarity + keyword search
	•	strong metadata filters:
	•	ticker(s), date range, session
	•	doc_type, rule_id, model_version
	•	premium thresholds (where supported)
	•	returns:
	•	doc text
	•	metadata
	•	pointers to raw rows and rollups

14.3 Agent-Friendly Query Examples
	•	“Show me the last 10 major call sweep clusters in TSLA and whether they led to 1D upside.”
	•	“Summarize today’s performance by rule_id and identify which rules degraded vs last week.”
	•	“Find all trades where slippage exceeded 30 bps and correlate with time-of-day.”

⸻

15) Observability & Robust Error Logging (Required)

15.1 Structured Logging

All services log JSON with:
	•	service, run_id, trace_id
	•	event_type, ticker
	•	provider_request_id if available
	•	error_code enum + stacktrace
	•	timing: fetch latency, write latency, lag

15.2 Error Taxonomy

Standard error codes include:
	•	PROVIDER_AUTH_FAILED, PROVIDER_RATE_LIMIT, PROVIDER_TIMEOUT
	•	PROVIDER_SCHEMA_DRIFT, DESERIALIZATION_ERROR, VALIDATION_ERROR
	•	HOTSTORE_WRITE_FAILED, LAKE_WRITE_FAILED, VECTOR_WRITE_FAILED
	•	BACKPRESSURE, DUPLICATE_EVENT, CLOCK_SKEW_DETECTED

15.3 DLQ + Replay
	•	failed events go to DLQ with full context
	•	replay tool can reprocess DLQ safely (idempotent writes)

15.4 Metrics + Alerts

Alert on:
	•	ingestion quiet during sessions
	•	lag > threshold
	•	DLQ spike
	•	schema drift
	•	execution failures
	•	drift thresholds breached

⸻

16) Security
	•	Secrets in env/secret manager only
	•	Query API auth (API keys/JWT)
	•	Audit log for:
	•	queries
	•	exports
	•	trade actions & kill switches
	•	Principle of least privilege:
	•	read-only creds where possible
	•	trading creds isolated to execution service

⸻

17) Deliverables (What the coding agent must implement)

17.1 Services
	•	Connectors: UW flow/dark/alerts; Alpaca market; Alpaca trading
	•	Processors: normalizer, rollups, features, labels, backtest, signal engine
	•	RAG: indexer + query API
	•	EOD agent: report generator + PR/config patch generator

17.2 Data Tables (minimum)
	•	bronze events + silver tables for each feed
	•	gold rollups
	•	feature store + labels
	•	candidates + signals
	•	orders/fills/trades + PnL snapshots
	•	model registry + experiment registry
	•	rule registry + backtest results
	•	RAG docs index pointers

17.3 CLI / Jobs
	•	ingest_live (runs continuously)
	•	build_rollups (continuous or scheduled)
	•	feature_engine_live (continuous)
	•	signal_engine_live (continuous)
	•	execute_signals (continuous / scheduled)
	•	eod_review_agent (scheduled)
	•	retrain_models (scheduled, gated)
	•	reconcile_backfill (daily)

⸻

18) Vertical Slice Milestones (End-to-End Each Time)
	1.	Slice A: UW Flow (poll) → hot store + lake → query raw events
	2.	Slice B: Alpaca 1m bars → join keys + rollups
	3.	Slice C: Gold rollups → RAG docs → /search works with pointers
	4.	Slice D: Rule engine → candidates → backtest report (offline)
	5.	Slice E: Live candidate generation + paper trading execution + PnL tracking
	6.	Slice F: Meta-label model + walk-forward validation + paper gating
	7.	Slice G: EOD review agent generates proposals + PR/config patch output
	8.	Slice H: Hardening (reconcile/backfill, drift detection, kill-switches, **Automated Test Suite**)
	9.	Slice I: UW websocket upgrade path (swap transport, same pipeline)

⸻

19) Acceptance Criteria (v1)
	•	Live ingestion runs through PRE/REG/POST with low lag and no silent data loss
	•	Rule-based candidate generation produces deterministic, reproducible outputs
	•	Backtests:
	•	time-series walk-forward
	•	purged/embargoed CV (where needed)
	•	costs/slippage included
	•	produces a “promotion report” with explicit gates
	•	Paper trading:
	•	signals executed
	•	Alpaca fills stored and reconciled
	•	daily PnL + attribution by rule/model
	•	RAG search:
	•	agents can retrieve summaries + evidence pointers for any signal/trade
	•	EOD agent:
	•	produces improvement report + at least one valid proposal artifact (config patch or PR)
	•	proposals are auditable and do not auto-deploy without gates

If you want to make this even tighter for the coding agent, the next file to add to the repo is `docs/promotion_gates.md` with the exact numeric thresholds you want for: minimum sample size, OOS expectancy, drawdown limits, and the paper→live promotion checklist.



ADDENDUM:
# PRD: Poetiq-Style Meta-Solver Layer for UW Options Signal Platform

**Codename:** “Orion-Poetiq”
**Base System:** Real-Time UW + Alpaca Data Lake + Signal Engine + Promotion Gates v1
**Date:** December 2025

---

## 0. Purpose & Vision

The existing platform:

- Ingests real-time **Unusual Whales** (UW) flow + **Alpaca** 1m bars into a lakehouse.
- Generates rule-first candidates, then uses ML meta-labeling, with strict **backtest & promotion gates** for Research → Shadow → Paper → Limited Live → Scaled Live.
- Runs a **daily LLM EOD Review Agent** that proposes config/code improvements as auditable artifacts.  [oai_citation:0‡PRD.md](sediment://file_0000000095f871fdb01d43f42975fb8c)

This PRD adds a **Poetiq-style meta-solver layer**:

> Treat each strategy version (rules + features + model + risk config) as a **Solver**. Maintain a library of solvers. Evaluate them systematically on historical “tasks” (event windows). Use an LLM-driven meta‑search engine to propose solver variants, evaluate them, and promote only those that pass the existing promotion gates.

The live system still runs rule+ML pipelines, but now those pipelines are **discovered, ranked, and evolved** by a meta-layer, not hand-tuned.

---

## 1. Scope

### 1.1 In-scope for this PRD

- Definition of **Solver** as a first-class object over:
  - Rule sets
  - Feature sets
  - ML meta-models
  - Execution & risk configs
  - Promotion stage
- A **Solver Library & DSL** for defining and versioning solvers (backed by DB).
- An **Evaluation Harness** that runs solvers against historical datasets, leveraging existing feature & label pipelines and backtest engine.  [oai_citation:1‡PRD.md](sediment://file_0000000095f871fdb01d43f42975fb8c)
- A **Meta-Search Orchestrator** that:
  - Generates solver variants (Poetiq-style edits).
  - Runs evaluation batches.
  - Scores & ranks solvers.
- A **Runtime Solver Router** that selects which solver(s) to use for live signals, per context (ticker, regime, stage).
- Integration with:
  - Existing **promotion gates** (research → shadow → paper → live).  [oai_citation:2‡promotion_gates.md](sediment://file_00000000c61471fdb4bc5ee54a804122)
  - Existing **EOD LLM Review Agent**.

### 1.2 Out of scope

- Changes to underlying ingestion (UW/Alpaca connectors) or lakehouse plumbing.  [oai_citation:3‡PRD.md](sediment://file_0000000095f871fdb01d43f42975fb8c)
- Changes to promotion thresholds in `promotion_gates.md` (numeric values remain configurable elsewhere).  [oai_citation:4‡promotion_gates.md](sediment://file_00000000c61471fdb4bc5ee54a804122)
- High-frequency microstructure modelling or fully autonomous code deployment (still forbidden).

---

## 2. Existing System Summary (Context)

From the base PRD:  [oai_citation:5‡PRD.md](sediment://file_0000000095f871fdb01d43f42975fb8c)

- **Connectors & Storage**
  - UW connectors for flow, dark pool, alerts.
  - Alpaca connectors for 1m bars + trading.
  - Bronze/Silver/Gold tables; feature store (`features_window`, `features_event`) + labels (`labels_window`, `labels_event`).

- **Signal Stack**
  - **Rule-first hypotheses** → `candidates` (event-level).  [oai_citation:6‡PRD.md](sediment://file_0000000095f871fdb01d43f42975fb8c)
  - **ML meta-labeling** to decide TRADE / SKIP / SIZE.
  - Robust time-series validation (walk-forward, purged/embargoed CV, cost/slippage, multiple hypothesis control).

- **Execution & Risk**
  - `signals_live` table with decision traces.
  - Alpaca orders/fills/positions; kill-switches; daily/rolling drawdown limits.

- **Governance**
  - Stage model with explicit promotion gates and demotion rules.  [oai_citation:7‡promotion_gates.md](sediment://file_00000000c61471fdb4bc5ee54a804122)
  - EOD LLM Review Agent that produces Improvement Reports and PR/config proposals (never directly deploys).  [oai_citation:8‡PRD.md](sediment://file_0000000095f871fdb01d43f42975fb8c)
  - RAG layer indexing backtest reports, trade journals, and daily recaps.  [oai_citation:9‡PRD.md](sediment://file_0000000095f871fdb01d43f42975fb8c)

This PRD plugs into **feature store, backtest engine, promotion gates, and LLM review**—no changes to raw data contracts.

---

## 3. Core Concepts

### 3.1 Solver

A **Solver** is a versioned, declarative definition of a complete signal→decision pipeline:

- `ruleset_id`: which rule(s) produce candidates (e.g. “Bullish Sweep + Confirming Dark”).  [oai_citation:10‡PRD.md](sediment://file_0000000095f871fdb01d43f42975fb8c)
- `feature_set_id`: which features from `features_event` / `features_window` it uses.
- `model_version`: ML meta-model (or `null` for rule-only).
- `risk_profile_id`: sizing, max positions, symbol filters, time-of-day bans.
- `promotion_stage`: Research / Shadow / Paper / Limited Live / Scaled Live (mirrors promotion_gates).  [oai_citation:11‡promotion_gates.md](sediment://file_00000000c61471fdb4bc5ee54a804122)
- `runtime_policy`: runtime-specific behavior (e.g. require strong confirmation vs moderate).

In promotion_gates language, a **Strategy Version** is now an **instance of Solver**.  [oai_citation:12‡promotion_gates.md](sediment://file_00000000c61471fdb4bc5ee54a804122)

### 3.2 TickerSnapshot / Evaluation Task

A **Task** is “evaluate solver S on all candidate events within [t0, t1] and its corresponding labels”.

We reuse existing **feature + label** generation and backtest engine infrastructure.  [oai_citation:13‡PRD.md](sediment://file_0000000095f871fdb01d43f42975fb8c)

### 3.3 Meta-Experiment

A **Meta-Experiment** is an offline search run:

- Seed solver(s).
- Generation of N solver variants via edit operations.
- Evaluation on train/validation splits.
- Comparison vs baseline using robust metrics.

The meta-layer is responsible for generating candidate solvers but **must obey promotion gates** for any deployment.

---

## 4. Data Model Extensions

We assume existing tables for events, features, labels, `candidates`, `signals_live`, model registry, experiment registry, and promotion-gate tracking.

New or extended tables:

### 4.1 `solvers`

Defines each solver (strategy version).

| Column             | Type        | Description |
|--------------------|------------|-------------|
| id                 | UUID (PK)  | Solver identifier |
| name               | TEXT       | Human-readable name |
| version            | INT        | Monotonic version |
| status             | ENUM       | `draft`, `candidate`, `active`, `deprecated` |
| stage              | ENUM       | `research`, `shadow`, `paper`, `limited_live`, `scaled_live` |
| ruleset_id         | UUID       | FK to rule registry (rule definitions) |
| feature_set_id     | UUID       | FK to feature set registry |
| model_version      | UUID NULL  | FK to model registry (meta-model) |
| risk_profile_id    | UUID       | FK to risk/risk_profile config |
| definition_json    | JSONB      | Full solver DSL (see §5.1) |
| parent_solver_id   | UUID NULL  | Lineage |
| created_by         | TEXT       | `"human"`, `"llm_eod_agent"`, `"meta_agent"` |
| created_at         | TIMESTAMPTZ| Creation time |
| notes              | TEXT       | Rationale |

### 4.2 `solver_runs`

One row per solver per backtest / evaluation run (offline or live shadow analysis).

| Column             | Type        | Description |
|--------------------|------------|-------------|
| id                 | UUID (PK)  | Run ID |
| solver_id          | UUID       | FK `solvers.id` |
| dataset_tag        | TEXT       | `train`, `val`, `test`, `live_replay`, `shadow` |
| time_window_start  | DATE/TIMESTAMP | Start of evaluation window |
| time_window_end    | DATE/TIMESTAMP | End of evaluation window |
| num_candidates     | INT        | # rule-fired candidates seen |
| num_trades         | INT        | # trades actually taken (post meta-model) |
| gross_pnl          | NUMERIC    | Gross PnL over window |
| net_pnl            | NUMERIC    | After costs/slippage |
| profit_factor      | NUMERIC    | PF over window |
| max_drawdown_pct   | NUMERIC    | Max DD in % of equity |
| expect_return_bp   | NUMERIC    | Mean trade net return (bps) |
| metrics_json       | JSONB      | Extra metrics (Sharpe, hit rate, etc.) |
| created_at         | TIMESTAMPTZ| Run exec time |

### 4.3 `solver_metrics`

Aggregated metrics per solver per context.

| Column            | Type        | Description |
|-------------------|------------|-------------|
| id                | UUID (PK)  | |
| solver_id         | UUID       | |
| sector            | TEXT       | `ALL` or GICS sector |
| ticker_bucket     | TEXT       | `TOP_UW`, `HIGH_LIQUIDITY`, etc. |
| horizon_profile   | TEXT       | `intraday`, `swing` |
| dataset_tag       | TEXT       | `train`, `val`, `test` |
| num_runs          | INT        | # solver_runs aggregated |
| num_trades        | INT        | Total trades |
| info_ratio        | NUMERIC    | E.g. IR vs benchmark |
| profit_factor     | NUMERIC    | |
| oos_expect_bp     | NUMERIC    | OOS expectancy |
| max_dd_pct        | NUMERIC    | Worst drawdown |
| stability_score   | NUMERIC    | fold-to-fold stability |
| metrics_json      | JSONB      | Additional |
| evaluated_at      | TIMESTAMPTZ| |

### 4.4 `meta_experiments`

Track meta-search experiments.

| Column       | Type        | Description |
|--------------|------------|-------------|
| id           | UUID (PK)  | |
| name         | TEXT       | |
| objective    | TEXT       | e.g. "maximize intraday 1D PF" |
| base_solver_ids | UUID[]  | Seed solvers |
| config_json  | JSONB      | Candidate count, dataset, bounds |
| status       | ENUM       | `running`, `completed`, `failed` |
| started_at   | TIMESTAMPTZ| |
| completed_at | TIMESTAMPTZ| |
| summary      | TEXT       | Human-readable |

### 4.5 `solver_edits`

Captures how new solvers were derived.

| Column          | Type        | Description |
|-----------------|------------|-------------|
| id              | UUID (PK)  | |
| experiment_id   | UUID       | FK `meta_experiments.id` |
| base_solver_id  | UUID       | FK `solvers.id` |
| new_solver_id   | UUID       | FK `solvers.id` |
| edit_json       | JSONB      | Structured patch (see §5.4) |
| generated_by    | TEXT       | `"llm_eod_agent"` or `"meta_agent"` |
| reward          | NUMERIC    | Improvement vs base (e.g. ΔPF, ΔIR) |
| evaluated_at    | TIMESTAMPTZ| |

(Where desired, reuse/extend the existing **model registry**, **rule registry**, and **experiment registry** described as deliverables in v1.  [oai_citation:14‡PRD.md](sediment://file_0000000095f871fdb01d43f42975fb8c))

---

## 5. Functional Requirements

### 5.1 Solver DSL

The solver’s `definition_json` must follow a strict DSL schema. Example:

```json
{
  "rules": [
    "bullish_sweep_confirming_dark",
    "no_earnings_within_24h"
  ],
  "features": {
    "event_features": ["uw_premium_norm", "uw_vol_oi_ratio", "dp_cluster_score"],
    "window_features": ["5m_return", "15m_range", "session_volatility"],
    "feature_engine_version": "v3"
  },
  "model": {
    "type": "meta_classifier",
    "model_version": "lgbm_meta_2025_11",
    "thresholds": {
      "p_take_min": 0.6,
      "downsize_band": [0.5, 0.6]
    }
  },
  "risk": {
    "risk_per_trade_pct": 0.25,
    "max_positions": 5,
    "max_ticker_exposure_pct": 5,
    "time_of_day_bans": ["FIRST_5_MIN", "LAST_5_MIN"],
    "session_filter": ["REG", "PRE"]
  },
  "execution": {
    "order_type": "LIMIT",
    "max_spread_frac": 0.2,
    "slippage_bps_assumed": 20
  },
  "promotion_policy": {
    "target_stage": "paper",
    "min_trades_for_eval": 300,
    "gates_profile": "default"
  }
}

FR 5.1.1 – Validation
	•	DSL must be validated on creation/update:
	•	All referenced rules / features / models / risk profiles exist.
	•	Numeric ranges within safe bounds (e.g. risk_per_trade_pct ≤ global cap from promotion_gates).  ￼
	•	Execution constraints obey platform-wide risk rules.

5.2 Evaluation Harness

Reuses feature & label pipelines and backtest engine:  ￼

FR 5.2.1 – RunSolverBacktest

Implement a service:

def run_solver_backtest(
    solver_id: UUID,
    dataset_spec: DatasetSpec
) -> SolverRun:
    ...

	•	DatasetSpec includes:
	•	Time range(s), horizon(s) (5–60m intraday, 1–3D swing).
	•	Universe filters (tickers by UV, sector, ADV buckets).
	•	Split type: train, val, test.

FR 5.2.2 – Backtest logic

For each event-level candidate in the dataset:
	1.	Generate features using feature_engine.  ￼
	2.	Apply solver’s ruleset (filters).
	3.	If ML model present:
	•	Compute p_take, possibly size_modifier.
	•	Apply deterministic decision policy (TRADE / SKIP / SIZE_DOWN).  ￼
	4.	Simulate trade using triple-barrier labels & cost model:
	•	Incorporate slippage, commissions, latency assumptions.  ￼
	5.	Aggregate trade outcomes into metrics consistent with promotion_gates: profit factor, net expectancy, max drawdown, etc.  ￼

FR 5.2.3 – Time-series discipline
	•	Use walk-forward splits, purged/embargoed CV where labels overlap.
	•	No random shuffling.

FR 5.2.4 – Multiple-hypothesis control
	•	For experiments testing many solvers:
	•	Enforce minimum sample size (min N trades) already in promotion_gates.  ￼
	•	Maintain untouched “final test” period for unbiased evaluation.

5.3 Meta-Search Orchestrator

FR 5.3.1 – Candidate generation

Meta-Search Orchestrator uses an LLM MetaAgent (Poetiq-style) to generate solver_edits:
	•	Inputs:
	•	Existing solvers definitions + metrics.
	•	RAG summaries for strategies & experiments (already indexed).  ￼
	•	Outputs:
	•	A set of structured edit_json objects (see §5.4).

Examples:
	•	Tighten or loosen rule thresholds.
	•	Change feature subset (drop leaky or unstable features).
	•	Adjust model thresholds or calibration.
	•	Change risk caps within pre-approved bounds.

FR 5.3.2 – Experiment cycle

Given base solver(s) + objective:
	1.	Generate N candidate solver definitions via solver_edits.
	2.	Insert candidates with status = 'candidate', stage = 'research'.
	3.	For each candidate:
	•	Run Evaluation Harness on train+val.
	•	Store solver_runs + solver_metrics.
	4.	Compute reward vs base solver(s) (Delta PF, Delta IR, stability).
	5.	Optionally apply an RL-style update to MetaAgent’s policy (GRPO-like relative reward scheme over edits).

Meta-Search Orchestrator must not alter promotion stages directly; instead it annotates candidates with “recommended stage” based on promotion_gates metrics (see §5.5).

5.4 SolverEdit Schema

edit_json is a set of constrained operations, e.g.:

{
  "ops": [
    {
      "op": "modify_rule_threshold",
      "rule_id": "bullish_sweep_confirming_dark",
      "param": "min_premium_usd",
      "delta": 50000
    },
    {
      "op": "toggle_feature",
      "feature_set_id": "event_features_v3",
      "feature_name": "intraday_vix_change",
      "enabled": true
    },
    {
      "op": "modify_model_threshold",
      "model_version": "lgbm_meta_2025_11",
      "param": "p_take_min",
      "delta": 0.05
    }
  ]
}

FR 5.4.1 – Edit validation
	•	Edits must be validated against:
	•	Global risk constraints (e.g., risk per trade, drawdown caps).
	•	Data integrity (cannot remove mandatory leakage-safe features, cannot change label definitions).
	•	Some ops (e.g., changing labels) are disallowed for automatic edits.

5.5 Integration with Promotion Gates

Promotion gates define numeric thresholds for moving Strategy Versions from research → shadow → paper → limited live → scaled live.  ￼

FR 5.5.1 – Stage compatibility
	•	Each solver has a stage field mirroring promotion_gates stages.
	•	For a solver to be eligible for a stage, its aggregated metrics (solver_metrics) must satisfy that stage’s thresholds:
	•	Research → Shadow: data sufficiency, walk-forward OOS PF and expectancy, fold consistency.  ￼
	•	Shadow → Paper → Limited Live → Scaled Live: as defined (PF, DD, slippage, etc.).

FR 5.5.2 – Recommendation vs decision
	•	Meta-Search Orchestrator and EOD LLM may recommend stage changes by writing structured recommendations into a promotion_recommendations view (could be a lightweight materialized view or query).
	•	Actual stage changes must:
	•	Either be triggered by an explicit promotion workflow (human or automated gating service).
	•	Be recorded with artifacts required in promotion_gates (backtest reports, live reports, approval record).  ￼

FR 5.5.3 – Demotion
	•	Demotion rules from promotion_gates still apply (rolling performance/drawdown, drift, incidents).  ￼
	•	A background “Health Monitor” evaluates solver_metrics on rolling windows and proposes demotions if thresholds are breached.

5.6 Runtime Solver Router & Ensembling

FR 5.6.1 – Router

Implement a SolverRouter service:

def select_solvers_for_context(
    context: LiveContext
) -> List[UUID]:
    ...

Where LiveContext includes:
	•	Ticker, sector, ADV/liquidity bucket.
	•	Session (PRE, REG, POST).  ￼
	•	Volatility regime (e.g. realized volatility, VIX regime).
	•	Stage (only solvers at stage ≥ strategy’s allowed live stage are eligible).

Router logic:
	•	Query solver_metrics for solvers with status='active' and appropriate stage and context.
	•	Select top‑K (configurable, typically 1–3) by objective (e.g. info_ratio or PF subject to max drawdown constraints).

FR 5.6.2 – Ensemble decision
	•	If multiple solvers selected:
	•	Run their decision pipelines independently on the same candidates.
	•	Combine results via weighted aggregation, e.g.:

p_take_ensemble = Σ (p_take_i * weight_i)
where weight_i ∝ solver_i.info_ratio (capped)


	•	Agentic decision layer (existing signal engine) then treats p_take_ensemble as meta-model probability.

	•	Disagreement handling:
	•	If solvers strongly disagree (votes split, PF-weighted signal near 0.5):
	•	Mark candidate as low consensus → either skip or pass to LLM for extra scrutiny.

FR 5.6.3 – Fallback
	•	If no suitable solvers at live stage:
	•	Fallback to a conservative baseline solver that previously passed gates.
	•	If any solver fails at runtime (errors, invalid outputs):
	•	Exclude for that decision and log error; do not halt the pipeline.

5.7 EOD LLM Review Agent Integration

EOD Review Agent already produces daily improvement reports and config/PR proposals.  ￼

FR 5.7.1 – New outputs

Extend EOD agent to:
	•	Use RAG over:
	•	Backtest & live performance by solver.
	•	trade & PnL attribution by solver and rule_id.
	•	Propose SolverEdits instead of free-form config suggestions:
	•	Example: “For Solver A, reduce risk_per_trade_pct from 0.5 to 0.25 due to recent DD” → becomes a structured edit.

FR 5.7.2 – Workflow
	1.	EOD agent writes proposals into solver_edits with generated_by='llm_eod_agent' and reward = NULL.
	2.	Meta-Search Orchestrator picks up these edits, evaluates them using Evaluation Harness, and fills in reward.
	3.	If reward positive and promotion_gates satisfied, EOD agent may also produce a PR/config patch tying this solver change to code/infra, consistent with existing change-management process.

⸻

6. Error Logging & Observability (Poetiq Layer)

We reuse and extend the existing structured logging & DLQ strategy, which already defines error codes, DLQ, and alerts for ingestion and execution.

6.1 Logging Schema

All meta-layer components log JSON entries with:
	•	component ∈ SolverEngine, MetaSearch, EvaluationManager, SolverRouter, EODAgent.
	•	severity ∈ DEBUG, INFO, WARN, ERROR, CRITICAL.
	•	entity_type ∈ solver, experiment, run, strategy_version.
	•	entity_id (UUID).
	•	message (short text).
	•	metadata (JSON; includes metrics summary, error codes, stack traces if any).

6.2 Error Taxonomy (extensions)

Add codes to existing taxonomy:  ￼
	•	SOLVER_DSL_VALIDATION_FAILED
	•	SOLVER_EVAL_FAILED (backtest error, data missing)
	•	META_EDIT_INVALID (unsafe or out-of-bounds edits)
	•	META_METRICS_INCONSISTENT (e.g., impossible PF vs trade count)
	•	ROUTER_NO_ELIGIBLE_SOLVER (router fallback triggered)

6.3 Metrics & Alerts
	•	Metrics:
	•	Number of active solvers by stage.
	•	Meta-experiments per week.
	•	Fraction of solver candidates that improve over baseline.
	•	Live vs backtest delta per solver (drift).
	•	Alerts:
	•	No active solver for a given context.
	•	Frequent evaluation failures.
	•	Solver promoted to live that starts to violate promotion_gates demotion thresholds.

⸻

7. RAG Integration

RAG already indexes strategy backtest reports, trade journals, daily recaps, and dataset catalogs.  ￼

New doc types:
	•	meta_experiment_report – summary text + metrics for each meta experiment.
	•	solver_profile_doc – high-level description of each solver, including:
	•	DSL snippet,
	•	context where it performs well/badly,
	•	promotion history,
	•	associated risk profile.

Requirements:
	•	Meta-agent and EOD agent must use RAG to ground their proposals in actual data (no free-floating speculation).
	•	UI should allow viewing meta-experiment reports and solver profiles via existing search API.

⸻

8. Vertical Slice Implementation Plan

Implementation must follow vertical slice architecture: each slice touches DB → backend services → orchestrator → minimal UI, with robust error logging.  ￼

VS1 – Solver Library & DSL

Goal: Define and store solvers; run them in offline backtests.
	•	Add solvers, solver_runs, solver_metrics tables + migrations.
	•	Implement DSL schema & validation.
	•	Implement run_solver_backtest using existing backtest engine & feature/label pipelines.
	•	Add minimal UI/admin endpoint:
	•	List solvers.
	•	View a solver definition and its latest metrics.
	•	Logging:
	•	Log SOLVER_DSL_VALIDATION_FAILED for invalid creations.
	•	Log each solver_run summary at INFO.

VS2 – Meta-Search v1 (Static Objective)

Goal: Generate solver variants and evaluate them.
	•	Add meta_experiments, solver_edits.
	•	Implement Meta-Search Orchestrator:
	•	For now, simple LLM prompts to propose edits and limited RL-style scoring.
	•	Wire Evaluation Harness to run experiments.
	•	Initial objective: maximize PF subject to max DD constraint in OOS.
	•	Minimal UI:
	•	List experiments and candidate solvers with ΔPF, ΔDD vs baseline.

VS3 – Runtime Solver Router & Ensemble

Goal: Use best solvers in live signal engine.
	•	Implement SolverRouter and integrate into signal_engine_live.
	•	Router uses solver_metrics to choose solver(s).
	•	Support K=1 (single best solver) and K>1 ensemble modes.
	•	Extended live logs:
	•	For each live signal, store which solver(s) participated and their p_take contributions in signals_live.decision_trace_json.  ￼

VS4 – Promotion Gates + Poetiq

Goal: Align meta-system with promotion_gates.
	•	Implement compatibility checks between solver_metrics and promotion thresholds from promotion_gates.md.  ￼
	•	Implement a “Promotion Recommendation” job that:
	•	Computes whether solvers meet stage gates.
	•	Writes recommendations (with evidence).
	•	Minimal UI:
	•	For each solver, show current stage, whether it meets criteria for next stage, and a “request promotion” button (or API hook).

VS5 – EOD Agent Integration

Goal: Convert EOD suggestions into structured SolverEdits and run them through meta-layer.
	•	Update EOD LLM prompts:
	•	Provide solver performance summaries from RAG.
	•	Ask for structured SolverEdit proposals.
	•	Pipe proposals into solver_edits and let Meta-Search Orchestrator evaluate them.
	•	Ensure change-management still creates PR/config patches and routes through CI + promotion workflow, in line with existing rules.

⸻

9. Non-Functional Requirements
	•	Performance: Meta-Search & Evaluation Harness are offline; they may process large datasets but must be horizontally scalable and resumable.
	•	Safety:
	•	No solver can bypass global kill-switch or risk limits.  ￼
	•	Solvers at stages ≥ paper must be immutable except through controlled, logged edits.
	•	Auditability: For any live trade, we must be able to reconstruct:
	•	Which solver(s) produced the signal.
	•	What their historical performance was at time of decision.
	•	Which meta-experiments led to that solver’s current form.

⸻
