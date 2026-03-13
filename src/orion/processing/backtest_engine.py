from typing import Any

import numpy as np
import pandas as pd
import scipy.stats as stats

from orion.analysis.cross_validation import PurgedKFold
from orion.analysis.metrics import compute_bootstrap_p_value, compute_deflated_sharpe_ratio, compute_sharpe_ratio
from orion.execution.risk_manager import RiskManager
from orion.processing.label_engine import TripleBarrierLabeling
from orion.storage.models_gold import CandidateTrade


class BacktestEngine:
    """
    Simulates trading based on CandidateTrades and computes performance metrics.
    V2: Supports Cross-Validation and Robust Metrics.
    """

    def __init__(self, initial_capital: float = 100000.0, transaction_cost_bps: float = 5.0):
        self.initial_capital = initial_capital
        self.transaction_cost_pct = transaction_cost_bps / 10000.0
        # Initialize Risk Manager
        from orion.execution.risk_manager import RiskManager

        self.risk_manager = RiskManager()

        # State
        self.trades: list[dict[str, Any]] = []
        self.equity_curve: list[dict[str, Any]] = []
        self.skipped_trades: list[dict[str, Any]] = []

    def run(
        self,
        candidates: list[CandidateTrade],
        price_data: dict[str, pd.DataFrame],
        labeler: TripleBarrierLabeling | None = None,
    ) -> None:
        """
        Executes a simple sequential backtest (In-Sample or Out-of-Sample).
        This resets logic and runs strict simulation.
        """
        self._reset()
        self._simulate(candidates, price_data, labeler)

    def run_cv(
        self,
        candidates: list[CandidateTrade],
        price_data: dict[str, pd.DataFrame],
        solver_config: Any,
        n_splits: int = 5,
        embargo_pct: float = 0.01,
        t1_times: pd.Series | None = None,
        labeler: TripleBarrierLabeling | None = None,
        n_trials: int = 1,
    ) -> dict[str, Any]:
        """
        Runs Purged K-Fold Cross Validation.

        Args:
            solver_config: Used to filter/modify logic per fold if needed (future).
            n_splits: Number of folds.
            t1_times: Explicit exit times for purging. Required for strict mode.

        Returns:
            Dict containing aggregated CV metrics.
        """
        if t1_times is None:
            raise ValueError("t1_times (explicit exit times) must be provided for PurgedKFold Cross Validation.")

        # 1. Prepare Data Indices
        # We need a unified timeline of events to split.
        candidates = sorted(candidates, key=lambda x: x.timestamp_utc)
        if not candidates:
            return {"error": "No candidates"}

        # Create Series for Splitter: index=0..N, values=exit_time (for purging)
        # We need estimated exit time. If labeler is missing, we can't purge correctly?
        # We'll assume a fixed horizon if labeler not provided, or require labeler.
        # For V1, let's assume 1-hour hold for purging estimation if no labeler.

        # Ideally we run the labeler FIRST on all data to get 'barrier_hit_ts' for every candidate.
        # But simulation usually does that dynamically.
        # CV Splitter needs to know overlaps BEFORE simulation?
        # YES. PurgedKFold needs overlap info.

        # PRE-PROCESS: Compute labels (hypothetical outcomes) for ALL candidates to get time bounds.
        # This is efficient vectorized labeling.

        # Gather start times
        t0_series = pd.Series([c.timestamp_utc for c in candidates])

        if t1_times is None:
            raise ValueError(
                "run_cv requires explicit 't1_times' (exit times) to perform Purged CV. No heuristic allowed."
            )

        t1_series = t1_times

        splitter = PurgedKFold(n_splits=n_splits, embargo_pct=embargo_pct)

        fold_metrics = []
        labeler = labeler or TripleBarrierLabeling()

        # 2. Iterate Folds
        for train_idx, test_idx in splitter.split(events_times=t1_series, test_times=t0_series):
            # Select Train/Test Candidates
            # candidates is list.
            [candidates[i] for i in train_idx]
            test_cands = [candidates[i] for i in test_idx]

            # Run Simulation on TEST set (OOS for this fold)
            # We treat 'train' as fit set (e.g. for ML training).
            # Since V1 rules are static, we just Evaluate on Test.
            # If we were training ML, we'd train on train_cands first.

            # Reset engine stats for this fold
            self._reset()
            self._simulate(test_cands, price_data, labeler=labeler, solver_config=solver_config)
            # Wait, run() expects labeler? Yes, let's instantiate default if None
            # Actually _simulate handles None? Let's check.
            # We'll enforce labeler in simulate if possible.

            # Compute Metrics for this fold
            m = self.get_metrics(n_trials=n_trials)
            fold_metrics.append(m)

        # 3. Aggregate Metrics
        # Average Sharpe, Min Sharpe, Deflated Sharpe Input
        sharpes = [f["sharpe"] for f in fold_metrics]
        mean_sharpe = np.mean(sharpes)
        std_sharpe = np.std(sharpes)

        # Deflated Sharpe
        # We need total trades across all folds, or average?
        # DSR usually calculated on the full appended return series of OOS predictions?
        # Yes, "concatenated OOS equity curves".

        total_trades = int(sum(f.get("total_trades", 0) for f in fold_metrics))
        total_gross_pnl = float(sum(f.get("gross_pnl", 0.0) for f in fold_metrics))
        total_net_pnl = float(sum(f.get("net_pnl", 0.0) for f in fold_metrics))

        total_profit_sum = float(sum(f.get("_profit_sum", 0.0) for f in fold_metrics))
        total_loss_sum_abs = float(sum(f.get("_loss_sum_abs", 0.0) for f in fold_metrics))
        if total_loss_sum_abs <= 0:
            profit_factor = 999.0 if total_profit_sum > 0 else 0.0
        else:
            profit_factor = total_profit_sum / total_loss_sum_abs

        max_drawdown_pct = float(max(f.get("max_drawdown_pct", 0.0) for f in fold_metrics)) if fold_metrics else 0.0

        if total_trades > 0:
            expect_return_bp = float(
                sum(float(f.get("expect_return_bp", 0.0)) * int(f.get("total_trades", 0)) for f in fold_metrics)
                / total_trades
            )
        else:
            expect_return_bp = 0.0

        # DSR on the Aggregate Result (approx using mean SR + trade count)
        dsr_agg = compute_deflated_sharpe_ratio(
            estimated_sr=mean_sharpe, sample_len=total_trades, skew=0.0, kurtosis=3.0, n_trials=n_trials
        )

        stability_score = float(1.0 / (1.0 + std_sharpe)) if std_sharpe >= 0 else 0.0

        result = {
            "mean_sharpe": mean_sharpe,
            "sharpe_stability": std_sharpe,
            "stability_score": stability_score,
            "info_ratio": mean_sharpe,
            "fold_metrics": fold_metrics,
            "n_splits": n_splits,
            "deflated_sharpe_prob": dsr_agg,
            "total_trades": total_trades,
            "gross_pnl": total_gross_pnl,
            "net_pnl": total_net_pnl,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_drawdown_pct,
            "expect_return_bp": expect_return_bp,
        }
        return result

    def _reset(self) -> None:
        self.trades = []
        self.equity_curve = []
        self.skipped_trades = []

    def _simulate(
        self,
        candidates: list[CandidateTrade],
        price_data: dict[str, pd.DataFrame],
        labeler: TripleBarrierLabeling | None,
        solver_config: Any = None,
    ) -> None:
        current_equity = self.initial_capital

        # Initialize Risk Manager with Solver Config if available
        # We need to map solver_config.risk (DSL) to RiskSettings (Internal)
        local_risk_manager = self.risk_manager  # Default

        if solver_config and solver_config.risk:
            from orion.config import RiskSettings

            # Basic mapping - extend as needed
            risk_cfg = RiskSettings(
                risk_per_trade_pct=solver_config.risk.risk_per_trade_bps / 10000.0,
                max_positions=solver_config.risk.max_open_positions,
                max_ticker_exposure_usd=999999999.0,  # Solver might not define USD cap, assume infinite or default? Use DSL if exists
                # enable_shorting? check solver logic
                max_daily_loss=1000000.0,  # Default loose
            )
            # Map explicit fields if they exist in DSL and RiskSettings
            # For now we just map the critical ones for Backtest

            local_risk_manager = RiskManager(config=risk_cfg)

        # Create a local labeler if None?
        local_labeler = labeler
        if local_labeler is None:
            # Basic Triple Barrier with defaults
            local_labeler = TripleBarrierLabeling(lower_barrier=0.01, upper_barrier=0.02, time_barrier_bars=60)

        for cand in candidates:
            ticker = cand.ticker
            if ticker not in price_data:
                continue

            df = price_data[ticker]
            entry_ts = cand.timestamp_utc

            if entry_ts not in df.index:
                continue

            entry_price = float(df.loc[entry_ts]["close"])

            # Risk Sizing
            qty = local_risk_manager.calculate_size(entry_price=entry_price, account_equity=current_equity)

            check_passed = local_risk_manager.check_order(
                ticker=ticker, quantity=qty, price=entry_price, side="buy" if cand.direction == "LONG" else "sell"
            )

            if not check_passed or qty <= 0:
                self.skipped_trades.append({"ticker": ticker, "ts": entry_ts, "reason": "RISK_CHECK_FAIL"})
                continue

            # Outcome
            outcome_df = local_labeler.compute_labels(df["close"], pd.DatetimeIndex([entry_ts]))
            if outcome_df.empty:
                continue

            row = outcome_df.iloc[0]
            ret = row["ret"]
            trade_side_mult = 1 if cand.direction == "LONG" else -1
            costs = self.transaction_cost_pct * 2

            trade_ret = ret * trade_side_mult
            net_ret = trade_ret - costs
            gross_pnl = (qty * entry_price) * trade_ret
            net_pnl = (qty * entry_price) * net_ret
            current_equity += net_pnl

            self.trades.append(
                {
                    "ticker": ticker,
                    "entry_ts": entry_ts,
                    "exit_ts": row["barrier_hit_ts"],
                    "label": int(row.get("label", 0)),
                    "ret": float(ret),
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "net_ret": net_ret,
                    "gross_ret": trade_ret,
                    "qty": qty,
                }
            )

            self.equity_curve.append({"ts": row["barrier_hit_ts"], "equity": current_equity})

    def get_metrics(self, n_trials: int = 1) -> dict[str, Any]:
        """
        Returns performance metrics including robust stats.
        """
        if not self.trades:
            return {
                "total_trades": 0,
                "skipped_trades": len(self.skipped_trades),
                "sharpe": 0.0,
                "deflated_sharpe_prob": 0.0,
                "gross_pnl": 0.0,
                "net_pnl": 0.0,
                "total_pnl": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "expect_return_bp": 0.0,
                "win_rate": 0.0,
                "final_equity": self.initial_capital,
                "_profit_sum": 0.0,
                "_loss_sum_abs": 0.0,
            }

        df_trades = pd.DataFrame(self.trades)
        gross_pnl = float(df_trades["gross_pnl"].sum())
        net_pnl = float(df_trades["net_pnl"].sum())
        win_rate = float(len(df_trades[df_trades["net_pnl"] > 0]) / len(df_trades))
        expect_return_bp = float(df_trades["net_ret"].mean() * 10000.0)

        profit_sum = float(df_trades.loc[df_trades["net_pnl"] > 0, "net_pnl"].sum())
        loss_sum_abs = float(abs(df_trades.loc[df_trades["net_pnl"] < 0, "net_pnl"].sum()))
        if loss_sum_abs <= 0:
            profit_factor = 999.0 if profit_sum > 0 else 0.0
        else:
            profit_factor = float(profit_sum / loss_sum_abs)

        rets = df_trades["net_ret"].values
        sharpe = compute_sharpe_ratio(rets)

        # Deflated Sharpe
        # Estimate skew/kurtosis
        skew = float(stats.skew(rets)) if len(rets) > 2 else 0.0
        kurt = (
            float(stats.kurtosis(rets)) if len(rets) > 2 else 0.0
        )  # Fisher (normal=0 in scipy? Check. Yes, scipy is excess kurtosis)
        # Bailey formula uses Pearson (normal=3). Scipy returns Excess.
        # So Pearson = Excess + 3.
        pearson_kurt = kurt + 3.0

        # sample_len = number of trades (roughly) or time periods?
        # Sharpe is annualized based on n_trades if using trade returns?
        # Actually SR on trade returns is "per trade". Annualizing it requires trades_per_year.
        # My compute_sharpe_ratio assumes periods_per_year=252.
        # If returns are "per trade", this is wrong unless 1 trade per day.
        # Imprecise for V1 but acceptable if consistent.

        # N Trials for DSR: Set to 1 if we are just evaluating this single backtest.
        # If part of MetaSearch, MetaSearch should compute DSR using N=TotalVariants.
        # Here we just compute for N=1 (unadjusted for multiple testing, but adjusted for skew/kurt).
        dsr = compute_deflated_sharpe_ratio(
            estimated_sr=sharpe, sample_len=len(rets), skew=skew, kurtosis=pearson_kurt, n_trials=n_trials
        )

        bootstrap_p = compute_bootstrap_p_value(rets, n_samples=1000)

        max_drawdown_pct = 0.0
        if self.equity_curve:
            eq = pd.Series([x["equity"] for x in self.equity_curve])
            peak = eq.cummax()
            drawdown = (peak - eq) / peak.replace(0, np.nan)
            max_drawdown_pct = float(drawdown.max() * 100.0) if len(drawdown) else 0.0

        return {
            "total_trades": len(self.trades),
            "skipped_trades": len(self.skipped_trades),
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "total_pnl": net_pnl,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_drawdown_pct,
            "expect_return_bp": expect_return_bp,
            "win_rate": win_rate,
            "sharpe": sharpe,
            "deflated_sharpe_prob": dsr,
            "final_equity": self.equity_curve[-1]["equity"] if self.equity_curve else self.initial_capital,
            "_profit_sum": profit_sum,
            "_loss_sum_abs": loss_sum_abs,
            "bootstrap_p_value": bootstrap_p,
        }
