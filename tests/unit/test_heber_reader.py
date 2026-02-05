from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

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
