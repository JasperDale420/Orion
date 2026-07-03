from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from orion.clients.heber_reader import HeberReader


def _write_parquet(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _write_partition_conflict_parquet(path: Path, ts: datetime) -> None:
    """Write a file where hive partition keys overlap with file columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "instrument_type": pa.array(["equity"], type=pa.string()),
            "instrument_key": pa.array(["equity:AAPL"], type=pa.string()),
            "bar_start_ts": pa.array([ts]),
            "ts_event": pa.array([ts]),
            "ts_available": pa.array([ts]),
            "open": pa.array([100.0]),
            "high": pa.array([101.0]),
            "low": pa.array([99.0]),
            "close": pa.array([100.5]),
            "volume": pa.array([10]),
            "net_call_premium": pa.array([150.0]),
            "net_put_premium": pa.array([-25.0]),
        }
    )
    pq.write_table(table, path)


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
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
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
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
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
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
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


def test_read_darkpool_falls_back_to_legacy_alias_dataset(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
    darkpool = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY"],
            "ts_event": [base],
            "ts_available": [base],
            "premium": [250_000.0],
        }
    )
    _write_parquet(tmp_path / "silver" / "feed=darkpool_trades" / "dt=2026-02-05" / "part-0.parquet", darkpool)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_darkpool(symbols=["SPY"], asof_time=base + timedelta(minutes=1))

    assert len(result) == 1
    assert set(result["instrument_key"]) == {"equity:SPY"}


def test_read_darkpool_prefers_canonical_dataset_when_both_exist(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
    canonical = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY"],
            "ts_event": [base],
            "ts_available": [base],
            "premium": [111_000.0],
        }
    )
    legacy = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY"],
            "ts_event": [base],
            "ts_available": [base],
            "premium": [222_000.0],
        }
    )
    _write_parquet(tmp_path / "silver" / "feed=darkpool" / "dt=2026-02-05" / "part-0.parquet", canonical)
    _write_parquet(tmp_path / "silver" / "feed=darkpool_trades" / "dt=2026-02-05" / "part-0.parquet", legacy)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_darkpool(symbols=["SPY"], asof_time=base + timedelta(minutes=1))

    assert len(result) == 1
    assert float(result.iloc[0]["premium"]) == 111_000.0


def test_read_gold_features_filters_asof_and_symbols(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
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


def test_read_gold_features_prunes_dt_partitions_older_than_lookback(tmp_path: Path) -> None:
    """With lookback_days set, rows in dt-partitions older than the window are
    excluded even though their ts_available is at/before asof (so this isolates
    dt-partition pruning from the existing as-of filter)."""
    asof = datetime(2026, 6, 5, 14, 0, tzinfo=UTC)
    recent = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL"],
            "ts_event": [asof - timedelta(days=1)],
            "ts_available": [asof - timedelta(days=1)],
            "momentum_5d": [0.42],
        }
    )
    stale = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL"],
            "ts_event": [asof - timedelta(days=20)],
            "ts_available": [asof - timedelta(days=20)],
            "momentum_5d": [0.11],
        }
    )
    root = tmp_path / "gold" / "dataset=momentum_features" / "project=quant" / "version=v1"
    _write_parquet(root / "dt=2026-06-04" / "part-0.parquet", recent)
    _write_parquet(root / "dt=2026-05-16" / "part-0.parquet", stale)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_gold_features(
        dataset="momentum_features",
        asof_time=asof,
        symbols=["AAPL"],
        lookback_days=7,
    )

    assert len(result) == 1
    assert float(result.iloc[0]["momentum_5d"]) == 0.42


def test_read_gold_features_includes_partition_exactly_at_lookback_cutoff(tmp_path: Path) -> None:
    """The cutoff (asof_date - lookback_days) is inclusive: a partition dated
    exactly on the cutoff is kept; one day earlier is pruned."""
    asof = datetime(2026, 6, 5, 14, 0, tzinfo=UTC)  # cutoff = 2026-05-29 with lookback 7
    on_cutoff = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL"],
            "ts_event": [datetime(2026, 5, 29, 12, 0, tzinfo=UTC)],
            "ts_available": [datetime(2026, 5, 29, 12, 0, tzinfo=UTC)],
            "momentum_5d": [0.29],
        }
    )
    before_cutoff = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL"],
            "ts_event": [datetime(2026, 5, 28, 12, 0, tzinfo=UTC)],
            "ts_available": [datetime(2026, 5, 28, 12, 0, tzinfo=UTC)],
            "momentum_5d": [0.28],
        }
    )
    root = tmp_path / "gold" / "dataset=momentum_features" / "project=quant" / "version=v1"
    _write_parquet(root / "dt=2026-05-29" / "part-0.parquet", on_cutoff)
    _write_parquet(root / "dt=2026-05-28" / "part-0.parquet", before_cutoff)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_gold_features(
        dataset="momentum_features",
        asof_time=asof,
        symbols=["AAPL"],
        lookback_days=7,
    )

    assert len(result) == 1
    assert float(result.iloc[0]["momentum_5d"]) == 0.29


def test_read_gold_features_reads_all_partitions_without_lookback(tmp_path: Path) -> None:
    """Default (no lookback_days) preserves full-history reads for training/backfill."""
    asof = datetime(2026, 6, 5, 14, 0, tzinfo=UTC)
    recent = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL"],
            "ts_event": [asof - timedelta(days=1)],
            "ts_available": [asof - timedelta(days=1)],
            "momentum_5d": [0.42],
        }
    )
    stale = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL"],
            "ts_event": [asof - timedelta(days=20)],
            "ts_available": [asof - timedelta(days=20)],
            "momentum_5d": [0.11],
        }
    )
    root = tmp_path / "gold" / "dataset=momentum_features" / "project=quant" / "version=v1"
    _write_parquet(root / "dt=2026-06-04" / "part-0.parquet", recent)
    _write_parquet(root / "dt=2026-05-16" / "part-0.parquet", stale)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_gold_features(
        dataset="momentum_features",
        asof_time=asof,
        symbols=["AAPL"],
    )

    assert len(result) == 2
    assert set(round(float(v), 2) for v in result["momentum_5d"]) == {0.42, 0.11}


def test_read_gold_features_widens_to_full_history_when_window_empty(tmp_path: Path) -> None:
    """When no partition falls within the lookback window, fall back to a full
    read so a low-cadence/stalled dataset's most recent (older) row is still
    returned rather than silently dropped to None."""
    asof = datetime(2026, 6, 5, 14, 0, tzinfo=UTC)
    stale = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL"],
            "ts_event": [datetime(2026, 4, 1, 12, 0, tzinfo=UTC)],
            "ts_available": [datetime(2026, 4, 1, 12, 0, tzinfo=UTC)],
            "momentum_5d": [0.99],
        }
    )
    root = tmp_path / "gold" / "dataset=momentum_features" / "project=quant" / "version=v1"
    _write_parquet(root / "dt=2026-04-01" / "part-0.parquet", stale)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_gold_features(
        dataset="momentum_features",
        asof_time=asof,
        symbols=["AAPL"],
        lookback_days=7,
    )

    assert len(result) == 1
    assert float(result.iloc[0]["momentum_5d"]) == 0.99


def test_read_gold_features_returns_empty_for_genuinely_empty_dataset_with_lookback(tmp_path: Path) -> None:
    """A dataset with no parquet files at all returns empty even with the
    widen-if-empty fallback (nothing to widen to)."""
    asof = datetime(2026, 6, 5, 14, 0, tzinfo=UTC)
    (tmp_path / "gold" / "dataset=momentum_features").mkdir(parents=True)

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_gold_features(
        dataset="momentum_features",
        asof_time=asof,
        symbols=["AAPL"],
        lookback_days=7,
    )

    assert result.empty


def test_read_gold_features_supports_nested_watch_layout(tmp_path: Path) -> None:
    base = datetime(2026, 2, 12, 14, 0, tzinfo=UTC)
    features = pd.DataFrame(
        {
            "alert_id": ["evt-1"],
            "alert_time": [base],
            "premium": [125000.0],
            "ts_available": [base],
        }
    )
    _write_parquet(
        tmp_path
        / "gold"
        / "labels_alert_barriers"
        / "dataset=meta_label_features"
        / "project=watch"
        / "version=v1"
        / "dt=2026-02-12"
        / "part-0.parquet",
        features,
    )

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_gold_features(
        dataset="meta_label_features",
        asof_time=base + timedelta(minutes=1),
    )

    assert len(result) == 1
    assert str(result.iloc[0]["alert_id"]) == "evt-1"
    assert float(result.iloc[0]["premium"]) == 125000.0


def test_read_greek_exposure_filters_symbol_and_asof(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
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
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
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
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
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
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
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


def test_read_bars_handles_hive_partition_column_type_conflict(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
    _write_partition_conflict_parquet(
        tmp_path / "silver" / "feed=bars" / "instrument_type=equity" / "dt=2026-02-05" / "part-0.parquet",
        ts=base,
    )

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_bars(symbols=["AAPL"], asof_time=base + timedelta(minutes=1))

    assert len(result) == 1
    assert set(result["instrument_key"]) == {"equity:AAPL"}


def test_read_market_tide_handles_hive_partition_column_type_conflict(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
    _write_partition_conflict_parquet(
        tmp_path / "silver" / "feed=market_tide" / "instrument_type=equity" / "dt=2026-02-05" / "part-0.parquet",
        ts=base,
    )

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_market_tide(asof_time=base + timedelta(minutes=1))

    assert len(result) == 1
    assert float(result.iloc[0]["net_call_premium"]) == 150.0


def test_read_bars_skips_corrupt_parquet_file_and_keeps_valid_rows(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
    valid = pd.DataFrame(
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

    valid_path = tmp_path / "silver" / "feed=bars" / "instrument_type=equity" / "dt=2026-02-05"
    _write_parquet(valid_path / "part-valid.parquet", valid)
    (valid_path / "part-corrupt.parquet").write_text("not parquet data", encoding="utf-8")

    reader = HeberReader(data_root=tmp_path)
    result = reader.read_bars(symbols=["AAPL"], asof_time=base + timedelta(minutes=1))

    assert len(result) == 1
    assert set(result["instrument_key"]) == {"equity:AAPL"}


def test_is_corrupt_parquet_error_detects_dataset_open_variant() -> None:
    exc = RuntimeError(
        "Error creating dataset. Could not open Parquet input source '/tmp/bad.parquet': "
        "ArrowInvalid: malformed parquet metadata"
    )

    assert HeberReader._is_corrupt_parquet_error(exc) is True


def test_is_schema_merge_error_detects_incompatible_types_variant() -> None:
    exc = TypeError(
        "Unable to merge: Field feed has incompatible types: "
        "string vs dictionary<values=string, indices=int32, ordered=0>"
    )

    assert HeberReader._is_schema_merge_parquet_error(exc) is True


def test_is_schema_merge_error_detects_int64_to_date32_cast() -> None:
    # 2026-06-30 incident: meta_label_features `expiry` written int64 in one
    # dt= partition (date32 elsewhere). pyarrow raised ArrowNotImplementedError
    # with this message; the old matcher required "to null"/"cast_null" so it
    # missed it, total-failing the whole gold read instead of degrading to the
    # file-wise reader.
    exc = RuntimeError("Unsupported cast from int64 to date32 using function cast_date32")

    assert HeberReader._is_schema_merge_parquet_error(exc) is True


def test_read_parquet_falls_back_to_filewise_on_incompatible_types_error(tmp_path: Path) -> None:
    reader = HeberReader(data_root=tmp_path)
    expected = pd.DataFrame({"instrument_key": ["equity:AAPL"], "close": [150.0]})

    def _raise_incompatible_types_error(**_kwargs):  # noqa: ANN003
        raise TypeError(
            "Unable to merge: Field feed has incompatible types: "
            "string vs dictionary<values=string, indices=int32, ordered=0>"
        )

    reader._read_table = _raise_incompatible_types_error  # type: ignore[method-assign]
    reader._read_parquet_filewise = lambda **_kwargs: expected  # type: ignore[method-assign]

    result = reader._read_parquet(path=tmp_path / "silver" / "feed=bars", columns=None, filters=None)

    assert result.equals(expected)


def test_read_parquet_falls_back_to_filewise_on_schema_merge_error(tmp_path: Path) -> None:
    reader = HeberReader(data_root=tmp_path)
    expected = pd.DataFrame({"alert_id": ["evt-1"], "premium": [125000.0]})

    def _raise_schema_merge_error(**_kwargs):  # noqa: ANN003
        raise RuntimeError("Unsupported cast from double to null using function cast_null")

    reader._read_table = _raise_schema_merge_error  # type: ignore[method-assign]
    reader._read_parquet_filewise = lambda **_kwargs: expected  # type: ignore[method-assign]

    result = reader._read_parquet(path=tmp_path / "gold" / "dataset=meta_label_features", columns=None, filters=None)

    assert result.equals(expected)


def test_read_parquet_filewise_skips_hidden_sidecar_files(tmp_path: Path) -> None:
    base = datetime(2026, 2, 5, 14, 0, tzinfo=UTC)
    valid = pd.DataFrame(
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
    dataset_path = tmp_path / "silver" / "feed=bars" / "instrument_type=equity" / "dt=2026-02-05"
    valid_file = dataset_path / "part-valid.parquet"
    _write_parquet(valid_file, valid)
    (dataset_path / "._part-valid.parquet").write_text("not parquet data", encoding="utf-8")

    reader = HeberReader(data_root=tmp_path)
    seen_paths: list[Path] = []
    original_read_table = reader._read_table

    def _capture_read_table(path: Path, columns, filters, partitioning):  # type: ignore[no-untyped-def]
        seen_paths.append(path)
        return original_read_table(path=path, columns=columns, filters=filters, partitioning=partitioning)

    reader._read_table = _capture_read_table  # type: ignore[method-assign]
    result = reader._read_parquet_filewise(path=dataset_path, columns=None, filters=None)

    assert len(result) == 1
    assert all(not path.name.startswith("._") for path in seen_paths)


def test_read_table_disables_arrow_threadpools_on_primary_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression for the 2026-06-02 PyArrow SIGABRT: the primary read must never
    # spin up Arrow's CPU thread pool (use_threads) or the background I/O prefetch
    # pool (pre_buffer) from inside the asyncio.to_thread executor.
    df = pd.DataFrame({"instrument_key": ["equity:AAPL"], "close": [100.5]})
    dataset_path = tmp_path / "gold" / "dataset=meta_label_features" / "dt=2026-02-05"
    _write_parquet(dataset_path / "part-0.parquet", df)

    reader = HeberReader(data_root=tmp_path)
    calls: list[dict] = []
    real_read_table = pq.read_table

    def _spy(source, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return real_read_table(source, **kwargs)

    monkeypatch.setattr("orion.clients.heber_reader.pq.read_table", _spy)

    reader._read_table(path=dataset_path, columns=None, filters=None, partitioning=None)

    assert len(calls) == 1
    assert calls[0]["use_threads"] is False
    assert calls[0]["pre_buffer"] is False


def test_read_table_disables_arrow_threadpools_on_filter_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The filter-pushdown fallback read must also pin both Arrow thread pools off.
    df = pd.DataFrame({"instrument_key": ["equity:AAPL"], "close": [100.5]})
    dataset_path = tmp_path / "gold" / "dataset=meta_label_features" / "dt=2026-02-05"
    _write_parquet(dataset_path / "part-0.parquet", df)

    reader = HeberReader(data_root=tmp_path)
    calls: list[dict] = []
    real_read_table = pq.read_table

    def _spy(source, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        if kwargs.get("filters"):
            # Force the primary (filtered) read to fail with a non-corrupt error
            # so _read_table retries via the filter-pushdown fallback path.
            raise RuntimeError("forced filter pushdown failure")
        return real_read_table(source, **kwargs)

    monkeypatch.setattr("orion.clients.heber_reader.pq.read_table", _spy)

    reader._read_table(path=dataset_path, columns=None, filters=[("close", ">", 0)], partitioning=None)

    assert len(calls) == 2  # primary (filtered, raised) + fallback
    for call in calls:
        assert call["use_threads"] is False
        assert call["pre_buffer"] is False


def test_read_parquet_converts_to_pandas_single_threaded(tmp_path: Path) -> None:
    # to_pandas() defaults to use_threads=True, spawning the same Arrow CPU pool
    # that aborted on 2026-06-02 — the direct read path must pin it off.
    reader = HeberReader(data_root=tmp_path)
    expected = pd.DataFrame({"instrument_key": ["equity:AAPL"], "close": [100.5]})
    fake_table = MagicMock()
    fake_table.to_pandas.return_value = expected
    reader._read_table = MagicMock(return_value=fake_table)  # type: ignore[method-assign]

    result = reader._read_parquet(path=tmp_path / "gold" / "dataset=x", columns=None, filters=None)

    fake_table.to_pandas.assert_called_once_with(use_threads=False)
    assert result.equals(expected)


def test_read_parquet_filewise_converts_to_pandas_single_threaded(tmp_path: Path) -> None:
    # The per-file fallback path converts to pandas too; same single-threaded pin.
    df = pd.DataFrame({"instrument_key": ["equity:AAPL"], "close": [100.5]})
    dataset_path = tmp_path / "gold" / "dataset=x" / "dt=2026-02-05"
    _write_parquet(dataset_path / "part-0.parquet", df)

    reader = HeberReader(data_root=tmp_path)
    fake_table = MagicMock()
    fake_table.to_pandas.return_value = df
    reader._read_table = MagicMock(return_value=fake_table)  # type: ignore[method-assign]

    result = reader._read_parquet_filewise(path=dataset_path, columns=None, filters=None)

    fake_table.to_pandas.assert_called_once_with(use_threads=False)
    assert len(result) == 1
