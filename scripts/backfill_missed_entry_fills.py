"""One-shot: backfill FillRecords for Orion entry orders that poll_fills' 200-row
`get_orders` window aged out before processing (last live fill 2026-06-10).

Without a `fills` row, GatewayPositionAdapter attributes 0 of N positions to
Orion (it keys off `fills.ticker`), so the position monitor never tracks — or
exits — these positions. This recovers each missed entry fill via the broker
(the same `client.get_order` path `_recover_missed_fill` uses) and writes the
FillRecord + ProcessedFill marker through the system's own persistence helpers.

Idempotent (persist_fill_record upserts by broker_order_id; ProcessedFill guards
re-processing). Places NO orders. Run --dry-run first.

    set -a; . .env; set +a
    export DB_URL="postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db"  # pragma: allowlist secret
    export DATA_GATEWAY_URL="http://localhost:8080" GATEWAY_URL="http://localhost:8080"
    uv run python scripts/backfill_missed_entry_fills.py --dry-run
    uv run python scripts/backfill_missed_entry_fills.py
"""

import asyncio
import sys

from sqlalchemy import text

from orion.clients.gateway_trading_client import GatewayTradingClient
from orion.execution.persistence import mark_fill_processed, persist_fill_record
from orion.shared.db_utils import db_query

# Entry orders that filled at the broker but never got a fills row, keyed off
# CURRENTLY-OPEN positions so we attribute exactly what Orion still holds. The
# DB status is unreliable here: poll_fills' 200-row miss left entries marked
# `filled` (reconciled) OR `canceled` (the cancel "succeeded" in our DB after
# the fill already landed at the broker) — both leave the position attributable
# only by a fresh broker get_order. REJECTED never reached the broker (no
# broker_order_id / pre-submit failure), so it's excluded; the broker get_order
# then skips anything with filled_qty=0 (a genuine cancel). orders.raw_json is
# the pre-fill snapshot, so the OCC symbol comes from there but the fill PRICE
# comes from the broker.
ORPHAN_SQL = text(
    """
    WITH latest AS (SELECT max(snapshot_ts_utc) AS t FROM positions_snapshots),
    open_syms AS (
        SELECT DISTINCT ticker FROM positions_snapshots, latest
        WHERE snapshot_ts_utc = latest.t AND qty != 0
    )
    SELECT o.broker_order_id, o.client_order_id, o.raw_json->>'symbol' AS occ, o.side
    FROM orders o
    JOIN open_syms s ON o.raw_json->>'symbol' = s.ticker
    WHERE o.client_order_id LIKE 'orion_%'
      AND o.side = 'buy'
      AND o.status <> 'REJECTED'
      AND o.broker_order_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM fills f WHERE f.broker_order_id = o.broker_order_id)
    ORDER BY o.created_at_utc
    """
)


async def _fetch_orphans() -> list[dict]:
    async def q(session):
        rows = (await session.execute(ORPHAN_SQL)).all()
        return [{"boid": r[0], "coid": r[1], "occ": r[2], "side": r[3]} for r in rows]

    return await db_query(q)


async def _get_order_resilient(client, boid: str, attempts: int = 4) -> dict | None:
    """get_order with backoff — the shared Data-Gateway flaps (~45min cadence),
    so a blip mid-run must not strand an otherwise-recoverable order. Returns the
    order dict on success, or None after exhausting retries."""
    delay = 0.5
    for _ in range(attempts):
        try:
            order = await client.get_order(boid)
        except Exception:
            order = None
        if isinstance(order, dict) and "error" not in order:
            return order
        await asyncio.sleep(delay)
        delay *= 2
    return None


async def main(dry_run: bool) -> None:
    client = GatewayTradingClient()
    orphans = await _fetch_orphans()
    print(f"orphan entry orders missing a fill row: {len(orphans)}  (dry_run={dry_run})")

    recovered = skipped = failed = 0
    for i, o in enumerate(orphans, 1):
        boid = o["boid"]
        await asyncio.sleep(0.05)  # gentle throttle on the shared gateway
        order = await _get_order_resilient(client, boid)
        if order is None:
            print(f"[{i:3}] {boid} unrecoverable after retries (404/legacy-unowned or gateway down)")
            failed += 1
            continue

        fq = float(order.get("filled_qty") or 0)
        if fq <= 0:
            print(f"[{i:3}] {boid} filled_qty=0 (status={order.get('status')}) — skip")
            skipped += 1
            continue

        occ = order.get("symbol") or o["occ"]
        price = order.get("filled_avg_price")
        if dry_run:
            print(f"[{i:3}] WOULD write {occ:22} qty={fq:g} @ {price}")
            recovered += 1
            continue

        fill = {
            "id": boid,
            "symbol": occ,
            "client_order_id": o["coid"],  # canonical orion_ form (our DB)
            "filled_qty": fq,
            "filled_avg_price": price,
            "side": order.get("side") or o["side"],
            "filled_at": order.get("filled_at"),
        }
        await persist_fill_record(fill)
        # Marker must match FillProcessor's f"{order_id}:{filled_qty}" so the live
        # poller's idempotency check recognises it if it ever re-sees the order.
        await mark_fill_processed(f"{boid}:{fq}", client_oid=o["coid"], ticker=occ, qty=fq)
        recovered += 1
        if i % 25 == 0:
            print(f"    ... {i}/{len(orphans)}")

    print(f"DONE  recovered={recovered}  skipped(0qty)={skipped}  failed={failed}")
    aclose = getattr(client, "aclose", None)
    if aclose:
        await aclose()


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
