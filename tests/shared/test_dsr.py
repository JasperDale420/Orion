from unittest.mock import patch

from orion.processing.backtest_engine import BacktestEngine


def test_dsr_passing_n_trials():
    # Path to the function we want to check
    with patch("orion.processing.backtest_engine.compute_deflated_sharpe_ratio") as mock_dsr:
        mock_dsr.return_value = 0.95

        engine = BacktestEngine(initial_capital=10000)

        # Inject some fake trades so get_metrics proceeds
        engine.trades = [{"gross_pnl": 100.0, "net_pnl": 100.0, "net_ret": 0.01}]

        # 1. Call with n_trials=100
        engine.get_metrics(n_trials=100)

        # Verify call
        args, kwargs = mock_dsr.call_args
        assert kwargs["n_trials"] == 100

        # 2. Call with default
        engine.get_metrics()
        args, kwargs = mock_dsr.call_args
        assert kwargs["n_trials"] == 1
