"""One-shot: backfill local orders/fills/journal from Alpaca for orion-attributed
orders since the sync gap opened. Idempotent (ProcessedFill marker).

Closes the historical view of the May 14 trading session where poll_fills had
no order/fill polling and 94 orion_-prefixed orders sat at pending_new locally
despite filling at Alpaca.

Usage:
    DB_URL=... GATEWAY_URL=... GATEWAY_API_KEY=... \
      uv run python scripts/backfill_fills_from_alpaca.py --dry-run \
      --since 2026-05-14T00:00:00+00:00
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from typing import Any

from orion.clients.gateway_trading_client import GatewayTradingClient
from orion.execution.attribution import ORDER_ID_PREFIX
from orion.execution.fill_processor import FillProcessor
from orion.execution.persistence import (
    mark_fill_processed,
    persist_fill_record,
    persist_order_status_update,
)
from orion.execution.risk.manager import RiskManager
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.scripts.backfill_fills")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


async def main(since_iso: str, dry_run: bool, force: bool) -> None:
    since = _parse_iso(since_iso)
    if since is None:
        raise SystemExit("--since must be a valid ISO-8601 datetime")

    client = GatewayTradingClient()
    processor = FillProcessor(ledger=None)
    risk = RiskManager()
    await risk.initialize()

    try:
        orders = await client.get_orders(status="all", limit=500)
    finally:
        # close the underlying httpx client only after we're fully done below
        pass

    orion_orders: list[dict[str, Any]] = []
    for o in orders:
        coid = str(o.get("client_order_id", "") or "")
        if not coid.startswith(ORDER_ID_PREFIX):
            continue
        submitted = _parse_iso(o.get("submitted_at") or o.get("created_at"))
        if submitted is None or submitted < since:
            continue
        orion_orders.append(o)

    scanned = len(orion_orders)
    filled = sum(1 for o in orion_orders if float(o.get("filled_qty") or 0) > 0)
    logger.info(
        f"Scanning {scanned} orion orders since {since_iso} ({filled} have fills to backfill)"
    )

    if dry_run:
        logger.info("DRY RUN — no DB writes")
        await client.close()
        return

    status_updates = 0
    fill_writes = 0

    async def _noop_remove_pending(_oid: str) -> None:
        return None

    for o in orion_orders:
        broker_id = o.get("id") or o.get("broker_order_id")
        if not broker_id:
            continue

        # Status update
        status = o.get("status")
        if status:
            try:
                n = await persist_order_status_update(
                    broker_order_id=str(broker_id),
                    status=str(status),
                    filled_qty=o.get("filled_qty"),
                    filled_avg_price=o.get("filled_avg_price"),
                )
                status_updates += int(n or 0)
            except Exception as e:
                logger.warning(f"Status update failed for {broker_id}: {e}")

        # Fill write — only for filled (or partially filled) orders.
        # In --force mode we bypass FillProcessor (skipping its ProcessedFill
        # idempotency check) to recover from the historical bug where
        # persist_fill_record silently failed due to string-typed `filled_at`
        # timestamps — that left orphaned ProcessedFill markers with no
        # matching fills row. We still write the ProcessedFill marker after
        # persist so re-runs (force or not) remain idempotent. Without
        # --force the normal dedup applies.
        if float(o.get("filled_qty") or 0) > 0:
            try:
                if force:
                    await persist_fill_record(o)
                    # Write the ProcessedFill marker so a future run without
                    # --force won't re-fire FillProcessor.process_single_fill
                    # (which would double-credit risk state and duplicate
                    # ledger entries). The marker is what is_fill_processed
                    # checks in the non-force path. See fill_processor.py:66.
                    filled_qty = float(o.get("filled_qty") or 0)
                    fill_marker = f"{broker_id}:{filled_qty}"
                    client_oid = str(o.get("client_order_id", "") or "") or None
                    ticker = o.get("symbol")
                    try:
                        await mark_fill_processed(
                            fill_marker,
                            client_oid=client_oid,
                            ticker=ticker,
                            qty=filled_qty,
                        )
                    except Exception as mark_exc:
                        logger.warning(
                            f"Force-path mark_fill_processed failed for {broker_id}: {mark_exc}"
                        )
                else:
                    await processor.process_single_fill(o, risk, _noop_remove_pending)
                fill_writes += 1
            except Exception as e:
                logger.warning(f"Fill write failed for {broker_id}: {e}")

    logger.info(
        f"Backfill complete: scanned={scanned} status_updates={status_updates} "
        f"fill_writes={fill_writes}"
    )

    await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--since",
        default="2026-05-14T00:00:00+00:00",
        help="ISO-8601 cutoff — only backfill orders submitted at or after this.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan + count but do not write to the DB.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass FillProcessor + ProcessedFill dedup and write fills directly "
            "via persist_fill_record (upsert by broker_order_id). After persist, "
            "the ProcessedFill marker is written so subsequent runs (with or "
            "without --force) are idempotent and safe. NOTE: --force does NOT "
            "update RiskManager post-fill state and does NOT write empire-core "
            "ledger entries — use only when those have already been updated "
            "out-of-band, or when those side effects don't matter for the "
            "backfill case (e.g. recovering historical fills for orders that "
            "have already closed)."
        ),
    )
    args = parser.parse_args()
    asyncio.run(main(args.since, args.dry_run, args.force))
