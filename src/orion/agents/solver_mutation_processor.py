"""Solver mutation processing for EOD agent proposals.

Takes mutation proposals from EODReviewAgent, applies edits to solver configs,
runs the MetaSearchAgent refinement loop, and promotes successful solvers to paper.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.core.id_utils import deterministic_solver_id
from orion.core.solver_schema import EditOp, EditOpType, SolverConfig, SolverEdit
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.storage.models_solvers import Solver

logger = setup_struct_logger("orion.agents.solver_mutation_processor")


async def process_solver_mutations(proposals: list[dict[str, Any]]) -> None:
    """Process solver_mutation proposals using iterative refinement loop.

    Flow:
    1. Build initial SolverConfig from proposal
    2. Call MetaSearchAgent.refine_and_promote() which:
       - Backtests the solver
       - If score < threshold, asks MetaAgent for refinement
       - Repeats until threshold met or max iterations reached
       - Auto-promotes to paper stage if threshold met
    """
    logger.info(
        f"Processing {len(proposals)} solver mutation proposals with refinement loop",
        extra={"event": "solver_mutations_start", "count": len(proposals)},
    )

    meta_search = MetaSearchAgent()
    promoted_count = 0
    failed_count = 0

    for proposal in proposals:
        try:
            mutation = proposal.get("mutation", {})
            base_solver_id = mutation.get("base_solver_id")
            ops_data = mutation.get("ops", [])

            if not ops_data:
                logger.warning("Skipping mutation with no ops")
                continue

            # Load base solver config
            base_config = None
            if base_solver_id:

                async def fetch_base(session: Any, sid: str = base_solver_id) -> Any:
                    stmt = select(Solver).where(Solver.solver_id == sid)
                    result = await session.execute(stmt)
                    return result.scalars().first()

                base_solver = await db_query(fetch_base)
                if base_solver:
                    base_config = SolverConfig(**base_solver.config)

            if not base_config:
                # Use default baseline config
                from orion.core.solver_schema import ExitLogic, SolverFeatures, SolverRiskConfig

                base_config = SolverConfig(
                    version_id="baseline",
                    rules=[],
                    features=SolverFeatures(),
                    risk=SolverRiskConfig(),
                    exit_logic=ExitLogic(
                        take_profit_atr_multiple=2.0,
                        stop_loss_atr_multiple=1.0,
                    ),
                )

            # Convert ops to EditOp objects
            ops = []
            for op_data in ops_data:
                try:
                    op_type = EditOpType(op_data.get("op", "modify_param"))
                    ops.append(
                        EditOp(
                            op=op_type,
                            param_name=op_data.get("param_name"),
                            feature_name=op_data.get("feature_name"),
                            new_value=op_data.get("new_value"),
                            reasoning=op_data.get("reasoning", "EOD agent generated"),
                        )
                    )
                except Exception as e:
                    logger.warning(f"Skipping invalid op: {e}")
                    continue

            if not ops:
                continue

            # Generate new solver ID
            new_solver_id = deterministic_solver_id(
                base_solver_id=base_solver_id or "baseline",
                edit_ops={"ops": [o.model_dump(mode="json") for o in ops]},
                prefix="eod",
            )

            # Apply edit to create initial config
            solver_edit = SolverEdit(
                base_solver_id=base_solver_id or "baseline",
                new_solver_id=new_solver_id,
                generated_by="eod_agent",
                ops=ops,
            )

            new_config = meta_search.apply_edit(base_config, solver_edit)

            logger.info(
                f"Starting refinement loop for solver {new_solver_id}",
                extra={
                    "event": "refinement_start",
                    "solver_id": new_solver_id,
                    "base_solver_id": base_solver_id,
                    "ops_count": len(ops),
                },
            )

            # Run refinement loop - backtests and refines until threshold met
            promoted_id = await meta_search.refine_and_promote(
                solver_id=new_solver_id,
                config=new_config,
                base_solver_id=base_solver_id or "baseline",
            )

            if promoted_id:
                promoted_count += 1
                logger.info(
                    f"Solver {promoted_id} promoted to paper trading",
                    extra={"event": "solver_promoted_to_paper", "solver_id": promoted_id},
                )
            else:
                failed_count += 1

        except Exception as e:
            logger.error(f"Failed to process mutation: {e}", exc_info=True)
            failed_count += 1

    logger.info(
        f"Solver mutation processing complete: {promoted_count} promoted, {failed_count} failed/gave up",
        extra={
            "event": "solver_mutations_complete",
            "promoted": promoted_count,
            "failed": failed_count,
        },
    )
