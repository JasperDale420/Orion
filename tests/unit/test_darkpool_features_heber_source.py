from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from orion.ml.darkpool_features import get_darkpool_features


@pytest.mark.asyncio
async def test_get_darkpool_features_aggregates_heber_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    as_of = datetime(2026, 2, 12, 15, 0, tzinfo=UTC)
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL"],
            "dark_ts_utc": [as_of - timedelta(minutes=30), as_of - timedelta(minutes=10)],
            "trade_price": [10.0, 20.0],
            "size_shares": [100.0, 300.0],
        }
    )

    reader = MagicMock()
    reader.read_darkpool.return_value = frame
    monkeypatch.setattr("orion.ml.darkpool_features.get_heber_reader", lambda: reader)

    result = await get_darkpool_features("AAPL", as_of, lookback_hours=24)

    assert result == {
        "darkpool_volume_24h": 400.0,
        "darkpool_trade_count": 2,
        "darkpool_avg_price": 17.5,
        "darkpool_max_block": 300.0,
        "darkpool_dollar_volume": 7000.0,
    }


@pytest.mark.asyncio
async def test_get_darkpool_features_returns_zeroes_when_heber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    as_of = datetime(2026, 2, 12, 15, 0, tzinfo=UTC)
    reader = MagicMock()
    reader.read_darkpool.side_effect = RuntimeError("heber unavailable")
    monkeypatch.setattr("orion.ml.darkpool_features.get_heber_reader", lambda: reader)

    result = await get_darkpool_features("AAPL", as_of, lookback_hours=24)

    assert result == {
        "darkpool_volume_24h": 0.0,
        "darkpool_trade_count": 0,
        "darkpool_avg_price": 0.0,
        "darkpool_max_block": 0.0,
        "darkpool_dollar_volume": 0.0,
    }
