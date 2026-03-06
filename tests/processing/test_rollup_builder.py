from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock  # AsyncMock needs explicit import or patch magic

import pytest

from orion.processing.rollup_builder import RollupBuilder
from orion.storage.models_silver import SignalType, SilverSignal


# Mock Async Session
class MockAsyncSession:
    def __init__(self):
        self.store = {}  # PK -> Obj
        self.merged = []

    async def execute(self, stmt):
        # We assume the test manually provides the data to return
        pass

    async def merge(self, obj):
        # Simple mock merge
        # PK is ticker+period+ts
        key = f"{obj.ticker}_{obj.period}_{obj.timestamp_utc.isoformat()}"
        self.store[key] = obj
        self.merged.append(obj)
        return obj

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_rollup_builder_logic():
    # 1. Setup Mock Data (10 minutes of 1m bars)
    # Price rises 100 -> 109
    base_ts = datetime(2023, 1, 1, 10, 0, 0, tzinfo=UTC)
    signals = []

    for i in range(10):
        ts = base_ts + timedelta(minutes=i)
        price = 100.0 + i
        # 1m bar
        f = {
            "open": price,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price,
            "volume": 100.0,
            "vwap": price,  # Simple vwap = price
        }

        sig = SilverSignal(ticker="TEST", signal_ts_utc=ts, signal_type=SignalType.OHLCV_1M.value, features=f)
        signals.append(sig)

    # 2. Setup Mock Session
    mock_session = MockAsyncSession()
    # Mock execute result
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = signals
    mock_session.execute = AsyncMock(return_value=mock_result)

    # 3. Run Builder
    builder = RollupBuilder(mock_session)
    await builder.build_rollups("TEST", base_ts, base_ts + timedelta(minutes=10))

    # 4. Verify Gold Rollups
    # Expect:
    # 5m bars: 2 (10:00-10:05, 10:05-10:10)
    # 1h bar: 1 (10:00)
    # 1d bar: 1

    merged = mock_session.merged

    five_min_bars = [r for r in merged if r.period == "5m"]
    one_hour_bars = [r for r in merged if r.period == "1h"]

    assert len(five_min_bars) == 2
    assert len(one_hour_bars) == 1

    # Check Aggregation Logic (First 5m: 100, 101, 102, 103, 104)
    # Start 10:00.
    # Open: 100.0 (from first bar)
    # High: 104.5 (Max of highs) - actually max is 104+0.5=104.5
    # Low: 99.5 (Min of lows) - min is 100-0.5=99.5
    # Close: 104.0 (Last bar in bucket)
    # Volume: 500 (100 * 5)

    b1 = five_min_bars[0]
    # Timestamps in pandas resample label is usually left edge
    assert b1.timestamp_utc == base_ts
    assert b1.open == 100.0
    assert b1.close == 104.0
    assert b1.volume == 500.0

    # Second 5m (105..109)
    b2 = five_min_bars[1]
    assert b2.open == 105.0
    assert b2.close == 109.0

    # 1H Bar should cover all 10 mins
    h1 = one_hour_bars[0]
    assert h1.open == 100.0
    assert h1.close == 109.0
    assert h1.volume == 1000.0
