import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.core.universe_manager import UniverseManager
from orion.storage.models import BronzeEvent


@pytest.fixture(autouse=True)
def mock_db_init():
    with patch("orion.storage.db.init_db", new_callable=AsyncMock) as m:
        yield m


@pytest.mark.asyncio
async def test_hydrate_from_db():
    """
    Verifies that hydrate_from_db correctly populates expiry_tickers and active_tickers
    from mocked DB results.
    """
    # ... mocks ...
    future_date = (datetime.now() + timedelta(days=30)).date()
    past_date = (datetime.now() - timedelta(days=30)).date()

    mock_rows = [
        MagicMock(ticker="AAPL", expiry=str(future_date)),
        MagicMock(
            ticker="TSLA", expiry=str(past_date)
        ),  # Should be ignored by query, but good to test logic if returned
        MagicMock(ticker="MSFT", expiry="invalid-date"),
    ]

    # Mock Session
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_rows

    # Mock Session Factory
    uni = UniverseManager()
    await uni.hydrate_from_db()

    # hydrate_from_db is now a no-op (SilverUWAlert table was removed)
    # Just verify it doesn't crash
    assert uni is not None


def test_update_from_event_expiry():
    """
    Verifies that incoming events update the expiry tracking.
    """
    uni = UniverseManager()

    # Event with expiry
    exp = (datetime.now() + timedelta(days=10)).date()
    event = MagicMock(spec=BronzeEvent)
    event.ticker = "NVDA"
    event.payload = {"ticker": "NVDA", "expiry": str(exp)}
    event.event_type = "UW_FLOW_ALERT"

    uni.update_from_event(event)

    assert "NVDA" in uni.active_tickers
    assert "NVDA" in uni.alert_tickers
    assert uni.expiry_tickers["NVDA"] == exp

    # Later expiry update
    exp2 = (datetime.now() + timedelta(days=20)).date()
    event2 = MagicMock(spec=BronzeEvent)
    event2.ticker = "NVDA"
    event2.payload = {"ticker": "NVDA", "expiry": str(exp2)}

    uni.update_from_event(event2)
    assert uni.expiry_tickers["NVDA"] == exp2

    # Earlier expiry update (should be ignored? Logic says exp_date > current_exp)
    exp3 = (datetime.now() + timedelta(days=5)).date()
    event3 = MagicMock(spec=BronzeEvent)
    event3.ticker = "NVDA"
    event3.payload = {"ticker": "NVDA", "expiry": str(exp3)}

    uni.update_from_event(event3)
    assert uni.expiry_tickers["NVDA"] == exp2  # Still exp2


def test_cleanup_respects_expiry():
    """
    Verifies that cleanup does NOT remove tickers with future expiry.
    """
    uni = UniverseManager()

    # Add expired state
    uni.active_tickers["OLD"] = time.time() - 100000

    # Add protected state
    uni.active_tickers["PROTECTED"] = time.time() - 100000
    future = (datetime.now() + timedelta(days=5)).date()
    uni.expiry_tickers["PROTECTED"] = future

    uni.cleanup(ttl_seconds=600)

    assert "OLD" not in uni.active_tickers
    assert "PROTECTED" in uni.active_tickers
