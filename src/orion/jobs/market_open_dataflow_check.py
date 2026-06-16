"""market-open data-flow check — page us at the open if the bronze feed stalls.

Born from the 2026-06-08 incident where a service-lease split-brain starved
ingestion and only 403 bronze events landed all day (normal is ~150k-200k).
The split-brain was fixed, but the stall was discovered hours later by hand.
This check is the automated guard: every trading-day morning shortly after the
US equity open it verifies that fresh ``bronze_events`` rows are landing in
Orion's TimescaleDB, and emits a CRITICAL alert if they are not.

Wired in via scripts/launchd/com.empire.orion.market-open-dataflow-check.plist
with StartCalendarInterval entries a few minutes after the cash open
(06:40 / 07:00 / 07:30 PDT == 09:40 / 10:00 / 10:30 ET), weekdays only.

Mirrors orion.jobs.launchd_health_probe: pure functions for parsing and
threshold logic, a thin run loop, and a best-effort stdlib Slack notifier so
the safety net does not itself depend on the system it is watching. The check
deliberately uses ``docker exec ... psql`` and ``curl`` rather than importing
Orion's async DB / Gateway clients — if ingestion is wedged, the heavyweight
import path may be exactly what is broken, and the guard must still run.

Logic:
  - Only alert during market hours. Outside the regular cash session (or on
    a weekend) the check reports "market closed -- no alert" plus the raw
    freshness / gateway diagnostics and exits 0.
  - During market hours, if the newest ``bronze_events.received_ts_utc`` is
    older than ``--max-age`` (default 300s) it is a CRITICAL stall alert.
  - Gateway reachability is reported alongside as a secondary signal: a
    non-200 from the account endpoint during market hours is its own CRITICAL
    (no point checking freshness if the upstream feed is unreachable).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timezone
from enum import Enum
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

DEFAULT_LOG_PATH = Path("logs/market_open_dataflow_check.log")
DEFAULT_MAX_AGE_SECONDS = 300  # 5 min — see module docstring / spec

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)  # 09:30 ET cash open
MARKET_CLOSE = time(16, 0)  # 16:00 ET cash close

DB_CONTAINER = "orion_timescaledb"
DB_USER = "orion"
DB_NAME = "orion_db"
BRONZE_FRESHNESS_SQL = "SELECT max(received_ts_utc) FROM bronze_events;"

# Gateway account endpoint — a cheap 200/!=200 liveness probe.
GATEWAY_ACCOUNT_URL = "http://localhost:8080/api/v1/alpaca/account"
# Read the gateway key from the environment (the wrapper sources .env first and
# fail-fasts if neither name is set). No literal fallback: the legacy plaintext
# key was revoked 2026-06-11, so any baked-in default could only produce a 401
# -> a false "gateway down" CRITICAL during market hours. A bare `python -m ...`
# invocation without a key probes with an empty key and fails the same loud way.
# DATA_GATEWAY_API_KEY is canonical; GATEWAY_API_KEY is the wrapper's alias.
GATEWAY_KEY = os.environ.get("DATA_GATEWAY_API_KEY") or os.environ.get("GATEWAY_API_KEY") or ""


class Severity(str, Enum):
    OK = "OK"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class DataflowResult:
    """Outcome of one check pass. ``alert`` is True iff a row should be
    persisted and a notification dispatched."""

    severity: Severity
    alert: bool
    market_open: bool
    bronze_fresh: bool | None  # None = could not determine (no rows / query failed)
    bronze_age_seconds: float | None
    bronze_max_ts: str | None
    gateway_up: bool
    message: str
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_log_row(self) -> str:
        return json.dumps(
            {
                "ts": self.ts,
                "severity": self.severity.value,
                "market_open": self.market_open,
                "bronze_fresh": self.bronze_fresh,
                "bronze_age_seconds": self.bronze_age_seconds,
                "bronze_max_ts": self.bronze_max_ts,
                "gateway_up": self.gateway_up,
                "message": self.message,
            },
            sort_keys=True,
        )


def is_market_open(now_utc: datetime) -> bool:
    """True iff ``now_utc`` falls within the regular US equity cash session
    (09:30-16:00 ET, Mon-Fri).

    Intentionally simple — it does NOT consult an exchange holiday calendar.
    A holiday produces at most a false "market open" window, in which the
    worst case is one spurious stall alert on a day the feed is legitimately
    idle. That is the safe direction for a guard whose whole purpose is to
    err loud; the alternative (vendoring a holiday calendar) adds a moving
    part to the safety net for marginal benefit. ``now_utc`` must be
    timezone-aware.
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    now_et = now_utc.astimezone(ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN <= now_et.time() < MARKET_CLOSE


def parse_bronze_max_ts(raw: str) -> datetime | None:
    """Parse the ``max(received_ts_utc)`` value emitted by ``psql -At``.

    psql renders a timestamptz as e.g. ``2026-06-08 18:23:36.934345+00``.
    An empty table yields an empty string. Returns a timezone-aware UTC
    datetime, or None when the value is empty/unparseable (treated by the
    caller as "no fresh data"). The trailing ``+00`` offset is normalised
    to ``+00:00`` for ``fromisoformat``; a value with no offset at all is
    assumed UTC (the column is timestamptz and psql prints UTC here).
    """
    value = raw.strip()
    if not value:
        return None
    # psql separates date and time with a space; ISO wants 'T'.
    iso = value.replace(" ", "T", 1)
    # Normalise a bare "+00"/"-05" style offset to "+00:00".
    if (iso[-3] in "+-") and iso.count(":") <= 2:  # offset like +00 / -05
        iso = iso + ":00"
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(UTC)


def evaluate(
    now_utc: datetime,
    bronze_max_ts: datetime | None,
    gateway_up: bool,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> DataflowResult:
    """Pure decision function: given the current time, the newest bronze
    timestamp, and gateway liveness, decide whether to alert.

    Alert rules (only ever during market hours):
      * gateway unreachable  -> CRITICAL (upstream feed is down)
      * no bronze rows / unparseable max ts -> CRITICAL (nothing landing)
      * newest bronze row older than ``max_age_seconds`` -> CRITICAL (stall)
    Outside market hours the result is always OK/no-alert, regardless of
    freshness, but the diagnostics are still populated for the log row.
    """
    market_open = is_market_open(now_utc)

    age_seconds: float | None = None
    bronze_fresh: bool | None = None
    if bronze_max_ts is not None:
        age_seconds = (now_utc - bronze_max_ts).total_seconds()
        bronze_fresh = age_seconds <= max_age_seconds

    max_ts_iso = bronze_max_ts.isoformat() if bronze_max_ts is not None else None

    diag = (
        f"bronze_max_ts={max_ts_iso} "
        f"age={'n/a' if age_seconds is None else f'{age_seconds:.0f}s'} "
        f"gateway_up={gateway_up}"
    )

    if not market_open:
        return DataflowResult(
            severity=Severity.OK,
            alert=False,
            market_open=False,
            bronze_fresh=bronze_fresh,
            bronze_age_seconds=age_seconds,
            bronze_max_ts=max_ts_iso,
            gateway_up=gateway_up,
            message=f"market closed -- no alert ({diag})",
        )

    if not gateway_up:
        return DataflowResult(
            severity=Severity.CRITICAL,
            alert=True,
            market_open=True,
            bronze_fresh=bronze_fresh,
            bronze_age_seconds=age_seconds,
            bronze_max_ts=max_ts_iso,
            gateway_up=False,
            message=(
                f"Data-Gateway unreachable during market hours -- upstream "
                f"feed is down; bronze ingestion cannot recover until it "
                f"returns ({diag})"
            ),
        )

    if bronze_max_ts is None:
        return DataflowResult(
            severity=Severity.CRITICAL,
            alert=True,
            market_open=True,
            bronze_fresh=None,
            bronze_age_seconds=None,
            bronze_max_ts=None,
            gateway_up=True,
            message=(f"no bronze_events rows during market hours -- ingestion is not landing any data ({diag})"),
        )

    if age_seconds is not None and age_seconds > max_age_seconds:
        return DataflowResult(
            severity=Severity.CRITICAL,
            alert=True,
            market_open=True,
            bronze_fresh=False,
            bronze_age_seconds=age_seconds,
            bronze_max_ts=max_ts_iso,
            gateway_up=True,
            message=(
                f"bronze feed STALLED during market hours -- newest event is "
                f"{age_seconds:.0f}s old (threshold {max_age_seconds}s); "
                f"ingestion may be wedged (cf. 2026-06-08 split-brain) ({diag})"
            ),
        )

    return DataflowResult(
        severity=Severity.OK,
        alert=False,
        market_open=True,
        bronze_fresh=True,
        bronze_age_seconds=age_seconds,
        bronze_max_ts=max_ts_iso,
        gateway_up=True,
        message=f"bronze feed healthy ({diag})",
    )


# ---- Production probes — shell out. Tests inject fakes. ----------------------


def _real_bronze_max_ts() -> str:
    """Query the newest bronze timestamp via ``docker exec ... psql``.

    Returns the raw psql ``-At`` value (empty string for an empty table).
    On any failure (container down, psql error) returns "" so the caller
    treats it as "no fresh data" and — during market hours — alerts.
    """
    try:
        return subprocess.check_output(
            [
                "docker",
                "exec",
                DB_CONTAINER,
                "psql",
                "-U",
                DB_USER,
                "-d",
                DB_NAME,
                "-At",
                "-c",
                BRONZE_FRESHNESS_SQL,
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=30,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return ""


def _real_gateway_up() -> bool:
    """True iff the Gateway account endpoint returns HTTP 200."""
    try:
        code = subprocess.check_output(
            [
                "curl",
                "-s",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-H",
                f"X-Gateway-Key: {GATEWAY_KEY}",
                "--max-time",
                "10",
                GATEWAY_ACCOUNT_URL,
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return code == "200"


def _slack_notifier(result: DataflowResult) -> None:
    """Best-effort Slack webhook POST. No-op if SLACK_WEBHOOK_URL unset.

    Kept dependency-free (stdlib urllib) so the check runs even if Orion
    isn't fully importable — the safety net must not depend on the system
    it watches.
    """
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return
    import urllib.request

    payload = {
        "text": f"\U0001f6a8 *[{result.severity.value}]* {result.message}",
        "attachments": [
            {
                "color": "#ff0000",
                "fields": [
                    {"title": "Market open", "value": str(result.market_open), "short": True},
                    {"title": "Gateway up", "value": str(result.gateway_up), "short": True},
                    {
                        "title": "Bronze age (s)",
                        "value": "n/a" if result.bronze_age_seconds is None else f"{result.bronze_age_seconds:.0f}",
                        "short": True,
                    },
                    {"title": "Timestamp", "value": result.ts, "short": True},
                ],
            }
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=5).read()  # noqa: S310 — fixed https webhook URL


def run_check(
    bronze_runner: Callable[[], str] = _real_bronze_max_ts,
    gateway_runner: Callable[[], bool] = _real_gateway_up,
    notifier: Callable[[DataflowResult], None] = _slack_notifier,
    log_path: Path = DEFAULT_LOG_PATH,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now_utc: datetime | None = None,
) -> DataflowResult:
    """Run one pass: probe bronze freshness + gateway, evaluate, persist, notify.

    Always writes a log row (the diagnostic value is useful even on healthy
    runs at the open), but only invokes the notifier when ``result.alert`` is
    True. The notifier is best-effort — its exceptions are caught and printed
    to stderr so a transient Slack outage cannot prevent the durable log row
    from landing.
    """
    now = now_utc or datetime.now(UTC)
    bronze_max_ts = parse_bronze_max_ts(bronze_runner())
    gateway_up = gateway_runner()

    result = evaluate(now, bronze_max_ts, gateway_up, max_age_seconds)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(result.to_log_row() + "\n")

    if result.alert:
        try:
            notifier(result)
        except Exception as exc:  # noqa: BLE001 — best-effort notification
            print(
                f"market_open_dataflow_check: notifier failed: {exc}",
                file=sys.stderr,
            )

    return result


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m orion.jobs.market_open_dataflow_check`.

    Exit 2 on a CRITICAL alert, 0 otherwise (including the market-closed
    diagnostic path). The human-readable summary always prints to stdout so
    a manual invocation shows the freshness/gateway numbers.
    """
    parser = argparse.ArgumentParser(description="Orion market-open data-flow check")
    parser.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE_SECONDS,
        help=f"bronze staleness threshold in seconds (default {DEFAULT_MAX_AGE_SECONDS})",
    )
    args = parser.parse_args(argv)

    result = run_check(max_age_seconds=args.max_age)
    print(f"[{result.severity.value}] {result.message}")
    return 2 if result.alert else 0


if __name__ == "__main__":
    sys.exit(main())
