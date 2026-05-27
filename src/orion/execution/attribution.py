"""Centralized attribution helpers for the shared Alpaca paper account.

Multiple Empire trading systems (3Roses, Cerberus, Kairos, Orbit, WhaleHunter,
Orion) trade through the same paper account via Data-Gateway. Orion identifies
its own orders, fills, and positions by a `client_order_id` prefix.

All callers MUST go through this module — keeping the prefix string and
default-deny semantics in one place is what prevents the kind of bug fixed in
2026-04 where a truthiness check on the empty-set return collapsed
"Orion has no orders" into "skip the filter entirely" and let every shared-
account position count toward Orion's risk.

`is_orion_owned(None)` and `is_orion_owned("")` both return False — never
attribute an order to Orion that lacks a prefix.
"""

from __future__ import annotations

import re
import uuid

# Prefix for all Orion order IDs — used to identify Orion's positions
# in the shared Alpaca paper account.
ORDER_ID_PREFIX = "orion_"

# OCC option-symbol regex: 1-6 alphanumeric ticker + 6-digit date (YYMMDD)
# + 'C' or 'P' + 8-digit strike (8 digits, strike × 1000). Example:
# `NVDA260522C00250000` = NVDA 2026-05-22 $250 Call.
_OCC_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}\d{6}[CP]\d{8}$")


def is_occ_option_symbol(symbol: str | None) -> bool:
    """Return True iff `symbol` matches the OCC option-symbol pattern.

    Used to branch between equity and option code paths
    (e.g. ``ExecutionEngine.close_position`` uses limit orders for
    options and may use market for equity). Centralized here so the
    regex stays consistent across the codebase — moved from
    `position_monitor.py` so `execution_engine.py` can use it without
    risking a circular import.
    """
    return bool(symbol and _OCC_SYMBOL_RE.match(symbol))


# `occ_underlying` (introduced briefly in commit 39174f8) was removed
# the same day after codex review. Its only use was the shared-account
# attribution filter, but underlying-level matching is unsafe — a
# sibling system's same-underlying option (different OCC contract)
# would be incorrectly admitted as Orion-owned. Attribution now sources
# from `fills.ticker`, which stores the full OCC contract for options,
# and matches broker positions by exact symbol. If a legitimate future
# use case for extracting the underlying arises, add the helper back
# WITHOUT routing the attribution filter through it.


def mint_orion_order_id() -> str:
    """Generate a new Orion-attributed `client_order_id`.

    Format: `orion_{uuid4}`. The prefix is what every downstream attribution
    check matches on, so all order-emit paths must use this helper instead of
    constructing the id inline.
    """
    return f"{ORDER_ID_PREFIX}{uuid.uuid4()}"


def is_orion_owned(client_order_id: str | None) -> bool:
    """Return True iff `client_order_id` was minted by Orion.

    Default-deny: an absent or empty `client_order_id` is NOT considered
    Orion-owned, even though the empty string trivially "doesn't start with
    orion_". This matches the contract every callsite needs and removes the
    truthiness foot-gun that produced the 2026-04 shared-account attribution
    bug.
    """
    if not client_order_id:
        return False
    return client_order_id.startswith(ORDER_ID_PREFIX)


def orion_order_id_sql_pattern() -> str:
    """Return the SQL `LIKE` pattern that matches every Orion-minted id.

    Used in DB filters such as
    `OrderRecord.client_order_id.like(orion_order_id_sql_pattern())`.
    Centralizing the pattern alongside the prefix keeps the SQL and the
    Python-level check in lock-step if the prefix ever changes.
    """
    return f"{ORDER_ID_PREFIX}%"
