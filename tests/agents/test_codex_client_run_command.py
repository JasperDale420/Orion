"""Tests for the hardened run_command tool in codex_client.

The diagnostic agent's run_command tool must:
- run allowlisted commands via create_subprocess_exec (no shell)
- reject non-allowlisted programs with a structured error (not an exception)
- reject shell metacharacters that exec cannot honor
- preserve the 60s timeout behavior
"""

import asyncio

import pytest

from orion.agents import codex_client
from orion.agents.codex_client import ALLOWED_COMMANDS, execute_tool

pytestmark = pytest.mark.unit


async def test_allowed_command_executes_and_returns_output():
    """An allowlisted command executes and returns its output."""
    result = await execute_tool("run_command", {"command": "python3 -c print(123)"})
    assert "Error:" not in result
    assert "123" in result


async def test_disallowed_command_rejected():
    """A non-allowlisted program is rejected with a structured error, no exception."""
    result = await execute_tool("run_command", {"command": "rm -rf /tmp/whatever"})
    assert result.startswith("Error:")
    assert "not allowed" in result
    assert "rm" in result


async def test_metacharacter_command_rejected():
    """Commands with shell metacharacters are rejected with guidance."""
    result = await execute_tool("run_command", {"command": "cat /etc/passwd | grep root"})
    assert result.startswith("Error:")
    assert "metacharacter" in result.lower()


async def test_metacharacter_chaining_rejected():
    result = await execute_tool("run_command", {"command": "ls; rm -rf /"})
    assert result.startswith("Error:")
    assert "metacharacter" in result.lower()


async def test_command_substitution_rejected():
    result = await execute_tool("run_command", {"command": "echo $(whoami)"})
    assert result.startswith("Error:")
    assert "metacharacter" in result.lower()


async def test_empty_command_rejected():
    result = await execute_tool("run_command", {"command": "   "})
    assert result.startswith("Error:")


async def test_timeout_path_unchanged(monkeypatch):
    """A command that exceeds the 60s timeout is caught and returned as a string,
    not raised. We patch wait_for to raise TimeoutError immediately to avoid
    a real 60s wait, and confirm execute_tool swallows it into a string result."""

    class _FakeProc:
        returncode = None

        async def communicate(self):
            return b"", b""

    async def fake_exec(*_args, **_kwargs):
        return _FakeProc()

    async def fake_wait_for(*_args, **_kwargs):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(codex_client.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(codex_client.asyncio, "wait_for", fake_wait_for)

    result = await execute_tool("run_command", {"command": "python3 -c print(1)"})
    assert isinstance(result, str)
    assert "Tool execution failed" in result


def test_allowlist_contents():
    """Sanity-check the conservative allowlist for a read-mostly diagnostic agent."""
    expected = {
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
    assert set(ALLOWED_COMMANDS) == expected


class _FakeResp:
    """Mimics an httpx.Response: status_code attr, sync .text/.json()."""

    status_code = 200
    text = ""

    @staticmethod
    def json() -> dict:
        return {"choices": [{"message": {"content": "hello"}}]}


class _FakeClient:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, **kwargs) -> _FakeResp:
        return self._resp


async def test_run_codex_completion_returns_content(monkeypatch):
    """A 200 response with no tool calls is parsed via the httpx API and returns content.

    Guards the aiohttp->httpx swap: fails if the code uses resp.status (not
    status_code), awaits resp.json()/resp.text, or mishandles the client context.
    """
    monkeypatch.setattr(codex_client.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResp()))
    out = await codex_client.run_codex_completion(prompt="hi")
    assert out == "hello"


class _HangingClient:
    """A client whose POST never returns within the deadline."""

    async def __aenter__(self) -> "_HangingClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, **kwargs) -> _FakeResp:
        await asyncio.sleep(10)
        return _FakeResp()


async def test_run_codex_completion_enforces_total_timeout(monkeypatch):
    """A response slower than timeout_seconds raises CodexClientError.

    Guards the total-deadline (asyncio.wait_for): httpx's float timeout is
    per-operation, so without wait_for a trickling response could exceed the
    intended budget.
    """
    monkeypatch.setattr(codex_client.httpx, "AsyncClient", lambda *a, **k: _HangingClient())
    with pytest.raises(codex_client.CodexClientError):
        await codex_client.run_codex_completion(prompt="hi", timeout_seconds=0)
