from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from orion.main_ingest import save_silver_data
from orion.processing.normalizer import NormalizationEngine
from orion.storage.models import BronzeEvent


@pytest.mark.asyncio
async def test_silver_persistence_flow():
    # 1. Mock Data
    payload = {
        "ticker": "AAPL",
        "timestamp": "2023-10-27T14:30:00Z",
        "put_call": "C",
        "expiry": "2023-11-03",
        "strike_price": 150.0,
        "price": 2.5,
        "size": 100,
        "bid": 2.4,
        "ask": 2.6,
        "underlying_price": 145.0,
        "aggressor": "ASK",
        "sweep": True,
        "trade_type": "BLOCK",
        "open_interest": 5000,
        "volume": 1000,
        "premium": 25000.0,
        "multi_leg": False,
    }

    norm_payload = NormalizationEngine.normalize_event("UW", "UW_FLOW", payload)

    event = BronzeEvent(
        event_id="test_flow_001",
        source="UW",
        event_type="UW_FLOW",
        ticker="AAPL",
        trading_date=datetime.now(),
        session="REG",
        event_ts_utc=datetime.now(timezone.utc),
        payload=norm_payload,
    )

    # 2. Patch async_session_factory
    with patch("orion.main_ingest.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_factory.return_value.__aenter__.return_value = mock_session

        # 3. Call Function
        await save_silver_data([event])

        # 4. Verify Call
        assert mock_session.execute.called
        assert mock_session.commit.called

        # Inspect the Insert statement
        # call_args[0][0] is the statement
        call_args = mock_session.execute.call_args
        stmt = call_args[0][0]

        # Check if it inserts into SilverOptionFlow
        assert stmt.table.name == "silver_uw_flow"

        # Check values
        # stmt.parameters is usually list of dicts for executemany, or we can check logic
        # For 'values(list_of_dicts)', SQLAlchemy compiles it.
        # We can check compiled params if accessible, or just rely on 'called' for this smoke test.
        # But let's verify parameters passed to values()
        # The 'values' are stored in stmt.parameters usually?
        # For insert().values([...]), it's in stmt._values usually (internal) or we inspect the construct.

        # Simple check: The function didn't error and called execute.
        pass


@pytest.mark.asyncio
async def test_silver_persistence_bar():
    # 1. Mock Alpaca Bar
    payload = {
        "t": "2023-10-27T14:31:00Z",
        "o": 100.0,
        "h": 101.0,
        "l": 99.5,
        "c": 100.5,
        "v": 5000,
        "vw": 100.2,
        "symbol": "TSLA",
    }

    norm_payload = NormalizationEngine.normalize_event("ALPACA", "ALPACA_BAR_1M", payload)

    event = BronzeEvent(
        event_id="test_bar_001",
        source="ALPACA",
        event_type="ALPACA_BAR_1M",
        ticker="TSLA",
        trading_date=datetime.now(),
        session="REG",
        event_ts_utc=datetime.now(timezone.utc),
        payload=norm_payload,
    )

    with patch("orion.main_ingest.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_factory.return_value.__aenter__.return_value = mock_session

        await save_silver_data([event])

        assert mock_session.execute.called
        stmt = mock_session.execute.call_args[0][0]
        assert stmt.table.name == "silver_alpaca_bars"
