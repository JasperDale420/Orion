	•	Align the change set into one coherent “target state” (Orion PRDv2 + Orion-Poetiq meta-solver + regime/activation upgrades).
	•	Translate Poetiq’s concrete solver-loop mechanics (multi-expert configs, scoring/selection, feedback loops, time budgets, sandboxing) into Orion’s existing MetaSearch/EOD workflows.
	•	Specify the exact system behaviors as testable requirements (functional + non-functional), with clear acceptance criteria.
	•	Define the technical spec details needed to build it (interfaces, schemas, data contracts, failure modes).
	•	Lay out a vertical-slice delivery plan that ships working end-to-end increments with rollback points.
	•	Standardize observability: structured logs, metrics, and an error taxonomy that plugs into Orion’s existing error model and DLQ.

PRD/Specification Hybrid Addendum

Orion: Poetiq-Style Meta-Solver + Multi-Axis Regime + ActivationPolicy + Observability Hardening

Document Meta
	•	Applies to: Orion PRDv2 live trading system and its “Orion-Poetiq” meta-solver extension.
	•	Scope anchor: Orion already includes (a) daily EOD review that can emit solver_mutation proposals and (b) a refinement loop that backtests, requests refinements from a MetaAgent, and promotes to paper when a threshold is met.  ￼ ￼
	•	Key upgrades instituted by this addendum:
	1.	Multi-axis market regime model + persistence + risk scaling + activation rules.  ￼ ￼
	2.	Poetiq-inspired meta-solver mechanics: multi-expert parallel proposals, scoring-based selection, feedback packaging, and time-budget governance.  ￼ ￼
	3.	Robust error logging + error taxonomy extensions consistent with Orion’s ErrorCode pattern and DLQ workflows.  ￼ ￼

⸻

1) Background and Intent

1.1 Current capabilities (baseline)

Orion’s “Orion-Poetiq” PRD already frames the intended direction: treat each strategy version as a Solver, maintain a library, evaluate on historical “tasks” (event windows), and use an LLM-driven meta-search engine to propose variants and promote only those passing promotion gates.  ￼ ￼

Operationally, the existing implementation includes:
	•	EOD Review Scheduler/Runner that executes daily review and then processes solver_mutation proposals.  ￼
	•	Solver mutation processing that builds SolverConfig edits (ops), generates deterministic solver IDs, applies edits, then runs a refinement loop via MetaSearchAgent.refine_and_promote().  ￼
	•	Refinement loop that backtests, computes a composite score, compares to a threshold, requests refinements, and applies the first suggested edit.  ￼

1.2 What Poetiq contributes (mechanics we will concretely adopt)

Poetiq’s ARC solver is a tight reference implementation of a meta-solver loop:
	•	Explicit expert configuration list with knobs for prompts, model ID, temperature, request timeouts, max timeouts, retries, number of experts, iteration caps, and selection probability.  ￼
	•	Time-budget governance: early exit when allotted time/timeouts are exceeded; retries per iteration; token usage aggregation.  ￼ ￼
	•	Keep-best behavior + scoring/feedback packaging: track best result, optionally return best; build feedback from failures to improve next iteration.  ￼ ￼
	•	Sandboxed execution for evaluating generated code with hard timeouts and process kill.  ￼

This addendum specifies how those exact mechanics are implemented in Orion’s meta-solver and evaluation workflow, without inventing new “magic.”

⸻

2) Goals, Non-Goals, and Guardrails

2.1 Goals
	1.	Deterministic, auditable strategy evolution: every meta edit is recorded, evaluated, scored, and either promoted through gates or rejected with a trace. (Orion already stores solver DSL/config and tracks meta experiments/metrics; we will standardize selection and logging.)  ￼ ￼
	2.	Regime-aware execution that reduces overtrading in toxic conditions by:
	•	computing multi-axis regimes (trend/vol/liquidity/risk/session),
	•	applying risk multipliers,
	•	enforcing ActivationPolicy instead of brittle regime routing.  ￼ ￼
	3.	Operational robustness: consistent error codes, structured logs, and time budgets across meta-search, evaluation, and regime computations.  ￼ ￼

2.2 Non-Goals (explicit)
	•	No changes to core ingestion connectors/lakehouse plumbing in this addendum (consistent with Orion-Poetiq PRD’s stated out-of-scope).  ￼
	•	No autonomous code deployment to production (still forbidden by the Orion-Poetiq scope).  ￼

2.3 Guardrails
	•	Config-first: regime outputs and agent recommendations must remain config-only for activation and risk multipliers.  ￼
	•	Strict schema validation for solver definitions via Solver DSL before evaluation/promotion.  ￼

⸻

3) Functional Requirements

FR-1 Multi-Axis Market Regime Service (deterministic v1)

Description: Implement regime computation as a deterministic service producing a MarketRegimeSnapshot with axes: trend, vol, liquidity, risk, session; include hysteresis per axis to avoid flapping.  ￼

Requirements
	•	Inputs include SPY and optionally a volatility proxy (VXX preferred), plus session/time inputs (exchange timezone).  ￼
	•	Features include baseline vol, vol-of-vol, shock flag, liquidity proxy, risk score.  ￼
	•	Classification rules per axis must follow the specified threshold logic (trend/vol/liquidity/risk/session).  ￼
	•	Smoothing must be per-axis hysteresis + minimum hold time (with SHOCK override).  ￼

Acceptance criteria
	•	Unit tests cover each axis classification and hysteresis transitions (explicitly required by the existing vertical slice plan).  ￼

⸻

FR-2 Regime Persistence and Analytics Schema Updates

Description: Persist regimes in a richer regime_history table and record regime tags at signal/trade entry and exit.

Requirements
	•	Regime history schema includes: timestamp, model_version, axis labels, feature values, confidence JSON.  ￼
	•	Replace per-trade regime_at_entry/exit with regime_tags_entry_json and regime_tags_exit_json (plus optional compact label).  ￼

Acceptance criteria
	•	A new regime snapshot written to regime_history includes model_version.  ￼

⸻

FR-3 Risk Scaling by Regime

Description: RiskManager applies multipliers keyed by regime tags (starting with vol + session as minimum viable set).

Requirements
	•	Apply multipliers in RiskManager sizing logic and log sizing inputs.  ￼
	•	Minimum viable set explicitly includes vol regime + session regime + ActivationPolicy replacement.  ￼

Acceptance criteria
	•	Paper-trade at least one strategy with risk multipliers enabled and show reduced drawdown during high-vol/shock regimes as part of validation workflow (as specified).  ￼

⸻

FR-4 Strategy ActivationPolicy (replace brittle regime routing)

Description: Replace strategies_by_regime routing with deterministic ActivationPolicy evaluation over axes.

Requirements
	•	Remove the set intersection routing logic and replace with StrategyActivationPolicy that checks allowed lists across axes.  ￼
	•	Add deterministic activation tests across bars (no flapping).  ￼

Acceptance criteria
	•	The router produces consistent active strategy set given identical (candidate, snapshot) input across repeated runs.

⸻

FR-5 Solver DSL is the canonical solver definition

Description: Every Solver must have a strict DSL representation stored as solvers.definition_json and validated.

Requirements
	•	Solver DSL must validate rule IDs via registry existence checks.  ￼
	•	Provide best-effort conversion from legacy SolverConfig blobs to DSL.  ￼

Acceptance criteria
	•	Any solver mutation or ingest proposal that fails DSL validation is rejected with ErrorCode.SOLVER_DSL_VALIDATION_FAILED (already defined in Orion’s error taxonomy).  ￼

⸻

FR-6 Poetiq-style multi-expert meta-edit proposals with scoring-based selection

Description: Upgrade MetaAgent/MetaSearchAgent so refinements are not “take the first edit,” but “generate a set of candidate edits, evaluate, then select the best.”

Why this is grounded
	•	Today Orion’s refinement loop applies the first edit returned by MetaAgent.  ￼
	•	Poetiq explicitly supports multiple experts, multiple iterations, and “return best” behavior.  ￼ ￼

Requirements
	•	Implement a MetaAgent configuration model analogous to Poetiq’s CONFIG_LIST fields:
	•	prompts (solver/refinement prompt templates),
	•	model ID,
	•	temperature,
	•	request timeout,
	•	max total timeouts/time,
	•	per-iteration retries,
	•	number of experts,
	•	max iterations,
	•	selection_probability,
	•	return_best_result.  ￼
	•	For each refinement iteration in Orion:
	1.	ask N experts for edits in parallel,
	2.	validate each edit via DSL rules,
	3.	evaluate each edit variant (backtest harness),
	4.	select the best by composite score delta vs base, and apply only that edit.

Acceptance criteria
	•	Given the same base solver + same refinement context + same seed, the chosen edit is deterministic.
	•	The system records all proposed edits and their evaluation results, not just the winner (audit trail requirement).

⸻

FR-7 Time budgets, retries, and early-exit governance for MetaAgent calls and evaluations

Description: Enforce per-solver and per-experiment time budgets and timeouts analogous to Poetiq’s execution loop.

Why this is grounded
	•	Poetiq enforces request timeouts, max remaining time, max remaining timeouts, and per-iteration retries, with early exit when budgets are exceeded.  ￼
	•	Orion already runs repeated refinement iterations and backtests in a loop.  ￼

Requirements
	•	Define and store budgets at:
	•	MetaExperiment level (total wall-clock budget),
	•	SolverRun evaluation level (max runtime),
	•	LLM request level (timeout + retry caps).
	•	Record token usage for MetaAgent calls in experiment artifacts (Poetiq aggregates prompt/completion tokens).  ￼ ￼

Acceptance criteria
	•	When budgets are exceeded, the experiment ends gracefully with a structured terminal event, and no partial solver is promoted.

⸻

FR-8 Safe evaluation isolation policy (sandbox-inspired)

Description: Introduce hard timeouts and isolation for potentially expensive evaluation steps.

Why this is grounded
	•	Poetiq evaluates generated code by executing a subprocess with a hard timeout and kill, returning “timeout” on expiration.  ￼

Requirements
	•	Every solver evaluation (backtest) must have a hard timeout that:
	•	cancels the evaluation,
	•	marks the run failed with ErrorCode.SOLVER_EVAL_FAILED,
	•	records the timeout as a specific failure reason.
	•	Keep generated changes config-only; this isolation is for evaluation runtime, not for executing arbitrary LLM code.

Acceptance criteria
	•	A deliberately “hung” evaluation job is terminated and does not block the refinement loop.

⸻

4) System Interfaces and Workflows

4.1 Daily EOD workflow (end-to-end)

Current flow (baseline)
	•	EOD review runs, logs completion, extracts solver_mutation proposals, then processes them in a refinement loop.  ￼ ￼

Addendum changes
	•	Replace single-edit application with multi-expert proposal generation + evaluation + best-selection (FR-6).
	•	Attach structured regime context in the EOD prompt inputs (EOD agent already lists “Regime Data” as a data source).  ￼

4.2 Promotion and governance
	•	Gatekeeper audits solvers and prioritizes demotion safety over promotion speed.  ￼
	•	Promotion recommendations are visible and can be approved via API endpoints.  ￼

4.3 Public API (existing, must remain stable)
	•	/solvers, /solvers/{solver_id}, /metrics, /experiments, /promotions, /promotions/{id}/approve exist and are API-key protected.  ￼ ￼

Addendum requirement
	•	Add read-only endpoints for:
	•	regime history retrieval (filterable by time/session),
	•	activation policy inspection,
	•	meta-edit proposal evaluation summaries.

(These are additive and do not break existing endpoints.)

⸻

5) Data Model Additions and Changes

5.1 Regime analytics schema

Implement the regime_history and trade/signal schema updates as specified.  ￼

5.2 Solver canonical definition
	•	Ensure solvers.definition_json is always present and DSL-valid (FR-5).  ￼

5.3 Meta-edit artifacts
	•	Store:
	•	each expert’s proposed edits,
	•	per-edit validation outcome,
	•	per-edit evaluation metrics,
	•	composite score and reward delta.

Orion already updates edit reward as new_score - base_score when processing edits; the addendum requires capturing the full candidate set before selection.  ￼

⸻

6) Scoring and Selection Spec

6.1 Composite score (Orion)
	•	Continue using Orion’s composite score computed from solver metrics during refinement; log the score, sharpe, and profit factor at each iteration (already done).  ￼

6.2 Best-of-N selection (Poetiq-inspired)
	•	Poetiq keeps best_result and can return_best_result.  ￼
	•	Addendum mandates the same behavior for Orion refinement iterations:
	•	evaluate all candidate edits,
	•	select the edit with maximum composite score improvement (reward delta),
	•	if none improve, either:
	•	stop early, or
	•	request another iteration (bounded by time budgets, FR-7).

⸻

7) Observability, Error Logging, and DLQ

7.1 Structured logging requirements

Regime logs must include
	•	regime.model_version, regime.tags, regime.confidence, regime.features.  ￼

Meta-solver logs must include
	•	experiment_id, solver_id, base_solver_id, edit_id, iteration, score, reward_delta, and budget counters (timeouts used, elapsed seconds, token usage if applicable).

Event naming
	•	Follow the existing practice of emitting explicit event or event_type fields (EOD logs already do this).  ￼

7.2 Error taxonomy additions

Orion already has a centralized ErrorCode enum with meta-solver and DSL-related codes such as SOLVER_EVAL_FAILED, SOLVER_DSL_VALIDATION_FAILED, META_EDIT_INVALID.  ￼

Add these error codes (as specified by the regime addendum text)
	•	REGIME_COMPUTE_FAILED
	•	REGIME_DATA_MISSING
	•	REGIME_RISK_ZERO
	•	STRATEGY_ACTIVATION_BLOCKED  ￼

7.3 DLQ policy
	•	Any hard failure in ingestion/evaluation loops must either:
	•	be raised as an OrionError with ErrorCode, or
	•	be written to DLQ with stack trace for postmortem (pattern already exists for critical loop crashes).  ￼

⸻

8) Vertical Slice Delivery Plan (end-to-end increments)

This plan intentionally mirrors the existing regime slice plan and extends it to meta-solver mechanics.  ￼

Slice 1: Market Context Skeleton
	•	Subscribe SPY (+VXX if used), compute snapshot, persist regime_history with model_version, unit tests for axes + hysteresis.  ￼

Slice 2: Risk Scaling by Regime
	•	RiskManager applies multipliers and logs sizing inputs; paper-trade one strategy to validate reduced drawdown.  ￼

Slice 3: ActivationPolicy Routing
	•	Replace strategies_by_regime with ActivationPolicy evaluation; tests for deterministic activation.  ￼

Slice 4: Solver DSL Canonicalization
	•	Require DSL for new solvers; legacy conversion path; rejection on invalid rules.  ￼ ￼

Slice 5: Multi-expert meta proposals + best-of-N selection
	•	Implement Poetiq-style expert configs and parallel proposal generation; replace “apply first edit” with “evaluate and select best.”  ￼ ￼

Slice 6: Budget governance + evaluation hard timeouts
	•	Add time budgets and early exit; evaluation timeout enforcement; record token usage and elapsed time.  ￼ ￼

Slice 7: EOD integration validation
	•	Ensure daily EOD solver mutation loop uses new selection mechanism (still bounded, safe, auditable).  ￼

⸻

9) Testing and Verification

9.1 Test layers (required)
	•	Unit tests: regime axis classification + hysteresis; ActivationPolicy evaluation; DSL validation.  ￼ ￼
	•	Integration tests: proposal ingestion, promotion scanning, solver failure handling. (Existing meta promotion tests cover ingestion/promotion logic and should be extended to multi-expert selection.)  ￼ ￼
	•	E2E tests: maintain a working vertical slice from signal to execution (existing e2e slice validates end-to-end flow).  ￼

9.2 Tooling
	•	Use the existing Makefile targets for unit/integration/e2e and linting.  ￼

⸻

10) Rollout and Safety

10.1 Feature flags / staged activation
	•	Phase 0: compute + persist regimes, no behavioral changes (multipliers = 1.0; ActivationPolicy permissive).
	•	Phase 1: enable risk multipliers for paper only.
	•	Phase 2: enable ActivationPolicy gating for paper, then limited live.
	•	Phase 3: enable multi-expert meta edits in research only; only allow promotion via existing gatekeeper/promotion recommendation workflow.

10.2 Abort/rollback
	•	Any spike in STRATEGY_ACTIVATION_BLOCKED or regime compute failures triggers rollback to permissive ActivationPolicy and multipliers 1.0, while continuing to log/persist regimes for diagnosis.  ￼

⸻

Appendix A: Concrete Config Knobs to Add (grounded in Poetiq)

Mirror these Poetiq-proven knobs in Orion meta-agent configs:
	•	llm_id, solver_temperature, request_timeout, max_total_timeouts, max_total_time, per_iteration_retries
	•	num_experts, max_iterations, max_solutions, selection_probability, seed
	•	return_best_result, plus voting toggles as applicable.  ￼

These are not theoretical: they exist in Poetiq’s CONFIG_LIST and are exercised by its iterative solve loop.  ￼ ￼

⸻

Appendix B: Implementation Notes (tight, non-speculative)
	•	The addendum does not change Orion’s overall control loop design: it upgrades how refinements are generated and selected, and it hardens runtime constraints. Orion already has the refinement loop and promotion path; we are making it “best-of-N” rather than “first edit wins.”  ￼
	•	The regime upgrade is likewise already specified as a deterministic v1 logic change with explicit vertical slices and logging requirements; this addendum makes it mandatory and ties it into ActivationPolicy and risk scaling.  ￼ ￼

⸻

If you want, I can also format this as a repository-ready markdown file name (for example docs/PRD_Addendum_Orion_Poetiq_Regime_Activation_Observability.md) and include a short “diff checklist” mapping each section to the specific modules it touches (MetaSearchAgent, MetaAgent config, RiskManager, Router, regime service, DB migrations, API).
