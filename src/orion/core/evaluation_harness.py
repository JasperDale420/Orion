from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

import pandas as pd

from orion.processing.backtest_engine import BacktestEngine
from orion.processing.label_engine import TripleBarrierLabeling
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_solvers import SolverRun


@dataclass(frozen=True)
class DatasetSpec:
    """
    Minimal DatasetSpec compatible with PRD.md FR 5.2.1.
    Existing code commonly uses `EvaluationTask`; this wrapper keeps the harness deterministic and explicit.
    """

    dataset_tag: str
    time_window_start: datetime | None
    time_window_end: datetime | None


def _compute_t1_times(
    candidates: list[CandidateTrade],
    price_data: dict[str, pd.DataFrame],
    labeler: TripleBarrierLabeling,
) -> pd.Series:
    candidates_sorted = sorted(candidates, key=lambda x: x.timestamp_utc)

    events_by_ticker: dict[str, list[datetime]] = {}
    for c in candidates_sorted:
        events_by_ticker.setdefault(c.ticker, []).append(c.timestamp_utc)

    t1_by_event: dict[tuple[str, datetime], datetime] = {}
    for ticker, events in events_by_ticker.items():
        df = price_data.get(ticker)
        if df is None or "close" not in df.columns:
            continue
        labels = labeler.compute_labels(df["close"], pd.DatetimeIndex(events))
        if labels.empty:
            continue
        for event_ts, row in labels.iterrows():
            hit_ts = row.get("barrier_hit_ts")
            if pd.notna(hit_ts):
                t1_by_event[(ticker, event_ts.to_pydatetime() if hasattr(event_ts, "to_pydatetime") else event_ts)] = (
                    hit_ts
                )

    t1_times: list[datetime] = []
    for c in candidates_sorted:
        hit = t1_by_event.get((c.ticker, c.timestamp_utc))
        if hit is None:
            # Best-effort: if compute_labels bfilled the start_ts, use that mapping
            # by looking for the nearest event_ts in the label index.
            df = price_data.get(c.ticker)
            if df is None or "close" not in df.columns:
                continue
            labels = labeler.compute_labels(df["close"], pd.DatetimeIndex([c.timestamp_utc]))
            if labels.empty:
                continue
            hit = labels.iloc[0].get("barrier_hit_ts")
            if hit is None or pd.isna(hit):
                continue
        t1_times.append(hit)

    if len(t1_times) != len(candidates_sorted):
        raise ValueError("Unable to compute t1_times for all candidates; dataset has missing price coverage.")

    return pd.Series(t1_times)


def run_solver_backtest(
    *,
    solver_id: str,
    candidates: list[CandidateTrade],
    price_data: dict[str, pd.DataFrame],
    dataset_spec: DatasetSpec,
    solver_config: Any,
    n_splits: int = 3,
    embargo_pct: float = 0.01,
    n_trials: int = 1,
) -> tuple[SolverRun, dict[str, Any]]:
    """
    PRD.md FR 5.2.1: RunSolverBacktest.

    Returns:
        (SolverRun row, metrics dict)
    """

    # Map solver config exit logic -> labeling parameters deterministically.
    upper = getattr(getattr(solver_config, "exit_logic", None), "fixed_tp_pct", None) or 0.02
    lower = getattr(getattr(solver_config, "exit_logic", None), "fixed_sl_pct", None) or 0.01
    time_bars = getattr(getattr(solver_config, "exit_logic", None), "time_exit_bars", None) or 60
    labeler = TripleBarrierLabeling(upper_barrier=upper, lower_barrier=lower, time_barrier_bars=time_bars)

    engine = BacktestEngine(initial_capital=100000.0)

    if not candidates:
        empty_metrics = engine.get_metrics(n_trials=n_trials)
        run = SolverRun(
            id=str(uuid4()),
            solver_id=solver_id,
            dataset_tag=dataset_spec.dataset_tag,
            time_window_start=dataset_spec.time_window_start,
            time_window_end=dataset_spec.time_window_end,
            num_candidates=0,
            num_trades=0,
            gross_pnl=0.0,
            net_pnl=0.0,
            profit_factor=0.0,
            max_drawdown_pct=0.0,
            expect_return_bp=0.0,
            metrics_json={"note": "no_candidates", **empty_metrics},
        )
        return run, {
            "mean_sharpe": 0.0,
            "deflated_sharpe_prob": 0.0,
            "total_trades": 0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
        }

    t1_times = _compute_t1_times(candidates=candidates, price_data=price_data, labeler=labeler)

    metrics = engine.run_cv(
        candidates=sorted(candidates, key=lambda x: x.timestamp_utc),
        price_data=price_data,
        solver_config=solver_config,
        n_splits=n_splits,
        embargo_pct=embargo_pct,
        t1_times=t1_times,
        labeler=labeler,
        n_trials=n_trials,
    )

    run = SolverRun(
        id=str(uuid4()),
        solver_id=solver_id,
        dataset_tag=dataset_spec.dataset_tag,
        time_window_start=dataset_spec.time_window_start,
        time_window_end=dataset_spec.time_window_end,
        num_candidates=len(candidates),
        num_trades=int(metrics.get("total_trades", 0)),
        gross_pnl=float(metrics.get("gross_pnl", 0.0)),
        net_pnl=float(metrics.get("net_pnl", 0.0)),
        profit_factor=float(metrics.get("profit_factor", 0.0)),
        max_drawdown_pct=float(metrics.get("max_drawdown_pct", 0.0)),
        expect_return_bp=float(metrics.get("expect_return_bp", 0.0)),
        metrics_json={
            "mean_sharpe": float(metrics.get("mean_sharpe", 0.0)),
            "deflated_sharpe_prob": float(metrics.get("deflated_sharpe_prob", 0.0)),
            "stability_score": float(metrics.get("stability_score", 0.0)),
            "n_splits": int(metrics.get("n_splits", n_splits)),
            "bootstrap_p_value": float(metrics.get("bootstrap_p_value", 1.0)),
        },
    )

    return run, metrics
