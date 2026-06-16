"""Hermetic contract tests for the native launchd wrapper scripts.

These verify the gateway-key resolution block in each wrapper:

  * an operator env with only ``GATEWAY_API_KEY`` set must NOT be clobbered by
    a default — both alias names must resolve to that value;
  * only ``DATA_GATEWAY_API_KEY`` set must resolve both names too;
  * the service wrappers must fail fast (exit 78, before exec'ing python) when
    neither alias is present, rather than silently substituting a revoked key;
  * the revoked plaintext literal ``gw_orion_trading_key_55555`` must appear
    nowhere in any of the wrappers.

The tests are fully hermetic: each wrapper is copied into a tmpdir with its
hardcoded ``PROJECT_ROOT`` repointed there, ``uv sync`` neutralised, and the
final ``exec ... python ...`` replaced by a sentinel — so no python service,
``uv``, network, or the real ``.env`` is ever touched. Wrappers run under a
minimal env (not the parent process env) so conftest's globals can't leak in.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

REVOKED_LITERAL = "gw_orion_trading_key_55555"
SENTINEL = "SENTINEL_EXEC_REACHED"

# Wrappers that resolve the gateway key and MUST fail fast when it is absent.
# run_market_open_dataflow_check.sh is hard-exit too (round-4 review): its
# gateway probe AUTHENTICATES, and probing with an empty key turns an auth
# misconfiguration into a market-hours CRITICAL "gateway unreachable" page.
HARD_EXIT_WRAPPERS = [
    "run_position_monitor_native.sh",
    "run_data_quality_native.sh",
    "run_ingestion_native.sh",
    "run_execution_native.sh",
    "run_market_open_dataflow_check.sh",
]

# Wrappers that resolve/preserve the key aliases but do not require one.
SOFT_WRAPPERS: list[str] = []

# All wrappers in scope for the "no revoked literal" guarantee.
ALL_WRAPPERS = HARD_EXIT_WRAPPERS + SOFT_WRAPPERS + ["run_launchd_health_probe.sh"]


def _harness(wrapper_name: str, tmp_path: Path) -> Path:
    """Copy a wrapper into ``tmp_path``, repoint PROJECT_ROOT at it, neutralise
    ``uv sync``, and replace the final ``exec ... python/uv ...`` with a sentinel
    that records the resolved key. Returns the path to the rewritten script."""
    src = (SCRIPTS_DIR / wrapper_name).read_text()

    # Repoint the hardcoded project root at the hermetic tmpdir so the wrapper
    # sources our stub .env and cd's somewhere harmless.
    rewritten = src.replace(
        'PROJECT_ROOT="/Users/jacobmcmillan/Empire/Orion"',
        f'PROJECT_ROOT="{tmp_path}"',
    )
    assert f'PROJECT_ROOT="{tmp_path}"' in rewritten, "PROJECT_ROOT repoint failed"

    # Neutralise the dependency sync so the wrapper never shells out to uv.
    rewritten = re.sub(r"^if ! uv sync; then$", "if ! true; then", rewritten, flags=re.M)

    # Replace the terminal exec line (service python OR `uv run python`) with a
    # sentinel that prints a marker and records the resolved key, then exits 0.
    # This guarantees no python service is launched even when resolution passes.
    sentinel = (
        f'echo "{SENTINEL}"\n'
        f'printf \'%s\\n\' "${{DATA_GATEWAY_API_KEY:-<unset>}}" '
        f'"${{GATEWAY_API_KEY:-<unset>}}" > "{tmp_path}/gwkey.out"\n'
        "exit 0"
    )
    rewritten, n = re.subn(r"^exec .*$", sentinel, rewritten, flags=re.M)
    assert n == 1, f"expected exactly one exec line in {wrapper_name}, found {n}"

    dest = tmp_path / wrapper_name
    dest.write_text(rewritten)
    return dest


def _run(script: Path, tmp_path: Path, env_pairs: dict[str, str]) -> subprocess.CompletedProcess:
    """Run a rewritten wrapper under a minimal, hermetic env."""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        **env_pairs,
    }
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _read_resolved(tmp_path: Path) -> tuple[str, str]:
    """Return (DATA_GATEWAY_API_KEY, GATEWAY_API_KEY) as resolved by the wrapper."""
    lines = (tmp_path / "gwkey.out").read_text().splitlines()
    assert len(lines) == 2, f"unexpected gwkey.out: {lines!r}"
    return lines[0], lines[1]


# --- no revoked literal anywhere -------------------------------------------


@pytest.mark.parametrize("wrapper_name", ALL_WRAPPERS)
def test_no_revoked_literal(wrapper_name: str) -> None:
    text = (SCRIPTS_DIR / wrapper_name).read_text()
    assert REVOKED_LITERAL not in text, f"{wrapper_name} still contains the revoked key literal"


# --- alias-preserving resolution: only GATEWAY_API_KEY set ------------------


@pytest.mark.parametrize("wrapper_name", HARD_EXIT_WRAPPERS + SOFT_WRAPPERS)
def test_only_gateway_api_key_resolves_both(wrapper_name: str, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("GATEWAY_API_KEY=op_only_legacy_alias\n")
    script = _harness(wrapper_name, tmp_path)

    result = _run(script, tmp_path, {})

    assert result.returncode == 0, result.stderr
    assert SENTINEL in result.stdout
    data_key, gw_key = _read_resolved(tmp_path)
    assert data_key == "op_only_legacy_alias"
    assert gw_key == "op_only_legacy_alias"
    assert REVOKED_LITERAL not in (data_key + gw_key)


# --- alias-preserving resolution: only DATA_GATEWAY_API_KEY set -------------


@pytest.mark.parametrize("wrapper_name", HARD_EXIT_WRAPPERS + SOFT_WRAPPERS)
def test_only_data_gateway_api_key_resolves_both(wrapper_name: str, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DATA_GATEWAY_API_KEY=op_only_canonical\n")
    script = _harness(wrapper_name, tmp_path)

    result = _run(script, tmp_path, {})

    assert result.returncode == 0, result.stderr
    assert SENTINEL in result.stdout
    data_key, gw_key = _read_resolved(tmp_path)
    assert data_key == "op_only_canonical"
    assert gw_key == "op_only_canonical"


# --- neither set: service wrappers fail fast BEFORE exec'ing python ---------


@pytest.mark.parametrize("wrapper_name", HARD_EXIT_WRAPPERS)
def test_missing_key_fails_fast_before_exec(wrapper_name: str, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("# no gateway key here\n")
    script = _harness(wrapper_name, tmp_path)

    result = _run(script, tmp_path, {})

    assert result.returncode == 78, f"expected exit 78, got {result.returncode}: {result.stdout}"
    assert SENTINEL not in result.stdout, "wrapper reached exec despite missing key"
    assert "FATAL: no gateway key" in result.stderr
    # The sentinel never ran, so no resolved-key file should have been written.
    assert not (tmp_path / "gwkey.out").exists()
