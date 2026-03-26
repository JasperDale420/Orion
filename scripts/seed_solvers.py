"""Seed initial solvers into the Orion database.

Creates 5 conservative paper-stage solvers covering the main flow rules:
- Bullish Sweep (intraday)
- Bearish Put Pressure (intraday)
- Swing Entry (swing)
- RSI Oversold Mean Reversion (intraday)
- Diversified Baseline (all rules)

Each solver gets a companion SolverMetrics row so the SolverRouter
does not skip them for missing metrics.

Usage:
    uv run python scripts/seed_solvers.py
"""

import asyncio
import contextlib
import os
import sys
import uuid
from datetime import UTC, datetime

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv()

from orion.storage.db import async_session_factory, init_db
from orion.storage.models_solvers import Solver, SolverMetrics

# ---------------------------------------------------------------------------
# Solver definitions
# ---------------------------------------------------------------------------

SEED_SOLVERS: list[dict] = [
    {
        "solver_id": "bullish_sweep_paper_v1",
        "family_name": "BullishSweep",
        "name": "Bullish Sweep Paper V1",
        "stage": "paper",
        "status": "active",
        "is_active": True,
        "created_by": "seed_script",
        "notes": "Conservative bullish sweep solver for paper trading",
        "config": {
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
            "universe": {
                "ticker_allowlist": None,
                "ticker_blocklist": None,
                "required_regime": None,
            },
            "exit_logic": {
                "fixed_tp_pct": 0.03,
                "fixed_sl_pct": 0.015,
                "time_exit_bars": 78,
            },
            "volatility_penalty_threshold": 0.02,
        },
        "definition_json": {
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
            "execution": {
                "order_type": "LIMIT",
                "max_spread_frac": 0.2,
                "slippage_bps_assumed": 20,
            },
            "promotion_policy": {
                "target_stage": "paper",
                "min_trades_for_eval": 50,
                "gates_profile": "default",
            },
        },
        # Snapshot metrics on the Solver row itself
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
        "created_by": "seed_script",
        "notes": "Conservative bearish put pressure solver for paper trading",
        "config": {
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
            "universe": {
                "ticker_allowlist": None,
                "ticker_blocklist": None,
                "required_regime": None,
            },
            "exit_logic": {
                "fixed_tp_pct": 0.025,
                "fixed_sl_pct": 0.015,
                "time_exit_bars": 78,
            },
            "volatility_penalty_threshold": 0.02,
        },
        "definition_json": {
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
            "execution": {
                "order_type": "LIMIT",
                "max_spread_frac": 0.2,
                "slippage_bps_assumed": 20,
            },
            "promotion_policy": {
                "target_stage": "paper",
                "min_trades_for_eval": 50,
                "gates_profile": "default",
            },
        },
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
        "created_by": "seed_script",
        "notes": "Mean reversion on RSI oversold conditions",
        "config": {
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
            "universe": {
                "ticker_allowlist": None,
                "ticker_blocklist": None,
                "required_regime": None,
            },
            "exit_logic": {
                "fixed_tp_pct": 0.02,
                "fixed_sl_pct": 0.01,
                "time_exit_bars": 60,
            },
            "volatility_penalty_threshold": 0.025,
        },
        "definition_json": {
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
            "execution": {
                "order_type": "LIMIT",
                "max_spread_frac": 0.15,
                "slippage_bps_assumed": 15,
            },
            "promotion_policy": {
                "target_stage": "paper",
                "min_trades_for_eval": 50,
                "gates_profile": "default",
            },
        },
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
        "created_by": "seed_script",
        "notes": "Multi-day swing entry using daily context features",
        "config": {
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
            "universe": {
                "ticker_allowlist": None,
                "ticker_blocklist": None,
                "required_regime": None,
            },
            "exit_logic": {
                "fixed_tp_pct": 0.06,
                "fixed_sl_pct": 0.025,
                "time_exit_bars": 390,
            },
            "volatility_penalty_threshold": 0.03,
        },
        "definition_json": {
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
                "max_positions": 3,
                "max_ticker_exposure_pct": 5.0,
                "session_filter": [],
            },
            "execution": {
                "order_type": "LIMIT",
                "max_spread_frac": 0.2,
                "slippage_bps_assumed": 25,
            },
            "promotion_policy": {
                "target_stage": "paper",
                "min_trades_for_eval": 30,
                "gates_profile": "default",
            },
        },
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
        "created_by": "seed_script",
        "notes": "Baseline solver covering all rules; intended as ORION_BASELINE_SOLVER_ID fallback",
        "config": {
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
            "universe": {
                "ticker_allowlist": None,
                "ticker_blocklist": None,
                "required_regime": None,
            },
            "exit_logic": {
                "fixed_tp_pct": 0.03,
                "fixed_sl_pct": 0.02,
                "time_exit_bars": 120,
            },
            "volatility_penalty_threshold": 0.02,
        },
        "definition_json": {
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
                "max_positions": 5,
                "max_ticker_exposure_pct": 5.0,
                "session_filter": [],
            },
            "execution": {
                "order_type": "LIMIT",
                "max_spread_frac": 0.2,
                "slippage_bps_assumed": 20,
            },
            "promotion_policy": {
                "target_stage": "paper",
                "min_trades_for_eval": 100,
                "gates_profile": "default",
            },
        },
        "info_ratio": 1.0,
        "sharpe_ratio": 1.2,
        "profit_factor": 1.2,
        "max_dd_pct": 8.0,
        "stability_score": 0.9,
        "oos_expect_bp": 10.0,
    },
]


def _build_metrics_row(solver_def: dict) -> SolverMetrics:
    """Create a SolverMetrics row so the router does not skip the solver for missing metrics."""
    return SolverMetrics(
        id=str(uuid.uuid4()),
        solver_id=solver_def["solver_id"],
        sector="ALL",
        dataset_tag="seed",
        num_runs=1,
        num_trades=0,
        sharpe_ratio=solver_def.get("sharpe_ratio", 0.0),
        info_ratio=solver_def.get("info_ratio", 0.0),
        profit_factor=solver_def.get("profit_factor", 0.0),
        oos_expect_bp=solver_def.get("oos_expect_bp", 0.0),
        max_dd_pct=solver_def.get("max_dd_pct", 0.0),
        stability_score=solver_def.get("stability_score", 0.0),
        metrics_json={"seeded": True},
        evaluated_at_utc=datetime.now(UTC),
    )


async def seed() -> None:
    print("Orion Solver Seed: Initializing database connection...")
    await init_db()

    created = 0
    skipped = 0

    async with async_session_factory() as session:
        for solver_def in SEED_SOLVERS:
            solver_id = solver_def["solver_id"]
            existing = await session.get(Solver, solver_id)
            if existing:
                print(f"  SKIP  {solver_id} (already exists)")
                skipped += 1
                continue

            solver = Solver(
                solver_id=solver_id,
                family_name=solver_def["family_name"],
                name=solver_def.get("name"),
                stage=solver_def["stage"],
                status=solver_def.get("status", "active"),
                is_active=solver_def["is_active"],
                created_by=solver_def.get("created_by", "seed_script"),
                notes=solver_def.get("notes"),
                config=solver_def["config"],
                definition_json=solver_def.get("definition_json"),
                # Snapshot metrics on the Solver row
                info_ratio=solver_def.get("info_ratio", 0.0),
                sharpe_ratio=solver_def.get("sharpe_ratio", 0.0),
                profit_factor=solver_def.get("profit_factor", 0.0),
                max_dd_pct=solver_def.get("max_dd_pct", 0.0),
                stability_score=solver_def.get("stability_score", 0.0),
                oos_expect_bp=solver_def.get("oos_expect_bp"),
            )
            metrics = _build_metrics_row(solver_def)

            session.add(solver)
            session.add(metrics)
            print(f"  ADD   {solver_id} (family={solver_def['family_name']}, stage={solver_def['stage']})")
            created += 1

        await session.commit()

    print(f"\nSolver Seed Complete: {created} created, {skipped} skipped (already existed).")
    print("\nTIP: Set ORION_BASELINE_SOLVER_ID=diversified_baseline_v1 in .env for fallback routing.")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(seed())
