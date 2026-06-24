"""
Codex CLI Client - Async wrapper for headless LLM execution.

Routes through Empire AI Gateway (localhost:8002/v1) with glm-5.2 as the
default model. Supports tool execution for self-editing capabilities.
"""

import asyncio
import json
import os
import re
import shlex
from typing import Any

import httpx

from orion.config import agent_settings
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger(__name__)


class CodexClientError(Exception):
    """Raised when LLM execution fails."""


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


# Allowlist of program basenames the diagnostic agent may invoke. Conservative
# set for a read-mostly EOD/meta agent: inspection, search, and read-only DB/HTTP
# probes. Anything outside this set is rejected back to the LLM as a tool error.
ALLOWED_COMMANDS = frozenset(
    {
        "git",
        "ls",
        "cat",
        "head",
        "tail",
        "grep",
        "rg",
        "find",
        "wc",
        "python",
        "python3",
        "uv",
        "pytest",
        "docker",
        "psql",
        "curl",
        "jq",
    }
)

# Shell metacharacters that create_subprocess_exec cannot honor (no shell parses
# them). Reject rather than silently mangle, so the model reissues single commands.
_SHELL_METACHARACTERS = ("|", ";", "&", ">", "<", "`", "$(")


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

        if name == "write_file":
            path = args.get("path", "")
            content = args.get("content", "")

            def _write_file():
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

            await asyncio.to_thread(_write_file)
            return f"Successfully wrote {len(content)} bytes to {path}."

        if name == "run_command":
            cmd = args.get("command", "")

            # Reject shell metacharacters: exec cannot honor them, so silently
            # splitting would mangle the command. Tell the model to reissue.
            if any(meta in cmd for meta in _SHELL_METACHARACTERS):
                logger.warning("codex_command_rejected", command=cmd, reason="shell_metacharacter")
                return (
                    "Error: command contains shell metacharacters (one of | ; & > < ` $() ), "
                    "which are not supported. Issue a single command without pipes, "
                    "redirection, or chaining."
                )

            try:
                argv = shlex.split(cmd)
            except ValueError as exc:
                logger.warning("codex_command_rejected", command=cmd, reason=f"unparseable: {exc}")
                return f"Error: could not parse command ({exc}). Issue a single, well-formed command."

            if not argv:
                logger.warning("codex_command_rejected", command=cmd, reason="empty")
                return "Error: empty command."

            # Require a bare program name. Reject any path component so an
            # allowlisted basename can't smuggle a different binary
            # (e.g. /tmp/python3, ./git) past the check.
            if "/" in argv[0] or os.sep in argv[0] or (os.altsep and os.altsep in argv[0]):
                logger.warning("codex_command_rejected", command=cmd, reason="path_in_program", program=argv[0])
                return (
                    f"Error: command must be a bare program name, not a path ('{argv[0]}'). "
                    f"Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}."
                )
            program = argv[0]
            if program not in ALLOWED_COMMANDS:
                logger.warning("codex_command_rejected", command=cmd, reason="not_allowed", program=program)
                return (
                    f"Error: command '{program}' is not allowed. "
                    f"Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}."
                )

            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)

            output = ""
            if stdout:
                output += f"STDOUT:\n{stdout.decode('utf-8', errors='replace')}\n"
            if stderr:
                output += f"STDERR:\n{stderr.decode('utf-8', errors='replace')}\n"

            logger.info("codex_command_executed", command=cmd, output_bytes=len(output))

            return output if output else f"Command completed with exit code {proc.returncode} and no output."

        return f"Error: Tool '{name}' not recognized."

    except Exception as e:
        return f"Tool execution failed: {e}"


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
        timeout_seconds: Max time to wait for completion.
    """
    model = model or getattr(agent_settings, "model_name", "glm-5.2")
    api_key = getattr(agent_settings, "ai_gateway_key", "empire-ai-gateway-key")
    base_url = getattr(agent_settings, "ai_gateway_url", "http://localhost:8002/v1")

    if not messages:
        messages = [{"role": "user", "content": prompt}]

    logger.info(
        "Starting LLM execution via AI Gateway",
        event_type="llm_exec_start",
        model=model,
        message_count=len(messages),
    )

    async with httpx.AsyncClient() as client:
        # Agent execution loop to process tool calls
        for _ in range(30):  # Max 30 tool iterations
            pay_json = {
                "model": model,
                "messages": messages,
                "tools": AGENT_TOOLS,
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_tokens": 8192,
            }

            try:
                # wait_for enforces a hard total deadline (httpx's float timeout
                # is per-operation, unlike aiohttp's ClientTimeout(total=...)).
                resp = await asyncio.wait_for(
                    client.post(
                        f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=pay_json,
                        timeout=timeout_seconds,
                    ),
                    timeout=timeout_seconds,
                )
                if resp.status_code != 200:
                    error_text = resp.text
                    logger.error(
                        "AI Gateway error",
                        status=resp.status_code,
                        response_body=error_text[:500],
                    )
                    raise CodexClientError(f"Gateway API error {resp.status_code}: {error_text}")

                data = resp.json()
                message = data["choices"][0]["message"]

                # Add assistant response to history
                messages.append(message)

                # Check if there are tool calls
                if message.get("tool_calls"):
                    for tc in message["tool_calls"]:
                        tool_name = tc["function"]["name"]
                        tool_args = json.loads(tc["function"]["arguments"])

                        logger.info(
                            "LLM executing tool",
                            tool_name=tool_name,
                            tool_arg_keys=list(tool_args.keys()),
                        )

                        result_str = await execute_tool(tool_name, tool_args)

                        logger.info(
                            "Tool completed",
                            tool_name=tool_name,
                            result_len=len(result_str),
                        )

                        messages.append(
                            {"role": "tool", "tool_call_id": tc["id"], "name": tool_name, "content": result_str}
                        )

                    # Continue loop to send tool results back to LLM
                    continue

                # No tool calls, return final content
                return message.get("content", "")

            except (TimeoutError, httpx.TimeoutException) as exc:
                logger.error("LLM request timed out", timeout_seconds=timeout_seconds)
                raise CodexClientError(f"LLM request timed out after {timeout_seconds}s") from exc

        raise CodexClientError("Max tool iterations exceeded")


def extract_json_from_response(response: str) -> dict:
    """Extract JSON from a codex response that may contain markdown fences or surrounding text.

    Handles:
    - Clean JSON
    - JSON wrapped in ```json ... ``` or ``` ... ``` fences
    - JSON embedded in prose text (finds outermost { ... })
    - Truncated JSON (attempts to repair missing closing braces/brackets)
    """
    if not response or not response.strip():
        raise ValueError("Empty response from LLM")

    text = response.strip()

    # Strategy 1: Try direct parse (clean JSON)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract from markdown fences
    if "```json" in text:
        fenced = text.split("```json", 1)[1].split("```", 1)[0].strip()
        try:
            return json.loads(fenced)
        except json.JSONDecodeError:
            # May be truncated, try repair below
            text = fenced
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 2:
            fenced = parts[1].strip()
            try:
                return json.loads(fenced)
            except json.JSONDecodeError:
                text = fenced

    # Strategy 3: Find the outermost JSON object in the text
    brace_start = text.find("{")
    if brace_start != -1:
        # Find matching closing brace by counting depth
        depth = 0
        in_string = False
        escape_next = False
        best_end = -1
        for i in range(brace_start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    best_end = i
                    break

        if best_end != -1:
            candidate = text[brace_start : best_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # Strategy 4: Truncated JSON - try to repair by closing open braces/brackets
        candidate = text[brace_start:]
        candidate = _repair_truncated_json(candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Failed to parse JSON from LLM response (length={len(response)})")


def _repair_truncated_json(text: str) -> str:
    """Attempt to repair truncated JSON by closing unclosed braces/brackets and strings."""
    # Strip trailing comma or incomplete key-value
    text = re.sub(r",\s*$", "", text)
    # Strip trailing incomplete string value (key: "incomplete...)
    text = re.sub(r':\s*"[^"]*$', ': ""', text)
    # Strip trailing incomplete key ("key...)
    text = re.sub(r',\s*"[^"]*$', "", text)

    # Track the stack of unclosed delimiters in order
    stack: list[str] = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]") and stack and stack[-1] == ch:
            stack.pop()

    # Close in reverse order (innermost first)
    stack.reverse()
    text += "".join(stack)
    return text


def build_chat_prompt(
    system_prompt: str,
    user_prompt: str,
    conversation_history: list | None = None,
) -> str:
    """
    Backwards compatibility function to build a single prompt string.
    New callers should pass native `messages` lists to run_codex_completion.
    """
    parts = [f"<system>\\n{system_prompt}\\n</system>"]

    if conversation_history:
        for msg in conversation_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"<{role}>\\n{content}\\n</{role}>")

    parts.append(f"<user>\\n{user_prompt}\\n</user>")
    parts.append("<assistant>")

    return "\\n\\n".join(parts)
