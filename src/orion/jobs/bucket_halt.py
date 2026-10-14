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
  trades that would prove it recovered. Release converts the row to a RESUMED
  marker that runs for an equal number of sessions, during which the criterion
  may not re-arm the halt. Without that, release would be theatre: a halted
  bucket takes no new entries, so on the night its window lapses the trailing
  fifty closed trades are still the same losing fifty, and the very same pass
  would halt it again — a permanent halt wearing a time-box. The verdict is
  still alerted throughout the resume window; only the automatic action waits.
* NOT A KILL SWITCH. Every read failure fails toward the pre-existing
  behaviour (trade), never toward a silent halt. The circuit breaker, the
  daily-loss limit and the drawdown kill switch remain the hard, independent
  stops; this gate only removes one bucket from the entry menu and composes
  additively with them.

Operator control. A live operator halt outranks everything here: the nightly
pass neither releases nor overwrites it. It is still time-boxed by its own
``--sessions``, and once that window lapses it stops gating entries and stops
outranking the automatic halt — otherwise a single temporary operator hold
would suppress the bucket's automatic protection forever.

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
# A released bucket keeps its row, flipped to this status, for the length of
# its sampling window. It never gates an entry; it only holds the automatic
# criterion off long enough for fresh trades to reach the measurement window.
RESUMED_STATUS = "RESUMED"
DEFAULT_HALT_SESSIONS = 10
RESUME_SAMPLING_SESSIONS = DEFAULT_HALT_SESSIONS
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
    """One parsed ``bucket_halt:<BUCKET>`` row, HALTED or RESUMED."""

    bucket: str
    expires_after_session: date
    set_by: str
    status: str = HALT_STATUS
    profit_factor: float | None = None
    n_closed: int | None = None
    reason: str | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        """True once the current trading date has moved past the window.

        The window is in force *through* ``expires_after_session``; one opened
        after Friday's close therefore covers the next ten sessions and lapses
        on the eleventh.
        """
        current, _session = derive_trading_date_and_session(now or datetime.now(UTC))
        return current > self.expires_after_session

    def gates_entries(self, now: datetime | None = None) -> bool:
        """Only a live HALTED row stops an entry. A RESUMED marker never does."""
        return self.status == HALT_STATUS and not self.is_expired(now)

    def describe(self) -> str:
        pf = f"{self.profit_factor:.2f}" if self.profit_factor is not None else "n/a"
        n = str(self.n_closed) if self.n_closed is not None else "n/a"
        return f"{self.bucket} PF={pf} n={n} until {self.expires_after_session.isoformat()}"


@dataclass(frozen=True)
class HaltWrite:
    """Outcome of a halt write.

    ``written`` — the halt is now in force.
    ``already_halted`` — a live halt was already there and was left untouched.
    ``operator_halt_present`` — a live operator halt outranks the automatic one.
    ``resuming`` — the bucket is inside its post-halt sampling window.
    """

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
        status=row.status,
        profit_factor=payload.get("pf"),
        n_closed=payload.get("n"),
        reason=payload.get("reason"),
    )


def _select_halt_rows() -> Any:
    return select(SystemStatus).where(SystemStatus.key.like(f"{HALT_KEY_PREFIX}%"))


async def _load_halts() -> dict[str, BucketHalt]:
    """Every readable halt/resume row, regardless of expiry, keyed by bucket."""

    async def read(session: Any) -> list[SystemStatus]:
        return list((await session.execute(_select_halt_rows())).scalars().all())

    parsed = (_parse(row) for row in await db_query(read))
    return {halt.bucket: halt for halt in parsed if halt is not None}


async def list_halts() -> list[BucketHalt]:
    """Every halt/resume row, expired ones included (operator view)."""
    return sorted((await _load_halts()).values(), key=lambda h: h.bucket)


async def active_halts(*, now: datetime | None = None) -> dict[str, BucketHalt]:
    """The halts currently blocking entries, keyed by bucket. Reads the DB."""
    return {bucket: halt for bucket, halt in (await _load_halts()).items() if halt.gates_entries(now)}


def reset_halt_cache() -> None:
    """Drop the in-process halt cache (tests, and any explicit refresh)."""
    global _cached_rows, _cached_at
    _cached_rows = None
    _cached_at = 0.0


async def _halt_rows(*, use_cache: bool) -> dict[str, BucketHalt] | None:
    """Rows for a gate read, or None when they could not be read at all."""
    global _cached_rows, _cached_at

    if use_cache and _cached_rows is not None and (_monotonic() - _cached_at) <= CACHE_TTL_SECONDS:
        return _cached_rows
    try:
        rows = await _load_halts()
    except Exception as exc:
        # A failed read is never cached: it must not pin "no halts" for a TTL.
        logger.warning("bucket_halt_read_failed_entry_allowed", error=str(exc))
        return None
    _cached_rows = rows
    _cached_at = _monotonic()
    return rows


async def get_active_halt(bucket: str, *, now: datetime | None = None, use_cache: bool = True) -> BucketHalt | None:
    """The live halt for ``bucket``, or None — the entry gates' read.

    Fails toward the pre-existing behaviour: a DB error logs a WARNING and
    returns None (do not halt). A halt is an active measurement verdict, not a
    kill switch, so a database blip must never silently stop trading; the
    circuit breaker and risk limits are the gates that fail closed.

    Rows are cached for ``CACHE_TTL_SECONDS``; expiry is evaluated against
    ``now`` on every call, so a warm cache still releases a halt on time. Pass
    ``use_cache=False`` at the submission authority, where the point of the
    read is to catch a halt opened since the candidate cleared preflight.
    """
    rows = await _halt_rows(use_cache=use_cache)
    if rows is None:
        return None
    halt = rows.get(normalize_bucket(bucket))
    return halt if halt is not None and halt.gates_entries(now) else None


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

    Three things are left alone by an automatic write, and only by an automatic
    write — an operator ``--set`` is an explicit instruction and overrides all
    of them:

    * a live halt, whatever opened it: re-running the nightly pass must not
      roll the expiry forward, or the window would never end;
    * a live *operator* halt, which outranks the criterion outright;
    * a live RESUMED marker, the sampling window a released bucket is owed.

    Expiry is what makes an operator halt an instruction rather than a
    permanent veto: once its own ``--sessions`` window lapses it stops gating
    entries, and it stops outranking the automatic halt at the same moment.

    The window is anchored on the most recently closed session, so a pass that
    fires after Friday's close halts the next ``sessions`` sessions.
    """
    ts = now or datetime.now(UTC)
    halt = BucketHalt(
        bucket=normalize_bucket(bucket),
        expires_after_session=sessions_forward(last_closed_trading_date(ts), sessions),
        set_by=set_by,
        status=HALT_STATUS,
        profit_factor=profit_factor,
        n_closed=n_closed,
        reason=reason,
    )
    operator_write = set_by == SET_BY_OPERATOR

    async def write(session: Any) -> HaltWrite:
        stmt = select(SystemStatus).where(SystemStatus.key == halt_key(bucket)).with_for_update()
        row = (await session.execute(stmt)).scalars().first()
        if row is not None:
            existing = _parse(row)
            if existing is not None and not operator_write and not existing.is_expired(ts):
                if existing.status == RESUMED_STATUS:
                    return HaltWrite("resuming", existing)
                if existing.set_by == SET_BY_OPERATOR:
                    return HaltWrite("operator_halt_present", existing)
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
        # Another writer inserted this key between our SELECT and INSERT. Retry
        # against the row it committed rather than guessing an outcome: an
        # operator write must still take ownership of a row the nightly pass
        # won the race to create, and an automatic one must report what is
        # really there.
        write_result = await db_write(write)

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
    """Advance every lapsed window one step. Returns the buckets set resuming.

    An automatic halt past its window becomes a RESUMED marker running for
    ``RESUME_SAMPLING_SESSIONS``: the bucket takes entries again, and the
    criterion may not re-arm until that marker itself lapses. Without the
    marker the release would be undone by the same nightly pass, since a
    halted bucket closes no new trades and its trailing fifty are unchanged.

    A lapsed RESUMED marker is deleted — the bucket is fully re-eligible.
    Operator halts are never released here; they are the operator's to clear.

    Selection and mutation share one transaction, with the rows locked, so a
    concurrent operator ``--set`` cannot be read as expired and then deleted.
    """
    ts = now or datetime.now(UTC)

    async def work(session: Any) -> list[BucketHalt]:
        rows = list((await session.execute(_select_halt_rows().with_for_update())).scalars().all())
        resumed: list[BucketHalt] = []
        for row in rows:
            halt = _parse(row)
            if halt is None or not halt.is_expired(ts):
                continue
            if halt.status == RESUMED_STATUS:
                await session.delete(row)
                logger.info("bucket_resume_window_closed", bucket=halt.bucket)
                continue
            if halt.set_by == SET_BY_OPERATOR:
                continue
            marker = BucketHalt(
                bucket=halt.bucket,
                expires_after_session=sessions_forward(last_closed_trading_date(ts), RESUME_SAMPLING_SESSIONS),
                set_by=halt.set_by,
                status=RESUMED_STATUS,
                profit_factor=halt.profit_factor,
                n_closed=halt.n_closed,
                reason=f"sampling window after a halt that ran to {halt.expires_after_session.isoformat()}",
            )
            row.status = RESUMED_STATUS
            row.details = _details(marker)
            row.last_updated_utc = datetime.now(UTC)
            resumed.append(marker)
            logger.warning(
                "bucket_halt_released",
                bucket=halt.bucket,
                halted_through=halt.expires_after_session.isoformat(),
                sampling_through=marker.expires_after_session.isoformat(),
            )
        return sorted(resumed, key=lambda h: h.bucket)

    return await db_write(work)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orion.jobs.bucket_halt",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--bucket", help="bucket name, e.g. SWING (required for --set / --clear)")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--set", action="store_true", help="open an operator halt on the bucket")
    action.add_argument("--clear", action="store_true", help="remove the bucket's halt or resume row")
    action.add_argument("--list", action="store_true", help="show every halt / resume row and its state")
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
            if halt.is_expired():
                state = "EXPIRED"
            else:
                state = "HALTED" if halt.status == HALT_STATUS else "SAMPLING"
            print(f"{state:<9} {halt.describe()}  set_by={halt.set_by} reason={halt.reason}")  # noqa: T201
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
