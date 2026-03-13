import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from orion.core.solver_schema import ExitLogic, SolverConfig
from orion.core.solver_validation import ensure_solver_definition_json
from orion.shared.db_utils import db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from orion.storage.models_solvers import Solver

logger = setup_struct_logger("orion.jobs.seed_solvers")


async def seed_default_solver() -> None:
    logger.info("Initializing DB...")
    await init_db()

    exit_logic = ExitLogic(
        take_profit_atr_multiple=2.0,
        stop_loss_atr_multiple=1.0,
        time_exit_bars=60,
    )

    # Deterministic ID based on config content (excluding version_id)
    base_config = {
        "rules": [
            {"id": "rule_bullish_sweep_v1", "params": {"min_premium": 100000.0}},
        ],
        "features": {
            "feature_set_id": "v1_legacy",
            "event_features": [],
            "window_features": [],
            "feature_engine_version": "v1",
        },
        "model": {"type": "none"},
        "risk": {
            "risk_per_trade_bps": 100,
            "max_open_positions": 3,
            "max_ticker_exposure_pct": 5.0,
            "time_of_day_bans": None,
            "session_filter": ["REG"],
        },
        "exit_logic": exit_logic.model_dump(),
        "volatility_penalty_threshold": 0.02,
        "entry_logic": None,
        "rule_overrides": {"rule_bullish_sweep_v1": {"min_premium": 100000.0}},
    }

    # Hash it
    config_str = json.dumps(base_config, sort_keys=True)
    version_id = hashlib.sha256(config_str.encode()).hexdigest()[:12]

    solver_config = SolverConfig(
        version_id=version_id,
        rules=base_config["rules"],
        features=base_config["features"],
        model=base_config["model"],
        risk=base_config["risk"],
        exit_logic=exit_logic,
        volatility_penalty_threshold=base_config["volatility_penalty_threshold"],
        entry_logic=base_config["entry_logic"],
        rule_overrides=base_config["rule_overrides"],
    )

    logger.info("generated_solver_id", solver_id=version_id)

    async def _seed_solver_transaction(session: Any) -> None:
        # Check if exists
        existing = await session.get(Solver, version_id)
        if existing:
            logger.info("Solver already exists. Activating...")
            existing.is_active = True
        else:
            logger.info("Creating new Solver...")
            new_solver = Solver(
                solver_id=version_id,
                family_name="Momentum_V1",
                config=solver_config.model_dump(),
                is_active=True,
                stage="research",  # Start in research for refinement loop
                created_at_utc=datetime.now(UTC),
                definition_json=ensure_solver_definition_json(solver_config.model_dump(mode="json"), None),
            )
            session.add(new_solver)
        logger.info("Default Solver Seeded and Activated.")

    await db_write(_seed_solver_transaction)


if __name__ == "__main__":
    asyncio.run(seed_default_solver())
