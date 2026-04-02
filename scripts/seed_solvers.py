"""Seed Orion's default paper solvers into the database."""

import asyncio
import contextlib
import os
import sys

from dotenv import load_dotenv

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv()

from orion.jobs.seed_solvers import DEFAULT_BASELINE_SOLVER_ID, seed_default_solvers


async def main() -> None:
    summary = await seed_default_solvers()
    print(
        "Solver Seed Complete: "
        f"{summary['created']} created, {summary['updated']} updated, {summary['skipped']} metrics already present."
    )
    print(f"Baseline fallback: {DEFAULT_BASELINE_SOLVER_ID}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
