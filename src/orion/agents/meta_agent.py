from dotenv import load_dotenv

from orion.agents.codex_client import (
    extract_json_from_response,
    run_codex_completion,
)
from orion.core.id_utils import deterministic_solver_id
from orion.core.solver_schema import EditOp, EditOpType, SolverConfig, SolverEdit
from orion.shared.logger import setup_struct_logger

load_dotenv()

logger = setup_struct_logger(__name__)


class MetaAgent:
    """
    PRD Addendum 5.3: MetaAgent (Poetiq-style).
    Uses LLM to propose evolutionary mutations (Edits) to Solvers.
    Routes through Empire AI Gateway for LLM access.
    """

    def __init__(self) -> None:
        pass

    async def _fetch_strategy_research(
        self,
        base_config: SolverConfig,
        performance_context: str,
    ) -> str:
        """
        Fetch relevant strategy research from TradingRAG.

        Queries indexed trading books for insights related to the solver's
        strategy type and performance issues.
        """
        try:
            from orion.clients.trading_rag import get_rag_client

            rag = get_rag_client()

            # Build a research query based on solver config
            rule_ids = ", ".join(base_config.rules) if base_config.rules else "momentum"
            query = (
                f"Best practices for {rule_ids} trading strategies. "
                f"How to optimize exits and risk management for options trading."
            )

            # If performance context mentions issues, add to query
            if "drawdown" in performance_context.lower():
                query += " How to reduce drawdown in trading systems."
            if "win rate" in performance_context.lower():
                query += " How to improve win rate in trading."

            result = await rag.answer(query, top_k=3)

            if result.get("answer"):
                return f"--- Strategy Research from Trading Books ---\n{result['answer']}\n"

            return ""
        except Exception as e:
            logger.warning(f"TradingRAG research failed (non-fatal): {e}")
            return ""

    async def propose_edits(self, base_config: SolverConfig, performance_context: str = "") -> list[SolverEdit]:
        """
        Generates a list of valid SolverEdit objects to mutate the base_config.
        Uses a ReAct loop to query MCP tools for market context before deciding.
        Also queries TradingRAG for strategy research from indexed trading books.
        """
        # 0. Fetch strategy research from TradingRAG
        rag_context = await self._fetch_strategy_research(base_config, performance_context)

        # 0b. Fetch weekly ML performance metrics
        ml_performance_context = ""
        try:
            from orion.ml.performance_tracker import get_weekly_performance

            weekly_perf = await get_weekly_performance()
            if weekly_perf:
                ml_lines = ["--- ML Model Performance (Weekly) ---"]
                for key, data in weekly_perf.items():
                    acc = data.get("accuracy_pct", 0) or 0
                    avg_ret = data.get("avg_return", 0) or 0
                    ml_lines.append(f"{key}: accuracy={acc:.1f}%, avg_return={avg_ret:.2f}%")
                    if acc < 55:
                        ml_lines.append("  ⚠️ LOW ACCURACY - model may need retraining")
                ml_performance_context = "\n".join(ml_lines)
        except Exception as e:
            logger.debug(f"Failed to fetch ML performance: {e}")

        augmented_context = performance_context
        if rag_context:
            augmented_context += f"\n\n{rag_context}"
        if ml_performance_context:
            augmented_context += f"\n\n{ml_performance_context}"

        # 1. Initialize MCP Client & Fetch Tools
        from orion.connectors.mcp_client import MCPClient

        mcp = MCPClient()
        available_tools = []
        try:
            available_tools = await mcp.list_tools()
            logger.info(f"MetaAgent discovered {len(available_tools)} MCP tools.")
        except Exception as e:
            logger.warning(f"MetaAgent failed to list MCP tools: {e}")

        system_prompt = (
            "You are an expert Quant Researcher AI (Poetiq-style).\n"
            "Your goal is to evolve trading strategies (Solvers) to maximize Sharpe Ratio and minimize Drawdown.\n"
            "You have access to live market data tools. USE THEM. Verify current market regime (volatility, sector rotation, flow) before proposing changes.\n"
            "You also have access to codebase tools: read_file, write_file, and run_command.\n"
            "If you run into errors or missing information, USE YOUR TOOLS to search the codebase, read files, edit your own code, and fix bugs directly.\n"
            "You are fully capable of solving your own problems and editing your own code.\n\n"
            "You will be given a Base Solver Configuration (JSON) and its Performance Context.\n"
            "You must propose 3 distinct variations (mutations) to improve the strategy.\n"
            "Mutations can include:\n"
            "- Modifying parameters (thresholds, multipliers)\n"
            "- Toggling features\n"
            "- Adding or Removing entry rules\n"
            "- Modifying risk settings (within safety bounds)\n\n"
            "Final Output must be a strictly Valid JSON object with this schema (and nothing else):\n"
            "{\n"
            '  "variants": [\n'
            "    {\n"
            '      "ops": [\n'
            '        {"op": "modify_param", "param_name": "exit_logic.take_profit_atr_multiple", "new_value": 3.0, "reasoning": "Expanding profit target..."},\n'
            '        {"op": "toggle_feature", "feature_name": "vol_oi_ratio", "new_value": true, "reasoning": "Adding volume signal"},\n'
            '        {"op": "add_rule", "new_value": "no_earnings_24h", "reasoning": "Avoiding event risk"}\n'
            "      ],\n"
            '      "rationale": "Variant A focuses on tightening exits..."\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )

        user_prompt = (
            f"Base Config:\n{base_config.model_dump_json(indent=2)}\n\n"
            f"Context:\n{augmented_context}\n\n"
            "Step 1: Analyze the market using available tools (e.g. get_market_tide, get_volatility).\n"
            "Step 2: Propose 3 variants based on your findings."
        )

        # 3. Execute via AI Gateway with native messages
        try:
            from orion.config import agent_settings

            messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

            final_json_response = await run_codex_completion(
                messages=messages,
                model=agent_settings.model_name,
                timeout_seconds=600,  # Longer timeout for complex strategy analysis
            )

            if not final_json_response:
                logger.warning("MetaAgent received empty response from LLM.")
                return []

            # 4. Parse Final JSON
            data = extract_json_from_response(final_json_response)

            variants = []
            for v_data in data.get("variants", []):
                ops = []
                for op_data in v_data.get("ops", []):
                    # Map LLM output to EditOp
                    op_type_str = op_data.get("op", "modify_param").lower()

                    try:
                        op_enum = EditOpType(op_type_str)
                    except ValueError:
                        logger.warning(f"Unknown op type: {op_type_str}, defaulting to modify_param")
                        op_enum = EditOpType.MODIFY_PARAM

                    ops.append(
                        EditOp(
                            op=op_enum,
                            param_name=op_data.get("param_name"),
                            feature_name=op_data.get("feature_name"),
                            rule_id=op_data.get("rule_id"),  # For remove_rule potentially
                            new_value=op_data.get("new_value"),
                            old_value=op_data.get("old_value"),
                            reasoning=op_data.get("reasoning", "LLM generated"),
                        )
                    )

                new_solver_id = deterministic_solver_id(
                    base_solver_id=base_config.version_id,
                    edit_ops={"ops": [o.model_dump(mode="json") for o in ops]},
                    prefix="meta",
                )
                variants.append(
                    SolverEdit(
                        base_solver_id=base_config.version_id,
                        new_solver_id=new_solver_id,
                        generated_by="meta_agent_llm",
                        ops=ops,
                    )
                )

            return variants

        except Exception as e:
            logger.error(f"MetaAgent Gen Failed: {e}")
            return []
