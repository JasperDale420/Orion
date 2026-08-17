# Circuit Breaker Runbook

## Overview

The circuit breaker is a global kill switch backed by the `system_status` table,
under the key `GLOBAL_CIRCUIT_BREAKER`.

**It halts new entries. It never blocks an exit.** A halted system still has to
be able to get flat, so no close/exit path consults it. When OPEN:

- `preflight_live_signal` SKIPs each candidate with `Circuit Breaker Open`;
- `ExecutionEngine._pre_flight_checks` blocks at the engine
  (`EXECUTION BLOCKED: Circuit breaker is OPEN`);
- `close_position`, the position monitor's exit loop, and the execution loop's
  rule-based exit sweep all keep running.

**There is no disable switch.** `ORION_GLOBAL_CIRCUIT_BREAKER_ENABLED` used to
make `is_open()` return False on an OPEN row. It disabled nothing — the engine
read the row directly and blocked entries anyway — while emitting a WARNING per
call (2,287 on 2026-08-14). It was removed; an OPEN row always halts entries.

**It latches.** Nothing closes it automatically — once open it stays open until an
operator resets it, across restarts. That is deliberate: the conditions that trip
it are ones a human should look at. It also means a breaker left open silently
disables new entries for as long as nobody notices.

Schema: `key`, `status`, `details`, `last_updated_utc`.

## Check Circuit Breaker Status

```sql
SELECT key, status, details, last_updated_utc FROM system_status WHERE key = 'GLOBAL_CIRCUIT_BREAKER';
```

Expected when CLOSED (normal):

```
key: GLOBAL_CIRCUIT_BREAKER
status: CLOSED
details: Reset by system/operator
last_updated_utc: <timestamp>
```

No row at all also means nominal — the breaker has never tripped.

Note the DB is the docker TimescaleDB on port **5440**, not the homebrew Postgres
on 5432, and native services connect as `orion` with a password. Take the exact
URL from `scripts/run_ingestion_native.sh` (it pins `DB_URL`); a plain
`postgresql://orion@localhost:5440/...` fails password auth.

## Close Circuit Breaker (Resume Operations)

Only after confirming the condition that tripped it has cleared.

### Via Python (preferred — this is the path that works)

```bash
DB_URL="<the DB_URL pinned in scripts/run_ingestion_native.sh>" uv run python -c "
import asyncio, os
from orion.storage import db
async def main():
    db.configure_db(os.environ['DB_URL'], echo=False)
    from orion.core.circuit_breaker import CircuitBreaker
    print(await CircuitBreaker().get_state())
    await CircuitBreaker().close()
    print(await CircuitBreaker().get_state())
asyncio.run(main())"
```

Drop the `close()` line to read the state without changing it.

### Not `scripts/reset_circuit_breaker.py`

That script imports `psycopg2`, which is not a declared dependency and is not
installed in the venv, so it fails at import with `ModuleNotFoundError`. Use the
Python path above until the script is fixed or removed.

### Via admin API

```
POST /admin/circuit-breaker/reset?reason=<why>
```

## Open Circuit Breaker (Emergency Stop)

```python
from orion.core.circuit_breaker import CircuitBreaker

await CircuitBreaker().open("Manual halt - investigating issue X")
```

Or `POST /admin/circuit-breaker/open?reason=<why>`.

Note: `open()` keeps the FIRST reason. If the breaker is already OPEN, a second
open is a no-op and `details` still shows the original cause — so `details` is not
a reliable record of every condition that has since tripped.

## Alerting

The dead-man watchdog (`orion.jobs.deadman_watchdog`, launchd one-shot every
5 minutes) alerts to Discord whenever the breaker is OPEN, with the cause and how
long it has been open. This is **not** market-hours gated — a breaker latched
overnight blocks the whole next session, so it pages before the bell. Dedupe key
`deadman_circuit_breaker`, 15-minute suppression window.

## When to Open Circuit Breaker

- API rate limits exhausted
- Unusual market conditions
- Suspected data corruption
- During maintenance windows
- After detecting anomalous trading behavior

## Known false-positive class (fixed 2026-08-14)

`HealthMonitor` stamps its heartbeat at construction, but `IngestionService.initialize()`
then spends several minutes hydrating the universe and feature-engine history. The
first `check_health()` — reached on the market-closed path via
`_check_overnight_sleep -> _maybe_run_eod` before `_run_cycle` bumps the heartbeat —
measured that startup window as a liveness gap and tripped the breaker on every
restart (`Heartbeat missing for 425.76s > 60.0s`, 2026-08-13, 21h latched, a full
session of blocked orders). `run()` now starts the heartbeat clock on loop entry.

If you see a `Heartbeat missing` cause on a breaker that opened within ~10 minutes
of a restart, check that this fix is deployed before assuming a real stall.

## Known gap: a wedged ingestion loop does not trip the breaker

The `Heartbeat missing` guard cannot catch the case it sounds like it catches.
`IngestionService.run()` refreshes the heartbeat immediately before checking it,
so a cycle that runs long is made fresh before evaluation, and a cycle that hangs
reaches neither. This predates the 2026-08-14 fix and is unchanged by it.

What actually stops trading when ingestion wedges:

- `ExecutionEngine._check_system_health` blocks new entries once the
  `global_health` row is older than `ingestion_heartbeat_max_age` (600s).
- The dead-man watchdog alerts once ingestion misses its liveness cadence
  budget (300s).

Neither latches the breaker, so both recover on their own. Closing this gap —
having a genuine stall latch the kill switch — would be a deliberate design
change and needs its own review; a latching halt on a merely slow cycle is the
same class of self-inflicted outage this runbook section documents.
