"""
Codex CLI Client - Async wrapper for headless LLM execution.

Now routes through AI Gateway using Claude 4.6 Opus with built-in
tool execution for self-editing capabilities.
"""

import asyncio
import json
import logging
import os
from typing import Any

from orion.config import agent_settings

logger = logging.getLogger(__name__)


class CodexClientError(Exception):
    """Raised when LLM execution fails."""

    pass


# Basic tools the agent can use to solve its own problems
AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute path to the file"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write contents to a file, completely overwriting it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file"},
                    "content": {"type": "string", "description": "The new contents of the file"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command and return its output.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "The shell command to run"}},
                "required": ["command"],
            },
        },
    },
]


async def execute_tool(name: str, args: dict) -> str:
    """Execute local tools on behalf of the agent."""
    try:
        if name == "read_file":
            path = args.get("path", "")
            if not os.path.exists(path):
                return f"Error: File {path} does not exist."

            def _read_file():
                with open(path, encoding="utf-8") as f:
                    return f.read()

            return await asyncio.to_thread(_read_file)

        elif name == "write_file":
            path = args.get("path", "")
            content = args.get("content", "")

            def _write_file():
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

            await asyncio.to_thread(_write_file)
            return f"Successfully wrote {len(content)} bytes to {path}."

        elif name == "run_command":
            cmd = args.get("command", "")
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)

            output = ""
            if stdout:
                output += f"STDOUT:\n{stdout.decode('utf-8', errors='replace')}\n"
            if stderr:
                output += f"STDERR:\n{stderr.decode('utf-8', errors='replace')}\n"

            return output if output else f"Command completed with exit code {proc.returncode} and no output."

        else:
            return f"Error: Tool '{name}' not recognized."

    except Exception as e:
        return f"Tool execution failed: {str(e)}"


async def run_codex_completion(
    prompt: str = None,
    *,
    messages: list[dict[str, Any]] = None,
    model: str = None,
    timeout_seconds: int = 300,
) -> str:
    """
    Run LLM completion using AI Gateway with tool-calling support.

    Args:
        prompt: Legacy full prompt string.
        messages: List of chat messages (preferred).
        model: Model to use (defaults to agent_settings.model_name).
        reasoning_level: Unused for Claude, kept for backwards compatibility.
        timeout_seconds: Max time to wait for completion.
    """
    model = model or getattr(agent_settings, "model_name", "claude-4.6-opus")
    api_key = getattr(agent_settings, "ai_gateway_key", "empire-ai-gateway-key")
    base_url = getattr(agent_settings, "ai_gateway_url", "http://localhost:8002/v1")

    if not messages:
        messages = [{"role": "user", "content": prompt}]

    import aiohttp

    logger.info(
        "Starting LLM execution via AI Gateway",
        extra={"event": "llm_exec_start", "model": model, "message_count": len(messages)},
    )

    async with aiohttp.ClientSession() as session:
        # Agent execution loop to process tool calls
        for _ in range(10):  # Max 10 tool iterations
            pay_json = {
                "model": model,
                "messages": messages,
                "tools": AGENT_TOOLS,
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_tokens": 4096,
            }

            try:
                async with session.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=pay_json,
                    timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"AI Gateway error {resp.status}: {error_text}")
                        raise CodexClientError(f"Gateway API error {resp.status}: {error_text}")

                    data = await resp.json()
                    message = data["choices"][0]["message"]

                    # Add assistant response to history
                    messages.append(message)

                    # Check if there are tool calls
                    if message.get("tool_calls"):
                        for tc in message["tool_calls"]:
                            tool_name = tc["function"]["name"]
                            tool_args = json.loads(tc["function"]["arguments"])

                            logger.info(f"LLM executing tool: {tool_name}", extra={"args": tool_args})

                            result_str = await execute_tool(tool_name, tool_args)

                            logger.info(f"Tool {tool_name} completed", extra={"result_len": len(result_str)})

                            messages.append(
                                {"role": "tool", "tool_call_id": tc["id"], "name": tool_name, "content": result_str}
                            )

                        # Continue loop to send tool results back to LLM
                        continue

                    # No tool calls, return final content
                    return message.get("content", "")

            except TimeoutError as exc:
                logger.error("LLM request timed out")
                raise CodexClientError(f"LLM request timed out after {timeout_seconds}s") from exc

        raise CodexClientError("Max tool iterations exceeded")


def extract_json_from_response(response: str) -> dict:
    """Extract JSON from a codex response that may contain markdown fences."""
    text = response.strip()

    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON from response: {e}") from e


def build_chat_prompt(
    system_prompt: str,
    user_prompt: str,
    conversation_history: list | None = None,
) -> str:
    """
    Backwards compatibility function to build a single prompt string.
    New callers should pass native `messages` lists to run_codex_completion.
    """
    parts = []
    parts.append(f"<system>\\n{system_prompt}\\n</system>")

    if conversation_history:
        for msg in conversation_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"<{role}>\\n{content}\\n</{role}>")

    parts.append(f"<user>\\n{user_prompt}\\n</user>")
    parts.append("<assistant>")

    return "\\n\\n".join(parts)
