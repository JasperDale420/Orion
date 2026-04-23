# RCA: Shared-Alpaca position attribution false-positive

**Date:** 2026-04-22
**Severity:** Medium — incorrect risk aggregation, no incorrect trades placed
**Affected service:** `orion_execution`

## Symptoms
- On startup the execution service logged `GATEWAY_POSITIONS_SYNC` with `open_positions: 15, skipped_non_orion: 0, total_account_positions: 15` — every shared-account position treated as Orion-owned.
- Shared Alpaca paper account (`PA32RTH85PT4`) held 15 positions: stocks (ALMU, HIMX, KOPN, LWLG, etc.) plus one AMD put option. Orion is options-only and had **zero** orders in the `orders` table all-time. None of those positions could possibly be Orion's.
- Downstream impact: risk manager's `ticker_exposures` and `positions` map were populated with other systems' positions. This would inflate Orion's perceived notional / concentration exposure and make sizing decisions incorrect.

## Root cause
File: [`src/orion/execution/execution_engine.py`](../src/orion/execution/execution_engine.py)

Original filter (around line 241):
```python
for p in positions:
    symbol = p.get("symbol", "")
    # Only load positions that Orion has traded
    if orion_tickers and symbol not in orion_tickers:
        skipped += 1
        continue
    …  # load position into risk_manager
```

`_fetch_orion_tickers()` returns `set[str] | None`. `None` is the error sentinel (handled above the loop with an early return). When Orion has never placed an order, the method returns an **empty** set — which is the correct semantic answer ("no tickers are Orion-owned") but is **falsy** in Python.

The guard `if orion_tickers and …` short-circuits on a falsy left-hand side, so the `symbol not in orion_tickers` check never runs and every position falls through to the "load into risk_manager" branch. Empty-set-means-skip-all degenerated into empty-set-means-accept-all.

## Evidence
```sql
orion_db=# SELECT COUNT(*), MIN(created_at_utc), MAX(created_at_utc) FROM orders;
 count | min | max
-------+-----+-----
     0 |     |
```

Log line pre-fix:
```
{"event_type": "GATEWAY_POSITIONS_SYNC",
 "open_positions": 15, "skipped_non_orion": 0, "total_account_positions": 15}
```

Log line post-fix:
```
{"event_type": "GATEWAY_POSITIONS_SYNC",
 "open_positions": 0, "skipped_non_orion": 15, "total_account_positions": 15}
```

## Fix
Remove the `orion_tickers and` guard. `None` is already handled earlier as an abort case, so the remaining cases are:

- empty set → skip every position (`symbol not in set()` is always true) ✓
- non-empty set → skip positions whose ticker isn't in it ✓

```python
for p in positions:
    symbol = p.get("symbol", "")
    # Only load positions Orion has ever placed an order for.
    # Empty orion_tickers means Orion owns no positions at all —
    # skip everything (None is the error sentinel, handled above).
    if symbol not in orion_tickers:
        skipped += 1
        continue
```

## Verification
Rebuilt `orion_execution` and confirmed `GATEWAY_POSITIONS_SYNC` log reports `open_positions: 0, skipped_non_orion: 15, total_account_positions: 15`. When Orion does place its first order, `_fetch_orion_tickers` will include that ticker and the corresponding account position will be admitted into the risk map as expected.

## Why it wasn't caught sooner
Bug was dormant until the orders table was empty for a sustained period. If Orion had any historical orders, the set would be non-empty (truthy) and the filter would work. Recent fixes to the side-enum bug (b9e3d74, de42ad0) and upstream pipeline issues (ef98a5e, a818d4f) stopped all order submissions for long enough that the orders table drained, exposing the latent fail-open.

## Follow-ups (not done in this PR)
- Consider adding a `system` column filter when orders are seeded from external sources — currently `_fetch_orion_tickers` relies solely on the `orion_` prefix on `client_order_id`. The `system` column exists on `OrderRecord` and is defaulted to `"orion"`, but it isn't used by this query.
- Add a unit test covering empty-set, non-empty-set, and None return values from `_fetch_orion_tickers` against the position-load loop.
