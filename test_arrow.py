import pyarrow.dataset as ds
import pyarrow.parquet as pq

path = "/Volumes/heber/data/silver/feed=bars"

try:
    print("Test 1: pq.read_table(dir)")
    pq.read_table(path)
    print("Test 1 OK")
except Exception as e:
    print(f"Test 1 Error: {e}")

try:
    print("\nTest 2: pq.read_table(dir, ignore_prefixes=['._'])")
    pq.read_table(path, ignore_prefixes=["._"])
    print("Test 2 OK")
except Exception as e:
    print(f"Test 2 Error: {e}")

try:
    print("\nTest 3: ds.dataset(dir, ignore_prefixes=['._'])")
    ds.dataset(path, ignore_prefixes=["._"])
    print("Test 3 OK")
except Exception as e:
    print(f"Test 3 Error: {e}")
