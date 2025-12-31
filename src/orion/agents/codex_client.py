"""
Codex CLI Client - Async wrapper for headless codex execution.

Calls `codex exec` via subprocess to run LLM completions through the
locally authenticated codex CLI instead of direct API calls.
"""

import asyncio
import json
import logging
import shutil
from typing import Optional

logger = logging.getLogger(__name__)


class CodexClientError(Exception):
    """Raised when codex CLI execution fails."""

    pass


async def run_codex_completion(
    prompt: str,
    *,
    model: str = "gpt-5.2",
    reasoning_level: str = "extra_high",
    timeout_seconds: int = 300,
) -> str:
    """
    Run codex CLI in headless mode and return the response.

    Args:
        prompt: The full prompt to send (system + user combined).
        model: Model to use (default: gpt-5.2).
        reasoning_level: Reasoning intensity (extra_high for complex analysis).
        timeout_seconds: Max time to wait for completion.
        json_output: If True, instruct codex to output JSON.

    Returns:
        The raw text response from codex.

    Raises:
        CodexClientError: If codex is not installed or execution fails.
    """
    # Verify codex is available
    codex_path = shutil.which("codex")
    if not codex_path:
        raise CodexClientError("codex CLI not found in PATH. Please install codex: https://github.com/openai/codex")

    # Build command with autonomy flags
    cmd = [
        codex_path,
        "exec",
        "-m",
        model,
        "--full-auto",  # Non-interactive, auto-approve commands
        "--skip-git-repo-check",  # Allow running outside git repos
        "--sandbox",
        "workspace-write",  # Allow file writes in workspace
    ]

    # Add reasoning level config if specified
    if reasoning_level:
        cmd.extend(["-c", f"reasoning_level={reasoning_level}"])

    logger.info(
        "Running codex completion",
        extra={
            "event": "codex_exec_start",
            "model": model,
            "reasoning_level": reasoning_level,
            "prompt_length": len(prompt),
        },
    )

    try:
        # Run codex with prompt via stdin
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=prompt.encode("utf-8")),
            timeout=timeout_seconds,
        )

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")
            logger.error(
                "Codex execution failed",
                extra={
                    "event": "codex_exec_error",
                    "returncode": process.returncode,
                    "stderr": error_msg[:500],
                },
            )
            raise CodexClientError(f"Codex failed (exit {process.returncode}): {error_msg}")

        response = stdout.decode("utf-8", errors="replace").strip()

        logger.info(
            "Codex completion successful",
            extra={
                "event": "codex_exec_success",
                "response_length": len(response),
            },
        )

        return response

    except asyncio.TimeoutError as err:
        logger.error(
            f"Codex execution timed out after {timeout_seconds}s",
            extra={"event": "codex_exec_timeout", "timeout_seconds": timeout_seconds},
        )
        raise CodexClientError(f"Codex timed out after {timeout_seconds} seconds") from err


def extract_json_from_response(response: str) -> dict:
    """
    Extract JSON from a codex response that may contain markdown fences.

    Args:
        response: Raw text response from codex.

    Returns:
        Parsed JSON dict.

    Raises:
        ValueError: If no valid JSON found.
    """
    text = response.strip()

    # Try to extract from markdown code fences
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        # Try generic code fence
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
    conversation_history: Optional[list] = None,
) -> str:
    """
    Build a single prompt string from chat-style messages.

    Codex exec takes a single prompt, so we combine system/user/history
    into a structured format.

    Args:
        system_prompt: System instructions.
        user_prompt: User message.
        conversation_history: Optional list of prior messages.

    Returns:
        Combined prompt string.
    """
    parts = []

    parts.append(f"<system>\n{system_prompt}\n</system>")

    if conversation_history:
        for msg in conversation_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"<{role}>\n{content}\n</{role}>")

    parts.append(f"<user>\n{user_prompt}\n</user>")

    parts.append("<assistant>")

    return "\n\n".join(parts)
