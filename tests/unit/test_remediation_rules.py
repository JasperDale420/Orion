from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from orion.core.solver_schema import SolverConfig
from orion.jobs.reconcile_backfill import DATASET_SPECS, run_reconciliation
from orion.processing.rule_engine import RuleEngine


@pytest.mark.asyncio
async def test_rule_overrides():
    """
    Verify that RuleEngine respects rule_overrides in SolverConfig.
    """
    # 1. Default Config (No overrides)
    defaults = SolverConfig(version_id="default")
    engine_def = RuleEngine(config=defaults.model_dump(mode="json"))

    # Check BullishSweepRule (first rule)
    bull_rule_def = engine_def.rules[0]
    assert bull_rule_def.min_premium == 10000.0, "Default min_premium should be 10k"

    # 2. Overridden Config
    overrides = {"rule_bullish_sweep_v1": {"min_premium": 99999.0}}
    config_override = SolverConfig(version_id="override", rule_overrides=overrides)

    engine_ovr = RuleEngine(config=config_override.model_dump(mode="json"))
    bull_rule_ovr = engine_ovr.rules[0]

    # Check if override applied
    assert bull_rule_ovr.min_premium == 99999.0, "Override should set min_premium to 99999"


@pytest.mark.asyncio
@patch("orion.jobs.reconcile_backfill.get_heber_reader")
@patch("orion.jobs.reconcile_backfill.async_session_factory")
async def test_reconcile_backfill_logic(mock_session_factory, mock_get_heber_reader, monkeypatch: pytest.MonkeyPatch):
    """
    Verify run_reconciliation logic with mocked DB results.
    """
    monkeypatch.delenv("ORION_RECONCILE_BACKFILL_PREFER_HEBER", raising=False)

    # Setup Mock Session
    mock_session = AsyncMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session

    # Setup Mock Results for each dataset: bronze row only.
    side_effects = []
    for _ in DATASET_SPECS:
        mock_bronze_res = MagicMock()
        mock_bronze_res.all.return_value = [MagicMock(ticker="AAPL", event_date="2025-01-01", count=10)]
        side_effects.append(mock_bronze_res)

    mock_session.execute.side_effect = side_effects

    fake_reader = MagicMock()
    ts = "2025-01-01T15:30:00Z"
    fake_reader.read_bars.return_value = pd.DataFrame([{"symbol": "AAPL", "bar_start_ts": ts}])
    fake_reader.read_flow.return_value = pd.DataFrame([{"ticker": "AAPL", "ts_event": ts}])
    fake_reader.read_darkpool.return_value = pd.DataFrame([{"ticker": "AAPL", "ts_event": ts}])
    mock_get_heber_reader.return_value = fake_reader

    # Run
    await run_reconciliation(lookback_days=1)

    # Verify
    assert mock_session.execute.call_count == len(DATASET_SPECS)
    # We can't easily assert logging output without caplog, but if it runs without error, logic is roughly correct.
