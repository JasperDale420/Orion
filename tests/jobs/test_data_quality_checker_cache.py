"""Tests for the per-run Heber DataFrame cache in `run_quality_checks`.

FOLLOWUPS #6 — the May 13 cgroup-OOM cascade in `orion_data_quality`
traced to redundant Heber reads within a single quality-check pass:
flow read twice (summary + staleness), darkpool read twice, and each
of two Gold datasets (`labels_alert_barriers`, `meta_label_features`)
read twice (ML features summary + recent-labels coverage). Combined
with no GC between phases, 5+ GiB of pandas DataFrames could be alive
simultaneously, pushing the container past its mem_limit and SIGKILL'd
by the cgroup memory controller.

The fix introduces a per-run ContextVar cache so duplicate consumers
share one materialization, and evicts the cache between logical
phases so DataFrames are GC-eligible before the next phase allocates.

These tests pin:
  1. A full `run_quality_checks` triggers each Heber read at most once
     per (call-signature, phase) tuple.
  2. Public per-check functions called WITHOUT the ContextVar set
     behave exactly as before (no cache, no eviction). Existing unit
     tests cover this; we add an explicit assertion here too.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from orion.jobs import data_quality_checker as dqc


def _make_flow_df() -> pd.DataFrame:
    now = datetime.now(UTC)
    return pd.DataFrame(
        {
            "instrument_key": ["equity:SPY", "equity:QQQ"],
            "ts_event": [now - timedelta(minutes=2), now - timedelta(minutes=5)],
            "premium_usd": [100.0, 200.0],
        }
    )


def _make_darkpool_df() -> pd.DataFrame:
    now = datetime.now(UTC)
    return pd.DataFrame(
        {
            "instrument_key": ["equity:SPY"],
            "ts_event": [now - timedelta(minutes=10)],
            "size_shares": [1000],
            "trade_price": [10.0],
        }
    )


def _make_bars_df() -> pd.DataFrame:
    now = datetime.now(UTC)
    return pd.DataFrame(
        {
            "ticker": ["SPY", "QQQ"],
            "ts_event": [now - timedelta(minutes=1), now - timedelta(minutes=2)],
            "close": [400.0, 350.0],
        }
    )


def _make_gold_df(name: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "alert_id": [f"{name}_1", f"{name}_2"],
            "ts_event": [datetime.now(UTC) - timedelta(hours=h) for h in (1, 5)],
            "delta": [0.5, 0.6],
            "gamma": [0.01, 0.02],
            "iv": [0.3, 0.4],
            "iv_rank": [50, 60],
            "ticker": ["SPY", "QQQ"],
        }
    )


class _CountingReader:
    """Reader stub that records every read_* call.

    A second call to the same method with the same logical args must
    not happen during a single `run_quality_checks` invocation
    (excluding the bars reads which intentionally have distinct
    signatures and can't be deduped).
    """

    def __init__(self) -> None:
        self.read_flow_calls = 0
        self.read_darkpool_calls = 0
        self.read_bars_calls = 0
        self.read_gold_calls: dict[str, int] = {}

    def read_flow(self, **_kwargs):
        self.read_flow_calls += 1
        return _make_flow_df()

    def read_darkpool(self, **_kwargs):
        self.read_darkpool_calls += 1
        return _make_darkpool_df()

    def read_bars(self, **_kwargs):
        self.read_bars_calls += 1
        return _make_bars_df()

    def read_gold_features(self, *, dataset: str, **_kwargs):
        self.read_gold_calls[dataset] = self.read_gold_calls.get(dataset, 0) + 1
        return _make_gold_df(dataset)


@pytest.mark.asyncio
async def test_run_quality_checks_dedupes_heber_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """One `run_quality_checks` invocation must not pull the same
    Heber dataset twice. Flow + darkpool each have two consumers
    (summary + staleness); the two Gold datasets each have two
    consumers (ML features summary + recent labels). All should
    materialize once and be reused."""
    reader = _CountingReader()
    monkeypatch.setenv("ORION_DATA_QUALITY_CHECKER_PREFER_HEBER", "true")
    monkeypatch.setattr(dqc, "get_heber_reader", lambda: reader)
    # Force market-hours so the staleness branches don't short-circuit
    # before reaching the read.
    monkeypatch.setattr(dqc, "_is_market_hours", lambda *_a, **_kw: True, raising=False)

    # init_db isn't needed for this path; stub it so we don't touch
    # the test DB binding.
    with patch.object(dqc, "init_db", new=AsyncMock()):
        results = await dqc.run_quality_checks()

    # Flow: get_flow_summary + check_flow_staleness → 1 read (was 2)
    assert reader.read_flow_calls == 1, f"flow expected 1 read, got {reader.read_flow_calls} — cache dedup not working"
    # Darkpool: get_darkpool_summary + check_darkpool_staleness → 1 read (was 2)
    assert reader.read_darkpool_calls == 1, (
        f"darkpool expected 1 read, got {reader.read_darkpool_calls} — cache dedup not working"
    )
    # Each Gold dataset: 2 consumers (get_ml_features_summary + check_recent_labels_features)
    # → 1 read each.
    assert reader.read_gold_calls.get("labels_alert_barriers", 0) == 1
    assert reader.read_gold_calls.get("meta_label_features", 0) == 1

    # Bars reads have 4 distinct signatures (24h-all, 1h-all, 24h-critical,
    # 24h-SPY) so we don't expect dedup — just that no single signature
    # gets repeated. With 4 distinct sigs and the bars phase orchestration,
    # we expect exactly 4 read_bars calls.
    assert reader.read_bars_calls == 4, (
        f"bars expected 4 distinct reads, got {reader.read_bars_calls} "
        f"— either dedup over-collapsed or an extra unexpected call landed"
    )

    # Sanity: results dict is populated end-to-end.
    assert "bars" in results
    assert "flow" in results
    assert "darkpool" in results
    assert "ml_features" in results
    assert "recent_labels" in results


@pytest.mark.asyncio
async def test_run_quality_checks_evicts_cache_between_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    """After `run_quality_checks` returns, the ContextVar cache must
    be reset to None so the cache dict (and the DataFrames it held)
    are eligible for GC."""
    reader = _CountingReader()
    monkeypatch.setenv("ORION_DATA_QUALITY_CHECKER_PREFER_HEBER", "true")
    monkeypatch.setattr(dqc, "get_heber_reader", lambda: reader)
    monkeypatch.setattr(dqc, "_is_market_hours", lambda *_a, **_kw: True, raising=False)

    with patch.object(dqc, "init_db", new=AsyncMock()):
        await dqc.run_quality_checks()

    assert dqc._run_cache.get() is None, "run_quality_checks must reset the ContextVar before returning"


@pytest.mark.asyncio
async def test_public_helpers_without_cache_keep_old_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public per-check functions called outside `run_quality_checks`
    must NOT use a cache — preserves the original behavior that
    standalone callers and unit tests rely on."""
    reader = _CountingReader()
    monkeypatch.setenv("ORION_DATA_QUALITY_CHECKER_PREFER_HEBER", "true")
    monkeypatch.setattr(dqc, "get_heber_reader", lambda: reader)
    monkeypatch.setattr(dqc, "_is_market_hours", lambda *_a, **_kw: True, raising=False)

    # Two consecutive calls without a cache token MUST re-read.
    await dqc.get_flow_summary()
    await dqc.get_flow_summary()
    assert reader.read_flow_calls == 2, f"without cache, every public call must re-read; got {reader.read_flow_calls}"
