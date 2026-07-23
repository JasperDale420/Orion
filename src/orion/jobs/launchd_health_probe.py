"""launchd health probe — detect silent failures of Orion's launchd jobs.

Born from the 2026-05-22 incident where com.empire.orion.orphan-close
exited 127 on every fire (wrong uv path in its ProgramArguments) for 4.5
hours with no alert. The probe parses `launchctl list`, classifies the
status of each `com.empire.orion.*` entry, appends a JSON row to
logs/launchd_health.log for anything not healthy, and dispatches a
notification.

Wired in via scripts/launchd/com.empire.orion.launchd-health.plist with
StartInterval=60 — i.e. once a minute. The notifier defaults to POSTing to
the Discord webhook (DISCORD_WEBHOOK_URL, sourced from .env by the wrapper)
when that env var is set; absent the env var the probe still writes to the
log so the failure is auditable after the fact. Because the probe fires every
minute on the SAME frozen last-exit code, the Discord notifier dedups per
(label, exit_code): a stuck job pages at most once per hour, not every minute.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Callable

DEFAULT_PREFIX = "com.empire.orion."
DEFAULT_LOG_PATH = Path("logs/launchd_health.log")

# Persistent dedup for the Discord notifier. The probe is a 60s one-shot that
# re-reads the SAME frozen last-exit code every fire, so without cross-fire
# dedup a single stuck service would page the channel every minute. State lives
# next to the log; the durable launchd_health.log row is still written for every
# alert, so suppressing a repeat page never loses the audit trail.
DEFAULT_DEDUP_STATE_PATH = Path("logs/launchd_health_alert_state.json")
# A persistent failure re-pages at most once per window; the first occurrence of
# any (label, exit_code) pages immediately (no prior state).
_DEDUP_WINDOW_SECONDS = 60 * 60

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
# Deliberately NOT required: the probe itself (it cannot report its own
# absence — see SELF_LABEL) and `com.empire.orion.orphan-close`. orphan-close
# is a one-shot emergency tool whose plist explicitly instructs operators to
# `launchctl bootout` / `rm` it after use, so its absence is the normal
# steady state, not a fault — requiring it would fire a permanent false
# CRITICAL every minute once it is correctly removed. meta-search/meta-weekly
# were removed 2026-07 with the LLM solver-evolution machinery — requiring
# their labels after the plists are gone would page a permanent CRITICAL.
# Pass `required_labels=` to run_probe / detect_missing_jobs to override.
REQUIRED_LABELS = frozenset(
    {
        DEFAULT_PREFIX + "execution",
        DEFAULT_PREFIX + "ingestion",
        DEFAULT_PREFIX + "position-monitor",
        DEFAULT_PREFIX + "data-quality",
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


def _dedup_key(alert: HealthAlert) -> str:
    """Suppression key. Keyed on (label, exit_code) so a NEW failure mode (a
    different exit code) on the same job pages immediately rather than being
    masked by an in-window page for the old code."""
    return f"{alert.label}:{alert.exit_code}"


def _load_alert_state(path: Path) -> dict[str, float]:
    """Load the {dedup_key: last_sent_epoch} map; empty on missing/corrupt."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _save_alert_state(path: Path, state: dict[str, float], now: float) -> None:
    """Persist the dedup map, dropping entries older than 2× the window so a
    long-recovered job's key can't linger and suppress a fresh re-failure."""
    pruned = {k: v for k, v in state.items() if now - v < 2 * _DEDUP_WINDOW_SECONDS}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pruned, sort_keys=True), encoding="utf-8")


def _post_discord(url: str, alert: HealthAlert) -> None:
    """POST the alert to a Discord webhook ({"content": ...} payload shape).

    Dependency-free (stdlib urllib) so the launchd safety net never depends on
    the system it watches. Raises on a failed POST so the caller does NOT record
    it as sent (the page is retried next fire instead of being lost).

    A non-default User-Agent is required: Discord's Cloudflare edge answers 403
    Forbidden to urllib's default ``Python-urllib/x.y`` UA, so without this every
    page silently fails even though the webhook URL is valid (the same URL posts
    fine from repos that use httpx/requests, which send their own UA)."""
    import urllib.request

    emoji = "🚨" if alert.severity is Severity.CRITICAL else "⚠️"
    exit_str = "-" if alert.exit_code is None else str(alert.exit_code)
    pid_str = "-" if alert.pid is None else str(alert.pid)
    content = f"{emoji} **[{alert.severity.value}]** `{alert.label}` — {alert.message} (exit={exit_str}, pid={pid_str})"
    req = urllib.request.Request(
        url,
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Orion-launchd-health-probe/1.0",
        },
    )
    urllib.request.urlopen(req, timeout=5).read()


def _discord_notifier(
    alert: HealthAlert,
    *,
    webhook_url: str | None = None,
    state_path: Path = DEFAULT_DEDUP_STATE_PATH,
    window_seconds: float = _DEDUP_WINDOW_SECONDS,
    now: float | None = None,
) -> None:
    """Best-effort Discord webhook page with persistent cross-fire dedup.

    No-op if no webhook is configured (DISCORD_WEBHOOK_URL unset). A given
    (label, exit_code) pages at most once per ``window_seconds``; the first
    occurrence pages immediately. Only a successful POST is recorded, so a
    transient Discord outage retries on the next fire rather than silently
    suppressing a never-delivered page.
    """
    url = webhook_url if webhook_url is not None else os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return

    now = time.time() if now is None else now
    key = _dedup_key(alert)
    state = _load_alert_state(state_path)
    last = state.get(key)
    if last is not None and (now - last) < window_seconds:
        return  # already paged within the window — the log row is still written

    _post_discord(url, alert)  # raises on failure -> not recorded -> retried
    state[key] = now
    _save_alert_state(state_path, state, now)


def run_probe(
    launchctl_runner: Callable[[], str] = _real_launchctl_list,
    notifier: Callable[[HealthAlert], None] = _discord_notifier,
    log_path: Path = DEFAULT_LOG_PATH,
    required_labels: frozenset[str] = REQUIRED_LABELS,
    self_label: str = SELF_LABEL,
) -> list[HealthAlert]:
    """Run one pass: parse launchctl, classify, persist alerts, notify.

    Returns the list of alerts (empty when all jobs are healthy). The
    notifier is best-effort — its exceptions are caught and printed to
    stderr so a transient Discord outage cannot prevent the durable log
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


def test_fire(webhook_url: str | None = None) -> int:
    """Send a synthetic alert to the configured webhook to verify the notifier
    end-to-end WITHOUT waiting for a real job failure.

    Calls ``_post_discord`` directly (bypassing the cross-fire dedup) so it
    always delivers, and returns non-zero if the webhook is unset or the POST
    fails. This is the on-demand check that would have caught the revoked
    webhook whose 403 otherwise only shows up the next time a job actually
    dies — the exact moment a page must not be silently dropped.
    """
    url = webhook_url if webhook_url is not None else os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("test-fire: DISCORD_WEBHOOK_URL is not set — nothing to test", file=sys.stderr)
        return 1

    alert = HealthAlert(
        label=SELF_LABEL,
        exit_code=0,
        pid=None,
        severity=Severity.WARNING,
        message="test-fire — notifier connectivity check, NOT a real job failure",
    )
    try:
        _post_discord(url, alert)
    except Exception as exc:  # noqa: BLE001 — surface the exact failure to the operator
        print(f"test-fire: POST failed: {exc}", file=sys.stderr)
        return 1

    print("test-fire: delivered — check the Discord channel for the test alert")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m orion.jobs.launchd_health_probe`.

    Pass ``--test-fire`` to POST a synthetic alert to DISCORD_WEBHOOK_URL and
    exit, instead of running the launchctl scan.
    """
    args = sys.argv[1:] if argv is None else argv
    if "--test-fire" in args:
        return test_fire()

    alerts = run_probe()
    if any(a.severity is Severity.CRITICAL for a in alerts):
        return 2
    if alerts:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
