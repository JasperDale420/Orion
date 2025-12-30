import json
import logging
from typing import Any, Dict, List

from dotenv import load_dotenv

from orion.agents.base import BaseAgent
from orion.core.id_utils import deterministic_solver_id
from orion.core.solver_schema import EditOp, EditOpType, SolverConfig, SolverEdit

load_dotenv()

logger = logging.getLogger(__name__)

# Optional dependency hook.
# Tests patch `orion.agents.meta_agent.acompletion`; keep a module-level name for patchability.
acompletion = None


class MetaAgent(BaseAgent):
    """
    PRD Addendum 5.3: MetaAgent (Poetiq-style).
    Uses LLM to propose evolutionary mutations (Edits) to Solvers.
    Integrated with any-llm for Deepseek support.
    """

    def __init__(self) -> None:
        from orion.config import agent_settings

        super().__init__(name="MetaAgent", model=agent_settings.model_name)
        self.api_key = agent_settings.openai_api_key
        if not self.api_key:
            logger.warning("OPENAI_API_KEY not found. Helper might fail.")

    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        BaseAgent run method support (optional generic entry point).
        """
        pass

    async def propose_edits(self, base_config: SolverConfig, performance_context: str = "") -> List[SolverEdit]:
        """
        Generates a list of valid SolverEdit objects to mutate the base_config.
        Uses a ReAct loop to query MCP tools for market context before deciding.
        """
        # 1. Initialize MCP Client & Fetch Tools
        from orion.connectors.mcp_client import MCPClient

        mcp = MCPClient()
        available_tools = []
        try:
            available_tools = await mcp.list_tools()
            logger.info(f"MetaAgent discovered {len(available_tools)} MCP tools.")
        except Exception as e:
            logger.warning(f"MetaAgent failed to list MCP tools: {e}")

        # 2. Convert MCP tools to OpenAI Tool Schemas
        openai_tools = []
        for t in available_tools:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema", {}),
                    },
                }
            )

        system_prompt = (
            "You are an expert Quant Researcher AI (Poetiq-style).\n"
            "Your goal is to evolve trading strategies (Solvers) to maximize Sharpe Ratio and minimize Drawdown.\n"
            "You have access to live market data tools. USE THEM. Verify current market regime (volatility, sector rotation, flow) before proposing changes.\n"
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
            f"Context:\n{performance_context}\n\n"
            "Step 1: Analyze the market using available tools (e.g. get_market_tide, get_volatility).\n"
            "Step 2: Propose 3 variants based on your findings."
        )

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

        # 3. Execution Loop (Max 5 turns)
        final_json_response = None

        try:
            acompletion_fn = acompletion
            if acompletion_fn is None:
                from litellm import acompletion as acompletion_fn

            for _ in range(5):  # Max turns
                if acompletion_fn is None:
                    logger.error("acompletion_fn is None, cannot proceed with LLM call")
                    return []
                response = await acompletion_fn(
                    model=self.model,
                    messages=messages,
                    tools=openai_tools if openai_tools else None,
                    tool_choice="auto" if openai_tools else None,
                    api_key=self.api_key,
                )

                msg = response.choices[0].message
                messages.append(msg)

                if msg.tool_calls:
                    # Execute Tools
                    for tc in msg.tool_calls:
                        func_name = tc.function.name
                        args = json.loads(tc.function.arguments)
                        logger.info(f"MetaAgent Calling Tool: {func_name} args={args}")

                        tool_result = await mcp.call_tool(func_name, args)

                        messages.append(
                            {"tool_call_id": tc.id, "role": "tool", "name": func_name, "content": str(tool_result)}
                        )
                else:
                    # Final response (presumably JSON)
                    final_json_response = msg.content
                    break

            if not final_json_response:
                logger.warning("MetaAgent exhausted turns without final JSON.")
                return []

            # 4. Parse Final JSON
            # Clean potential markdown fences
            if "```json" in final_json_response:
                final_json_response = final_json_response.split("```json")[1].split("```")[0].strip()
            elif "```" in final_json_response:
                final_json_response = final_json_response.split("```")[1].split("```")[0].strip()

            data = json.loads(final_json_response)

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
