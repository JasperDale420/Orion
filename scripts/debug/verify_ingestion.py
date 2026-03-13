import asyncio
import os
import sys

from sqlalchemy import func, select

# Path hack to ensure we can import orion
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv()

import contextlib

from orion.storage.db import async_session_factory  # noqa: E402
from orion.storage.models import BronzeEvent  # noqa: E402
from orion.storage.models_silver import SilverAlpacaBar, SilverSignal  # noqa: E402


async def check_ingestion():
    async with async_session_factory() as session:
        print("Checking Bronze Events...")
        result = await session.execute(select(func.count()).select_from(BronzeEvent))
        bronze_count = result.scalar()
        print(f"Total Bronze Events: {bronze_count}")

        print("Checking Silver Signals...")
        result = await session.execute(select(func.count()).select_from(SilverSignal))
        signal_count = result.scalar()
        print(f"Total Silver Signals: {signal_count}")

        print("Checking Silver Bars...")
        result = await session.execute(select(func.count()).select_from(SilverAlpacaBar))
        bar_count = result.scalar()
        print(f"Total Silver Bars: {bar_count}")

        if bronze_count > 0:
            # Show a sample
            stmt = select(BronzeEvent).order_by(BronzeEvent.received_ts_utc.desc()).limit(1)
            latest = (await session.execute(stmt)).scalars().first()
            if latest:
                print(f"Latest Event: {latest.event_type} at {latest.received_ts_utc} for {latest.ticker}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(check_ingestion())
