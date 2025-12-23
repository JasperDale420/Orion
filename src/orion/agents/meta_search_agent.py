import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import yaml
from orion.config import meta_settings
from orion.core.id_utils import deterministic_solver_id
from orion.core.meta_logging import log_meta_event
from orion.core.solver_schema import EditOp, EditOpType, EvaluationTask, SolverConfig, SolverEdit
from orion.core.solver_validation import ensure_solver_definition_json, solver_dsl_error_extra
from orion.storage.db import async_session_factory
from orion.storage.models_solvers import (
    MetaExperiment,
    PromotionRecommendation,
    Solver,
    SolverEdits,
    SolverMetrics,
    SolverRun,
)
from pydantic import ValidationError
from sqlalchemy import select

logger = logging.getLogger(__name__)


class MetaSearchAgent:
    """
    PRD Addendum 5.3: Meta-Search Orchestrator.
    Generates variants of solvers, evaluates them, and tracks experiments.
    """

    def __init__(self):
        from orion.agents.meta_agent import MetaAgent

        self.meta_agent = MetaAgent()

        try:
            from orion.rag.vector_store import VectorStore

            self.vector_store = VectorStore()
        except Exception as e:
            logger.warning(f"Failed to initialize VectorStore: {e}. RAG features disabled.")
            self.vector_store = None

    def _calculate_composite_score(self, metrics: SolverMetrics, weights: Optional[dict] = None) -> float:
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

    async def run_evolution_cycle(self, base_solver_id: str, experiment_name: str = "Evolution"):
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
            exp_id = str(uuid.uuid4())
            objective = "maximize composite_score(sharpe,profit_factor,info_ratio,stability) with drawdown penalty"
            experiment = MetaExperiment(
                experiment_id=exp_id,
                description=experiment_name,
                status="running",
                start_time_utc=datetime.now(timezone.utc),
                # PRD §4.4 fields (staged)
                id=exp_id,
                name=experiment_name,
                objective=objective,
                base_solver_ids=[str(base_solver_id)],
                config_json={"objective": objective, "weights": meta_settings.scoring_weights},
                started_at=datetime.now(timezone.utc),
            )
            session.add(experiment)
            await session.commit()

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
                        experiment_id=exp_id,
                        base_solver_id=edit_record.base_solver_id,
                        new_solver_id=edit_record.new_solver_id,
                        edit_json=edit_record.model_dump(mode="json"),
                        generated_by=edit_record.generated_by,
                    )
                    session.add(sql_edit)

                    # Create Solver Record (Inactive Candidate)
                    new_solver = Solver(
                        solver_id=new_config.version_id,
                        family_name=f"{base_solver.family_name}_gen_{exp_id[:4]}",
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

    async def ingest_proposals(self, proposals_dir: str = "proposals"):
        """
        Scans directory for YAML proposals (from EOD Agent) and persists them to DB.
        """
        if not os.path.exists(proposals_dir):
            return

        async with async_session_factory() as session:
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

                    sql_edit = SolverEdits(
                        id=edit_id,
                        experiment_id=None,
                        base_solver_id=base_id,
                        new_solver_id=new_solver_id,
                        edit_json={"ops": ops_data},
                        generated_by="llm_eod_agent",
                        reward=None,
                    )

                    session.add(sql_edit)
                    logger.info(f"Ingested proposal {filename} as Edit {edit_id}")

                    processed_dir = os.path.join(proposals_dir, "processed")
                    os.makedirs(processed_dir, exist_ok=True)
                    os.rename(path, os.path.join(processed_dir, filename))

                except Exception as e:
                    logger.error(f"Failed to ingest {filename}: {e}")

            await session.commit()

    async def process_pending_edits(self):
        """
        FR 5.7.2: Picks up pending EOD/Human edits and evaluates them.
        """
        async with async_session_factory() as session:
            stmt = select(SolverEdits).where(SolverEdits.reward == None)
            result = await session.execute(stmt)
            pending_edits = result.scalars().all()

            if not pending_edits:
                return

            logger.info(f"Processing {len(pending_edits)} pending edits...")

            for edit in pending_edits:
                try:
                    # 1. Load Base
                    stmt_b = select(Solver).where(Solver.solver_id == edit.base_solver_id)
                    res_b = await session.execute(stmt_b)
                    base_solver = res_b.scalars().first()

                    if not base_solver:
                        logger.error(f"Base solver {edit.base_solver_id} not found for edit {edit.id}")
                        continue

                    base_config = SolverConfig(**base_solver.config)

                    # 2. Reconstruct SolverEdit object
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

                    # 3. Apply Edit & VALIDATE
                    try:
                        new_config = self.apply_edit(base_config, solver_edit_obj)
                    except (ValueError, ValidationError) as ve:
                        logger.error(f"Edit {edit.id} Invalid: {ve}")
                        # Mark as invalid manually? Or just leave reward None?
                        # Set reward to very negative to indicate failure/rejection
                        edit.reward = -999.0
                        continue
                    try:
                        ensure_solver_definition_json(new_config.model_dump(mode="json"), None)
                    except Exception as dsl_err:
                        logger.error(
                            f"Edit {edit.id} DSL validation failed: {dsl_err}",
                            extra=solver_dsl_error_extra(dsl_err),
                        )
                        edit.reward = -999.0
                        continue

                    # 4. Create Solver
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

                    # 5. Evaluate
                    solver_run, metrics = await self.evaluate_variant(new_solver.solver_id, new_config)
                    session.add(solver_run)
                    session.add(metrics)

                    # 6. Update Reward using Composite
                    new_score = self._calculate_composite_score(metrics)

                    # We need a base score baseline.
                    stmt_m = (
                        select(SolverMetrics)
                        .where(SolverMetrics.solver_id == base_solver.solver_id)
                        .order_by(SolverMetrics.evaluated_at_utc.desc())
                        .limit(1)
                    )
                    res_m = await session.execute(stmt_m)
                    base_metrics = res_m.scalars().first()

                    base_score = (
                        self._calculate_composite_score(base_metrics)
                        if base_metrics
                        else (base_solver.sharpe_ratio or 0.0)
                    )

                    edit.reward = new_score - base_score
                    edit.evaluated_at_utc = datetime.now(timezone.utc)

                    logger.info(f"Processed Edit {edit.id}: Reward={edit.reward:.4f}")

                except Exception as e:
                    logger.error(f"Failed to process edit {edit.id}: {e}")

            await session.commit()

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

    async def _fetch_silver_events(self, task: EvaluationTask):
        import pandas as pd
        from orion.storage.models import BronzeEvent
        from orion.storage.models_silver import SilverAlpacaBar, SilverOptionFlow
        from sqlalchemy import and_, select

        alpaca_events = []
        flow_events = []
        price_data = {}

        async with async_session_factory() as session:
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
                    "bar_start_ts_utc": b.bar_start_ts_utc,
                }
                be = BronzeEvent(
                    event_id=f"silver_bar_{b.ticker}_{b.bar_start_ts_utc}",
                    event_type="ALPACA_BAR_1M",
                    source="BACKTEST",
                    event_ts_utc=b.bar_start_ts_utc,
                    payload=payload,
                    ticker=b.ticker,
                )
                alpaca_events.append(be)

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

            for t, rows in data_by_ticker.items():
                df = pd.DataFrame(rows).set_index("timestamp").sort_index()
                price_data[t] = df

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

    async def scan_for_promotions(self):
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
