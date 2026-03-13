from pathlib import Path

import pyarrow.parquet as pq

# specific file:
path = Path("/Volumes/heber/data/silver/feed=bars/instrument_type=equity/dt=2025-01-29")
files = [str(f) for f in path.glob("*.parquet") if not f.name.startswith("._")][:3]

assert len(files) > 0, "No files found"

print("Test 7: list of 1 file, filters, partitioning=None")
try:
    table = pq.read_table(files, filters=[("instrument_type", "=", "equity")], partitioning=None)
    print("Test 7 OK, rows:", table.num_rows)
except Exception as e:
    print(f"Test 7 Error: {type(e).__name__} {e}")
