"""One-time migration: record a verified risk-state baseline.

Option realized P&L was previously folded into `risk_state` without the 100x
contract multiplier, so rows written under that convention hold
`current_daily_loss` / `current_equity` at 1/100th scale. `RiskManager` refuses
to admit orders while such a row is in play (see `baseline_unverified`).

This is the deliberate operator action that resolves it: it overwrites the money
columns with figures a human has verified and stamps
`RiskState.accounting_version`, so the kill switch arms from a known-good
starting point instead of a mixed-unit total. It is intentionally NOT automatic —
the correct equity cannot be derived from Orion's own tables, because the fills
ledger contains closes with no recorded entry.

It is a MIGRATION, not a reset button. Zeroing `current_daily_loss` is exactly
what the daily-loss kill switch reads, so re-running this after a real loss would
erase an active limit. It therefore refuses when the row is already on the
current convention, and when the ledger shows open positions, unless the caller
passes an explicit acknowledgement.

Usage:
    uv run python -m orion.execution.risk.baseline --starting-equity 100000 \
        --note "verified against broker activities 2026-08-12"
"""

import argparse
import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from orion.core.service_lease import SERVICE_LEASE_STALE_SECONDS, _service_lease_key
from orion.execution.risk.manager import RISK_ACCOUNTING_VERSION, _trading_date
from orion.shared.db_utils import db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.models import SystemStatus
from orion.shared.utils import ensure_utc
from orion.storage.models_risk import RiskState

logger = setup_struct_logger(__name__)

RISK_STATE_ID = "global_risk_v1"

# Services that mutate `risk_state`. Recording a baseline while one of these is
# live is unsafe: it holds its own in-memory copy of the ledger loaded at
# startup, so a later save would overwrite the freshly-recorded figures, and any
# fill it processes in between would be lost from the persisted daily loss.
# Recording is therefore a QUIESCED operation — stop these, record, restart.
RISK_WRITER_SERVICE_IDS = ("execution", "position_monitor")


class BaselineRefused(Exception):
    """Raised when recording a baseline would destroy an active risk control."""


async def _live_risk_writers(session: Any) -> list[str]:
    """Service ids in RISK_WRITER_SERVICE_IDS holding a fresh lease right now."""
    now = datetime.now(UTC)
    live: list[str] = []
    for service_id in RISK_WRITER_SERVICE_IDS:
        row = (
            (await session.execute(select(SystemStatus).where(SystemStatus.key == _service_lease_key(service_id))))
            .scalars()
            .first()
        )
        if row is None:
            continue
        last = ensure_utc(row.last_updated_utc)
        if last is None:
            continue
        if (now - last).total_seconds() < SERVICE_LEASE_STALE_SECONDS:
            live.append(service_id)
    return live


async def record_risk_baseline(
    starting_equity: float,
    current_equity: float | None = None,
    note: str = "",
    force: bool = False,
) -> None:
    """Reset the persisted risk ledger to a verified baseline and stamp it.

    `current_equity` defaults to `starting_equity` — a fresh accounting epoch
    with zero realized P&L. `current_daily_loss` is reset to 0.0 and
    `peak_equity` to the high-water of the supplied figures, because carrying a
    pre-multiplier number forward is the contamination this exists to clear.

    Refuses (unless `force`) when:
      * the row already carries the current accounting version — there is
        nothing to migrate, so this would only be clearing a live kill switch;
      * the ledger reports open positions — rebaselining mid-exposure would
        discard the high-water mark that those positions are measured against.
    """
    if starting_equity <= 0:
        raise BaselineRefused(f"starting_equity must be positive, got {starting_equity}")
    equity = starting_equity if current_equity is None else current_equity
    if equity <= 0:
        raise BaselineRefused(f"current_equity must be positive, got {equity}")

    async def write(session: Any) -> None:
        # Quiescence first: a live risk-writing service holds its own in-memory
        # ledger and would overwrite whatever we record here. Refusing removes
        # the concurrent writer entirely, which is what makes the rest of this
        # safe without row locking.
        live = await _live_risk_writers(session)
        if live and not force:
            raise BaselineRefused(
                f"risk-writing service(s) still live: {', '.join(live)}. Recording a baseline now would be "
                "overwritten by their in-memory ledger, and any fill they process meanwhile would be lost from "
                "the persisted daily loss. Stop them, record the baseline, then restart them. "
                "(force=True overrides only with separate authorization.)"
            )

        state = (await session.execute(select(RiskState).where(RiskState.id == RISK_STATE_ID))).scalars().first()

        if state is not None and not force:
            if state.accounting_version == RISK_ACCOUNTING_VERSION:
                raise BaselineRefused(
                    f"risk_state is already on accounting version {RISK_ACCOUNTING_VERSION}; there is nothing to "
                    f"migrate. Re-running would zero current_daily_loss={state.current_daily_loss} and reset "
                    f"peak_equity={state.peak_equity}, erasing live kill-switch inputs. Pass force=True only with "
                    "separate authorization."
                )
            if (state.open_positions_count or 0) > 0:
                raise BaselineRefused(
                    f"risk_state reports {state.open_positions_count} open position(s). Rebaselining now would "
                    "discard the high-water mark those positions are measured against. Flatten or reconcile "
                    "first, or pass force=True with separate authorization."
                )

        if state is None:
            state = RiskState(id=RISK_STATE_ID)
            session.add(state)

        previous = {
            "current_daily_loss": state.current_daily_loss,
            "current_equity": state.current_equity,
            "starting_equity": state.starting_equity,
            "peak_equity": state.peak_equity,
            "accounting_version": state.accounting_version,
        }

        now = datetime.now(UTC)
        state.starting_equity = starting_equity
        state.current_equity = equity
        state.peak_equity = max(starting_equity, equity)
        state.current_daily_loss = 0.0
        # The zeroed figure belongs to today's session; without a date it would
        # read as a legacy row and be discarded again on the next load.
        state.daily_loss_date = _trading_date(now)
        state.accounting_version = RISK_ACCOUNTING_VERSION
        state.updated_at_utc = now

        # The superseded figures are logged rather than discarded silently: this
        # command overwrites the only persisted copy of the kill-switch inputs.
        logger.warning(
            f"Risk baseline recorded at accounting version {RISK_ACCOUNTING_VERSION}: "
            f"starting_equity={starting_equity} current_equity={equity} (force={force}). "
            f"Superseded values: {previous}",
            extra={
                "event_type": "RISK_BASELINE_RECORDED",
                "version": RISK_ACCOUNTING_VERSION,
                "starting_equity": starting_equity,
                "current_equity": equity,
                "force": force,
                "note": note,
                "superseded": previous,
            },
        )

    await db_write(write)


async def read_risk_baseline() -> dict[str, Any] | None:
    """Return the persisted baseline figures, or None when no row exists."""

    async def read(session: Any) -> dict[str, Any] | None:
        state = (await session.execute(select(RiskState).where(RiskState.id == RISK_STATE_ID))).scalars().first()
        if state is None:
            return None
        return {
            "accounting_version": state.accounting_version,
            "starting_equity": state.starting_equity,
            "current_equity": state.current_equity,
            "peak_equity": state.peak_equity,
            "current_daily_loss": state.current_daily_loss,
            "open_positions_count": state.open_positions_count,
            "updated_at_utc": state.updated_at_utc,
        }

    return await db_write(read)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Record a verified Orion risk-state baseline (one-time migration).")
    parser.add_argument("--starting-equity", type=float, required=True, help="Verified Orion-only starting equity USD")
    parser.add_argument("--current-equity", type=float, default=None, help="Defaults to --starting-equity")
    parser.add_argument("--note", default="", help="Why this baseline is trusted (recorded in the log)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override the already-migrated / open-positions refusals. Erases live kill-switch inputs.",
    )
    args = parser.parse_args(argv)

    asyncio.run(
        record_risk_baseline(
            starting_equity=args.starting_equity,
            current_equity=args.current_equity,
            note=args.note,
            force=args.force,
        )
    )


if __name__ == "__main__":
    main()
