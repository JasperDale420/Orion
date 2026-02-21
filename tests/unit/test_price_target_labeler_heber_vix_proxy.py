from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import orion.main_price_target_labeler as labeler


def test_get_heber_vix_proxy_snapshot_uses_latest_and_prior_close(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"ts_event": entry_ts - timedelta(minutes=2), "close": 18.2},
                    {"ts_event": entry_ts - timedelta(days=1), "close": 17.8},
                    {"ts_event": entry_ts + timedelta(minutes=1), "close": 99.9},
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    snapshot = labeler._get_heber_vix_proxy_snapshot_at_or_before(entry_ts)
    assert snapshot is not None
    assert snapshot["vix"] == 18.2
    assert snapshot["vix_1d_change"] == pytest.approx(0.4)
    assert snapshot["vix_regime"] == "NORMAL"


@pytest.mark.asyncio
async def test_get_regime_at_entry_prefers_heber_vix_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    import orion.analysis.regime as regime_module

    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {}

    class _FakeDetector:
        def detect(self, **kwargs: Any):
            captured.update(kwargs)
            return SimpleNamespace(
                trend=SimpleNamespace(value="UP"),
                vol=SimpleNamespace(value="NORMAL"),
                risk=SimpleNamespace(value="RISK_ON"),
                session=SimpleNamespace(value="OPEN"),
                vix_level=kwargs.get("vix"),
                vix_regime=SimpleNamespace(value="NORMAL"),
            )

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber vix+tide data are available")

    monkeypatch.setattr(regime_module, "MultiAxisRegimeDetector", _FakeDetector)
    monkeypatch.setattr(
        labeler,
        "_get_heber_vix_proxy_snapshot_at_or_before",
        lambda _entry_ts: {"vix": 18.0, "vix_1d_change": 0.5, "vix_regime": "NORMAL"},
        raising=False,
    )
    monkeypatch.setattr(labeler, "_get_heber_market_tide_net_premium", lambda *_args, **_kwargs: 25.0, raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = labeler.get_regime_at_entry(entry_ts)

    assert captured["vix"] == 18.0
    assert captured["vix_1d_change"] == 0.5
    assert captured["market_tide_net"] == 25.0
    assert result["vix_at_entry"] == 18.0


@pytest.mark.asyncio
async def test_get_regime_at_entry_leaves_vix_none_when_heber_vix_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orion.analysis.regime as regime_module

    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {}

    class _FakeDetector:
        def detect(self, **kwargs: Any):
            captured.update(kwargs)
            return SimpleNamespace(
                trend=SimpleNamespace(value="UP"),
                vol=SimpleNamespace(value="NORMAL"),
                risk=SimpleNamespace(value="RISK_ON"),
                session=SimpleNamespace(value="OPEN"),
                vix_level=kwargs.get("vix"),
                vix_regime=SimpleNamespace(value="ELEVATED"),
            )

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber vix proxy is unavailable")

    monkeypatch.setattr(regime_module, "MultiAxisRegimeDetector", _FakeDetector)
    monkeypatch.setattr(
        labeler, "_get_heber_vix_proxy_snapshot_at_or_before", lambda *_args, **_kwargs: None, raising=False
    )
    monkeypatch.setattr(labeler, "_get_heber_market_tide_net_premium", lambda *_args, **_kwargs: 12.0, raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = labeler.get_regime_at_entry(entry_ts)

    assert captured["vix"] is None
    assert captured["vix_1d_change"] is None
    assert result["vix_at_entry"] is None


@pytest.mark.asyncio
async def test_get_regime_at_entry_leaves_market_tide_none_when_heber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orion.analysis.regime as regime_module

    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {}

    class _FakeDetector:
        def detect(self, **kwargs: Any):
            captured.update(kwargs)
            return SimpleNamespace(
                trend=SimpleNamespace(value="UP"),
                vol=SimpleNamespace(value="NORMAL"),
                risk=SimpleNamespace(value="RISK_ON"),
                session=SimpleNamespace(value="OPEN"),
                vix_level=kwargs.get("vix"),
                vix_regime=SimpleNamespace(value="NORMAL"),
            )

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be called directly for market tide fallback")

    monkeypatch.setattr(regime_module, "MultiAxisRegimeDetector", _FakeDetector)
    monkeypatch.setattr(
        labeler,
        "_get_heber_vix_proxy_snapshot_at_or_before",
        lambda _entry_ts: {"vix": 19.0, "vix_1d_change": 0.3, "vix_regime": "NORMAL"},
        raising=False,
    )
    monkeypatch.setattr(labeler, "_get_heber_market_tide_net_premium", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = labeler.get_regime_at_entry(entry_ts)

    assert captured["market_tide_net"] is None
    assert result["vix_at_entry"] == 19.0
