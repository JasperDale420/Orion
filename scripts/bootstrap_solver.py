import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

# Path hack to ensure we can import orion
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv()

import contextlib

from orion.config import system_settings
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import SystemStatus
from orion.storage.models_solvers import Solver, SolverMetrics


async def bootstrap():
    print("Orion Bootstrap: Initializing Database...")
    await init_db()

    # Define Default Solver "Paper V1"
    solver_id = "paper_v1"

    solver = Solver(
        solver_id=solver_id,
        family_name="OrionPaper",
        is_active=True,
        stage="paper",
        config={
            "version_id": solver_id,
            "universe": {
                "ticker_allowlist": system_settings.static_watchlist,
                "required_regime": None,  # Match all
            },
            "features": {"feature_set_id": "v1_basic"},
            "entry_logic": {
                "rules": [
                    # Example: Simple RSI or just AlwaysTrue if relying on external signal routing?
                    # Let's use a simple pass-through for now, assuming RuleEngine does heavy lifting
                    # OR we can add the dummy rule to ensure flow.
                    {"rule_name": "AlwaysTrueRule", "params": {}}
                ],
                "combination_method": "OR",
            },
            "exit_logic": {"take_profit_pct": 0.02, "stop_loss_pct": 0.01},
            "risk_per_trade_bps": 50,  # 0.5%
        },
    )

    metrics = SolverMetrics(
        id=str(uuid.uuid4()),
        solver_id=solver_id,
        sharpe_ratio=2.5,
        info_ratio=2.0,
        profit_factor=1.5,
        max_dd_pct=0.1,
        oos_expect_bp=15.0,
        stability_score=0.9,
    )

    sys_status = SystemStatus(
        key="global_health",
        status="HEALTHY",
        details="Bootstrapped for Paper Trading",
        last_updated_utc=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        print("Checking for existing SystemStatus...")
        existing_status = await session.get(SystemStatus, "global_health")
        if not existing_status:
            print("Creating SystemStatus: HEALTHY")
            session.add(sys_status)
        else:
            print("SystemStatus already exists (updating timestamp).")
            existing_status.last_updated_utc = datetime.now(UTC)
            existing_status.status = "HEALTHY"

        print(f"Checking for Solver: {solver_id}")
        existing_solver = await session.get(Solver, solver_id)
        if not existing_solver:
            print(f"Creating Solver: {solver_id}")
            session.add(solver)
            session.add(metrics)
        else:
            print(f"Solver {solver_id} already exists.")

        await session.commit()

    print("Bootstrap Complete. System ready for Ingest/Exec.")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(bootstrap())
