from datetime import datetime, timedelta

import pandas as pd

from orion.processing.label_engine import TripleBarrierLabeling


def test_label_engine_upper_barrier():
    # 1. Setup Prices: Flat then spike up
    base = datetime(2023, 1, 1, 9, 30)
    times = [base + timedelta(minutes=i) for i in range(10)]
    prices = [100.0] * 5 + [102.0] + [100.0] * 4  # 102 is +2%

    price_series = pd.Series(prices, index=times)

    # 2. Event at t=0
    events = pd.DatetimeIndex([base])

    # 3. Labeler with 1.5% target, 1% stop
    lbl = TripleBarrierLabeling(upper_barrier=0.015, lower_barrier=0.010, time_barrier_bars=10)

    # 4. Run
    df = lbl.compute_labels(price_series, events)

    # 5. Assert (Should contain 102.0 hit at index 5)
    assert len(df) == 1
    assert df.iloc[0]["label"] == 1
    assert df.iloc[0]["ret"] == 0.02
    assert df.iloc[0]["barrier_hit_ts"] == times[5]


def test_label_engine_lower_barrier():
    base = datetime(2023, 1, 1, 9, 30)
    times = [base + timedelta(minutes=i) for i in range(10)]
    prices = [100.0] * 5 + [98.0] + [100.0] * 4  # 98 is -2%

    price_series = pd.Series(prices, index=times)
    events = pd.DatetimeIndex([base])

    lbl = TripleBarrierLabeling(upper_barrier=0.015, lower_barrier=0.010, time_barrier_bars=10)
    df = lbl.compute_labels(price_series, events)

    assert df.iloc[0]["label"] == -1
    assert df.iloc[0]["ret"] == -0.02


def test_label_engine_time_barrier():
    base = datetime(2023, 1, 1, 9, 30)
    times = [base + timedelta(minutes=i) for i in range(20)]
    # Wiggles but stays within bounds
    prices = [100.0 + ((-1) ** i * 0.5) for i in range(20)]  # +/- 0.5%

    price_series = pd.Series(prices, index=times)
    events = pd.DatetimeIndex([base])

    # Time barrier 5 bars
    lbl = TripleBarrierLabeling(upper_barrier=0.015, lower_barrier=0.010, time_barrier_bars=5)
    df = lbl.compute_labels(price_series, events)

    assert df.iloc[0]["label"] == 0
    assert df.iloc[0]["barrier_hit_ts"] == times[5]  # Entry is 0, window is 0..5 (length 6) or just 5 bars hold?
    # Logic says iloc[:bars+1], so index 5 is the 6th element. Correct.
