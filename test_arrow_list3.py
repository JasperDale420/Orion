from pathlib import Path

import pyarrow.parquet as pq

# specific file:
path = Path("/Volumes/heber/data/silver/feed=flow_alerts")
files = [str(f) for f in path.rglob("*.parquet") if not f.name.startswith("._")]
print(f"Testing list with {len(files)} files...")

try:
    table = pq.read_table(files, partitioning=None)
    print("Test 8 OK, rows:", table.num_rows)
except Exception as e:
    print(f"Test 8 Error: {type(e).__name__} {e}")
