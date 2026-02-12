import os
import stat
import subprocess
from pathlib import Path


def _write_fake_docker(fake_bin: Path) -> None:
    docker_path = fake_bin / "docker"
    docker_path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "compose" ]]; then
  echo "unsupported command" >&2
  exit 1
fi

shift
subcommand="${1:-}"
shift || true

case "$subcommand" in
  up)
    exit 0
    ;;
  logs)
    echo "INFO healthy service"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    docker_path.chmod(docker_path.stat().st_mode | stat.S_IEXEC)


def test_run_system_burnin_script_passes_when_no_patterns_match(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    _write_fake_docker(fake_bin)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["ORION_BURNIN_OUTPUT_DIR"] = str(tmp_path / "burnin-output")

    result = subprocess.run(
        ["bash", "scripts/run_system_burnin.sh", "0"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: No hard errors and no redundant UW polling detected." in result.stdout
