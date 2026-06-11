"""launchd health probe — detect silent failures of Orion's launchd jobs.

Born from the 2026-05-22 incident where com.empire.orion.orphan-close
exited 127 on every fire (wrong uv path in its ProgramArguments) for 4.5
hours with no alert. The probe parses `launchctl list`, classifies the
status of each `com.empire.orion.*` entry, appends a JSON row to
logs/launchd_health.log for anything not healthy, and dispatches a
notification.

Wired in via scripts/launchd/com.empire.orion.launchd-health.plist with
StartInterval=60 — i.e. once a minute. The notifier defaults to POSTing
to SLACK_WEBHOOK_URL when that env var is set; absent the env var the
probe still writes to the log so the failure is auditable after the fact.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Callable

DEFAULT_PREFIX = "com.empire.orion."
DEFAULT_LOG_PATH = Path("logs/launchd_health.log")

# The probe's own launchd label. It is excluded from evaluation: `main()`
# returns 1/2 whenever it reports another job's failure, so launchd records
# THIS label's own "last exit status" as non-zero. Without the exclusion the
# probe would then classify itself as failing on the next fire and keep
# alerting on its own exit code even after the real failing job is fixed —
# a self-sustaining feedback loop.
SELF_LABEL = DEFAULT_PREFIX + "launchd-health"

# Always-on Orion launchd jobs that must be present as a row in
# `launchctl list` at all times. All are RunAtLoad+KeepAlive daemons, so a
# missing row means the daemon was booted out or never loaded — a silent
# failure `classify_entry` cannot see (there is no row to classify).
#
# meta-search and meta-weekly qualify: their `--scheduled` modes are
# internal poll loops that never exit (they self-fire at 18:00 ET weekdays /
# Friday 17:30 ET respectively and otherwise sleep), so the plists run them
# as RunAtLoad+KeepAlive daemons exactly like execution/ingestion. A missing
# row means the always-on scheduler loop is not running and the scheduled
# fire will silently never happen — precisely the steady-state-required case
# this set guards. (Contrast the StartCalendarInterval one-shots below.)
#
# Deliberately NOT required: the probe itself (it cannot report its own
# absence — see SELF_LABEL) and `com.empire.orion.orphan-close`. orphan-close
# is a one-shot emergency tool whose plist explicitly instructs operators to
# `launchctl bootout` / `rm` it after use, so its absence is the normal
# steady state, not a fault — requiring it would fire a permanent false
# CRITICAL every minute once it is correctly removed. Pass `required_labels=`
# to run_probe / detect_missing_jobs to override for a different deployment.
REQUIRED_LABELS = frozenset(
    {
        DEFAULT_PREFIX + "execution",
        DEFAULT_PREFIX + "ingestion",
        DEFAULT_PREFIX + "meta-search",
        DEFAULT_PREFIX + "meta-weekly",
    }
)

# Exit code 127 from a POSIX shell means "command not found" — i.e. the
# ProgramArguments path is wrong. That was the exact failure mode of the
# 2026-05-22 orphan-close incident, and it can never self-heal: every
# scheduled fire will exit 127 until a human edits the plist. Treating
# it as CRITICAL (vs WARNING) is the whole point of this probe.
EXIT_CODE_COMMAND_NOT_FOUND = 127


class Severity(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class LaunchctlEntry:
    pid: int | None
    exit_code: int
    label: str


@dataclass(frozen=True)
class HealthAlert:
    label: str
    exit_code: int | None  # None = job has no launchctl row at all (not loaded)
    pid: int | None
    severity: Severity
    message: str
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_log_row(self) -> str:
        return json.dumps(
            {
                "ts": self.ts,
                "label": self.label,
                "severity": self.severity.value,
                "exit_code": self.exit_code,
                "pid": self.pid,
                "message": self.message,
            },
            sort_keys=True,
        )


def parse_launchctl_list(output: str, prefix: str = DEFAULT_PREFIX) -> list[LaunchctlEntry]:
    """Parse `launchctl list` output, returning entries whose label starts
    with ``prefix``.

    Format is tab-separated `PID\\tStatus\\tLabel` with a single header
    row. PID is either an integer or `-` (not running). Status is the
    last exit code, which may be negative (signal-terminated).
    """
    entries: list[LaunchctlEntry] = []
    for line in output.splitlines():
        line = line.rstrip("\n")
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pid_raw, status_raw, label = parts
        if label == "Label":  # header
            continue
        if not label.startswith(prefix):
            continue
        pid = None if pid_raw == "-" else int(pid_raw)
        exit_code = int(status_raw)
        entries.append(LaunchctlEntry(pid=pid, exit_code=exit_code, label=label))
    return entries


def classify_entry(entry: LaunchctlEntry) -> HealthAlert:
    """Map a single launchctl entry to a HealthAlert (severity OK if
    nothing is wrong).

    Rule: alert iff exit_code != 0. PID `-` with exit 0 is the idle
    one-shot pattern (e.g. a StartCalendarInterval job between fires)
    and is explicitly NOT an alert — that distinction was the whole
    reason the rule was specified this way.
    """
    if entry.exit_code == 0:
        return HealthAlert(
            label=entry.label,
            exit_code=0,
            pid=entry.pid,
            severity=Severity.OK,
            message="healthy",
        )

    if entry.exit_code == EXIT_CODE_COMMAND_NOT_FOUND:
        severity = Severity.CRITICAL
        message = (
            f"{entry.label} exited 127 (command not found) — ProgramArguments "
            "path is wrong; job cannot self-heal until the plist is fixed"
        )
    else:
        severity = Severity.WARNING
        message = f"{entry.label} last exit code {entry.exit_code}"

    return HealthAlert(
        label=entry.label,
        exit_code=entry.exit_code,
        pid=entry.pid,
        severity=severity,
        message=message,
    )


def evaluate_orion_jobs(entries: list[LaunchctlEntry]) -> list[HealthAlert]:
    """Return only the alert-worthy classifications, in input order."""
    return [a for a in (classify_entry(e) for e in entries) if a.severity is not Severity.OK]


def detect_missing_jobs(
    entries: list[LaunchctlEntry],
    required_labels: frozenset[str] = REQUIRED_LABELS,
) -> list[HealthAlert]:
    """Emit a CRITICAL alert for each required label with no `launchctl list`
    row — the job was booted out or never loaded.

    This is the blind spot `classify_entry` cannot cover: it can only judge
    rows that exist, so a job that vanishes entirely produces no entry and
    would otherwise be silently ignored. Returned in sorted-label order for
    deterministic output.
    """
    present = {e.label for e in entries}
    return [
        HealthAlert(
            label=label,
            exit_code=None,
            pid=None,
            severity=Severity.CRITICAL,
            message=(
                f"{label} has no launchctl entry — the job is not loaded "
                "(booted out or never registered) and is silently not running"
            ),
        )
        for label in sorted(required_labels - present)
    ]


# Production runner — shells out to launchctl. Tests inject a fake.
def _real_launchctl_list() -> str:
    return subprocess.check_output(["launchctl", "list"], text=True)


def _slack_notifier(alert: HealthAlert) -> None:
    """Best-effort Slack webhook POST. No-op if SLACK_WEBHOOK_URL unset.

    Kept dependency-free (stdlib urllib) so the probe runs even if Orion
    isn't fully importable at the moment — the launchd safety net should
    not itself depend on the system it's watching.
    """
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return
    import urllib.request

    emoji = "🚨" if alert.severity is Severity.CRITICAL else "⚠️"
    payload = {
        "text": f"{emoji} *[{alert.severity.value}]* {alert.message}",
        "attachments": [
            {
                "color": "#ff0000" if alert.severity is Severity.CRITICAL else "#ffaa00",
                "fields": [
                    {"title": "Label", "value": alert.label, "short": True},
                    {
                        "title": "Exit code",
                        "value": "-" if alert.exit_code is None else str(alert.exit_code),
                        "short": True,
                    },
                    {"title": "PID", "value": "-" if alert.pid is None else str(alert.pid), "short": True},
                    {"title": "Timestamp", "value": alert.ts, "short": True},
                ],
            }
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=5).read()


def run_probe(
    launchctl_runner: Callable[[], str] = _real_launchctl_list,
    notifier: Callable[[HealthAlert], None] = _slack_notifier,
    log_path: Path = DEFAULT_LOG_PATH,
    required_labels: frozenset[str] = REQUIRED_LABELS,
    self_label: str = SELF_LABEL,
) -> list[HealthAlert]:
    """Run one pass: parse launchctl, classify, persist alerts, notify.

    Returns the list of alerts (empty when all jobs are healthy). The
    notifier is best-effort — its exceptions are caught and printed to
    stderr so a transient Slack outage cannot prevent the durable log
    row from landing.

    Two failure modes beyond a non-zero exit code are covered: the probe
    excludes its own job (``self_label``) so it never alerts on the
    exit code ``main()`` sets for it, and it reports any ``required_labels``
    that have no launchctl row at all (booted out / never loaded).
    """
    output = launchctl_runner()
    entries = [e for e in parse_launchctl_list(output) if e.label != self_label]
    alerts = evaluate_orion_jobs(entries) + detect_missing_jobs(entries, required_labels)

    if not alerts:
        return alerts

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        for alert in alerts:
            fh.write(alert.to_log_row() + "\n")

    for alert in alerts:
        try:
            notifier(alert)
        except Exception as exc:  # noqa: BLE001 — best-effort notification
            print(
                f"launchd_health_probe: notifier failed for {alert.label}: {exc}",
                file=sys.stderr,
            )

    return alerts


def main() -> int:
    """Entry point for `python -m orion.jobs.launchd_health_probe`."""
    alerts = run_probe()
    if any(a.severity is Severity.CRITICAL for a in alerts):
        return 2
    if alerts:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
