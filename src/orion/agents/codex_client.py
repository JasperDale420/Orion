"""
Codex CLI Client - Async wrapper for headless codex execution.

Calls `codex exec` via subprocess to run LLM completions through the
locally authenticated codex CLI instead of direct API calls.
"""

import asyncio
import json
from orion.shared.logger import setup_struct_logger
import shutil
from typing import Optional

from orion.config import agent_settings

logger = setup_struct_logger("orion.agents.codex_client")


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
    Run LLM completion using DeepSeek API (preferred) or codex CLI (fallback).

    Args:
        prompt: The full prompt to send (system + user combined).
        model: Model to use (default: gpt-5.2 for codex, deepseek-reasoner for DeepSeek).
        reasoning_level: Reasoning intensity (extra_high for complex analysis).
        timeout_seconds: Max time to wait for completion.

    Returns:
        The raw text response from LLM.

    Raises:
        CodexClientError: If both DeepSeek and codex fail.
    """
    # Try DeepSeek API first if configured
    deepseek_api_key = agent_settings.deepseek_api_key
    deepseek_model = agent_settings.deepseek_model

    if deepseek_api_key and deepseek_api_key != "your-deepseek-api-key-here":  # pragma: allowlist secret
        try:
            return await _run_deepseek_completion(
                prompt=prompt,
                model=deepseek_model,
                api_key=deepseek_api_key,
                timeout_seconds=timeout_seconds,
            )
        except Exception as e:
            logger.warning(f"DeepSeek failed, falling back to codex: {e}")

    # Fallback to codex CLI
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


async def _run_deepseek_completion(
    prompt: str,
    model: str,
    api_key: str,
    timeout_seconds: int = 300,
) -> str:
    """Run completion using DeepSeek API."""
    import aiohttp

    logger.info(
        "Running DeepSeek completion",
        extra={
            "event": "deepseek_start",
            "model": model,
            "prompt_length": len(prompt),
        },
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 4096,
            },
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise CodexClientError(f"DeepSeek API error {resp.status}: {error_text}")

            data = await resp.json()
            response = data["choices"][0]["message"]["content"]

            logger.info(
                "DeepSeek completion successful",
                extra={
                    "event": "deepseek_success",
                    "response_length": len(response),
                },
            )

            return response


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
