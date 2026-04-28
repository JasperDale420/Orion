import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select

from orion.config import system_settings
from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_solvers import Solver, SolverMetrics

logger = setup_struct_logger("orion.jobs.seed_solvers")

DEFAULT_BASELINE_SOLVER_ID = "diversified_baseline_v1"
AUTO_SEED_STAGES = {"paper", "test"}


def _solver_definition(
    *,
    solver_id: str,
    family_name: str,
    name: str,
    notes: str,
    rules: list[str],
    feature_set_id: str,
    risk_per_trade_bps: int,
    max_open_positions: int,
    max_ticker_exposure_pct: float,
    session_filter: list[str],
    fixed_tp_pct: float,
    fixed_sl_pct: float,
    time_exit_bars: int,
    volatility_penalty_threshold: float,
    info_ratio: float,
    sharpe_ratio: float,
    profit_factor: float,
    max_dd_pct: float,
    stability_score: float,
    oos_expect_bp: float,
) -> dict[str, Any]:
    config = {
        "version_id": solver_id,
        "rules": rules,
        "features": {
            "feature_set_id": feature_set_id,
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {
            "risk_per_trade_bps": risk_per_trade_bps,
            "max_open_positions": max_open_positions,
            "max_ticker_exposure_pct": max_ticker_exposure_pct,
            "session_filter": session_filter,
        },
        "universe": {
            "ticker_allowlist": None,
            "ticker_blocklist": None,
            "required_regime": None,
        },
        "exit_logic": {
            "fixed_tp_pct": fixed_tp_pct,
            "fixed_sl_pct": fixed_sl_pct,
            "time_exit_bars": time_exit_bars,
        },
        "volatility_penalty_threshold": volatility_penalty_threshold,
    }
    return {
        "solver_id": solver_id,
        "family_name": family_name,
        "name": name,
        "stage": "paper",
        "status": "active",
        "is_active": True,
        "created_by": "seed_script",
        "notes": notes,
        "config": config,
        # Router needs a valid SolverConfig shape; keep definition_json aligned with config.
        "definition_json": dict(config),
        "info_ratio": info_ratio,
        "sharpe_ratio": sharpe_ratio,
        "profit_factor": profit_factor,
        "max_dd_pct": max_dd_pct,
        "stability_score": stability_score,
        "oos_expect_bp": oos_expect_bp,
    }


SEED_SOLVERS: list[dict[str, Any]] = [
    _solver_definition(
        solver_id="bullish_sweep_paper_v1",
        family_name="BullishSweep",
        name="Bullish Sweep Paper V1",
        notes="Conservative bullish sweep solver for paper trading",
        rules=["rule_bullish_sweep_v1"],
        feature_set_id="v2_intraday",
        risk_per_trade_bps=50,
        max_open_positions=3,
        max_ticker_exposure_pct=5.0,
        session_filter=["RTH"],
        fixed_tp_pct=0.03,
        fixed_sl_pct=0.015,
        time_exit_bars=78,
        volatility_penalty_threshold=0.02,
        info_ratio=1.5,
        sharpe_ratio=1.8,
        profit_factor=1.4,
        max_dd_pct=5.0,
        stability_score=0.8,
        oos_expect_bp=12.0,
    ),
    _solver_definition(
        solver_id="bearish_put_paper_v1",
        family_name="BearishPutPressure",
        name="Bearish Put Pressure Paper V1",
        notes="Conservative bearish put pressure solver for paper trading",
        rules=["rule_bearish_put_pressure_v1"],
        feature_set_id="v2_intraday",
        risk_per_trade_bps=50,
        max_open_positions=3,
        max_ticker_exposure_pct=5.0,
        session_filter=["RTH"],
        fixed_tp_pct=0.025,
        fixed_sl_pct=0.015,
        time_exit_bars=78,
        volatility_penalty_threshold=0.02,
        info_ratio=1.3,
        sharpe_ratio=1.5,
        profit_factor=1.3,
        max_dd_pct=6.0,
        stability_score=0.75,
        oos_expect_bp=10.0,
    ),
    _solver_definition(
        solver_id="rsi_mean_revert_paper_v1",
        family_name="RSIMeanReversion",
        name="RSI Oversold Mean Reversion Paper V1",
        notes="Mean reversion on RSI oversold conditions",
        rules=["rsi_oversold_v1"],
        feature_set_id="v2_intraday",
        risk_per_trade_bps=40,
        max_open_positions=2,
        max_ticker_exposure_pct=5.0,
        session_filter=["RTH"],
        fixed_tp_pct=0.02,
        fixed_sl_pct=0.01,
        time_exit_bars=60,
        volatility_penalty_threshold=0.025,
        info_ratio=1.2,
        sharpe_ratio=1.4,
        profit_factor=1.25,
        max_dd_pct=4.0,
        stability_score=0.85,
        oos_expect_bp=8.0,
    ),
    _solver_definition(
        solver_id="swing_entry_paper_v1",
        family_name="SwingEntry",
        name="Swing Entry Paper V1",
        notes="Multi-day swing entry using daily context features",
        rules=["rule_swing_entry_v1"],
        feature_set_id="v2_swing",
        risk_per_trade_bps=75,
        max_open_positions=3,
        max_ticker_exposure_pct=5.0,
        session_filter=[],
        fixed_tp_pct=0.06,
        fixed_sl_pct=0.025,
        time_exit_bars=390,
        volatility_penalty_threshold=0.03,
        info_ratio=1.4,
        sharpe_ratio=1.6,
        profit_factor=1.35,
        max_dd_pct=7.0,
        stability_score=0.7,
        oos_expect_bp=15.0,
    ),
    _solver_definition(
        solver_id=DEFAULT_BASELINE_SOLVER_ID,
        family_name="DiversifiedBaseline",
        name="Diversified Baseline V1",
        notes="Baseline solver covering the main rules for paper fallback routing",
        rules=["rule_bullish_sweep_v1", "rule_bearish_put_pressure_v1", "rsi_oversold_v1", "rule_swing_entry_v1"],
        feature_set_id="v1_legacy",
        risk_per_trade_bps=50,
        max_open_positions=5,
        max_ticker_exposure_pct=5.0,
        session_filter=[],
        fixed_tp_pct=0.03,
        fixed_sl_pct=0.02,
        time_exit_bars=120,
        volatility_penalty_threshold=0.02,
        info_ratio=1.0,
        sharpe_ratio=1.2,
        profit_factor=1.2,
        max_dd_pct=8.0,
        stability_score=0.9,
        oos_expect_bp=10.0,
    ),
]


@dataclass(frozen=True)
class SolverInventoryStatus:
    active_solver_count: int
    seeded: bool
    baseline_solver_id: str | None


def _active_solver_clause() -> Any:
    return or_(Solver.status == "active", (Solver.status.is_(None)) & (Solver.is_active))


def _build_metrics_row(solver_def: dict[str, Any]) -> SolverMetrics:
    return SolverMetrics(
        id=str(uuid4()),
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


async def _count_active_solvers() -> int:
    async with async_session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(Solver).where(_active_solver_clause()))
    return int(count or 0)


async def _has_metrics(session: Any, solver_id: str) -> bool:
    metric_count = await session.scalar(
        select(func.count()).select_from(SolverMetrics).where(SolverMetrics.solver_id == solver_id)
    )
    return bool(metric_count)


def _resolve_baseline_id() -> str | None:
    if system_settings.baseline_solver_id:
        return system_settings.baseline_solver_id
    system_settings.baseline_solver_id = DEFAULT_BASELINE_SOLVER_ID
    logger.warning(
        "Baseline solver id missing; defaulting to seeded baseline",
        baseline_solver_id=DEFAULT_BASELINE_SOLVER_ID,
    )
    return system_settings.baseline_solver_id


async def seed_default_solvers() -> dict[str, Any]:
    await init_db()

    created = 0
    updated = 0
    skipped = 0

    async with async_session_factory() as session:
        for solver_def in SEED_SOLVERS:
            solver_id = solver_def["solver_id"]
            existing = await session.get(Solver, solver_id)
            if existing is None:
                session.add(
                    Solver(
                        solver_id=solver_id,
                        family_name=solver_def["family_name"],
                        name=solver_def["name"],
                        stage=solver_def["stage"],
                        status=solver_def["status"],
                        is_active=solver_def["is_active"],
                        created_by=solver_def["created_by"],
                        notes=solver_def["notes"],
                        config=solver_def["config"],
                        definition_json=solver_def["definition_json"],
                        info_ratio=solver_def["info_ratio"],
                        sharpe_ratio=solver_def["sharpe_ratio"],
                        profit_factor=solver_def["profit_factor"],
                        max_dd_pct=solver_def["max_dd_pct"],
                        stability_score=solver_def["stability_score"],
                        oos_expect_bp=solver_def["oos_expect_bp"],
                    )
                )
                created += 1
            else:
                existing.family_name = solver_def["family_name"]
                existing.name = solver_def["name"]
                existing.stage = solver_def["stage"]
                existing.status = solver_def["status"]
                existing.is_active = solver_def["is_active"]
                existing.created_by = solver_def["created_by"]
                existing.notes = solver_def["notes"]
                existing.config = solver_def["config"]
                existing.definition_json = solver_def["definition_json"]
                existing.info_ratio = solver_def["info_ratio"]
                existing.sharpe_ratio = solver_def["sharpe_ratio"]
                existing.profit_factor = solver_def["profit_factor"]
                existing.max_dd_pct = solver_def["max_dd_pct"]
                existing.stability_score = solver_def["stability_score"]
                existing.oos_expect_bp = solver_def["oos_expect_bp"]
                updated += 1

            if not await _has_metrics(session, solver_id):
                session.add(_build_metrics_row(solver_def))
            else:
                skipped += 1

        await session.commit()

    summary = {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "baseline_solver_id": DEFAULT_BASELINE_SOLVER_ID,
    }
    logger.info("Seeded default solvers", **summary)
    return summary


async def ensure_active_solvers_ready(stage: str | None) -> SolverInventoryStatus:
    normalized_stage = (stage or "paper").lower()
    await init_db()

    active_count = await _count_active_solvers()
    if active_count > 0:
        return SolverInventoryStatus(
            active_solver_count=active_count,
            seeded=False,
            baseline_solver_id=_resolve_baseline_id(),
        )

    if normalized_stage not in AUTO_SEED_STAGES:
        msg = f"No active solvers configured for stage={normalized_stage}. Refusing to start."
        logger.error(msg)
        raise RuntimeError(msg)

    logger.warning(
        "No active solvers found for paper-compatible stage; seeding defaults",
        stage=normalized_stage,
    )
    await seed_default_solvers()
    active_count = await _count_active_solvers()
    if active_count <= 0:
        msg = "Default solver seeding completed but no active solvers were available afterward."
        logger.error(msg)
        raise RuntimeError(msg)

    return SolverInventoryStatus(
        active_solver_count=active_count,
        seeded=True,
        baseline_solver_id=_resolve_baseline_id(),
    )


async def seed_default_solver() -> None:
    await seed_default_solvers()


if __name__ == "__main__":
    asyncio.run(seed_default_solvers())
