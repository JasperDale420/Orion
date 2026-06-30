# Runbook — clean up `meta_label_features` `expiry` schema drift

**Status:** ops task, run/approve manually. Not urgent — Orion already reads these
partitions correctly (degraded) after the reader fix below. This cleanup returns
execution CPU to baseline and makes gold reads uniform again.

## Background

The Heber gold dataset `meta_label_features` has its `expiry` column written in
**three** different physical types across `dt=` partitions:

| type | when | written by |
|------|------|-----------|
| `string` | older (e.g. Feb 2026) | legacy writer |
| `int64` (YYYYMMDD, e.g. `20260918`) | late-May / June backfill runs | `EnrichmentBackfillScanner` (the bug) |
| `date32` | recent live | live watch writer (correct) |

Reading a window that spans more than one type raises
`ArrowNotImplementedError: Unsupported cast from int64 to date32` (or string↔date32).
On 2026-06-30 this made every candidate's ML feature read fail → blind scoring +
the execution loop pegged at ~230% CPU. See the incident notes in
`CHANGELOG.md`.

### What is already fixed (no action needed)

- **Orion reader** ([Orion#142](https://github.com/JasperDale420/Orion/pull/142), deployed): a mixed-type read now
  degrades to the file-wise reader instead of total-failing — Orion functions, but
  file-wise reads are heavier, which keeps execution CPU elevated until the
  partitions are uniform.
- **Heber writer** ([Heber#31](https://github.com/JasperDale420/Heber/pull/31)): the backfill scanner now writes
  `expiry` as `date32`, so **new** backfill partitions are correct.

### What remains (this runbook)

The **existing** int64/string partitions still on disk. As of 2026-06-30 the
cache (`~/.heber-cache/data/gold/dataset=meta_label_features`) had **14 int64
partitions**:

```
2026-05-28  2026-05-29  2026-06-01  2026-06-02  2026-06-03  2026-06-04
2026-06-05  2026-06-08  2026-06-09  2026-06-10  2026-06-11  2026-06-18
2026-06-22  2026-06-30
```

plus ~54 older `string` partitions. **Only the in-window ones matter for live
CPU**: Orion's gold reader and `heber-sync` use a **30-day window**, so partitions
older than ~30 days (the May ones, the Feb `string` ones) are not read live and
can be left alone unless you also want clean ML-training reads over history.

## Prerequisite

**Deploy the Heber writer fix ([Heber#31](https://github.com/JasperDale420/Heber/pull/31)) first.** Otherwise the
backfill scanner keeps emitting fresh int64 partitions and re-poisons whatever
you clean.

## Source vs. cache — fix the source

Do **not** edit `~/.heber-cache` directly. `orion_heber_sync` rsyncs gold from
Heber's source (`/heber-source/gold` in the sidecar) into the cache **with
`--delete`** every 60s, so:

- Editing only the cache → re-poisoned on the next sync.
- Fixing Heber's **source** → the fix (and the removal of old int64 files)
  propagates to the cache automatically within 60s.

Confirm the host path the sidecar treats as source:

```bash
docker inspect orion_heber_sync --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
# the mount whose destination is /heber-source is Heber's gold/silver root
```

## Detection (run before and after)

```bash
cd /Users/jacobmcmillan/Empire/Orion
uv run python - <<'PY'
import pyarrow.parquet as pq, glob, os
base = os.path.expanduser('~/.heber-cache/data/gold/dataset=meta_label_features')
bad = []
for f in sorted(glob.glob(f'{base}/**/dt=*/*.parquet', recursive=True)):
    t = str(pq.read_schema(f).field('expiry').type)
    if t != 'date32[day]':
        bad.append((f.split('dt=')[1][:10], t))
for dt, t in bad:
    print(f'{dt}  expiry={t}')
print(f'{len(bad)} non-date32 partitions')
PY
```

## Option A — normalize in place (recommended; preserves data)

Rewrite each non-`date32` partition's `expiry` to `date32`, atomically. Run
against **Heber's source gold root** (the `/heber-source` host path from above),
then let `heber-sync` propagate.

Run **dry-run first** (`--apply` to write). The script is below; review it before
running.

```bash
# DRY RUN — lists what would change, writes nothing
uv run python scripts/normalize_gold_expiry_to_date32.py \
  --root <HEBER_SOURCE_GOLD_PATH>/dataset=meta_label_features

# APPLY — atomic temp-write + replace; keeps a .bak per file
uv run python scripts/normalize_gold_expiry_to_date32.py \
  --root <HEBER_SOURCE_GOLD_PATH>/dataset=meta_label_features --apply
```

```python
# scripts/normalize_gold_expiry_to_date32.py
"""One-off: normalize a gold dataset's `expiry` column to Arrow date32.

Mixed int64 (YYYYMMDD) / string / date32 across dt= partitions breaks pyarrow
reads. This rewrites each non-date32 file's expiry to date32 (date semantics
preserved), atomically (temp file + os.replace), keeping a .bak. Dry-run unless
--apply. Scoped to one dataset root; never recurses outside it.
"""
import argparse, glob, os
from datetime import date, datetime
import pyarrow as pa, pyarrow.parquet as pq, pandas as pd


def _to_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(int(v)) if isinstance(v, (int, float)) else str(v).strip().replace("-", "")
    try:
        return datetime.strptime(s[:8], "%Y%m%d").date()
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="dataset=... root to scan")
    ap.add_argument("--apply", action="store_true", help="write changes (default dry-run)")
    args = ap.parse_args()

    files = sorted(glob.glob(f"{args.root}/**/dt=*/*.parquet", recursive=True))
    changed = 0
    for f in files:
        t = str(pq.read_schema(f).field("expiry").type)
        if t == "date32[day]":
            continue
        changed += 1
        print(f"{'APPLY' if args.apply else 'DRY '}  {f}  ({t} -> date32)")
        if not args.apply:
            continue
        df = pd.read_parquet(f)
        df["expiry"] = df["expiry"].map(_to_date)
        tmp = f + ".tmp"
        os.replace(f, f + ".bak")
        try:
            df.to_parquet(tmp, index=False, compression="snappy")
            # force date32 (object column of date -> pyarrow infers date32)
            assert str(pq.read_schema(tmp).field("expiry").type) == "date32[day]"
            os.replace(tmp, f)
        except Exception:
            os.replace(f + ".bak", f)  # roll back
            raise
    print(f"{changed} partitions {'rewritten' if args.apply else 'would change'}")


if __name__ == "__main__":
    main()
```

After apply, delete the `.bak` files once you've verified (below).

## Option B — delete the in-window int64 partitions (faster, loses data)

If the backfill enrichment for those days isn't worth preserving, just remove the
bad in-window partitions from the **source**; `heber-sync --delete` clears the
cache copies within 60s.

```bash
# from HEBER_SOURCE_GOLD_PATH/dataset=meta_label_features/...
for dt in 2026-06-01 2026-06-02 2026-06-03 2026-06-04 2026-06-05 \
          2026-06-08 2026-06-09 2026-06-10 2026-06-11 2026-06-18 \
          2026-06-22 2026-06-30; do
  rm -rf <HEBER_SOURCE_GOLD_PATH>/dataset=meta_label_features/project=watch/version=v1/dt=$dt
done
```

Trade-off: ML training that reads those days loses the enriched rows. Live
scoring is unaffected (it doesn't depend on a specific day's backfill).

## Option C — do nothing

The reader fix already keeps Orion functional. CPU stays modestly elevated
(file-wise reads). Acceptable if you don't want to touch historical data.

## Verification

1. Re-run the **Detection** snippet → `0 non-date32 partitions` (within the
   window for Option A; the listed dts gone for Option B).
2. Confirm `heber-sync` propagated: re-run detection against `~/.heber-cache`
   after ~60–120s.
3. Confirm Orion recovered:
   ```bash
   ps -p "$(launchctl list | awk '/orion.execution/{print $1}')" -o %cpu   # back toward ~50-100%, not 230
   grep -c heber_reader_filewise_fallback logs/execution_native.log         # stops growing
   grep -c 'Unsupported cast' logs/execution_native.log                     # stops growing
   ```

## Rollback

Option A keeps a `.bak` next to each rewritten file — restore with
`for b in **/*.bak; do mv "$b" "${b%.bak}"; done`. Option B is destructive;
restore from a Heber backup if needed.
