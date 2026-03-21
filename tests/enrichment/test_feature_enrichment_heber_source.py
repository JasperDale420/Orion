from __future__ import annotations

import pandas as pd

from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df


def test_extract_top_tickers_prefers_most_frequent() -> None:
    now = pd.Timestamp.now(tz="UTC")
    df = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "AAPL", "SPY", "AAPL", "MSFT"],
            "ts_event": [now] * 6,
        }
    )

    out = _extract_top_tickers_from_flow_df(df, limit=2)
    assert out == ["AAPL", "MSFT"]


def test_extract_top_tickers_filters_old_rows() -> None:
    now = pd.Timestamp.now(tz="UTC")
    old = now - pd.Timedelta(days=3)
    df = pd.DataFrame(
        {
            "underlying": ["SPY", "QQQ"],
            "flow_ts_utc": [now, old],
        }
    )

    out = _extract_top_tickers_from_flow_df(df, limit=5)
    assert out == ["SPY"]


def test_extract_top_tickers_handles_missing_columns() -> None:
    df = pd.DataFrame({"foo": [1, 2, 3]})
    out = _extract_top_tickers_from_flow_df(df, limit=3)
    assert out == []
