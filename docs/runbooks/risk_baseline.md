# Risk Baseline

How to clear the fail-closed risk-baseline gate so Orion can admit orders again.

## What the gate is

`RiskManager` refuses every order (`check_order` returns `False`) when the persisted
`risk_state` row does not carry the current accounting version. You will see this in
the logs at startup:

```
RISK BASELINE UNVERIFIED — order admission blocked. Persisted risk_state carries
accounting_version=None, required 2 ...
```

and on each blocked candidate:

```
RISK REJECT: risk baseline unverified — persisted loss/equity may be pre-multiplier.
```

## Why it exists

Option realized P&L used to be folded into `current_daily_loss` and `current_equity`
**without the 100x contract multiplier**, so a real $3,879 loss was recorded as $38.79.
Those two columns are the daily-loss and drawdown kill-switch inputs, so any row written
before that fix understates them by 100x. The gate stops Orion trading against a
kill switch that cannot fire correctly.

Provenance is stored on the row itself (`risk_state.accounting_version`); `NULL` means
"written before the fix". `RiskManager` stamps the current version on every save, but
only while it is *not* gated — a gated process would otherwise relabel the legacy figures
it just loaded as verified.

## Clearing it

The correct equity **cannot** be derived from Orion's own tables: the `fills` ledger
contains closes with no recorded entry (59 contracts as of 2026-08-12), so a lot-book
replay is not trustworthy. A human has to supply the number.

Recording a baseline is a **quiesced** operation. The command refuses while `execution`
or `position_monitor` hold a fresh service lease, because a live service holds its own
in-memory copy of the ledger and would overwrite whatever you record.

```bash
launchctl bootout gui/$(id -u)/com.empire.orion.execution
```

```bash
launchctl bootout gui/$(id -u)/com.empire.orion.position-monitor
```

Verify both are stopped, then record the baseline (set `DB_URL` inline — a plain shell
otherwise reaches the homebrew Postgres on :5432, not the live DB on :5440):

```bash
cd /Users/jacobmcmillan/Empire/Orion && DB_URL="postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db" uv run python -m orion.execution.risk.baseline --starting-equity 100000 --note "verified against broker 2026-08-13"
```

Then restart the services:

```bash
launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution
```

```bash
launchctl kickstart -k gui/$(id -u)/com.empire.orion.position-monitor
```

Confirm the gate is down — the startup log should no longer contain
`RISK_BASELINE_UNVERIFIED`, and:

```bash
psql "postgresql://orion:orion_password@localhost:5440/orion_db" -c "select accounting_version, starting_equity, current_equity, current_daily_loss from risk_state;"
```

should show `accounting_version = 2`.

## Refusals you may hit

| Message | Meaning | Action |
|---|---|---|
| `risk-writing service(s) still live` | `execution` / `position_monitor` lease is fresher than 120s | Stop them, wait ~2 min, retry |
| `already on accounting version 2` | Nothing to migrate | This is **not** a kill-switch reset button — see below |
| `reports N open position(s)` | Rebaselining would discard the high-water mark those positions are measured against | Flatten or reconcile first |

## Do not use this to reset a tripped kill switch

The command zeroes `current_daily_loss` and resets `peak_equity`. Those are exactly the
inputs the daily-loss limit and drawdown kill switch read. Re-running it after a real
losing day would erase a live risk control, which is why it refuses once the row is
already on the current version. `--force` bypasses every refusal and should only be used
with a specific reason; it logs the superseded values at `WARNING`.

## Known residual risk

The quiescence check reads lease freshness at one instant and takes no lock. If the
execution service is started in the same moment the baseline is being recorded, it can
load the legacy row and later either have its save discarded (losing that fill from the
persisted daily loss) or, in a narrow transaction interleaving, write stale figures under
the new stamp. Follow the stop → record → start order above and this cannot occur.
Closing it fully would require an exclusive DB-backed maintenance lock that execution
startup consults before acquiring its own lease.
