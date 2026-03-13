from unittest.mock import patch

import pandas as pd
import pytest

from orion.processing.backtest_engine import BacktestEngine
from orion.processing.label_engine import TripleBarrierLabeling
from orion.storage.models_gold import CandidateTrade


@pytest.fixture
def mock_risk_manager():
    # Patch the class where it is DEFINED, because it is imported locally in __init__
    with patch("orion.execution.risk_manager.RiskManager") as MockRM:  # noqa: N806
        instance = MockRM.return_value
        # Default unrestricted behavior
        instance.calculate_size.return_value = 100.0
        instance.check_order.return_value = True
        yield instance


def test_backtest_simple_profit(mock_risk_manager):
    # 1. Setup Data
    # 2 days of data for AAPL
    dates = pd.date_range(start="2023-01-01", periods=10, freq="1h")
    # Price goes up from 100 to 110
    prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0]
    df = pd.DataFrame({"close": prices}, index=dates)

    trading_data = {"AAPL": df}

    # 2. Setup Candidates
    # Buy at step 0 (100.0)
    cand1 = CandidateTrade(
        candidate_id="c1",
        ticker="AAPL",
        timestamp_utc=dates[0],
        rule_id="rule1",
        direction="LONG",
        confidence=1.0,
        evidence={},
    )

    # 3. Setup Labeler (Mock Logic via Label Engine)
    # Barrier: 2% profit, 1% loss, 10 bars horizon.
    # 100 -> 109 is 9% gain. Should hit PT of 2% quickly.
    labeler = TripleBarrierLabeling(upper_barrier=0.02, lower_barrier=0.01, time_barrier_bars=10)

    # 4. Run Backtest
    engine = BacktestEngine(initial_capital=10000.0)
    engine.run([cand1], trading_data, labeler)

    # Verify RiskCalls
    mock_risk_manager.calculate_size.assert_called_once()
    mock_risk_manager.check_order.assert_called_once()

    # 5. Verify
    metrics = engine.get_metrics()

    assert metrics["total_trades"] == 1
    # Trade: Entry 100. Exit at 102 (2% gain) or next bar 101?
    # Label engine checks high/low usually, but here we pass 'close' series.
    # 100 -> 101 (1%), 102 (2%).
    # If pt=0.02, target is 102.0.
    # It hits 102.0 at index 2.
    # PnL = size * return - costs
    # Size = 100 * 100 = 10000 USD (from mock calc_size return 100 qty)
    # Gross Ret = 2% = 0.02.
    # Cost = 5bps entry + 5bps exit = 10bps = 0.001.
    # Net Ret = 0.019.
    # PnL = 10000 * 0.019 = 190.0

    assert metrics["total_pnl"] > 0
    assert len(engine.trades) == 1
    t = engine.trades[0]
    assert t["label"] == 1  # Profit
    assert t["exit_ts"] == dates[2]  # 102.0
    assert t["qty"] == 100.0


def test_backtest_risk_block(mock_risk_manager):
    """Test that BacktestEngine strictly follows RiskManager rejection."""
    # Setup - Risk Block
    mock_risk_manager.check_order.return_value = False

    dates = pd.date_range(start="2023-01-01", periods=5, freq="1h")
    df = pd.DataFrame({"close": [100.0] * 5}, index=dates)
    trading_data = {"AAPL": df}

    cand1 = CandidateTrade(
        candidate_id="c1",
        ticker="AAPL",
        timestamp_utc=dates[0],
        rule_id="rule1",
        direction="LONG",
        confidence=1.0,
        evidence={},
    )

    engine = BacktestEngine(initial_capital=10000.0)
    engine.run(
        [cand1], trading_data
    )  # Labeler optional if skipping anyway? No, labeler needed to process logic but let's see logic order
    # Actually run loop skips before labeler usage if risk fails

    metrics = engine.get_metrics()
    assert metrics["total_trades"] == 0
    assert metrics["skipped_trades"] == 1
    assert engine.skipped_trades[0]["reason"] == "RISK_CHECK_FAIL"


def test_backtest_no_trades_metrics(mock_risk_manager):
    engine = BacktestEngine()
    metrics = engine.get_metrics()
    assert metrics["total_trades"] == 0
    assert metrics["sharpe"] == 0.0
