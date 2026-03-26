from pathlib import Path

path = Path("/Volumes/heber/data/silver/feed=bars")
try:
    print("Testing rglob...")
    valid_files = [str(f) for f in sorted(path.rglob("*.parquet")) if not f.name.startswith("._")]
    print(f"rglob OK, found {len(valid_files)} files")
except Exception as e:
    print(f"rglob Error: {type(e).__name__} {e}")
