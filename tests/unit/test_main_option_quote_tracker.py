from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sys
import types
from unittest.mock import MagicMock

import pandas as pd
import pytest

stub_module = types.ModuleType("orion.connectors.alpaca_option_greeks_connector")


class _StubAlpacaOptionGreeksConnector:
    async def get_greeks_batch(self, _symbols):  # type: ignore[no-untyped-def]
        return {}


stub_module.AlpacaOptionGreeksConnector = _StubAlpacaOptionGreeksConnector
sys.modules.setdefault("orion.connectors.alpaca_option_greeks_connector", stub_module)

import orion.main_option_quote_tracker as tracker


@pytest.mark.asyncio
async def test_get_pending_checkpoints_returns_empty_list_for_empty_heber_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_flow(self, **_kwargs):  # type: ignore[no-untyped-def]
            return pd.DataFrame()

    monkeypatch.setenv("ORION_OPTION_QUOTE_TRACKER_PREFER_HEBER", "1")
    monkeypatch.setattr(tracker, "get_heber_reader", lambda: _FakeReader())

    pending = await tracker._get_pending_checkpoints_from_heber()

    assert pending == []


@pytest.mark.asyncio
async def test_get_pending_checkpoints_propagates_none_when_heber_helper_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_helper() -> None:
        return None

    monkeypatch.setenv("ORION_OPTION_QUOTE_TRACKER_PREFER_HEBER", "1")
    monkeypatch.setattr(tracker, "_get_pending_checkpoints_from_heber", _fake_helper)

    pending = await tracker.get_pending_checkpoints()

    assert pending is None


@pytest.mark.asyncio
async def test_get_pending_checkpoints_returns_none_and_logs_when_heber_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_flow(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    mock_logger = MagicMock()
    monkeypatch.setenv("ORION_OPTION_QUOTE_TRACKER_PREFER_HEBER", "1")
    monkeypatch.setattr(tracker, "logger", mock_logger)
    monkeypatch.setattr(tracker, "get_heber_reader", lambda: _FakeReader())

    pending = await tracker._get_pending_checkpoints_from_heber()

    assert pending is None
    assert mock_logger.warning.call_count == 1
    assert mock_logger.warning.call_args.kwargs["extra"]["event_type"] == "OPTION_QUOTE_TRACKER_HEBER_READ_FAILED"


@pytest.mark.asyncio
async def test_get_pending_checkpoints_returns_none_and_logs_when_schema_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_flow(self, **_kwargs):  # type: ignore[no-untyped-def]
            now = datetime.now(UTC)
            return pd.DataFrame(
                {
                    "event_id": ["flow-1"],
                    "flow_ts_utc": [now - timedelta(minutes=5)],
                    "ticker": ["AAPL"],
                }
            )

    mock_logger = MagicMock()
    monkeypatch.setenv("ORION_OPTION_QUOTE_TRACKER_PREFER_HEBER", "1")
    monkeypatch.setattr(tracker, "logger", mock_logger)
    monkeypatch.setattr(tracker, "get_heber_reader", lambda: _FakeReader())

    pending = await tracker._get_pending_checkpoints_from_heber()

    assert pending is None
    assert mock_logger.warning.call_count == 1
    assert mock_logger.warning.call_args.kwargs["extra"]["event_type"] == "OPTION_QUOTE_TRACKER_HEBER_SCHEMA_MISMATCH"
