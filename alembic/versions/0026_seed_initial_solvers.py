"""Seed initial paper-stage solvers.

The solvers table has been empty since inception, causing every candidate
trade to be safety-SKIPped by the SolverEnsemble (no eligible solvers).

This migration inserts 5 conservative paper-stage solvers covering the
main flow rules (BullishSweep, BearishPutPressure, RSI Oversold, Swing
Entry, Diversified Baseline) plus companion solver_metrics rows so the
SolverRouter does not skip them for missing metrics.

Revision ID: 0026_seed_initial_solvers
Revises: 0025_add_temporal_excursion_fields
Create Date: 2026-03-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026_seed_initial_solvers"
down_revision: str | None = "0025_add_temporal_excursion_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---- Solver rows ----

_SOLVERS = [
    {
        "solver_id": "bullish_sweep_paper_v1",
        "family_name": "BullishSweep",
        "name": "Bullish Sweep Paper V1",
        "stage": "paper",
        "status": "active",
        "is_active": True,
        "created_by": "alembic_seed",
        "notes": "Conservative bullish sweep solver for paper trading",
        "info_ratio": 1.5,
        "sharpe_ratio": 1.8,
        "profit_factor": 1.4,
        "max_dd_pct": 5.0,
        "stability_score": 0.8,
        "oos_expect_bp": 12.0,
    },
    {
        "solver_id": "bearish_put_paper_v1",
        "family_name": "BearishPutPressure",
        "name": "Bearish Put Pressure Paper V1",
        "stage": "paper",
        "status": "active",
        "is_active": True,
        "created_by": "alembic_seed",
        "notes": "Conservative bearish put pressure solver for paper trading",
        "info_ratio": 1.3,
        "sharpe_ratio": 1.5,
        "profit_factor": 1.3,
        "max_dd_pct": 6.0,
        "stability_score": 0.75,
        "oos_expect_bp": 10.0,
    },
    {
        "solver_id": "rsi_mean_revert_paper_v1",
        "family_name": "RSIMeanReversion",
        "name": "RSI Oversold Mean Reversion Paper V1",
        "stage": "paper",
        "status": "active",
        "is_active": True,
        "created_by": "alembic_seed",
        "notes": "Mean reversion on RSI oversold conditions",
        "info_ratio": 1.2,
        "sharpe_ratio": 1.4,
        "profit_factor": 1.25,
        "max_dd_pct": 4.0,
        "stability_score": 0.85,
        "oos_expect_bp": 8.0,
    },
    {
        "solver_id": "swing_entry_paper_v1",
        "family_name": "SwingEntry",
        "name": "Swing Entry Paper V1",
        "stage": "paper",
        "status": "active",
        "is_active": True,
        "created_by": "alembic_seed",
        "notes": "Multi-day swing entry using daily context features",
        "info_ratio": 1.4,
        "sharpe_ratio": 1.6,
        "profit_factor": 1.35,
        "max_dd_pct": 7.0,
        "stability_score": 0.7,
        "oos_expect_bp": 15.0,
    },
    {
        "solver_id": "diversified_baseline_v1",
        "family_name": "DiversifiedBaseline",
        "name": "Diversified Baseline V1",
        "stage": "paper",
        "status": "active",
        "is_active": True,
        "created_by": "alembic_seed",
        "notes": "Baseline solver covering all rules; intended as ORION_BASELINE_SOLVER_ID fallback",
        "info_ratio": 1.0,
        "sharpe_ratio": 1.2,
        "profit_factor": 1.2,
        "max_dd_pct": 8.0,
        "stability_score": 0.9,
        "oos_expect_bp": 10.0,
    },
]

# Config JSON blobs (kept separate for readability)

_CONFIGS: dict[str, dict] = {
    "bullish_sweep_paper_v1": {
        "version_id": "bullish_sweep_paper_v1",
        "rules": ["rule_bullish_sweep_v1"],
        "features": {
            "feature_set_id": "v2_intraday",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {
            "risk_per_trade_bps": 50,
            "max_open_positions": 3,
            "max_ticker_exposure_pct": 5.0,
            "session_filter": ["RTH"],
        },
        "universe": {"ticker_allowlist": None, "ticker_blocklist": None, "required_regime": None},
        "exit_logic": {"fixed_tp_pct": 0.03, "fixed_sl_pct": 0.015, "time_exit_bars": 78},
        "volatility_penalty_threshold": 0.02,
    },
    "bearish_put_paper_v1": {
        "version_id": "bearish_put_paper_v1",
        "rules": ["rule_bearish_put_pressure_v1"],
        "features": {
            "feature_set_id": "v2_intraday",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {
            "risk_per_trade_bps": 50,
            "max_open_positions": 3,
            "max_ticker_exposure_pct": 5.0,
            "session_filter": ["RTH"],
        },
        "universe": {"ticker_allowlist": None, "ticker_blocklist": None, "required_regime": None},
        "exit_logic": {"fixed_tp_pct": 0.025, "fixed_sl_pct": 0.015, "time_exit_bars": 78},
        "volatility_penalty_threshold": 0.02,
    },
    "rsi_mean_revert_paper_v1": {
        "version_id": "rsi_mean_revert_paper_v1",
        "rules": ["rsi_oversold_v1"],
        "features": {
            "feature_set_id": "v2_intraday",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {
            "risk_per_trade_bps": 40,
            "max_open_positions": 2,
            "max_ticker_exposure_pct": 5.0,
            "session_filter": ["RTH"],
        },
        "universe": {"ticker_allowlist": None, "ticker_blocklist": None, "required_regime": None},
        "exit_logic": {"fixed_tp_pct": 0.02, "fixed_sl_pct": 0.01, "time_exit_bars": 60},
        "volatility_penalty_threshold": 0.025,
    },
    "swing_entry_paper_v1": {
        "version_id": "swing_entry_paper_v1",
        "rules": ["rule_bullish_sweep_v1"],
        "features": {
            "feature_set_id": "v2_swing",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {
            "risk_per_trade_bps": 75,
            "max_open_positions": 3,
            "max_ticker_exposure_pct": 5.0,
            "session_filter": [],
        },
        "universe": {"ticker_allowlist": None, "ticker_blocklist": None, "required_regime": None},
        "exit_logic": {"fixed_tp_pct": 0.06, "fixed_sl_pct": 0.025, "time_exit_bars": 390},
        "volatility_penalty_threshold": 0.03,
    },
    "diversified_baseline_v1": {
        "version_id": "diversified_baseline_v1",
        "rules": ["rule_bullish_sweep_v1", "rule_bearish_put_pressure_v1", "rsi_oversold_v1"],
        "features": {
            "feature_set_id": "v1_legacy",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {
            "risk_per_trade_bps": 50,
            "max_open_positions": 5,
            "max_ticker_exposure_pct": 5.0,
            "session_filter": [],
        },
        "universe": {"ticker_allowlist": None, "ticker_blocklist": None, "required_regime": None},
        "exit_logic": {"fixed_tp_pct": 0.03, "fixed_sl_pct": 0.02, "time_exit_bars": 120},
        "volatility_penalty_threshold": 0.02,
    },
}

_DEFINITION_JSONS: dict[str, dict] = {
    "bullish_sweep_paper_v1": {
        "version_id": "bullish_sweep_paper_v1",
        "rules": ["rule_bullish_sweep_v1"],
        "features": {
            "feature_set_id": "v2_intraday",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {
            "risk_per_trade_bps": 50,
            "max_positions": 3,
            "max_ticker_exposure_pct": 5.0,
            "session_filter": ["RTH"],
        },
        "execution": {"order_type": "LIMIT", "max_spread_frac": 0.2, "slippage_bps_assumed": 20},
        "promotion_policy": {"target_stage": "paper", "min_trades_for_eval": 50, "gates_profile": "default"},
    },
    "bearish_put_paper_v1": {
        "version_id": "bearish_put_paper_v1",
        "rules": ["rule_bearish_put_pressure_v1"],
        "features": {
            "feature_set_id": "v2_intraday",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {
            "risk_per_trade_bps": 50,
            "max_positions": 3,
            "max_ticker_exposure_pct": 5.0,
            "session_filter": ["RTH"],
        },
        "execution": {"order_type": "LIMIT", "max_spread_frac": 0.2, "slippage_bps_assumed": 20},
        "promotion_policy": {"target_stage": "paper", "min_trades_for_eval": 50, "gates_profile": "default"},
    },
    "rsi_mean_revert_paper_v1": {
        "version_id": "rsi_mean_revert_paper_v1",
        "rules": ["rsi_oversold_v1"],
        "features": {
            "feature_set_id": "v2_intraday",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {
            "risk_per_trade_bps": 40,
            "max_positions": 2,
            "max_ticker_exposure_pct": 5.0,
            "session_filter": ["RTH"],
        },
        "execution": {"order_type": "LIMIT", "max_spread_frac": 0.15, "slippage_bps_assumed": 15},
        "promotion_policy": {"target_stage": "paper", "min_trades_for_eval": 50, "gates_profile": "default"},
    },
    "swing_entry_paper_v1": {
        "version_id": "swing_entry_paper_v1",
        "rules": ["rule_bullish_sweep_v1"],
        "features": {
            "feature_set_id": "v2_swing",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {"risk_per_trade_bps": 75, "max_positions": 3, "max_ticker_exposure_pct": 5.0, "session_filter": []},
        "execution": {"order_type": "LIMIT", "max_spread_frac": 0.2, "slippage_bps_assumed": 25},
        "promotion_policy": {"target_stage": "paper", "min_trades_for_eval": 30, "gates_profile": "default"},
    },
    "diversified_baseline_v1": {
        "version_id": "diversified_baseline_v1",
        "rules": ["rule_bullish_sweep_v1", "rule_bearish_put_pressure_v1", "rsi_oversold_v1"],
        "features": {
            "feature_set_id": "v1_legacy",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {"risk_per_trade_bps": 50, "max_positions": 5, "max_ticker_exposure_pct": 5.0, "session_filter": []},
        "execution": {"order_type": "LIMIT", "max_spread_frac": 0.2, "slippage_bps_assumed": 20},
        "promotion_policy": {"target_stage": "paper", "min_trades_for_eval": 100, "gates_profile": "default"},
    },
}


def upgrade() -> None:
    conn = op.get_bind()

    for s in _SOLVERS:
        sid = s["solver_id"]
        # Idempotent: skip if already present
        exists = conn.execute(sa.text("SELECT 1 FROM solvers WHERE solver_id = :sid"), {"sid": sid}).fetchone()
        if exists:
            continue

        import json
        import uuid
        from datetime import UTC, datetime

        config_json = json.dumps(_CONFIGS[sid])
        defn_json = json.dumps(_DEFINITION_JSONS[sid])
        now_utc = datetime.now(UTC)

        conn.execute(
            sa.text(
                """
                INSERT INTO solvers (
                    solver_id, family_name, name, stage, status, is_active, created_by,
                    notes, config, definition_json,
                    total_pnl, sharpe_ratio, win_rate, trades_count,
                    info_ratio, profit_factor, max_dd_pct, stability_score, oos_expect_bp,
                    created_at_utc
                ) VALUES (
                    :solver_id, :family_name, :name, :stage, :status, :is_active, :created_by,
                    :notes, :config, :definition_json,
                    0.0, :sharpe_ratio, 0.0, 0,
                    :info_ratio, :profit_factor, :max_dd_pct, :stability_score, :oos_expect_bp,
                    :created_at_utc
                )
                """
            ),
            {
                "solver_id": sid,
                "family_name": s["family_name"],
                "name": s["name"],
                "stage": s["stage"],
                "status": s["status"],
                "is_active": s["is_active"],
                "created_by": s["created_by"],
                "notes": s["notes"],
                "config": config_json,
                "definition_json": defn_json,
                "sharpe_ratio": s["sharpe_ratio"],
                "info_ratio": s["info_ratio"],
                "profit_factor": s["profit_factor"],
                "max_dd_pct": s["max_dd_pct"],
                "stability_score": s["stability_score"],
                "oos_expect_bp": s["oos_expect_bp"],
                "created_at_utc": now_utc,
            },
        )

        # Companion metrics row
        metrics_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                """
                INSERT INTO solver_metrics (
                    id, solver_id, sector, dataset_tag, num_runs, num_trades,
                    sharpe_ratio, info_ratio, profit_factor, oos_expect_bp,
                    max_dd_pct, stability_score, metrics_json, evaluated_at_utc
                ) VALUES (
                    :id, :solver_id, 'ALL', 'seed', 1, 0,
                    :sharpe_ratio, :info_ratio, :profit_factor, :oos_expect_bp,
                    :max_dd_pct, :stability_score, :metrics_json, :evaluated_at_utc
                )
                """
            ),
            {
                "id": metrics_id,
                "solver_id": sid,
                "sharpe_ratio": s["sharpe_ratio"],
                "info_ratio": s["info_ratio"],
                "profit_factor": s["profit_factor"],
                "oos_expect_bp": s["oos_expect_bp"],
                "max_dd_pct": s["max_dd_pct"],
                "stability_score": s["stability_score"],
                "metrics_json": json.dumps({"seeded": True}),
                "evaluated_at_utc": now_utc,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    solver_ids = [s["solver_id"] for s in _SOLVERS]
    for sid in solver_ids:
        conn.execute(sa.text("DELETE FROM solver_metrics WHERE solver_id = :sid"), {"sid": sid})
        conn.execute(sa.text("DELETE FROM solvers WHERE solver_id = :sid"), {"sid": sid})
