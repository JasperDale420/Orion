import asyncio

from orion.core.logging_config import setup_logging
from orion.ml.pattern_miner import run_all_pattern_mining
from orion.storage.db import init_db


async def main() -> None:
    setup_logging()
    await init_db()
    print("Starting ML mining job manually...")
    try:
        summary = await run_all_pattern_mining()
        print(f"Done. Insights generated: {len(summary.insights)}")
    except Exception as e:
        print(f"Error during mining: {e}")


if __name__ == "__main__":
    asyncio.run(main())
