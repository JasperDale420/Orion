# Pre-baseline alembic migrations (archived 2026-06-11)

These are the 34 incremental migration files that made up Orion's alembic
history before it was squashed into a single baseline migration
(`baseline_2026_06_11`, in `alembic/versions/`).

## Why they were archived

The chain was **incremental-only**: migration `0001` ALTERed an
assumed-pre-existing `bronze_events` table, and no migration in the chain
created the core tables. Fresh databases were therefore never built by
`alembic upgrade head` — they bootstrapped via `init_db()`
(`Base.metadata.create_all` + `CREATE EXTENSION vector`) followed by
`alembic stamp head`. The squash makes fresh databases migration-driven again.

## Old head

The single head at archive time was the merge revision:

```
e9ffae1b54c5  (merge of 2c4f1a8b9d3e + 72d3429dcac5)
```

All existing databases stamped at `e9ffae1b54c5` were re-stamped to
`baseline_2026_06_11` as part of the squash (stamp only — no schema mutation).

## Seed data note

`0026_seed_initial_solvers.py` INSERTed 5 paper-stage solvers + metrics. The
baseline migration is **schema-only**, matching the behavior of the
`create_all`-based bootstrap that fresh DBs (including CI) already used — that
path never applied these seeds either. The live local DB already contains the
seeded rows. If a fresh DB needs the starter solvers, run the seed step
separately; it is intentionally not part of the schema baseline.

## Restoring history (if ever needed)

These files are kept for forensic/reference purposes only. They are no longer
part of the active migration chain and must not be moved back into
`alembic/versions/` — doing so would reintroduce the multi-head/incremental
problem the baseline was created to fix.

## Remedy for a DB still stamped at the old head

Any database whose `alembic_version` still reads `e9ffae1b54c5` (or any
archived revision) will fail `alembic upgrade head` with "Can't locate
revision". Fix (stamp only — no schema change):

    uv run alembic stamp baseline_2026_06_11 --purge

The only known production DB (localhost:5440) was re-stamped on 2026-06-11.

Note: `silver_uw_alerts` (created by archived 0003/0006, zero code references,
empty) was dropped from the live DB on 2026-06-11 to eliminate live-vs-fresh
divergence; alembic/env.py also gained an include_object guard so autogenerate
can never propose drops of database-only legacy tables.
