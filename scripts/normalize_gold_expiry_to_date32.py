#!/usr/bin/env python
"""One-off: normalize a gold dataset's ``expiry`` column to Arrow ``date32``.

Mixed int64 (YYYYMMDD) / string / date32 across ``dt=`` partitions breaks pyarrow
reads (``Unsupported cast from int64 to date32``). This rewrites each non-date32
file's ``expiry`` to ``date32`` (date semantics preserved), atomically (temp file
+ ``os.replace``), keeping a ``.bak`` per file. Dry-run unless ``--apply``. Scoped
to a single ``dataset=...`` root; never recurses outside it.

See docs/runbooks/meta_label_features_expiry_cleanup.md for the full procedure.
"""

from __future__ import annotations

import argparse
import glob
import os
from datetime import date, datetime

import pandas as pd
import pyarrow.parquet as pq


def _to_date(v: object) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(int(v)) if isinstance(v, int | float) else str(v).strip().replace("-", "")
    try:
        return datetime.strptime(s[:8], "%Y%m%d").date()
    except ValueError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, help="a dataset=... root to scan")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
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
            # object column of datetime.date -> pyarrow infers date32
            written = str(pq.read_schema(tmp).field("expiry").type)
            if written != "date32[day]":
                raise RuntimeError(f"expiry normalized to {written}, expected date32[day]: {f}")
            os.replace(tmp, f)
        except Exception:
            os.replace(f + ".bak", f)  # roll back
            raise
    print(f"{changed} partitions {'rewritten' if args.apply else 'would change'}")


if __name__ == "__main__":
    main()
