from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
from orion.main_labeler import FlowRecord, _normalize_flow_df


def test_normalize_flow_df_builds_records_from_heber_shape() -> None:
    cutoff = datetime(2026, 2, 6, 15, 0, tzinfo=UTC)
    df = pd.DataFrame(
        {
            "event_id": ["evt-1", "evt-2"],
            "ticker": ["AAPL", "MSFT"],
            "ts_event": ["2026-02-06T12:00:00Z", "2026-02-06T16:00:00Z"],
            "underlying_price": [100.5, 200.0],
            "option_price": [1.2, 2.3],
            "premium_usd": [50000, 60000],
            "aggressor": ["ASK", "BID"],
            "put_call": ["C", "P"],
            "is_sweep": [True, False],
            "iv": [0.45, 0.35],
            "expiry": ["2026-02-21", "2026-02-21"],
        }
    )

    records = _normalize_flow_df(df, cutoff)

    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, FlowRecord)
    assert rec.event_id == "evt-1"
    assert rec.ticker == "AAPL"
    assert rec.flow_ts_utc == datetime(2026, 2, 6, 12, 0, tzinfo=UTC)
    assert rec.underlying_price == 100.5
    assert rec.option_price == 1.2


def test_normalize_flow_df_supports_alias_columns() -> None:
    cutoff = datetime(2026, 2, 6, 15, 0, tzinfo=UTC)
    df = pd.DataFrame(
        {
            "source_event_id": ["src-1"],
            "underlying": ["SPY"],
            "timestamp": ["2026-02-06T10:00:00Z"],
            "spot_px": [501.25],
            "price": [9.8],
            "premium": [120000],
            "side": ["ASK"],
            "type": ["CALL"],
            "sweep": ["true"],
            "implied_volatility": [0.22],
        }
    )

    records = _normalize_flow_df(df, cutoff)

    assert len(records) == 1
    rec = records[0]
    assert rec.event_id == "src-1"
    assert rec.ticker == "SPY"
    assert rec.underlying_price == 501.25
    assert rec.option_price == 9.8
    assert rec.premium_usd == 120000.0
    assert rec.aggressor == "ASK"
    assert rec.put_call == "CALL"
    assert rec.is_sweep == "true"
    assert rec.iv == 0.22
