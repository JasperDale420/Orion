from pathlib import Path

import pyarrow.parquet as pq

path = Path("/Volumes/heber/data/silver/feed=bars")
valid_files = [str(f) for f in sorted(path.rglob("*.parquet")) if not f.name.startswith("._")][:10]

print(f"Found {len(valid_files)} valid files. Testing passing list of files...")
try:
    print("\nTest 4: pq.read_table(list_of_files)")
    pq.read_table(valid_files)
    print("Test 4 OK")
except Exception as e:
    print(f"Test 4 Error: {type(e).__name__} {e}")

try:
    print("\nTest 5: pq.read_table(list_of_files, filters=[('instrument_type', '=', 'equity')])")
    pq.read_table(valid_files, filters=[("instrument_type", "=", "equity")])
    print("Test 5 OK")
except Exception as e:
    print(f"Test 5 Error: {type(e).__name__} {e}")

try:
    print("\nTest 6: pq._read_table_with_partitioning=None")
    pq.read_table(valid_files, partitioning=None)
    print("Test 6 OK")
except Exception as e:
    print(f"Test 6 Error: {type(e).__name__} {e}")
