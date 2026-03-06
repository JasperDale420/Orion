from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from orion.jobs.reconcile_backfill import DATASET_SPECS, run_reconciliation


def _mock_count_row(ticker: str, event_date: str, count: int) -> MagicMock:
    return MagicMock(ticker=ticker, event_date=event_date, count=count)


@pytest.mark.asyncio
@patch("orion.jobs.reconcile_backfill.get_heber_reader")
@patch("orion.jobs.reconcile_backfill.async_session_factory")
async def test_run_reconciliation_prefers_heber_counts(
    mock_session_factory: MagicMock,
    mock_get_heber_reader: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORION_RECONCILE_BACKFILL_PREFER_HEBER", raising=False)
    current_date = datetime.now(UTC).date().isoformat()

    mock_session = AsyncMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session

    bronze_results = []
    for _ in DATASET_SPECS:
        res = MagicMock()
        res.all.return_value = [_mock_count_row("AAPL", current_date, 2)]
        bronze_results.append(res)
    mock_session.execute.side_effect = bronze_results

    ts_values = [datetime.now(UTC), datetime.now(UTC)]
    fake_reader = MagicMock()
    fake_reader.read_bars.return_value = pd.DataFrame({"symbol": ["AAPL", "AAPL"], "bar_start_ts": ts_values})
    fake_reader.read_flow.return_value = pd.DataFrame({"ticker": ["AAPL", "AAPL"], "ts_event": ts_values})
    fake_reader.read_darkpool.return_value = pd.DataFrame({"ticker": ["AAPL", "AAPL"], "ts_event": ts_values})
    mock_get_heber_reader.return_value = fake_reader

    await run_reconciliation(lookback_days=1)

    assert mock_session.execute.call_count == len(DATASET_SPECS)


@pytest.mark.asyncio
@patch("orion.jobs.reconcile_backfill.get_heber_reader")
@patch("orion.jobs.reconcile_backfill.async_session_factory")
async def test_run_reconciliation_does_not_fall_back_to_local_sql(
    mock_session_factory: MagicMock,
    mock_get_heber_reader: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORION_RECONCILE_BACKFILL_PREFER_HEBER", raising=False)
    current_date = datetime.now(UTC).date().isoformat()

    mock_session = AsyncMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session

    side_effects = []
    for _ in DATASET_SPECS:
        bronze_res = MagicMock()
        bronze_res.all.return_value = [_mock_count_row("AAPL", current_date, 2)]
        silver_res = MagicMock()
        silver_res.all.return_value = [_mock_count_row("AAPL", current_date, 2)]
        side_effects.extend([bronze_res, silver_res])
    mock_session.execute.side_effect = side_effects

    fake_reader = MagicMock()
    fake_reader.read_bars.side_effect = RuntimeError("heber unavailable")
    fake_reader.read_flow.side_effect = RuntimeError("heber unavailable")
    fake_reader.read_darkpool.side_effect = RuntimeError("heber unavailable")
    mock_get_heber_reader.return_value = fake_reader

    await run_reconciliation(lookback_days=1)

    assert mock_session.execute.call_count == len(DATASET_SPECS)
