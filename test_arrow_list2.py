from pathlib import Path

import pyarrow.parquet as pq

path = Path("/Volumes/heber/data/silver/feed=bars")
valid_files = [str(f) for f in sorted(path.rglob("*.parquet")) if not f.name.startswith("._")][:3]

try:
    print("\nTest 7: list, filters, partitioning=None")
    table = pq.read_table(valid_files, filters=[("instrument_type", "=", "equity")], partitioning=None)
    print("Test 7 OK, rows:", table.num_rows)
except Exception as e:
    print(f"Test 7 Error: {type(e).__name__} {e}")
