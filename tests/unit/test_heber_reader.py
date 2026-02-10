from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
import pytest

from orion.clients.heber_reader import HeberReader


def _write_parquet(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def test_health_check_uses_supported_endpoint() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        return httpx.Response(404, json={"detail": "not found"})

    client = httpx.Client(
        base_url="http://localhost:8085/api/v1",
        transport=httpx.MockTransport(handler),
    )
    reader = HeberReader(http_client=client)

    try:
        assert reader.health_check() is True
        assert "/health" in seen_paths
    finally:
        reader.close()


@pytest.mark.parametrize("base_url", ["http://localhost:8085", "http://localhost:8085/api/v1"])
def test_list_datasets_uses_canonical_api_v1_endpoint(base_url: str) -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/api/v1/datasets":
            return httpx.Response(200, json={"data": [{"name": "bars"}]})
        return httpx.Response(404, json={"detail": "not found"})

    client = httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler))
    reader = HeberReader(http_client=client)

    try:
        datasets = reader.list_datasets()
        assert datasets == [{"name": "bars"}]
        assert seen_paths == ["/api/v1/datasets"]
    finally:
        reader.close()


def test_read_bars_filters_instrument_and_asof(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=timezone.utc)
    bars = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL", "equity:AAPL", "equity:MSFT"],
            "bar_start_ts": [base, base + timedelta(minutes=1), base],
            "ts_available": [base, base + timedelta(minutes=4), base],
            "open": [100.0, 101.0, 200.0],
            "high": [101.0, 102.0, 201.0],
            "low": [99.0, 100.0, 199.0],
            "close": [100.5, 101.5, 200.5],
            "volume": [10, 20, 30],
        }
    )
    _write_parquet(tmp_path / "silver" / "feed=bars" / "dt=2026-02-05" / "part-0.parquet", bars)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_bars(symbols=["AAPL"], asof_time=base + timedelta(minutes=2))

    assert len(result) == 1
    assert set(result["instrument_key"]) == {"equity:AAPL"}
    assert "ts_event" in result.columns
    assert set(result["symbol"]) == {"AAPL"}


def test_read_bars_rejects_unsupported_timeframe(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=timezone.utc)
    bars = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL"],
            "bar_start_ts": [base],
            "ts_available": [base],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [10],
        }
    )
    _write_parquet(tmp_path / "silver" / "feed=bars" / "dt=2026-02-05" / "part-0.parquet", bars)

    reader = HeberReader(data_root=tmp_path)
    with pytest.raises(ValueError, match="Unsupported bars timeframe"):
        reader.read_bars(symbols=["AAPL"], asof_time=base + timedelta(minutes=1), timeframe="5m")


def test_read_flow_applies_min_premium(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=timezone.utc)
    flow = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY", "equity:SPY", "equity:QQQ"],
            "ts_event": [base, base + timedelta(minutes=1), base],
            "ts_available": [base, base + timedelta(minutes=1), base],
            "premium": [40_000.0, 120_000.0, 300_000.0],
        }
    )
    _write_parquet(tmp_path / "silver" / "feed=flow_alerts" / "dt=2026-02-05" / "part-0.parquet", flow)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_flow(symbols=["SPY"], asof_time=base + timedelta(minutes=3), min_premium=100_000)

    assert len(result) == 1
    assert result.iloc[0]["instrument_key"] == "equity:SPY"
    assert float(result.iloc[0]["premium"]) == 120_000.0


def test_read_darkpool_falls_back_to_alias_dataset(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=timezone.utc)
    darkpool = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY"],
            "ts_event": [base],
            "ts_available": [base],
            "premium": [250_000.0],
        }
    )
    _write_parquet(tmp_path / "silver" / "feed=darkpool" / "dt=2026-02-05" / "part-0.parquet", darkpool)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_darkpool(symbols=["SPY"], asof_time=base + timedelta(minutes=1))

    assert len(result) == 1
    assert set(result["instrument_key"]) == {"equity:SPY"}


def test_read_gold_features_filters_asof_and_symbols(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=timezone.utc)
    gold = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL", "equity:AAPL", "equity:MSFT"],
            "ts_event": [base, base + timedelta(days=1), base],
            "ts_available": [base, base + timedelta(days=2), base],
            "momentum_5d": [0.1, 0.2, 0.3],
        }
    )
    _write_parquet(
        tmp_path
        / "gold"
        / "dataset=momentum_features"
        / "project=quant"
        / "version=v1"
        / "dt=2026-02-05"
        / "part-0.parquet",
        gold,
    )

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_gold_features(
        dataset="momentum_features",
        asof_time=base + timedelta(hours=1),
        symbols=["AAPL"],
    )

    assert len(result) == 1
    assert set(result["instrument_key"]) == {"equity:AAPL"}
    assert float(result.iloc[0]["momentum_5d"]) == 0.1


def test_read_greek_exposure_filters_symbol_and_asof(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=timezone.utc)
    greek = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL", "equity:AAPL", "equity:MSFT"],
            "ts_utc": [base, base + timedelta(minutes=5), base],
            "ts_available": [base, base + timedelta(minutes=10), base],
            "gex_oi": [10.0, 20.0, 30.0],
            "vex_oi": [1.0, 2.0, 3.0],
        }
    )
    _write_parquet(tmp_path / "silver" / "feed=greek_exposure" / "dt=2026-02-05" / "part-0.parquet", greek)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_greek_exposure(symbols=["AAPL"], asof_time=base + timedelta(minutes=6))

    assert len(result) == 1
    assert set(result["instrument_key"]) == {"equity:AAPL"}
    assert float(result.iloc[0]["gex_oi"]) == 10.0


def test_read_market_tide_filters_time_and_asof(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=timezone.utc)
    tide = pd.DataFrame(
        {
            "ts_utc": [base - timedelta(minutes=30), base, base + timedelta(minutes=10)],
            "ts_available": [base - timedelta(minutes=30), base, base + timedelta(minutes=20)],
            "net_call_premium": [100.0, 150.0, 200.0],
            "net_put_premium": [-50.0, -25.0, -10.0],
        }
    )
    _write_parquet(tmp_path / "silver" / "feed=market_tide" / "dt=2026-02-05" / "part-0.parquet", tide)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_market_tide(
        start_time=base - timedelta(minutes=5),
        asof_time=base + timedelta(minutes=5),
    )

    assert len(result) == 1
    assert float(result.iloc[0]["net_call_premium"]) == 150.0


def test_read_max_pain_filters_symbol_and_asof(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=timezone.utc)
    max_pain = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL", "equity:AAPL", "equity:MSFT"],
            "date": [base.date(), base.date() + timedelta(days=1), base.date()],
            "ts_available": [base, base + timedelta(days=1), base],
            "expiry": [
                base.date() + timedelta(days=7),
                base.date() + timedelta(days=7),
                base.date() + timedelta(days=7),
            ],
            "distance_to_max_pain_pct": [1.2, 2.3, 3.4],
        }
    )
    _write_parquet(tmp_path / "silver" / "feed=max_pain" / "dt=2026-02-05" / "part-0.parquet", max_pain)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_max_pain(symbols=["AAPL"], asof_time=base + timedelta(hours=1))

    assert len(result) == 1
    assert set(result["instrument_key"]) == {"equity:AAPL"}
    assert float(result.iloc[0]["distance_to_max_pain_pct"]) == 1.2


def test_read_iv_rank_filters_symbol_and_asof(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=timezone.utc)
    iv_rank = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL", "equity:AAPL", "equity:MSFT"],
            "ts_utc": [base, base + timedelta(minutes=5), base],
            "ts_available": [base, base + timedelta(minutes=10), base],
            "iv_rank": [25.0, 55.0, 75.0],
        }
    )
    _write_parquet(tmp_path / "silver" / "feed=iv_rank" / "dt=2026-02-05" / "part-0.parquet", iv_rank)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_iv_rank(symbols=["AAPL"], asof_time=base + timedelta(minutes=6))

    assert len(result) == 1
    assert set(result["instrument_key"]) == {"equity:AAPL"}
    assert float(result.iloc[0]["iv_rank"]) == 25.0
