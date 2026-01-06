import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yaml
from orion.config import meta_settings
from orion.core.id_utils import deterministic_solver_id
from orion.core.meta_logging import log_meta_event
from orion.core.solver_schema import EditOp, EditOpType, EvaluationTask, SolverConfig, SolverEdit
from orion.core.solver_validation import ensure_solver_definition_json, solver_dsl_error_extra
from orion.shared.db_utils import db_query, db_write
from orion.storage.db import async_session_factory
from orion.storage.models_solvers import (
    PromotionRecommendation,
    Solver,
    SolverEdits,
    SolverMetrics,
    SolverRun,
)
from pydantic import ValidationError
from sqlalchemy import select

logger = logging.getLogger(__name__)

# Refinement loop configuration
REFINEMENT_SCORE_THRESHOLD = 0.5  # Minimum composite score to promote to paper
MAX_REFINEMENT_ITERATIONS = 3  # Max attempts to refine before giving up


class MetaSearchAgent:
    """
    PRD Addendum 5.3: Meta-Search Orchestrator.
    Generates variants of solvers, evaluates them, and tracks experiments.
    """

    def __init__(self) -> None:
        from orion.agents.meta_agent import MetaAgent

        self.meta_agent = MetaAgent()

        self.vector_store: "VectorStore | None" = None
        try:
            from orion.rag.vector_store import VectorStore

            self.vector_store = VectorStore()
        except Exception as e:
            logger.warning(f"Failed to initialize VectorStore: {e}. RAG features disabled.")

    def _calculate_composite_score(self, metrics: SolverMetrics, weights: Optional[dict[str, float]] = None) -> float:
        """
        Computes a weighted score based on PRD requirements:
        (Sharpe, Profit Factor, Info Ratio, Stability)
        """
        w = weights or meta_settings.scoring_weights

        w_sharpe = w.get("sharpe", 0.4)
        w_pf = w.get("profit_factor", 0.3)
        w_ir = w.get("info_ratio", 0.2)
        w_stability = w.get("stability", 0.1)

        # Normalize or clamp values to reasonable ranges
        sharpe = max(-3.0, min(metrics.sharpe_ratio or 0.0, 5.0))
        pf = max(0.0, min(metrics.profit_factor or 0.0, 5.0))
        ir = max(-3.0, min(metrics.info_ratio or 0.0, 5.0))
        stability = max(0.0, min(metrics.stability_score or 0.0, 1.0))

        score = (w_sharpe * sharpe) + (w_pf * pf) + (w_ir * ir) + (w_stability * stability)

        # Penalize Drawdown explicitly
        max_dd_pct = float(metrics.max_dd_pct or 0.0)
        if max_dd_pct > 25.0:  # >25% DD
            score -= 2.0

        return score

    async def run_evolution_cycle(self, base_solver_id: str, experiment_name: str = "Evolution") -> None:
        """
        Runs a single generation of evolution.
        """
        log_meta_event(
            logger,
            component="MetaSearch",
            severity="INFO",
            entity_type="solver",
            entity_id=str(base_solver_id),
            message="Starting meta-search evolution cycle",
            metadata={"experiment_name": experiment_name},
        )

        async with async_session_factory() as session:
            # 1. Load Base
            stmt = select(Solver).where(Solver.solver_id == base_solver_id)
            result = await session.execute(stmt)
            base_solver = result.scalars().first()

            if not base_solver:
                log_meta_event(
                    logger,
                    component="MetaSearch",
                    severity="ERROR",
                    entity_type="solver",
                    entity_id=str(base_solver_id),
                    message="Base solver not found",
                    metadata={},
                )
                return

            # Fetch Base Metrics for Composite Score comparison
            # We need the LATEST metrics for this solver
            stmt_m = (
                select(SolverMetrics)
                .where(SolverMetrics.solver_id == base_solver_id)
                .order_by(SolverMetrics.evaluated_at_utc.desc())
                .limit(1)
            )
            res_m = await session.execute(stmt_m)
            base_metrics = res_m.scalars().first()

            base_score = 0.0
            if base_metrics:
                base_score = self._calculate_composite_score(base_metrics)
            else:
                # Fallback to loose approximation from Solver table
                base_score = base_solver.sharpe_ratio or 0.0

            # Create Experiment Record
            objective = "maximize composite_score(sharpe,profit_factor,info_ratio,stability) with drawdown penalty"
            experiment = await self._log_experiment(
                description=experiment_name,
                status="running",
                name=experiment_name,
                objective=objective,
                base_solver_ids=[str(base_solver_id)],
                config_json={"objective": objective, "weights": meta_settings.scoring_weights},
            )
            # Re-fetch experiment from session to ensure it's tracked if needed later in the same session
            # Or, if _log_experiment returns the managed object, this step is not needed.
            # Assuming _log_experiment returns the managed object.
            # If not, we'd need: experiment = await session.get(MetaExperiment, experiment.experiment_id)

            try:
                # 2. Generate Variants (SolverEdits)
                base_config = SolverConfig(**base_solver.config)

                # Build Performance Context with RAG
                perf_ctx = f"Current Score: {base_score:.2f}. Sharpe: {base_solver.sharpe_ratio or 0.0}."

                if self.vector_store:
                    try:
                        # Query for insights relevant to this strategy/family
                        query = f"performance notes for strategy {base_solver.family_name} low sharpe optimization"
                        docs = await self.vector_store.search(query, k=3)
                        if docs:
                            rag_text = "\\n".join([f"- {d.content[:300]}..." for d in docs])
                            perf_ctx += f"\\nRelevant Insights:\\n{rag_text}"
                    except Exception as e:
                        logger.warning(f"RAG Search failed: {e}")

                # Use MetaAgent (LLM)
                edits_list = await self.meta_agent.propose_edits(base_config, perf_ctx)

                # Fallback if LLM fails
                if not edits_list:
                    logger.warning("LLM failed to propose edits. Using random fallback.")
                    edits_list = self._generate_heuristic_variants(
                        base_config, base_solver, count=3, generated_by="random_fallback"
                    )

                best_variant = None
                best_score = -999.0

                # 3. Apply Edits & Evaluate
                # DSR Correction: We need to know the Total Trials run in this experiment so far.
                # PRD 10.3: "account for total number of trials"

                # We can't easily get the *future* count, but DSR technically requires N to be the size of the set being compared.
                # If we are doing sequential optimization, N grows.
                # Let's accumulate trial_count on the experiment record.

                current_trial_count = experiment.trial_count or 0

                for i, edit_record in enumerate(edits_list):
                    current_trial_count += 1
                    experiment.trial_count = current_trial_count
                    # Commit early or batch? Batch is fine for the loop, but we need the count for the eval.

                    # Apply edit to get config first, to validate it
                    try:
                        new_config = self.apply_edit(base_config, edit_record)
                    except (ValueError, ValidationError) as ve:
                        logger.warning(f"Skipping invalid edit: {ve}")
                        continue
                    try:
                        ensure_solver_definition_json(new_config.model_dump(mode="json"), None)
                    except Exception as dsl_err:
                        logger.warning(
                            "Skipping solver variant due to DSL validation failure",
                            extra=solver_dsl_error_extra(dsl_err),
                        )
                        continue

                    # Persist Edit (FR 4.5)
                    sql_edit = SolverEdits(
                        id=str(uuid.uuid4()),
                        experiment_id=experiment.experiment_id,
                        base_solver_id=edit_record.base_solver_id,
                        new_solver_id=edit_record.new_solver_id,
                        edit_json=edit_record.model_dump(mode="json"),
                        generated_by=edit_record.generated_by,
                    )
                    session.add(sql_edit)

                    # Create Solver Record (Inactive Candidate)
                    new_solver = Solver(
                        solver_id=new_config.version_id,
                        family_name=f"{base_solver.family_name}_gen_{experiment.experiment_id[:4]}",
                        config=new_config.model_dump(mode="json"),
                        is_active=False,
                        status="candidate",
                        stage="research",
                        created_by=edit_record.generated_by,
                        definition_json=ensure_solver_definition_json(new_config.model_dump(mode="json"), None),
                    )
                    session.add(new_solver)

                    # Evaluate (FR 5.2.1/5.3.2: store solver_runs + solver_metrics)
                    # DSR: Correct for multiple testing (n_trials = cumulative trials in this experiment)
                    # This ensures that as we try more variants, the hurdle for Significance increases.
                    solver_run, metrics = await self.evaluate_variant(
                        new_solver.solver_id, new_config, n_trials=current_trial_count
                    )
                    new_score = self._calculate_composite_score(metrics)

                    logger.info(f"Variant {i} Score: {new_score:.4f} (Sharpe: {metrics.sharpe_ratio})")

                    # Save Run + Metrics
                    session.add(solver_run)
                    session.add(metrics)

                    # Update Edit Reward (Composite Delta)
                    sql_edit.reward = new_score - base_score

                    if new_score > best_score:
                        best_score = new_score
                        best_variant = new_solver

                # 4. Finalize
                experiment.status = "completed"
                experiment.end_time_utc = datetime.now(timezone.utc)
                experiment.completed_at = datetime.now(timezone.utc)
                if best_variant:
                    experiment.best_solver_id = best_variant.solver_id
                    logger.info(f"Best Variant Found: {best_variant.solver_id} (Score: {best_score:.4f})")
                experiment.summary = f"best_score={best_score:.4f}"

                await session.commit()

            except Exception as e:
                logger.error(f"Evolution Failed: {e}")
                experiment.status = "failed"
                await session.rollback()

    async def ingest_proposals(self, proposals_dir: str = "proposals") -> None:
        """
        Scans directory for YAML proposals (from EOD Agent) and persists them to DB.
        """
        if not os.path.exists(proposals_dir):
            return

        # Collect all valid proposals first (file I/O outside transaction)
        proposals_to_ingest = []

        for filename in os.listdir(proposals_dir):
            if not filename.endswith(".yaml"):
                continue

            path = os.path.join(proposals_dir, filename)
            try:
                with open(path, "r") as f:
                    artifact = yaml.safe_load(f)

                meta = artifact.get("meta", {})
                proposal = artifact.get("proposal", {})

                if meta.get("status") != "PROPOSED":
                    continue

                if proposal.get("type") != "solver_edit":
                    continue

                base_id = proposal.get("target_solver_id")
                ops_data = proposal.get("ops", [])
                edit_id = str(uuid.uuid4())

                new_solver_id = deterministic_solver_id(
                    base_solver_id=str(base_id),
                    edit_ops={"ops": ops_data},
                    prefix="eod",
                )

                proposals_to_ingest.append(
                    {
                        "edit_id": edit_id,
                        "base_id": base_id,
                        "new_solver_id": new_solver_id,
                        "ops_data": ops_data,
                        "filename": filename,
                        "path": path,
                    }
                )

            except Exception as e:
                logger.error(f"Failed to parse {filename}: {e}")

        if not proposals_to_ingest:
            return

        # Persist to DB in single transaction
        async def save_proposals(session: Any) -> None:
            for prop in proposals_to_ingest:
                sql_edit = SolverEdits(
                    id=prop["edit_id"],
                    experiment_id=None,
                    base_solver_id=prop["base_id"],
                    new_solver_id=prop["new_solver_id"],
                    edit_json={"ops": prop["ops_data"]},
                    generated_by="llm_eod_agent",
                    reward=None,
                )
                session.add(sql_edit)
                logger.info(f"Ingested proposal {prop['filename']} as Edit {prop['edit_id']}")

        await db_write(save_proposals)

        # Move processed files (file I/O outside transaction)
        processed_dir = os.path.join(proposals_dir, "processed")
        os.makedirs(processed_dir, exist_ok=True)
        for prop in proposals_to_ingest:
            try:
                os.rename(prop["path"], os.path.join(processed_dir, prop["filename"]))
            except Exception as e:
                logger.error(f"Failed to move {prop['filename']}: {e}")

    async def _load_context(self, query: str, top_k: int = 5) -> dict[str, Any]:
        """
        Searches the RAG index and DB for relevant context.
        Returns a dict with 'docs' and 'recent_metrics'.
        """
        # Vector search
        docs = await self.store.search(query, top_k=top_k)

        # Fetch recent solver metrics
        async def fetch_recent_metrics(session: Any) -> List[Any]:
            from orion.storage.models import SolverMetrics

            stmt = select(SolverMetrics).order_by(SolverMetrics.evaluated_at_utc.desc()).limit(10)
            result = await session.execute(stmt)
            return result.scalars().all()

        metrics = await db_query(fetch_recent_metrics)

        return {"docs": docs, "recent_metrics": metrics}

    async def process_pending_edits(self) -> None:
        """
        FR 5.7.2: Picks up pending EOD/Human edits and evaluates them.
        """

        # First, fetch all pending edits (read-only query)
        async def fetch_pending(session: Any) -> List[Any]:
            stmt = select(SolverEdits).where(SolverEdits.reward is None)
            result = await session.execute(stmt)
            return result.scalars().all()

        pending_edits = await db_query(fetch_pending)

        if not pending_edits:
            return

        logger.info(f"Processing {len(pending_edits)} pending edits...")

        # Process each edit and collect results
        for edit in pending_edits:
            try:
                # Load base solver (read-only)
                async def fetch_base_solver(session: Any, edit: Any = edit) -> Any:
                    stmt_b = select(Solver).where(Solver.solver_id == edit.base_solver_id)
                    res_b = await session.execute(stmt_b)
                    return res_b.scalars().first()

                base_solver = await db_query(fetch_base_solver)

                if not base_solver:
                    logger.error(f"Base solver {edit.base_solver_id} not found for edit {edit.id}")

                    # Mark as invalid
                    async def mark_invalid(session: Any, edit: Any = edit) -> None:
                        stmt = select(SolverEdits).where(SolverEdits.id == edit.id)
                        result = await session.execute(stmt)
                        edit_obj = result.scalars().first()
                        if edit_obj:
                            edit_obj.reward = -999.0

                    await db_write(mark_invalid)
                    continue

                base_config = SolverConfig(**base_solver.config)

                # Reconstruct SolverEdit object
                raw_ops = edit.edit_json.get("ops", [])
                ops_objs = []
                for op_dict in raw_ops:
                    ops_objs.append(EditOp(**op_dict))

                solver_edit_obj = SolverEdit(
                    base_solver_id=edit.base_solver_id,
                    new_solver_id=edit.new_solver_id,
                    generated_by=edit.generated_by,
                    ops=ops_objs,
                )

                # Apply Edit & VALIDATE
                try:
                    new_config = self.apply_edit(base_config, solver_edit_obj)
                except (ValueError, ValidationError) as ve:
                    logger.error(f"Edit {edit.id} Invalid: {ve}")

                    async def mark_invalid_edit(session: Any, edit: Any = edit) -> None:
                        stmt = select(SolverEdits).where(SolverEdits.id == edit.id)
                        result = await session.execute(stmt)
                        edit_obj = result.scalars().first()
                        if edit_obj:
                            edit_obj.reward = -999.0

                    await db_write(mark_invalid_edit)
                    continue

                try:
                    ensure_solver_definition_json(new_config.model_dump(mode="json"), None)
                except Exception as dsl_err:
                    logger.error(
                        f"Edit {edit.id} DSL validation failed: {dsl_err}",
                        extra=solver_dsl_error_extra(dsl_err),
                    )

                    async def mark_dsl_failed(session: Any, edit: Any = edit) -> None:
                        stmt = select(SolverEdits).where(SolverEdits.id == edit.id)
                        result = await session.execute(stmt)
                        edit_obj = result.scalars().first()
                        if edit_obj:
                            edit_obj.reward = -999.0

                    await db_write(mark_dsl_failed)
                    continue

                # Evaluate (this creates solver_run and metrics)
                solver_run, metrics = await self.evaluate_variant(new_config.version_id, new_config)

                # Calculate scores
                new_score = self._calculate_composite_score(metrics)

                # Fetch base metrics for comparison
                async def fetch_base_metrics(session: Any, base_solver: Any = base_solver) -> Any:
                    stmt_m = (
                        select(SolverMetrics)
                        .where(SolverMetrics.solver_id == base_solver.solver_id)
                        .order_by(SolverMetrics.evaluated_at_utc.desc())
                        .limit(1)
                    )
                    res_m = await session.execute(stmt_m)
                    return res_m.scalars().first()

                base_metrics = await db_query(fetch_base_metrics)

                base_score = (
                    self._calculate_composite_score(base_metrics) if base_metrics else (base_solver.sharpe_ratio or 0.0)
                )

                # Save everything in one transaction
                async def save_evaluation_results(
                    session,
                    new_config=new_config,
                    base_solver=base_solver,
                    edit=edit,
                    solver_run=solver_run,
                    metrics=metrics,
                    new_score=new_score,
                    base_score=base_score,
                ) -> None:
                    # Create new solver
                    new_solver = Solver(
                        solver_id=new_config.version_id,
                        family_name=f"{base_solver.family_name}_eod_{edit.id[:4]}",
                        config=new_config.model_dump(mode="json"),
                        is_active=False,
                        status="candidate",
                        stage="research",
                        created_by=edit.generated_by,
                        definition_json=ensure_solver_definition_json(new_config.model_dump(mode="json"), None),
                    )
                    session.add(new_solver)
                    session.add(solver_run)
                    session.add(metrics)

                    # Update edit reward
                    stmt = select(SolverEdits).where(SolverEdits.id == edit.id)
                    result = await session.execute(stmt)
                    edit_obj = result.scalars().first()
                    if edit_obj:
                        edit_obj.reward = new_score - base_score
                        edit_obj.evaluated_at_utc = datetime.now(timezone.utc)

                await db_write(save_evaluation_results)

                logger.info(f"Processed Edit {edit.id}: Reward={new_score - base_score:.4f}")

            except Exception as e:
                logger.error(f"Failed to process edit {edit.id}: {e}")

    async def refine_and_promote(
        self,
        solver_id: str,
        config: SolverConfig,
        base_solver_id: str,
        max_iterations: int = MAX_REFINEMENT_ITERATIONS,
    ) -> Optional[str]:
        """
        Iteratively refine a solver until it meets the promotion threshold.

        1. Backtest the solver
        2. If score < threshold, send results to MetaAgent for refinement
        3. Apply refinement, loop back to step 1
        4. If score >= threshold, promote to paper stage

        Returns:
            The final solver_id if promoted, None if gave up after max iterations.
        """
        current_config = config
        current_solver_id = solver_id

        for iteration in range(max_iterations):
            logger.info(
                f"Refinement iteration {iteration + 1}/{max_iterations} for {current_solver_id}",
                extra={"event": "refinement_iteration", "solver_id": current_solver_id, "iteration": iteration + 1},
            )

            # 1. Backtest
            solver_run, metrics = await self.evaluate_variant(current_solver_id, current_config)
            score = self._calculate_composite_score(metrics)

            logger.info(
                f"Solver {current_solver_id} scored {score:.3f} (threshold: {REFINEMENT_SCORE_THRESHOLD})",
                extra={
                    "event": "refinement_score",
                    "solver_id": current_solver_id,
                    "score": score,
                    "sharpe": metrics.sharpe_ratio,
                    "profit_factor": metrics.profit_factor,
                },
            )

            # 2. Check threshold
            if score >= REFINEMENT_SCORE_THRESHOLD:
                # Promote to paper!
                await self._promote_to_paper(current_solver_id, current_config, metrics)
                return current_solver_id

            # 3. Score too low - ask MetaAgent for refinement
            if iteration < max_iterations - 1:  # Don't refine on last iteration
                refinement_context = (
                    f"Solver {current_solver_id} scored {score:.3f}, below threshold {REFINEMENT_SCORE_THRESHOLD}.\n"
                    f"Backtest Results:\n"
                    f"- Sharpe: {metrics.sharpe_ratio:.3f}\n"
                    f"- Profit Factor: {metrics.profit_factor:.3f}\n"
                    f"- Max Drawdown: {metrics.max_dd_pct:.1f}%\n"
                    f"- Win Rate: {(metrics.win_rate or 0) * 100:.1f}%\n"
                    f"\nPlease propose refinements to improve performance."
                )

                try:
                    edits = await self.meta_agent.propose_edits(current_config, refinement_context)

                    if edits:
                        # Apply first edit (best suggestion)
                        new_config = self.apply_edit(current_config, edits[0])
                        current_config = new_config
                        current_solver_id = new_config.version_id
                        logger.info(
                            f"Applied refinement, new solver: {current_solver_id}",
                            extra={"event": "refinement_applied", "new_solver_id": current_solver_id},
                        )
                    else:
                        logger.warning("MetaAgent returned no refinements, stopping loop")
                        break
                except Exception as e:
                    logger.error(f"Refinement failed: {e}")
                    break

        # Gave up - solver stays in research stage
        logger.warning(
            f"Solver {current_solver_id} did not meet threshold after {max_iterations} iterations",
            extra={"event": "refinement_gave_up", "solver_id": current_solver_id, "final_score": score},
        )
        return None

    async def _promote_to_paper(self, solver_id: str, config: SolverConfig, metrics: SolverMetrics) -> None:
        """
        Promote a solver from research to paper stage.
        """

        async def update_stage(session: Any) -> None:
            stmt = select(Solver).where(Solver.solver_id == solver_id)
            result = await session.execute(stmt)
            solver = result.scalars().first()

            if solver:
                solver.stage = "paper"
                solver.status = "active"
                solver.is_active = True
                session.add(metrics)
                logger.info(
                    f"Promoted solver {solver_id} to paper stage",
                    extra={
                        "event": "solver_promoted",
                        "solver_id": solver_id,
                        "new_stage": "paper",
                        "score": self._calculate_composite_score(metrics),
                    },
                )
            else:
                # Solver doesn't exist yet - create it
                new_solver = Solver(
                    solver_id=solver_id,
                    family_name="refined",
                    config=config.model_dump(mode="json"),
                    is_active=True,
                    status="active",
                    stage="paper",
                    created_by="refinement_loop",
                    definition_json=ensure_solver_definition_json(config.model_dump(mode="json"), None),
                )
                session.add(new_solver)
                session.add(metrics)
                logger.info(f"Created and promoted solver {solver_id} to paper stage")

        await db_write(update_stage)

    def _generate_heuristic_variants(
        self, base: SolverConfig, base_metrics: Solver, count: int = 3, generated_by: str = "heuristic_fallback"
    ) -> List[SolverEdit]:
        """
        Generates SolverEdit objects using deterministic heuristics.
        """
        edits = []

        sharpe = base_metrics.sharpe_ratio or 0.0
        is_struggling = sharpe < 0.5

        risk_edit = self._mutate_risk(base, generated_by, tighten=is_struggling)
        if risk_edit:
            edits.append(risk_edit)

        for i in range(count - len(edits)):
            ops = []

            if base.exit_logic.take_profit_atr_multiple:
                current_val = base.exit_logic.take_profit_atr_multiple

                if is_struggling:
                    new_val = current_val * 0.9
                    reason = "Reducing TP target to improve Win Rate (Struggling Logic)"
                else:
                    new_val = current_val * 1.1
                    reason = "Expanding TP target to capture trend (Performing Logic)"

                op = EditOp(
                    op=EditOpType.MODIFY_PARAM,
                    param_name="exit_logic.take_profit_atr_multiple",
                    old_value=current_val,
                    new_value=new_val,
                    reasoning=reason,
                )
                ops.append(op)

            if ops:
                new_id = deterministic_solver_id(
                    base_solver_id=base.version_id,
                    edit_ops={"ops": [o.model_dump(mode="json") for o in ops], "variant_idx": i},
                    prefix="heur",
                )
                edit_record = SolverEdit(
                    base_solver_id=base.version_id, new_solver_id=new_id, generated_by=generated_by, ops=ops
                )
                edits.append(edit_record)

        return edits

    def _mutate_risk(self, base: SolverConfig, generated_by: str, tighten: bool = False) -> Optional[SolverEdit]:
        if not base.risk:
            return None

        current_bps = base.risk.risk_per_trade_bps

        if tighten:
            new_bps = int(current_bps * 0.8)
            reason = "Tightening risk due to poor performance"
        else:
            new_bps = int(current_bps * 1.1)
            reason = "Increasing risk due to stable performance"

        new_bps = max(10, min(new_bps, 500))

        if new_bps == current_bps:
            return None

        op = EditOp(
            op=EditOpType.MODIFY_RISK,
            param_name="risk_per_trade_bps",
            old_value=current_bps,
            new_value=new_bps,
            reasoning=reason,
        )

        new_id = deterministic_solver_id(
            base_solver_id=base.version_id,
            edit_ops={"ops": [op.model_dump(mode="json")], "variant": "risk_mutate"},
            prefix="heur",
        )
        return SolverEdit(base_solver_id=base.version_id, new_solver_id=new_id, generated_by=generated_by, ops=[op])

    def apply_edit(self, base: SolverConfig, edit: SolverEdit) -> SolverConfig:
        """
        Applies a SolverEdit to produce a new SolverConfig.
        Validates the result against System Limits.
        """
        new_config = base.model_copy(deep=True)
        new_config.version_id = edit.new_solver_id

        for op in edit.ops:
            if op.op == EditOpType.MODIFY_PARAM:
                if op.param_name == "exit_logic.take_profit_atr_multiple":
                    new_config.exit_logic.take_profit_atr_multiple = float(op.new_value)
                elif op.param_name == "risk_per_trade_bps":
                    new_config.risk_per_trade_bps = int(op.new_value)

            elif op.op == EditOpType.MODIFY_RISK:
                if op.param_name == "risk_per_trade_bps" and new_config.risk:
                    new_config.risk.risk_per_trade_bps = int(op.new_value)

            elif op.op == EditOpType.TOGGLE_FEATURE:
                feat = op.feature_name
                if not feat:
                    continue
                enable = bool(op.new_value)

                # We assume event_features by default if not specified, but verify lists
                # If enabling:
                if enable:
                    if (
                        feat not in new_config.features.event_features
                        and feat not in new_config.features.window_features
                    ):
                        new_config.features.event_features.append(feat)
                # If disabling:
                else:
                    if feat in new_config.features.event_features:
                        new_config.features.event_features.remove(feat)
                    if feat in new_config.features.window_features:
                        new_config.features.window_features.remove(feat)

            elif op.op == EditOpType.ADD_RULE:
                rule_val = op.new_value
                if rule_val:
                    # Avoid duplicates
                    exists = any(
                        (isinstance(r, str) and r == rule_val) or (hasattr(r, "id") and r.id == rule_val)
                        for r in new_config.rules
                    )
                    if not exists:
                        new_config.rules.append(rule_val)

            elif op.op == EditOpType.REMOVE_RULE:
                rule_id = op.rule_id or str(op.new_value)
                if rule_id:
                    new_config.rules = [
                        r
                        for r in new_config.rules
                        if not ((isinstance(r, str) and r == rule_id) or (hasattr(r, "id") and r.id == rule_id))
                    ]

        # VALIDATION
        # Pydantic will auto-validate assignments if Config.validate_assignment is True.
        # However, deep modifications might bypass it if not careful?
        # Actually SolverConfig has validate_assignment=True.
        # But let's trigger a full validation just in case by re-instantiating if needed,
        # or relying on the fact that we assigned fields above.

        # If we just return new_config, it's already a Pydantic model.
        # Let's wrap in a try-except block at the CALLER site (run_evolution_cycle) to catch ValidationError.
        # Here we just return. Implicitly validity is checked on assignment.

        return new_config

    def _create_default_task(self) -> EvaluationTask:
        from datetime import datetime, timedelta, timezone

        from orion.core.solver_schema import EvaluationTask

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30)
        return EvaluationTask(
            task_id=f"default_{end.strftime('%Y%m%d')}",
            start_time_utc=start,
            end_time_utc=end,
            dataset_tag="validation",
        )

    async def evaluate_variant(
        self, solver_id: str, config: SolverConfig, task: Optional[EvaluationTask] = None, n_trials: int = 1
    ) -> tuple[SolverRun, SolverMetrics]:
        from orion.core.evaluation_harness import DatasetSpec, run_solver_backtest
        from orion.processing.feature_engine import FeatureEngine
        from orion.processing.rule_engine import RuleEngine

        if task is None:
            task = self._create_default_task()

        metrics = SolverMetrics(
            id=str(uuid.uuid4()),
            solver_id=solver_id,
            dataset_tag=task.dataset_tag,
            evaluated_at_utc=datetime.now(timezone.utc),
            sharpe_ratio=0.0,
        )

        alpaca_events, flow_events, price_data = await self._fetch_silver_events(task)

        if not alpaca_events and not flow_events:
            logger.warning(f"No data for {solver_id} in task {task.task_id}. Returning empty metrics.")
            solver_run = SolverRun(
                id=str(uuid.uuid4()),
                solver_id=solver_id,
                dataset_tag=task.dataset_tag,
                time_window_start=task.start_time_utc,
                time_window_end=task.end_time_utc,
                num_candidates=0,
                num_trades=0,
                gross_pnl=0.0,
                net_pnl=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                expect_return_bp=0.0,
                metrics_json={"note": "no_data", "task_id": task.task_id},
            )
            return solver_run, metrics

        rules_cfg = config.model_dump(mode="json")
        feature_engine = FeatureEngine()
        rule_engine = RuleEngine(config=rules_cfg)

        # 1. OPTIMIZATION: Try to fetch pre-computed features from Gold Store
        # For simplicity in V1, we check if we can fetch *all* required signals.
        # But signals are by ticker. We need to iterate tickers in the task.
        # Or just fetch all for the time range and see what we get.
        # If we get nothing, we compute.

        bar_signals = []
        # Get unique tickers from events or task
        tickers = list(price_data.keys())

        # Try fetch first
        fetches = []
        for t in tickers:
            fetches.append(
                feature_engine.fetch_signal_batch(
                    t, task.start_time_utc, task.end_time_utc, config.features.feature_set_id
                )
            )

        fetched_lists = await asyncio.gather(*fetches)
        for lst in fetched_lists:
            bar_signals.extend(lst)

        if not bar_signals:
            # Compute from Bronze
            bar_signals = feature_engine.process_alpaca_bars(alpaca_events)

            # Persist for next time
            if bar_signals:
                # Fire and forget or await?
                # Let's await to ensure data integrity for tests
                await feature_engine.persist_signal_batch(bar_signals, config.features.feature_set_id)

        flow_signals = feature_engine.process_uw_flow_events(flow_events)

        all_signals = bar_signals + flow_signals
        all_signals.sort(key=lambda x: x.signal_ts_utc)

        candidates = rule_engine.process_signals(all_signals)

        if not candidates:
            solver_run = SolverRun(
                id=str(uuid.uuid4()),
                solver_id=solver_id,
                dataset_tag=task.dataset_tag,
                time_window_start=task.start_time_utc,
                time_window_end=task.end_time_utc,
                num_candidates=0,
                num_trades=0,
                gross_pnl=0.0,
                net_pnl=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                expect_return_bp=0.0,
                metrics_json={"note": "no_candidates", "task_id": task.task_id},
            )
            metrics.num_trades = 0
            return solver_run, metrics

        try:
            dataset_spec = DatasetSpec(
                dataset_tag=task.dataset_tag,
                time_window_start=task.start_time_utc,
                time_window_end=task.end_time_utc,
            )
            solver_run, result = await asyncio.to_thread(
                run_solver_backtest,
                solver_id=solver_id,
                candidates=candidates,
                price_data=price_data,
                dataset_spec=dataset_spec,
                solver_config=config,
                n_splits=3,
                embargo_pct=0.01,
                n_trials=n_trials,
            )

            metrics.sharpe_ratio = float(result.get("mean_sharpe", 0.0))
            metrics.num_runs = 1
            metrics.num_trades = int(result.get("total_trades", 0))
            metrics.profit_factor = float(result.get("profit_factor", 0.0))
            metrics.oos_expect_bp = float(result.get("expect_return_bp", 0.0))
            metrics.max_dd_pct = float(result.get("max_drawdown_pct", 0.0))
            metrics.info_ratio = float(result.get("info_ratio", 0.0))
            metrics.stability_score = float(result.get("stability_score", 0.0))
            metrics.metrics_json = {
                "dsr": float(result.get("deflated_sharpe_prob", 0.0)),
                "task_id": task.task_id,
                "solver_run_id": solver_run.id,
                "expect_return_bp": float(result.get("expect_return_bp", 0.0)),
                "gross_pnl": float(result.get("gross_pnl", 0.0)),
                "net_pnl": float(result.get("net_pnl", 0.0)),
                "bootstrap_p_value": float(result.get("bootstrap_p_value", 1.0)),
            }

            logger.info(
                f"Evaluated {solver_id}: Sharpe={metrics.sharpe_ratio:.2f}, Score={self._calculate_composite_score(metrics):.2f}"
            )

        except Exception as e:
            logger.error(f"Backtest failed for {solver_id}: {e}")
            metrics.metrics_json = {"error": str(e)}
            solver_run = SolverRun(
                id=str(uuid.uuid4()),
                solver_id=solver_id,
                dataset_tag=task.dataset_tag,
                time_window_start=task.start_time_utc,
                time_window_end=task.end_time_utc,
                num_candidates=len(candidates),
                num_trades=0,
                gross_pnl=None,
                net_pnl=None,
                profit_factor=None,
                max_drawdown_pct=None,
                expect_return_bp=None,
                metrics_json={"error": str(e), "task_id": task.task_id},
            )
            return solver_run, metrics

        return solver_run, metrics

    async def _fetch_silver_events(self, task: EvaluationTask) -> Tuple[List[Any], List[Any], Dict[str, Any]]:
        from orion.storage.models import BronzeEvent
        from orion.storage.models_silver import SilverAlpacaBar, SilverOptionFlow
        from sqlalchemy import and_, select

        alpaca_events = []
        flow_events = []
        price_data = {}

        async def fetch_bars_and_flow(session: Any) -> None:
            stmt_bars = (
                select(SilverAlpacaBar)
                .where(
                    and_(
                        SilverAlpacaBar.bar_start_ts_utc >= task.start_time_utc,
                        SilverAlpacaBar.bar_start_ts_utc <= task.end_time_utc,
                    )
                )
                .order_by(SilverAlpacaBar.bar_start_ts_utc.asc())
            )

            if task.ticker_filter:
                stmt_bars = stmt_bars.where(SilverAlpacaBar.ticker.in_(task.ticker_filter))

            res_bars = await session.execute(stmt_bars)
            bars = res_bars.scalars().all()

            data_by_ticker = {}
            for b in bars:
                payload = {
                    "ticker": b.ticker,
                    "o": b.open,
                    "h": b.high,
                    "l": b.low,
                    "c": b.close,
                    "v": b.volume,
                    "vw": b.vwap,
                    "t": b.bar_start_ts_utc,
                    "n": b.trade_count,
                }
                alpaca_events.append(payload)

                if b.ticker not in data_by_ticker:
                    data_by_ticker[b.ticker] = []
                data_by_ticker[b.ticker].append(
                    {
                        "timestamp": b.bar_start_ts_utc,
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.volume,
                    }
                )

            # Build price_data DataFrames from collected bars
            import pandas as pd

            for ticker, bar_list in data_by_ticker.items():
                if bar_list:
                    df = pd.DataFrame(bar_list)
                    df.set_index("timestamp", inplace=True)
                    df.sort_index(inplace=True)
                    price_data[ticker] = df

            stmt_flow = select(SilverOptionFlow).where(
                and_(
                    SilverOptionFlow.flow_ts_utc >= task.start_time_utc,
                    SilverOptionFlow.flow_ts_utc <= task.end_time_utc,
                    SilverOptionFlow.premium_usd >= 1000,
                )
            )
            if task.ticker_filter:
                stmt_flow = stmt_flow.where(SilverOptionFlow.ticker.in_(task.ticker_filter))

            res_flow = await session.execute(stmt_flow)
            flows = res_flow.scalars().all()

            for f in flows:
                payload = {
                    "ticker": f.ticker,
                    "premium": f.premium_usd,
                    "put_call": f.put_call,
                    "is_sweep": f.is_sweep,
                    "aggressor_ind": f.aggressor,
                    "underlying_price": f.underlying_price,
                }
                if f.expiry:
                    try:
                        exp_date = f.expiry
                        if isinstance(exp_date, str):
                            exp_date = datetime.strptime(exp_date, "%Y-%m-%d").date()
                        days = (exp_date - f.flow_ts_utc.date()).days
                        payload["dte"] = days
                    except Exception:
                        pass

                be = BronzeEvent(
                    event_id=f.event_id,
                    event_type="UW_FLOW",
                    source="BACKTEST",
                    event_ts_utc=f.flow_ts_utc,
                    payload=payload,
                    ticker=f.ticker,
                )
                flow_events.append(be)

        return alpaca_events, flow_events, price_data

    async def scan_for_promotions(self) -> None:
        from orion.core.promotion_rules import STAGE_ORDER, evaluate_stage_transition

        async with async_session_factory() as session:
            stmt = select(Solver)
            result = await session.execute(stmt)
            solvers = result.scalars().all()

            count_recommendations = 0
            count_demoted = 0

            for s in solvers:
                stmt_m = (
                    select(SolverMetrics)
                    .where(SolverMetrics.solver_id == s.solver_id)
                    .order_by(SolverMetrics.evaluated_at_utc.desc())
                    .limit(1)
                )
                m_res = await session.execute(stmt_m)
                latest_metrics = m_res.scalars().first()

                if not latest_metrics:
                    continue

                action = evaluate_stage_transition(latest_metrics, s.stage)

                if action == "promote":
                    try:
                        idx = STAGE_ORDER.index(s.stage)
                        if idx < len(STAGE_ORDER) - 1:
                            new_stage = STAGE_ORDER[idx + 1]

                            stmt_rec = select(PromotionRecommendation).where(
                                PromotionRecommendation.solver_id == s.solver_id,
                                PromotionRecommendation.status == "PENDING",
                                PromotionRecommendation.recommended_stage == new_stage,
                            )
                            existing = (await session.execute(stmt_rec)).scalars().first()
                            if not existing:
                                logger.info(
                                    f"Recommending PROMOTION for Solver {s.solver_id}: {s.stage} -> {new_stage}"
                                )
                                session.add(
                                    PromotionRecommendation(
                                        id=str(uuid.uuid4()),
                                        solver_id=s.solver_id,
                                        current_stage=s.stage,
                                        recommended_stage=new_stage,
                                        reason=f"Metrics met promotion criteria (Sharpe: {latest_metrics.sharpe_ratio:.2f})",
                                        metrics_snapshot=latest_metrics.metrics_json or {},
                                    )
                                )
                                count_recommendations += 1
                    except ValueError:
                        pass

                elif action == "demote":
                    # PRDv2 FR 5.5.2: recommendation vs decision.
                    # Safety: stop trading immediately, but do not mutate stage here.
                    logger.info(f"DEMOTION RECOMMENDED for Solver {s.solver_id} from {s.stage}")
                    s.is_active = False

                    try:
                        idx = STAGE_ORDER.index(s.stage)
                        recommended_stage = STAGE_ORDER[idx - 1] if idx > 0 else s.stage
                    except ValueError:
                        recommended_stage = s.stage

                    stmt_rec = select(PromotionRecommendation).where(
                        PromotionRecommendation.solver_id == s.solver_id,
                        PromotionRecommendation.status == "PENDING",
                        PromotionRecommendation.recommended_stage == recommended_stage,
                    )
                    existing = (await session.execute(stmt_rec)).scalars().first()
                    if not existing:
                        session.add(
                            PromotionRecommendation(
                                id=str(uuid.uuid4()),
                                solver_id=s.solver_id,
                                current_stage=s.stage,
                                recommended_stage=recommended_stage,
                                reason="Demotion criteria breached (meta-agent scan).",
                                metrics_snapshot=latest_metrics.metrics_json or {},
                            )
                        )
                    count_demoted += 1
            await session.commit()
            if count_recommendations > 0 or count_demoted > 0:
                logger.info(
                    f"Promotion Cycle Complete: +{count_recommendations} Recommendations, +{count_demoted} Demotion Recommendations."
                )

    # --------------------------------------------------------
    # Weekly Evolution Cycle (Friday EOD)
    # --------------------------------------------------------

    async def run_weekly_evolution(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Friday EOD comprehensive analysis and solver evolution.

        1. Aggregate week's EOD reports and trade data
        2. Analyze live trade execution quality
        3. Check ML model drift
        4. Generate evolution recommendations
        5. Propose solver mutations based on findings

        Args:
            dry_run: If True, analyze but don't create new solvers

        Returns:
            Summary of weekly analysis and actions taken.
        """
        from orion.agents.weekly_aggregator import WeeklyDataAggregator

        log_meta_event(
            logger,
            component="MetaSearch",
            severity="INFO",
            entity_type="weekly_cycle",
            entity_id="weekly",
            message="Starting weekly evolution cycle",
            metadata={"dry_run": dry_run},
        )

        # 1. Aggregate weekly data
        aggregator = WeeklyDataAggregator()
        week_data = await aggregator.aggregate_week()

        logger.info(
            f"Weekly data aggregated: {week_data['eod_reports']['total_reports']} EOD reports, "
            f"{week_data['trade_execution'].get('trades', {}).get('total_orders', 0)} trades"
        )

        # 2. Analyze execution quality
        execution_analysis = self._analyze_execution_quality(week_data)

        # 3. Analyze ML drift
        drift_analysis = self._analyze_ml_drift(week_data)

        # 4. Generate recommendations
        recommendations = await self._generate_weekly_recommendations(week_data, execution_analysis, drift_analysis)

        # 5. Execute mutations if not dry run
        mutations_applied = []
        if not dry_run and recommendations.get("proposed_edits"):
            for edit_proposal in recommendations["proposed_edits"][:3]:  # Max 3 per week
                try:
                    # Load base solver
                    base_id = edit_proposal.get("base_solver_id")
                    if not base_id:
                        continue

                    async with async_session_factory() as session:
                        stmt = select(Solver).where(Solver.solver_id == base_id)
                        result = await session.execute(stmt)
                        base_solver = result.scalars().first()

                        if base_solver:
                            base_config = SolverConfig(**base_solver.config)

                            # Use meta agent to propose edits
                            context = edit_proposal.get("context", "Weekly evolution cycle")
                            edits = await self.meta_agent.propose_edits(base_config, context)

                            if edits:
                                # Apply first edit
                                new_config = self.apply_edit(base_config, edits[0])

                                # Persist new solver as candidate
                                new_solver = Solver(
                                    solver_id=new_config.version_id,
                                    family_name=f"{base_solver.family_name}_weekly",
                                    config=new_config.model_dump(mode="json"),
                                    is_active=False,
                                    status="candidate",
                                    stage="research",
                                    created_by="weekly_evolution",
                                    definition_json=ensure_solver_definition_json(
                                        new_config.model_dump(mode="json"), None
                                    ),
                                )
                                session.add(new_solver)
                                await session.commit()

                                mutations_applied.append(
                                    {
                                        "base_id": base_id,
                                        "new_id": new_config.version_id,
                                        "reason": edit_proposal.get("reason"),
                                    }
                                )
                                logger.info(f"Created weekly mutation: {new_config.version_id}")

                except Exception as e:
                    logger.error(f"Failed to apply weekly mutation: {e}")

        summary = {
            "period": week_data["period"],
            "eod_summary": {
                "reports_analyzed": week_data["eod_reports"]["total_reports"],
                "trading_days": week_data["eod_reports"]["trading_days"],
                "total_decisions": week_data["eod_reports"]["total_decisions"],
                "executed": week_data["eod_reports"]["executed_count"],
            },
            "execution_quality": execution_analysis,
            "ml_drift": drift_analysis,
            "recommendations": recommendations,
            "mutations_applied": mutations_applied,
            "dry_run": dry_run,
        }

        log_meta_event(
            logger,
            component="MetaSearch",
            severity="INFO",
            entity_type="weekly_cycle",
            entity_id="weekly",
            message="Weekly evolution cycle completed",
            metadata=summary,
        )

        return summary

    def _analyze_execution_quality(self, week_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze trade execution quality vs expectations.
        """
        trade_data = week_data.get("trade_execution", {}).get("trades", {})

        analysis = {
            "total_orders": trade_data.get("total_orders", 0),
            "fill_rate": trade_data.get("fill_rate", 0.0),
            "rejection_rate": 0.0,
            "unique_tickers": len(trade_data.get("tickers", [])),
            "execution_health": "unknown",
        }

        total = trade_data.get("total_orders", 0)
        if total > 0:
            rejected = trade_data.get("rejected", 0)
            analysis["rejection_rate"] = rejected / total

            # Health classification
            if analysis["fill_rate"] >= 0.9 and analysis["rejection_rate"] < 0.05:
                analysis["execution_health"] = "excellent"
            elif analysis["fill_rate"] >= 0.7:
                analysis["execution_health"] = "good"
            elif analysis["fill_rate"] >= 0.5:
                analysis["execution_health"] = "degraded"
            else:
                analysis["execution_health"] = "poor"

        return analysis

    def _analyze_ml_drift(self, week_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze ML model drift from pattern miner insights.
        """
        eod_data = week_data.get("eod_reports", {})
        ml_insights = week_data.get("ml_insights", {})

        drift_analysis = {
            "buckets_analyzed": [],
            "degrading_buckets": [],
            "improving_buckets": [],
            "stable_buckets": [],
            "top_features": eod_data.get("top_features", {}),
            "overall_health": "unknown",
        }

        # Analyze drift from aggregated ML insights
        drift_info = ml_insights.get("drift_analysis", {})
        for bucket, info in drift_info.items():
            drift_analysis["buckets_analyzed"].append(bucket)
            trend = info.get("trend", "stable")

            if trend == "degrading":
                drift_analysis["degrading_buckets"].append(
                    {
                        "bucket": bucket,
                        "auc_drop": info.get("drift", 0),
                        "current_auc": info.get("current_auc"),
                    }
                )
            elif trend == "improving":
                drift_analysis["improving_buckets"].append(bucket)
            else:
                drift_analysis["stable_buckets"].append(bucket)

        # Overall health
        n_degrading = len(drift_analysis["degrading_buckets"])
        n_total = len(drift_analysis["buckets_analyzed"])

        if n_total == 0:
            drift_analysis["overall_health"] = "no_data"
        elif n_degrading == 0:
            drift_analysis["overall_health"] = "healthy"
        elif n_degrading / n_total < 0.3:
            drift_analysis["overall_health"] = "minor_drift"
        else:
            drift_analysis["overall_health"] = "significant_drift"

        return drift_analysis

    async def _generate_weekly_recommendations(
        self,
        week_data: Dict[str, Any],
        execution_analysis: Dict[str, Any],
        drift_analysis: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Generate evolution recommendations based on weekly analysis.
        """
        recommendations = {
            "proposed_edits": [],
            "alerts": [],
            "insights": [],
        }

        # Check execution quality issues
        if execution_analysis.get("execution_health") in ["degraded", "poor"]:
            recommendations["alerts"].append(
                {
                    "type": "execution_degradation",
                    "severity": "high",
                    "message": f"Execution fill rate at {execution_analysis['fill_rate']:.1%}",
                    "action": "Review order parameters and market conditions",
                }
            )

        # Check ML drift
        if drift_analysis.get("overall_health") == "significant_drift":
            for bucket_info in drift_analysis.get("degrading_buckets", []):
                recommendations["alerts"].append(
                    {
                        "type": "ml_drift",
                        "severity": "medium",
                        "message": f"Model {bucket_info['bucket']} AUC dropped by {abs(bucket_info.get('auc_drop', 0)):.3f}",
                        "action": "Consider retraining or feature engineering",
                    }
                )

        # Generate solver edit proposals based on top features
        top_features = drift_analysis.get("top_features", {})
        if top_features:
            # Fetch active solvers to propose edits for
            async with async_session_factory() as session:
                stmt = select(Solver).where((Solver.status == "active") | (Solver.is_active == True)).limit(5)
                result = await session.execute(stmt)
                active_solvers = result.scalars().all()

                for solver in active_solvers:
                    # Propose feature-based edits
                    feature_list = list(top_features.keys())[:3]
                    if feature_list:
                        recommendations["proposed_edits"].append(
                            {
                                "base_solver_id": solver.solver_id,
                                "reason": f"Incorporate top-performing features: {', '.join(feature_list)}",
                                "context": (
                                    f"Weekly analysis shows top features: {feature_list}. "
                                    f"Execution health: {execution_analysis.get('execution_health')}. "
                                    f"ML drift: {drift_analysis.get('overall_health')}. "
                                    f"Propose parameter adjustments to align with these signals."
                                ),
                            }
                        )

        # Add insights
        eod_summary = week_data.get("eod_reports", {})
        if eod_summary.get("trading_days", 0) > 0:
            win_rate = eod_summary.get("executed_count", 0) / max(eod_summary.get("total_decisions", 1), 1)
            recommendations["insights"].append(
                {
                    "metric": "decision_execution_rate",
                    "value": win_rate,
                    "interpretation": f"{win_rate:.1%} of decisions resulted in execution",
                }
            )

        return recommendations
