from pathlib import Path

import pyarrow.parquet as pq

# specific file:
path = Path("/Volumes/heber/data/silver/feed=bars")
files = [str(f) for f in sorted(path.rglob("*.parquet")) if not f.name.startswith("._")]
print(f"Testing list with {len(files)} files...")

try:
    # Mimic line 451
    table = pq.read_table(files, filters=[("instrument_type", "=", "equity")], partitioning=None)
    print("Test 9: filters ok")
except Exception as e:
    print(f"Test 9 Error: {type(e).__name__}: {e}")
