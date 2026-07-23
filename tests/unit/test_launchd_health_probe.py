"""Tests for the launchd health probe.

Background: on 2026-05-22 the orphan-close launchd job exited 127 on every
fire because the wrapper used /opt/homebrew/bin/uv instead of the real path
at /Users/jacobmcmillan/.local/bin/uv. The failure was invisible — no alert
fired for 4.5 hours, costing ~$67K of additional unrealized loss. This probe
makes that class of silent failure loud.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orion.jobs.launchd_health_probe import (
    REQUIRED_LABELS,
    SELF_LABEL,
    HealthAlert,
    LaunchctlEntry,
    Severity,
    classify_entry,
    detect_missing_jobs,
    evaluate_orion_jobs,
    parse_launchctl_list,
    run_probe,
)


# `launchctl list` output with all always-on daemons (execution, ingestion,
# meta-search, meta-weekly, position-monitor, data-quality) present and healthy,
# plus caller-supplied rows for any extras. Keeps the missing-job detector quiet
# so run_probe tests can isolate the fault they actually exercise.
def _required_with(*job_lines: str) -> str:
    lines = [
        "PID\tStatus\tLabel",
        "50960\t0\tcom.empire.orion.execution",
        "12345\t0\tcom.empire.orion.ingestion",
        "23456\t0\tcom.empire.orion.meta-search",
        "34567\t0\tcom.empire.orion.meta-weekly",
        "45678\t0\tcom.empire.orion.position-monitor",
        "56789\t0\tcom.empire.orion.data-quality",
        *job_lines,
    ]
    return "\n".join(lines) + "\n"


# Real launchctl output captured 2026-05-22 — header + a handful of other
# system jobs + the three Orion jobs. The probe must skip the header, skip
# unrelated jobs, and parse PID `-` as None.
SAMPLE_LAUNCHCTL_OUTPUT = (
    "PID\tStatus\tLabel\n"
    "-\t0\tcom.apple.SafariHistoryServiceAgent\n"
    "-\t-9\tcom.apple.progressd\n"
    "50960\t0\tcom.empire.orion.execution\n"
    "12345\t0\tcom.empire.orion.ingestion\n"
    "-\t0\tcom.empire.orion.orphan-close\n"
)


class TestParseLaunchctlList:
    def test_skips_header_row(self) -> None:
        entries = parse_launchctl_list(SAMPLE_LAUNCHCTL_OUTPUT)
        assert all(e.label != "Label" for e in entries)

    def test_filters_to_orion_prefix_by_default(self) -> None:
        entries = parse_launchctl_list(SAMPLE_LAUNCHCTL_OUTPUT)
        labels = {e.label for e in entries}
        assert labels == {
            "com.empire.orion.execution",
            "com.empire.orion.ingestion",
            "com.empire.orion.orphan-close",
        }

    def test_parses_pid_dash_as_none(self) -> None:
        entries = parse_launchctl_list(SAMPLE_LAUNCHCTL_OUTPUT)
        by_label = {e.label: e for e in entries}
        assert by_label["com.empire.orion.orphan-close"].pid is None
        assert by_label["com.empire.orion.execution"].pid == 50960

    def test_parses_exit_code_as_int(self) -> None:
        entries = parse_launchctl_list(SAMPLE_LAUNCHCTL_OUTPUT)
        by_label = {e.label: e for e in entries}
        assert by_label["com.empire.orion.execution"].exit_code == 0

    def test_parses_negative_exit_codes(self) -> None:
        # launchd reports signal-terminated jobs as negative exit codes
        output = "PID\tStatus\tLabel\n-\t-15\tcom.empire.orion.execution\n"
        entries = parse_launchctl_list(output)
        assert entries[0].exit_code == -15

    def test_handles_empty_output(self) -> None:
        assert parse_launchctl_list("") == []

    def test_handles_header_only_output(self) -> None:
        assert parse_launchctl_list("PID\tStatus\tLabel\n") == []

    def test_custom_prefix(self) -> None:
        output = "PID\tStatus\tLabel\n100\t0\tcom.empire.orion.execution\n200\t0\tcom.example.other\n"
        entries = parse_launchctl_list(output, prefix="com.example.")
        assert len(entries) == 1
        assert entries[0].label == "com.example.other"


class TestClassifyEntry:
    def test_healthy_running_job_is_ok(self) -> None:
        entry = LaunchctlEntry(pid=50960, exit_code=0, label="com.empire.orion.execution")
        assert classify_entry(entry).severity is Severity.OK

    def test_idle_one_shot_is_ok(self) -> None:
        # pid=- (None) with exit=0 is the legitimate idle-one-shot pattern
        # (e.g. com.empire.orion.orphan-close between scheduled fires)
        entry = LaunchctlEntry(pid=None, exit_code=0, label="com.empire.orion.orphan-close")
        assert classify_entry(entry).severity is Severity.OK

    def test_exit_127_is_critical(self) -> None:
        # The exact failure mode from 2026-05-22 — program path was wrong,
        # so the shell returned 127 (command not found). Critical because
        # the job has NEVER run successfully and never will until the path
        # is fixed.
        entry = LaunchctlEntry(pid=None, exit_code=127, label="com.empire.orion.orphan-close")
        alert = classify_entry(entry)
        assert alert.severity is Severity.CRITICAL
        assert alert.label == "com.empire.orion.orphan-close"
        assert alert.exit_code == 127

    def test_other_nonzero_exit_is_warning(self) -> None:
        entry = LaunchctlEntry(pid=None, exit_code=1, label="com.empire.orion.execution")
        assert classify_entry(entry).severity is Severity.WARNING

    def test_running_with_nonzero_last_exit_is_warning(self) -> None:
        # KeepAlive jobs may show a non-zero last-exit even while currently
        # running (PID present) — we still want visibility because it means
        # the job is flapping.
        entry = LaunchctlEntry(pid=50960, exit_code=129, label="com.empire.orion.execution")
        assert classify_entry(entry).severity is Severity.WARNING


class TestEvaluateOrionJobs:
    def test_all_healthy_jobs_produce_no_alerts(self) -> None:
        # Case (a) from the task spec: all healthy → no alert.
        entries = [
            LaunchctlEntry(pid=50960, exit_code=0, label="com.empire.orion.execution"),
            LaunchctlEntry(pid=12345, exit_code=0, label="com.empire.orion.ingestion"),
            LaunchctlEntry(pid=None, exit_code=0, label="com.empire.orion.orphan-close"),
        ]
        assert evaluate_orion_jobs(entries) == []

    def test_one_exit_127_produces_critical_alert_with_label_and_code(self) -> None:
        # Case (b) from the task spec — the exact 2026-05-22 incident.
        entries = [
            LaunchctlEntry(pid=50960, exit_code=0, label="com.empire.orion.execution"),
            LaunchctlEntry(pid=None, exit_code=127, label="com.empire.orion.orphan-close"),
        ]
        alerts = evaluate_orion_jobs(entries)
        assert len(alerts) == 1
        assert alerts[0].severity is Severity.CRITICAL
        assert alerts[0].label == "com.empire.orion.orphan-close"
        assert alerts[0].exit_code == 127

    def test_idle_one_shot_with_pid_dash_and_exit_zero_produces_no_alert(self) -> None:
        # Case (c) from the task spec — distinguishing the idle one-shot
        # pattern from real failures is the whole reason this rule exists.
        entries = [
            LaunchctlEntry(pid=None, exit_code=0, label="com.empire.orion.orphan-close"),
        ]
        assert evaluate_orion_jobs(entries) == []

    def test_multiple_alerts_returned_in_input_order(self) -> None:
        entries = [
            LaunchctlEntry(pid=None, exit_code=127, label="com.empire.orion.orphan-close"),
            LaunchctlEntry(pid=50960, exit_code=1, label="com.empire.orion.execution"),
        ]
        alerts = evaluate_orion_jobs(entries)
        assert [a.label for a in alerts] == [
            "com.empire.orion.orphan-close",
            "com.empire.orion.execution",
        ]


class TestRunProbe:
    def test_no_log_write_and_no_notifier_when_all_healthy(self, tmp_path: Path) -> None:
        notifications: list[HealthAlert] = []
        log_path = tmp_path / "launchd_health.log"

        alerts = run_probe(
            launchctl_runner=lambda: _required_with("-\t0\tcom.empire.orion.orphan-close"),
            notifier=notifications.append,
            log_path=log_path,
        )

        assert alerts == []
        assert notifications == []
        # No alerts means no rows appended — the log file should not exist
        # (or should be empty if pre-created).
        assert not log_path.exists() or log_path.read_text() == ""

    def test_writes_alert_row_and_notifies_on_exit_127(self, tmp_path: Path) -> None:
        notifications: list[HealthAlert] = []
        log_path = tmp_path / "launchd_health.log"

        alerts = run_probe(
            launchctl_runner=lambda: _required_with("-\t127\tcom.empire.orion.orphan-close"),
            notifier=notifications.append,
            log_path=log_path,
        )

        assert len(alerts) == 1
        assert alerts[0].severity is Severity.CRITICAL
        assert len(notifications) == 1
        assert notifications[0].label == "com.empire.orion.orphan-close"
        assert notifications[0].exit_code == 127

        # Log file should contain one JSON line for the alert.
        contents = log_path.read_text().strip()
        assert contents, "expected at least one log row"
        rows = [json.loads(line) for line in contents.splitlines()]
        assert len(rows) == 1
        assert rows[0]["label"] == "com.empire.orion.orphan-close"
        assert rows[0]["exit_code"] == 127
        assert rows[0]["severity"] == "CRITICAL"
        assert "ts" in rows[0]

    def test_appends_rather_than_overwrites_log(self, tmp_path: Path) -> None:
        log_path = tmp_path / "launchd_health.log"
        log_path.write_text('{"ts": "2026-05-22T13:35:00Z", "label": "earlier"}\n')

        run_probe(
            launchctl_runner=lambda: _required_with("-\t127\tcom.empire.orion.orphan-close"),
            notifier=lambda _alert: None,
            log_path=log_path,
        )

        lines = log_path.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["label"] == "earlier"
        assert json.loads(lines[1])["label"] == "com.empire.orion.orphan-close"

    def test_notifier_exception_does_not_swallow_log_write(self, tmp_path: Path) -> None:
        # If Slack/webhook is down, we still want the local log row to land.
        log_path = tmp_path / "launchd_health.log"

        def boom(_alert: HealthAlert) -> None:
            raise RuntimeError("slack unreachable")

        run_probe(
            launchctl_runner=lambda: _required_with("-\t127\tcom.empire.orion.orphan-close"),
            notifier=boom,
            log_path=log_path,
        )

        assert log_path.exists()
        rows = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert rows[0]["label"] == "com.empire.orion.orphan-close"


class TestDetectMissingJobs:
    def test_all_required_present_produces_no_alerts(self) -> None:
        entries = [
            LaunchctlEntry(pid=1, exit_code=0, label="com.empire.orion.execution"),
            LaunchctlEntry(pid=2, exit_code=0, label="com.empire.orion.ingestion"),
            LaunchctlEntry(pid=3, exit_code=0, label="com.empire.orion.meta-search"),
            LaunchctlEntry(pid=4, exit_code=0, label="com.empire.orion.meta-weekly"),
            LaunchctlEntry(pid=5, exit_code=0, label="com.empire.orion.position-monitor"),
            LaunchctlEntry(pid=6, exit_code=0, label="com.empire.orion.data-quality"),
            LaunchctlEntry(pid=None, exit_code=0, label="com.empire.orion.orphan-close"),
        ]
        assert detect_missing_jobs(entries) == []

    def test_missing_required_job_produces_critical_alert(self) -> None:
        # ingestion was booted out — no row at all. classify_entry can never
        # see this; only the expected-set comparison catches it.
        entries = [
            LaunchctlEntry(pid=1, exit_code=0, label="com.empire.orion.execution"),
            LaunchctlEntry(pid=3, exit_code=0, label="com.empire.orion.meta-search"),
            LaunchctlEntry(pid=4, exit_code=0, label="com.empire.orion.meta-weekly"),
            LaunchctlEntry(pid=5, exit_code=0, label="com.empire.orion.position-monitor"),
            LaunchctlEntry(pid=6, exit_code=0, label="com.empire.orion.data-quality"),
            LaunchctlEntry(pid=None, exit_code=0, label="com.empire.orion.orphan-close"),
        ]
        alerts = detect_missing_jobs(entries)
        assert len(alerts) == 1
        assert alerts[0].label == "com.empire.orion.ingestion"
        assert alerts[0].severity is Severity.CRITICAL
        assert alerts[0].exit_code is None
        assert alerts[0].pid is None
        assert "not loaded" in alerts[0].message

    def test_multiple_missing_jobs_returned_in_sorted_order(self) -> None:
        # No required daemon is present (only the unrequired one-shot is)
        # → all missing, reported alphabetically for determinism.
        entries = [LaunchctlEntry(pid=None, exit_code=0, label="com.empire.orion.orphan-close")]
        alerts = detect_missing_jobs(entries)
        assert [a.label for a in alerts] == [
            "com.empire.orion.data-quality",
            "com.empire.orion.execution",
            "com.empire.orion.ingestion",
            "com.empire.orion.position-monitor",
        ]

    def test_one_shot_orphan_close_absence_is_not_required(self) -> None:
        # orphan-close is a removable one-shot — its absence must NOT alert,
        # even though all always-on daemons are present.
        entries = [
            LaunchctlEntry(pid=1, exit_code=0, label="com.empire.orion.execution"),
            LaunchctlEntry(pid=2, exit_code=0, label="com.empire.orion.ingestion"),
            LaunchctlEntry(pid=3, exit_code=0, label="com.empire.orion.meta-search"),
            LaunchctlEntry(pid=4, exit_code=0, label="com.empire.orion.meta-weekly"),
            LaunchctlEntry(pid=5, exit_code=0, label="com.empire.orion.position-monitor"),
            LaunchctlEntry(pid=6, exit_code=0, label="com.empire.orion.data-quality"),
        ]
        assert detect_missing_jobs(entries) == []

    def test_empty_launchctl_reports_every_required_job(self) -> None:
        # Nothing loaded at all — the worst case the probe must shout about.
        alerts = detect_missing_jobs([])
        assert {a.label for a in alerts} == set(REQUIRED_LABELS)
        assert all(a.severity is Severity.CRITICAL for a in alerts)

    def test_custom_required_set(self) -> None:
        alerts = detect_missing_jobs([], required_labels=frozenset({"com.x.only"}))
        assert [a.label for a in alerts] == ["com.x.only"]


class TestProbeSelfExclusion:
    def test_probe_does_not_alert_on_its_own_nonzero_exit(self) -> None:
        # main() returns 1/2 when it reports another job, so launchd records
        # the probe's OWN last status as non-zero. The probe must not then
        # alert on itself — otherwise it loops forever once it fires once.
        notifications: list[HealthAlert] = []
        alerts = run_probe(
            launchctl_runner=lambda: _required_with(
                "-\t0\tcom.empire.orion.orphan-close",
                f"4242\t2\t{SELF_LABEL}",  # the probe itself, last exit 2
            ),
            notifier=notifications.append,
            log_path=Path("/dev/null"),
        )
        assert alerts == []
        assert notifications == []

    def test_real_failure_alerts_while_probe_self_row_is_ignored(self, tmp_path: Path) -> None:
        # A genuine failure (orphan-close 127) must still surface even when
        # the probe's own non-zero row is present in the same listing.
        log_path = tmp_path / "launchd_health.log"
        alerts = run_probe(
            launchctl_runner=lambda: _required_with(
                "-\t127\tcom.empire.orion.orphan-close",
                f"4242\t2\t{SELF_LABEL}",
            ),
            notifier=lambda _a: None,
            log_path=log_path,
        )
        assert [a.label for a in alerts] == ["com.empire.orion.orphan-close"]
        assert SELF_LABEL not in {a.label for a in alerts}

    def test_run_probe_alerts_and_logs_when_required_job_missing(self, tmp_path: Path) -> None:
        # ingestion daemon entirely absent (booted out): run_probe must log +
        # notify, not just silently exit 0.
        notifications: list[HealthAlert] = []
        log_path = tmp_path / "launchd_health.log"
        alerts = run_probe(
            launchctl_runner=lambda: (
                "PID\tStatus\tLabel\n"
                "50960\t0\tcom.empire.orion.execution\n"
                "23456\t0\tcom.empire.orion.meta-search\n"
                "34567\t0\tcom.empire.orion.meta-weekly\n"
                "45678\t0\tcom.empire.orion.position-monitor\n"
                "56789\t0\tcom.empire.orion.data-quality\n"
            ),
            notifier=notifications.append,
            log_path=log_path,
        )
        assert [a.label for a in alerts] == ["com.empire.orion.ingestion"]
        assert alerts[0].severity is Severity.CRITICAL
        assert len(notifications) == 1
        rows = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert rows[0]["label"] == "com.empire.orion.ingestion"
        assert rows[0]["exit_code"] is None


class TestDiscordNotifier:
    """The probe pages through the Discord webhook (DISCORD_WEBHOOK_URL) with
    persistent dedup, so the 60s one-shot can't storm the channel by re-reading
    the same frozen last-exit code every minute (the failure mode that, applied
    to the per-order give-up path, tripped Discord's 429 limit on 2026-06-22)."""

    def _alert(
        self,
        label: str = "com.empire.orion.execution",
        exit_code: int | None = -6,
        severity: Severity = Severity.WARNING,
        pid: int | None = 96928,
    ) -> HealthAlert:
        from orion.jobs.launchd_health_probe import HealthAlert as HA

        return HA(
            label=label,
            exit_code=exit_code,
            pid=pid,
            severity=severity,
            message=f"{label} last exit code {exit_code}",
        )

    def test_noop_without_webhook(self, monkeypatch, tmp_path: Path) -> None:
        import orion.jobs.launchd_health_probe as mod

        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        posts: list[tuple] = []
        monkeypatch.setattr(mod, "_post_discord", lambda url, alert: posts.append((url, alert)))

        mod._discord_notifier(self._alert(), state_path=tmp_path / "s.json")
        assert posts == []  # no webhook configured -> no POST attempted

    def test_posts_to_discord_webhook(self, monkeypatch, tmp_path: Path) -> None:
        import orion.jobs.launchd_health_probe as mod

        posts: list[tuple] = []
        monkeypatch.setattr(mod, "_post_discord", lambda url, alert: posts.append((url, alert)))

        mod._discord_notifier(self._alert(), webhook_url="https://discord.test/wh", state_path=tmp_path / "s.json")
        assert len(posts) == 1
        assert posts[0][0] == "https://discord.test/wh"
        assert posts[0][1].label == "com.empire.orion.execution"

    def test_dedups_repeat_within_window(self, monkeypatch, tmp_path: Path) -> None:
        import orion.jobs.launchd_health_probe as mod

        posts: list = []
        monkeypatch.setattr(mod, "_post_discord", lambda url, alert: posts.append(alert))
        sp = tmp_path / "s.json"
        alert = self._alert()

        mod._discord_notifier(alert, webhook_url="https://d.test/wh", state_path=sp, window_seconds=3600, now=1000.0)
        mod._discord_notifier(alert, webhook_url="https://d.test/wh", state_path=sp, window_seconds=3600, now=1060.0)
        assert len(posts) == 1  # second suppressed — same label+exit within the window

    def test_repages_after_window(self, monkeypatch, tmp_path: Path) -> None:
        import orion.jobs.launchd_health_probe as mod

        posts: list = []
        monkeypatch.setattr(mod, "_post_discord", lambda url, alert: posts.append(alert))
        sp = tmp_path / "s.json"
        alert = self._alert()

        mod._discord_notifier(alert, webhook_url="https://d.test/wh", state_path=sp, window_seconds=3600, now=1000.0)
        mod._discord_notifier(alert, webhook_url="https://d.test/wh", state_path=sp, window_seconds=3600, now=4602.0)
        assert len(posts) == 2  # window elapsed -> persistent failure re-paged

    def test_distinct_exit_codes_not_deduped(self, monkeypatch, tmp_path: Path) -> None:
        import orion.jobs.launchd_health_probe as mod

        posts: list = []
        monkeypatch.setattr(mod, "_post_discord", lambda url, alert: posts.append(alert))
        sp = tmp_path / "s.json"

        mod._discord_notifier(
            self._alert(exit_code=-6), webhook_url="https://d.test/wh", state_path=sp, window_seconds=3600, now=1000.0
        )
        mod._discord_notifier(
            self._alert(exit_code=-9), webhook_url="https://d.test/wh", state_path=sp, window_seconds=3600, now=1001.0
        )
        assert len(posts) == 2  # a NEW failure mode (different exit code) pages immediately

    def test_failed_post_is_not_recorded_and_retries(self, monkeypatch, tmp_path: Path) -> None:
        import orion.jobs.launchd_health_probe as mod

        calls: list = []

        def flaky(url, alert):
            calls.append(alert)
            if len(calls) == 1:
                raise RuntimeError("discord unreachable")

        monkeypatch.setattr(mod, "_post_discord", flaky)
        sp = tmp_path / "s.json"
        alert = self._alert()

        # First send raises (Discord down); run_probe catches it. It must NOT be
        # recorded, so the next minute retries instead of suppressing a never-sent page.
        with pytest.raises(RuntimeError):
            mod._discord_notifier(
                alert, webhook_url="https://d.test/wh", state_path=sp, window_seconds=3600, now=1000.0
            )
        mod._discord_notifier(alert, webhook_url="https://d.test/wh", state_path=sp, window_seconds=3600, now=1060.0)
        assert len(calls) == 2

    def test_post_discord_sends_content_payload(self, monkeypatch) -> None:
        import urllib.request

        import orion.jobs.launchd_health_probe as mod

        captured: dict = {}

        class _Resp:
            def read(self) -> bytes:
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a) -> bool:
                return False

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            return _Resp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        mod._post_discord("https://discord.test/wh", self._alert(exit_code=-6))
        assert captured["url"] == "https://discord.test/wh"
        assert "content" in captured["body"]  # Discord webhook payload shape
        assert "com.empire.orion.execution" in captured["body"]["content"]


@pytest.mark.unit
class TestTestFire:
    """--test-fire posts a synthetic alert to the real webhook so an operator
    can verify the notifier without waiting for a job to actually die."""

    def test_posts_when_webhook_set(self, monkeypatch) -> None:
        import orion.jobs.launchd_health_probe as mod

        posts: list[tuple] = []
        monkeypatch.setattr(mod, "_post_discord", lambda url, alert: posts.append((url, alert)))

        rc = mod.test_fire(webhook_url="https://discord.test/wh")
        assert rc == 0
        assert len(posts) == 1
        assert posts[0][0] == "https://discord.test/wh"

    def test_returns_nonzero_without_webhook(self, monkeypatch) -> None:
        import orion.jobs.launchd_health_probe as mod

        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        posts: list = []
        monkeypatch.setattr(mod, "_post_discord", lambda url, alert: posts.append(alert))

        assert mod.test_fire() == 1
        assert posts == []  # nothing to POST -> caller learns the webhook is unconfigured

    def test_returns_nonzero_on_post_failure(self, monkeypatch) -> None:
        import orion.jobs.launchd_health_probe as mod

        def boom(url, alert):
            raise RuntimeError("HTTP Error 403: Forbidden")

        monkeypatch.setattr(mod, "_post_discord", boom)
        assert mod.test_fire(webhook_url="https://discord.test/wh") == 1

    def test_main_dispatches_test_fire(self, monkeypatch) -> None:
        import orion.jobs.launchd_health_probe as mod

        called: list = []
        monkeypatch.setattr(mod, "test_fire", lambda: called.append(True) or 0)
        # run_probe must NOT run when --test-fire is passed
        monkeypatch.setattr(mod, "run_probe", lambda: (_ for _ in ()).throw(AssertionError("scan ran")))

        assert mod.main(["--test-fire"]) == 0
        assert called == [True]


@pytest.mark.unit
class TestMarkerAttachment:
    """Smoke test ensuring the module is unit-test-marker-clean."""

    def test_module_importable(self) -> None:
        import orion.jobs.launchd_health_probe as m

        assert hasattr(m, "run_probe")
