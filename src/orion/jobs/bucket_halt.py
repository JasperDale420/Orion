"""Durable, time-boxed per-bucket entry halts — the measurement loop's brake.

``bucket_metrics`` measures realized per-bucket performance every night. Its
``consider_halting`` verdict (trailing-50 profit factor below 0.6) used to be
advisory only: it paged Discord and nothing stopped. This module is the record
that makes it act. A halt is one ``system_status`` row per bucket, keyed
``bucket_halt:<BUCKET>``, that ``preflight_live_signal`` reads on the entry path.

Deliberate scope, in both directions:

* ENTRIES only. Exits never run through preflight, so a halted bucket keeps
  closing its open positions — halting entries on a losing bucket must not
  strand the positions that made it losing.
* TIME-BOXED. A halt expires after ten trading sessions and the nightly pass
  releases it, because a permanently halted bucket can never generate the
  trades that would prove it recovered. If the criterion still fires on the
  night the window lapses, the bucket is halted again for another window — the
  rolling 30-day measurement window is what eventually clears it, as the losing
  sample ages out and the trailing window no longer fills.
* NOT A KILL SWITCH. Every read failure fails toward the pre-existing
  behaviour (trade), never toward a silent halt. The circuit breaker, the
  daily-loss limit and the drawdown kill switch remain the hard, independent
  stops; this gate only removes one bucket from the entry menu and composes
  additively with them.

Operator control (an operator halt is never auto-expired or overwritten):

    DB_URL="postgresql+asyncpg://…@localhost:5440/orion_db" \\
      uv run python -m orion.jobs.bucket_halt --list
    … --bucket SWING --set --sessions 10 --reason "manual hold pending review"
    … --bucket SWING --clear
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from time import monotonic as _monotonic
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from orion.core.timekeeping import derive_trading_date_and_session, last_closed_trading_date, sessions_forward
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.models import SystemStatus

logger = setup_struct_logger("orion.jobs.bucket_halt")

HALT_KEY_PREFIX = "bucket_halt:"
HALT_STATUS = "HALTED"
DEFAULT_HALT_SESSIONS = 10
SET_BY_MEASUREMENT = "bucket_metrics"
SET_BY_OPERATOR = "operator"

# The entry gate runs once per candidate. Reading the halt rows from the DB
# every time would put a query on the hot path for state that changes at most
# once a night, so reads are cached in-process. The TTL is the staleness bound
# an operator inherits: after `--clear`, a running execution process resumes
# the bucket within this many seconds, not instantly.
CACHE_TTL_SECONDS = 60.0

_cached_rows: dict[str, BucketHalt] | None = None
_cached_at: float = 0.0


@dataclass(frozen=True)
class BucketHalt:
    """One parsed ``bucket_halt:<BUCKET>`` row."""

    bucket: str
    expires_after_session: date
    set_by: str
    profit_factor: float | None = None
    n_closed: int | None = None
    reason: str | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        """True once the current trading date has moved past the window.

        The halt is in force *through* ``expires_after_session``; a window
        opened after Friday's close therefore covers the next ten sessions and
        releases on the eleventh.
        """
        current, _session = derive_trading_date_and_session(now or datetime.now(UTC))
        return current > self.expires_after_session

    def describe(self) -> str:
        pf = f"{self.profit_factor:.2f}" if self.profit_factor is not None else "n/a"
        n = str(self.n_closed) if self.n_closed is not None else "n/a"
        return f"{self.bucket} PF={pf} n={n} until {self.expires_after_session.isoformat()}"


@dataclass(frozen=True)
class HaltWrite:
    """Outcome of a halt write: ``written``, ``already_halted`` or ``operator_halt_present``."""

    outcome: str
    halt: BucketHalt | None


def halt_key(bucket: str) -> str:
    return f"{HALT_KEY_PREFIX}{normalize_bucket(bucket)}"


def normalize_bucket(bucket: str) -> str:
    return bucket.strip().upper()


def _details(halt: BucketHalt) -> str:
    return json.dumps(
        {
            "pf": halt.profit_factor,
            "n": halt.n_closed,
            "expires_after_session": halt.expires_after_session.isoformat(),
            "set_by": halt.set_by,
            "reason": halt.reason,
        }
    )


def _parse(row: SystemStatus) -> BucketHalt | None:
    """Parse a halt row, or None when it cannot be trusted.

    An unreadable row is treated as no halt rather than as an indefinite one:
    the same fail-toward-trading rule the read path uses. It stays visible in
    `--list` and in this WARNING so an operator can repair or clear it.
    """
    bucket = row.key[len(HALT_KEY_PREFIX) :]
    try:
        payload = json.loads(row.details or "")
        expires = date.fromisoformat(payload["expires_after_session"])
    except Exception as exc:
        logger.warning("bucket_halt_row_unreadable", bucket=bucket, key=row.key, error=str(exc))
        return None
    return BucketHalt(
        bucket=bucket,
        expires_after_session=expires,
        set_by=str(payload.get("set_by") or SET_BY_MEASUREMENT),
        profit_factor=payload.get("pf"),
        n_closed=payload.get("n"),
        reason=payload.get("reason"),
    )


async def _load_halts() -> dict[str, BucketHalt]:
    """Every readable halt row, regardless of expiry, keyed by bucket."""

    async def read(session: Any) -> list[SystemStatus]:
        stmt = select(SystemStatus).where(
            SystemStatus.key.like(f"{HALT_KEY_PREFIX}%"), SystemStatus.status == HALT_STATUS
        )
        return list((await session.execute(stmt)).scalars().all())

    parsed = (_parse(row) for row in await db_query(read))
    return {halt.bucket: halt for halt in parsed if halt is not None}


async def list_halts() -> list[BucketHalt]:
    """All halt rows, expired ones included (operator view)."""
    return sorted((await _load_halts()).values(), key=lambda h: h.bucket)


async def active_halts(*, now: datetime | None = None) -> dict[str, BucketHalt]:
    """Unexpired halt rows, keyed by bucket. Reads the DB, not the cache."""
    return {bucket: halt for bucket, halt in (await _load_halts()).items() if not halt.is_expired(now)}


def reset_halt_cache() -> None:
    """Drop the in-process halt cache (tests, and any explicit refresh)."""
    global _cached_rows, _cached_at
    _cached_rows = None
    _cached_at = 0.0


async def get_active_halt(bucket: str, *, now: datetime | None = None) -> BucketHalt | None:
    """The live halt for ``bucket``, or None — the entry gate's read.

    Fails toward the pre-existing behaviour: a DB error logs a WARNING and
    returns None (do not halt). A halt is an active measurement verdict, not a
    kill switch, so a database blip must never silently stop trading; the
    circuit breaker and risk limits are the gates that fail closed.

    Rows are cached for ``CACHE_TTL_SECONDS``; expiry is evaluated against
    ``now`` on every call, so a warm cache still releases a halt on time.
    """
    global _cached_rows, _cached_at

    if _cached_rows is None or (_monotonic() - _cached_at) > CACHE_TTL_SECONDS:
        try:
            rows = await _load_halts()
        except Exception as exc:
            # Not cached: a failed read must not pin "no halts" for a full TTL.
            logger.warning("bucket_halt_read_failed_entry_allowed", bucket=bucket, error=str(exc))
            return None
        _cached_rows = rows
        _cached_at = _monotonic()

    halt = _cached_rows.get(normalize_bucket(bucket))
    if halt is None or halt.is_expired(now):
        return None
    return halt


async def record_halt(
    bucket: str,
    *,
    profit_factor: float | None,
    n_closed: int | None,
    sessions: int = DEFAULT_HALT_SESSIONS,
    set_by: str = SET_BY_MEASUREMENT,
    reason: str | None = None,
    now: datetime | None = None,
) -> HaltWrite:
    """Open a halt window for ``bucket``, idempotently.

    An existing unexpired halt is left exactly as it is — re-running the
    nightly pass must not roll the expiry forward, or the window would never
    end. An operator halt is never touched at all: only an operator clears it.

    The window is anchored on the most recently closed session, so a pass that
    fires after Friday's close halts the next ``sessions`` sessions.
    """
    ts = now or datetime.now(UTC)
    halt = BucketHalt(
        bucket=normalize_bucket(bucket),
        expires_after_session=sessions_forward(last_closed_trading_date(ts), sessions),
        set_by=set_by,
        profit_factor=profit_factor,
        n_closed=n_closed,
        reason=reason,
    )

    async def write(session: Any) -> HaltWrite:
        stmt = select(SystemStatus).where(SystemStatus.key == halt_key(bucket))
        row = (await session.execute(stmt)).scalars().first()
        if row is not None:
            existing = _parse(row)
            if existing is not None and existing.set_by == SET_BY_OPERATOR and set_by != SET_BY_OPERATOR:
                return HaltWrite("operator_halt_present", existing)
            if existing is not None and not existing.is_expired(ts) and set_by != SET_BY_OPERATOR:
                return HaltWrite("already_halted", existing)
            row.status = HALT_STATUS
            row.details = _details(halt)
            row.last_updated_utc = datetime.now(UTC)
            return HaltWrite("written", halt)

        session.add(SystemStatus(key=halt_key(bucket), status=HALT_STATUS, details=_details(halt)))
        return HaltWrite("written", halt)

    try:
        write_result: HaltWrite = await db_write(write)
    except IntegrityError:
        # Another writer inserted the same key concurrently. Losing that race
        # is indistinguishable from finding the halt already in place.
        return HaltWrite("already_halted", None)

    if write_result.outcome == "written":
        logger.warning(
            "bucket_halt_opened",
            bucket=halt.bucket,
            profit_factor=halt.profit_factor,
            n_closed=halt.n_closed,
            expires_after_session=halt.expires_after_session.isoformat(),
            set_by=halt.set_by,
            reason=halt.reason,
        )
    return write_result


async def remove_halt(bucket: str) -> bool:
    """Delete a bucket's halt row. True when a row was removed."""

    async def write(session: Any) -> int:
        result = await session.execute(delete(SystemStatus).where(SystemStatus.key == halt_key(bucket)))
        return int(result.rowcount or 0)

    removed = await db_write(write) > 0
    if removed:
        logger.info("bucket_halt_cleared", bucket=normalize_bucket(bucket))
    return removed


async def release_expired_halts(*, now: datetime | None = None) -> list[BucketHalt]:
    """Delete halts whose session window has passed. Operator rows are kept.

    Releasing is what makes the halt a time-box rather than a death sentence:
    the bucket resumes sampling, and the next nightly pass re-halts it only if
    the criterion still fires on fresh data.
    """
    expired = [
        halt for halt in (await _load_halts()).values() if halt.set_by != SET_BY_OPERATOR and halt.is_expired(now)
    ]
    for halt in expired:
        await remove_halt(halt.bucket)
        logger.warning("bucket_halt_released", bucket=halt.bucket, expired_after=halt.expires_after_session.isoformat())
    return sorted(expired, key=lambda h: h.bucket)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orion.jobs.bucket_halt",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--bucket", help="bucket name, e.g. SWING (required for --set / --clear)")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--set", action="store_true", help="open an operator halt on the bucket")
    action.add_argument("--clear", action="store_true", help="remove the bucket's halt row")
    action.add_argument("--list", action="store_true", help="show every halt row and whether it is live")
    parser.add_argument("--sessions", type=int, default=DEFAULT_HALT_SESSIONS, help="halt length in trading sessions")
    parser.add_argument("--reason", default=None, help="free-text note stored on the halt row")
    return parser


async def run_cli(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if (args.set or args.clear) and not args.bucket:
        parser.error("--bucket is required with --set / --clear")

    if args.list:
        halts = await list_halts()
        if not halts:
            print("no bucket halts")  # noqa: T201
            return 0
        for halt in halts:
            state = "EXPIRED" if halt.is_expired() else "LIVE"
            print(f"{state:<8} {halt.describe()}  set_by={halt.set_by} reason={halt.reason}")  # noqa: T201
        return 0

    if args.clear:
        removed = await remove_halt(args.bucket)
        print(f"{'cleared' if removed else 'no halt found for'} {normalize_bucket(args.bucket)}")  # noqa: T201
        return 0

    write = await record_halt(
        args.bucket,
        profit_factor=None,
        n_closed=None,
        sessions=args.sessions,
        set_by=SET_BY_OPERATOR,
        reason=args.reason,
    )
    print(f"{write.outcome}: {write.halt.describe() if write.halt else normalize_bucket(args.bucket)}")  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_cli(argv))


if __name__ == "__main__":
    sys.exit(main())
