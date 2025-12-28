import time
import pandas as pd
from datetime import datetime, timezone
import uuid
import logging

# Mock BronzeEvent class to avoid importing the whole sqlalchemy model if not needed,
# or we can just use a dict or simple class since the LakehouseWriter converts it to dict anyway.
# But LakehouseWriter expects objects with attributes.
class MockBronzeEvent:
    def __init__(self, i):
        self.event_id = str(uuid.uuid4())
        self.source = "TEST"
        self.source_event_id = str(i)
        self.event_type = "TEST_TYPE"
        self.event_ts_utc = datetime.now(timezone.utc)
        self.received_ts_utc = datetime.now(timezone.utc)
        self.trading_date = None
        self.session = "OPEN"
        self.ticker = "AAPL"
        self.schema_version = "v1"
        self.payload = {"foo": "bar"}
        self.ingest = {}

def reproduce():
    # Setup
    N = 100000
    print(f"Generating {N} mock events...")
    events = [MockBronzeEvent(i) for i in range(N)]

    print("Converting events to dicts...")
    data = []
    for e in events:
        row = {
            "event_id": e.event_id,
            "source": e.source,
            "source_event_id": getattr(e, "source_event_id", None),
            "event_type": e.event_type,
            "event_ts_utc": e.event_ts_utc,
            "received_ts_utc": e.received_ts_utc,
            "trading_date": getattr(e, "trading_date", None),
            "session": getattr(e, "session", None),
            "ticker": getattr(e, "ticker", None),
            "schema_version": getattr(e, "schema_version", None),
            "payload": e.payload,
            "ingest": getattr(e, "ingest", None),
        }
        data.append(row)

    print("Creating DataFrame...")
    df = pd.DataFrame(data)

    # Check dtypes
    print(f"event_ts_utc dtype: {df['event_ts_utc'].dtype}")

    # Baseline: .apply()
    print("Running baseline optimization (apply lambda)...")
    start_apply = time.time()
    df["date_apply"] = df["event_ts_utc"].apply(lambda x: x.strftime("%Y-%m-%d"))
    end_apply = time.time()
    time_apply = end_apply - start_apply
    print(f"Baseline (apply) took: {time_apply:.4f}s")

    # Optimization 1: dt.strftime
    print("Running Opt 1 (dt.strftime)...")
    start_opt1 = time.time()
    df["date_opt1"] = df["event_ts_utc"].dt.strftime("%Y-%m-%d")
    end_opt1 = time.time()
    time_opt1 = end_opt1 - start_opt1
    print(f"Opt 1 (dt.strftime) took: {time_opt1:.4f}s")

    # Optimization 2: astype(str) slice
    print("Running Opt 2 (astype(str) slice)...")
    start_opt2 = time.time()
    # astype(str) produces '2023-11-20 12:34:56.789123+00:00'
    # We want '2023-11-20' -> first 10 chars
    df["date_opt2"] = df["event_ts_utc"].astype(str).str[:10]
    end_opt2 = time.time()
    time_opt2 = end_opt2 - start_opt2
    print(f"Opt 2 (astype(str) slice) took: {time_opt2:.4f}s")

    # Optimization 3: dt.date
    # NOTE: dt.date returns object (datetime.date), not string.
    # But checking if converting that to string is fast?
    print("Running Opt 3 (dt.date)...")
    start_opt3 = time.time()
    # This creates a column of date objects
    temp = df["event_ts_utc"].dt.date
    # If we need strings, we map str
    df["date_opt3"] = temp.astype(str)
    end_opt3 = time.time()
    time_opt3 = end_opt3 - start_opt3
    print(f"Opt 3 (dt.date + astype(str)) took: {time_opt3:.4f}s")

    print("-" * 20)
    print(f"Baseline: {time_apply:.4f}s")
    print(f"Opt 1:    {time_opt1:.4f}s (Speedup: {time_apply/time_opt1:.2f}x)")
    print(f"Opt 2:    {time_opt2:.4f}s (Speedup: {time_apply/time_opt2:.2f}x)")
    print(f"Opt 3:    {time_opt3:.4f}s (Speedup: {time_apply/time_opt3:.2f}x)")

    # Verification
    # Compare with baseline
    # Opt 2 might differ if astype(str) format is different than what we expect?
    # datetime64[ns, UTC] string representation is ISO.
    # lambda x: x.strftime("%Y-%m-%d")

    # Let's check correctness
    match1 = (df["date_apply"] == df["date_opt1"]).all()
    match2 = (df["date_apply"] == df["date_opt2"]).all()
    match3 = (df["date_apply"] == df["date_opt3"]).all()

    print(f"Opt 1 match: {match1}")
    print(f"Opt 2 match: {match2}")
    print(f"Opt 3 match: {match3}")

    if not match2:
        print("Sample Opt 2 mismatch:")
        print(df[["date_apply", "date_opt2"]].head())

if __name__ == "__main__":
    reproduce()
