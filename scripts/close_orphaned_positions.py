#!/usr/bin/env python3
"""Close orphaned options positions on the shared Alpaca account.

CONTEXT — 2026-05-21 emergency

37 options positions worth $556K market value (-$476K unrealized P&L,
35/37 losing, all expiring 2026-05-22) were on the broker but had ZERO
matching records in Orion's `orders` table. Cause: 47 EXECUTE decisions
were made between 5/12 and 5/21, but the docker_execution container
was crash-looping (380 restarts in 24h), so individual orders made it
to Alpaca's books but the matching `orders` table row never persisted.
The attribution chain broke → Orion's exit pipeline correctly said
"not ours" → positions sat for days losing money.

WHAT THIS SCRIPT DOES

For every options position on the broker (OCC symbol pattern), submit
a marketable LIMIT SELL (LONG close) or LIMIT BUY (SHORT close):
  - price derived from the current mark (avg_entry → current_price)
    shifted aggressively to cross the spread
  - rounded to the options tick ($0.05 < $3, $0.10 ≥ $3)
  - tagged with `orion_orphan_close_<uuid>` client_order_id so the
    DB-attribution chain is restored going forward
  - one-shot — exits after attempting every position

USAGE

  # Dry run (lists what would be submitted, no orders sent)
  python -m scripts.close_orphaned_positions --dry-run

  # Live submit
  python -m scripts.close_orphaned_positions

  # Restrict to a specific underlying
  python -m scripts.close_orphaned_positions --ticker COIN

  # Skip positions worth less than $1000 (already expired/worthless)
  python -m scripts.close_orphaned_positions --min-value 1000

SAFETY

- Options market is CLOSED outside 9:30am-4:00pm ET. Submitting
  outside that window will get rejected with Alpaca 42210000.
- This script does NOT touch positions that look like non-OCC
  (e.g., BTCUSD, equity tickers) — only option-symbol pattern
  positions get processed.
- All orders are submitted as LIMIT, never market.
- The `orion_orphan_close_` prefix is distinct from `orion_` so
  re-runs are idempotent (orders persist; the second run sees them).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

# Allow `python scripts/close_orphaned_positions.py` to find the project.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


_OCC_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}\d{6}[CP]\d{8}$")


def is_option_symbol(symbol: str) -> bool:
    return bool(symbol and _OCC_RE.match(symbol))


def round_to_options_tick(price: float) -> float:
    if price <= 0:
        return 0.0
    tick = 0.10 if price >= 3.0 else 0.05
    return round(round(price / tick) * tick, 2)


def derive_limit_price(current_price: float, direction: str, aggression: float = 0.075) -> float:
    """Marketable limit: cross the spread by `aggression` fraction.

    LONG close (SELL) → below mark.
    SHORT close (BUY) → above mark.
    Rounded to options tick. Minimum $0.05 (Alpaca rejects below).
    """
    if current_price <= 0:
        return 0.0
    if direction == "SHORT":
        raw = current_price * (1 + aggression)
    else:
        raw = current_price * (1 - aggression)
    return max(0.05, round_to_options_tick(raw))


async def main(dry_run: bool, ticker_filter: str | None, min_value_usd: float) -> int:
    from orion.clients.gateway_trading_client import get_gateway_trading_client

    client = get_gateway_trading_client()
    raw_positions = await client.get_positions()
    if not raw_positions:
        print("no broker positions; nothing to close")
        return 0

    # Idempotency check — query existing open orders so a re-run doesn't
    # stack duplicate close attempts on the same symbol. The first call
    # tagged each order with `orion_orphan_close_<uuid>`; if any of
    # those are still NEW/PARTIALLY_FILLED, skip submitting another.
    # Codex review 2026-05-21 flagged the previous version as
    # not-actually-idempotent despite the docstring claiming so.
    symbols_with_open_close: set[str] = set()
    try:
        open_orders = await client.get_orders(status="open") if hasattr(client, "get_orders") else []
        if isinstance(open_orders, list):
            for o in open_orders:
                coid = o.get("client_order_id", "") or ""
                if coid.startswith("orion_orphan_close_"):
                    sym = o.get("symbol", "")
                    if sym:
                        symbols_with_open_close.add(sym)
        if symbols_with_open_close:
            print(f"# {len(symbols_with_open_close)} symbol(s) already have open orphan-close orders — will skip them")
    except Exception as exc:
        # If we can't enumerate open orders, fail closed — don't risk
        # duplicates by assuming "no opens exist."
        print(f"# WARN: open-order enumeration failed ({exc}); refusing to submit to avoid duplicates")
        if not dry_run:
            return 1

    # Filter to options + optional ticker + optional min_value
    candidates: list[dict[str, Any]] = []
    for p in raw_positions:
        sym = p.get("symbol", "")
        if not is_option_symbol(sym):
            continue
        if ticker_filter:
            # underlying is the leading letters before the YYMMDD
            m = re.match(r"^([A-Z]+)", sym)
            if not m or m.group(1) != ticker_filter.upper():
                continue
        market_value = abs(float(p.get("market_value", 0) or 0))
        if market_value < min_value_usd:
            continue
        candidates.append(p)

    if not candidates:
        print(f"no candidates (after filters: ticker={ticker_filter}, min_value=${min_value_usd})")
        return 0

    # Sort by market_value desc — biggest dollar exposure first.
    candidates.sort(key=lambda p: abs(float(p.get("market_value", 0) or 0)), reverse=True)

    print(f"{'symbol':<24} {'qty':>8} {'mark':>8} {'limit':>8} {'side':>5} {'mv':>10} {'action':>8}")
    print("-" * 80)

    sent = 0
    skipped = 0
    failed = 0

    for p in candidates:
        sym = p.get("symbol", "")
        qty = abs(float(p.get("qty", 0) or 0))
        mark = float(p.get("current_price", 0) or 0)
        market_value = abs(float(p.get("market_value", 0) or 0))
        signed_qty = float(p.get("qty", 0) or 0)
        # If qty is negative, we're SHORT and need to BUY back. Otherwise LONG → SELL.
        direction = "SHORT" if signed_qty < 0 else "LONG"
        close_side = "buy" if direction == "SHORT" else "sell"

        if sym in symbols_with_open_close:
            print(
                f"{sym:<24} {qty:>8.0f} {mark:>8.2f} {'-':>8} {close_side:>5} {market_value:>10.0f} "
                f"{'skip-open':>8}"
            )
            skipped += 1
            continue

        if qty <= 0 or mark <= 0:
            print(f"{sym:<24} {qty:>8.0f} {mark:>8.2f} {'-':>8} {close_side:>5} {market_value:>10.0f} {'skip-0':>8}")
            skipped += 1
            continue

        limit = derive_limit_price(mark, direction)
        if limit <= 0:
            print(f"{sym:<24} {qty:>8.0f} {mark:>8.2f} {limit:>8.2f} {close_side:>5} {market_value:>10.0f} {'skip-lim':>8}")
            skipped += 1
            continue

        client_order_id = f"orion_orphan_close_{uuid.uuid4()}"
        action = "DRY" if dry_run else "SUBMIT"
        print(f"{sym:<24} {qty:>8.0f} {mark:>8.2f} {limit:>8.2f} {close_side:>5} {market_value:>10.0f} {action:>8}")

        if dry_run:
            continue

        try:
            result = await client.create_order(
                symbol=sym,
                qty=qty,
                side=close_side,
                order_type="limit",
                limit_price=limit,
                time_in_force="day",
                client_order_id=client_order_id,
            )
            # Strict success check — money is at stake. A Gateway success
            # response MUST carry a broker_order_id (Alpaca always returns
            # one on accept) AND must not contain `error`. Anything else
            # (empty dict, `{"success": false}`, malformed body, etc.) is
            # treated as a failure so the operator can re-run with a
            # wider limit instead of assuming the order is on the books.
            # Codex review 2026-05-21 flagged the previous lax check
            # ("any response without 'error' = success") as critical.
            if not isinstance(result, dict):
                print(f"    FAIL: non-dict response: {result!r}")
                failed += 1
            elif "error" in result:
                print(f"    FAIL: {result['error']}")
                failed += 1
            elif not result.get("id"):
                # Alpaca always returns `id` on accept. No id ⇒ no order.
                print(f"    FAIL: no broker_id in response: {result!r}")
                failed += 1
            else:
                print(f"    OK: broker_id={result['id']}  status={result.get('status', '?')}")
                sent += 1
        except Exception as exc:
            print(f"    EXC: {exc}")
            failed += 1

    print("-" * 80)
    print(f"submitted: {sent}    skipped: {skipped}    failed: {failed}    total candidates: {len(candidates)}")
    return 0 if failed == 0 else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Close orphaned Orion options positions")
    p.add_argument("--dry-run", action="store_true", help="List but don't submit")
    p.add_argument("--ticker", help="Restrict to a single underlying (e.g. COIN)")
    p.add_argument(
        "--min-value",
        type=float,
        default=0.0,
        help="Skip positions with market_value below this $ amount (default: 0)",
    )
    return p.parse_args()


if __name__ == "__main__":
    os.environ.setdefault("DB_URL", "postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db")  # pragma: allowlist secret
    os.environ.setdefault("GATEWAY_URL", "http://localhost:8080")
    os.environ.setdefault("GATEWAY_API_KEY", "gw_orion_trading_key_55555")
    args = parse_args()
    print(f"# close_orphaned_positions  {datetime.now(UTC).isoformat()}")
    print(f"# dry_run={args.dry_run}  ticker={args.ticker}  min_value=${args.min_value}")
    rc = asyncio.run(main(args.dry_run, args.ticker, args.min_value))
    sys.exit(rc)
