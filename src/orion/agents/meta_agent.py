import logging
from typing import Any, Dict, List

from dotenv import load_dotenv

from orion.agents.base import BaseAgent
from orion.agents.codex_client import (
    build_chat_prompt,
    extract_json_from_response,
    run_codex_completion,
    MaxToolIterationsExceededError,
)
from orion.core.id_utils import deterministic_solver_id
from orion.core.solver_schema import EditOp, EditOpType, SolverConfig, SolverEdit

load_dotenv()

logger = logging.getLogger(__name__)


class MetaAgent(BaseAgent):
    """
    PRD Addendum 5.3: MetaAgent (Poetiq-style).
    Uses LLM to propose evolutionary mutations (Edits) to Solvers.
    Integrated with any-llm for Deepseek support.
    """

    def __init__(self) -> None:
        from orion.config import agent_settings

        super().__init__(name="MetaAgent", model=agent_settings.model_name)

    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        BaseAgent run method support (optional generic entry point).
        """
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

    async def propose_edits(self, base_config: SolverConfig, performance_context: str = "") -> List[SolverEdit]:
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

        # 0c. Fetch market regime context directly (instead of relying on LLM tools)
        market_regime_context = await self._fetch_market_regime_context()

        augmented_context = performance_context
        if rag_context:
            augmented_context += f"\n\n{rag_context}"
        if ml_performance_context:
            augmented_context += f"\n\n{ml_performance_context}"
        if market_regime_context:
            augmented_context += f"\n\n{market_regime_context}"

        # Note: MCP tools are discovered but not currently passed to codex CLI.
        # The codex CLI may have its own built-in tools. The prompt below does not
        # require tool usage to complete the task.

        system_prompt = (
            "You are an expert Quant Researcher AI (Poetiq-style).\n"
            "Your goal is to evolve trading strategies (Solvers) to maximize Sharpe Ratio and minimize Drawdown.\n"
            "You will be given a Base Solver Configuration (JSON), its Performance Context, and current Market Regime data.\n"
            "Analyze the data provided and propose 3 distinct variations (mutations) to improve the strategy.\n"
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
            "Based on the provided data, propose 3 variants to improve the strategy."
        )

        # 3. Execute via Codex CLI
        final_json_response = None

        try:
            from orion.config import agent_settings

            full_prompt = build_chat_prompt(system_prompt, user_prompt)

            final_json_response = await run_codex_completion(
                prompt=full_prompt,
                model=agent_settings.model_name,
                reasoning_level=getattr(agent_settings, "reasoning_level", "extra_high"),
                timeout_seconds=600,  # Longer timeout for complex strategy analysis
                max_tool_iterations=15,  # Allow reasonable tool use iterations
            )

            if not final_json_response:
                logger.warning("MetaAgent received empty response from codex.")
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

        except MaxToolIterationsExceededError as e:
            logger.error(f"MetaAgent exceeded max tool iterations: {e}")
            # Return empty variants - the system will continue with existing solvers
            return []
        except Exception as e:
            logger.error(f"MetaAgent Gen Failed: {e}")
            return []

    async def _fetch_market_regime_context(self) -> str:
        """
        Fetch current market regime data directly via connectors.

        This provides market context to the LLM without relying on
        tool-calling which can cause iteration loops.
        """
        context_lines = ["--- Current Market Regime ---"]
        try:
            from orion.connectors.uw_market_tide_connector import UWMarketTideConnector

            connector = UWMarketTideConnector()
            tide = await connector.get_market_tide()
            if tide:
                context_lines.append(f"Market Tide: {tide.get('sentiment', 'unknown')}")
                context_lines.append(f"Trend: {tide.get('trend', 'unknown')}")
        except Exception as e:
            logger.debug(f"Failed to fetch market tide: {e}")
            context_lines.append("Market Tide: unavailable")

        try:
            from orion.connectors.uw_iv_rank_connector import UWIVRankConnector

            connector = UWIVRankConnector()
            # Get IV rank for SPY as market proxy
            iv_rank = await connector.get_iv_rank("SPY")
            if iv_rank is not None:
                context_lines.append(f"SPY IV Rank: {iv_rank:.1f}%")
        except Exception as e:
            logger.debug(f"Failed to fetch IV rank: {e}")
            context_lines.append("SPY IV Rank: unavailable")

        try:
            from orion.connectors.uw_greek_exposure_connector import UWGreekExposureConnector

            connector = UWGreekExposureConnector()
            exposure = await connector.get_market_exposure()
            if exposure:
                context_lines.append(f"Market Gamma Exposure: {exposure.get('gamma', 'unknown')}")
                context_lines.append(f"Market Delta Exposure: {exposure.get('delta', 'unknown')}")
        except Exception as e:
            logger.debug(f"Failed to fetch greek exposure: {e}")

        return "\n".join(context_lines)
