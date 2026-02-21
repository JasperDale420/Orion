import asyncio
from orion.shared.logger import setup_struct_logger
from datetime import datetime, timedelta, timezone
from typing import Iterable

from orion.config import system_settings
from orion.core.logging_config import setup_logging
from orion.processing.rollup_builder import RollupBuilder
from orion.storage.db import async_session_factory
from orion.storage.watermarks import get_watermark, upsert_watermark

logger = setup_struct_logger("orion.jobs.rollup_job")


class RollupJob:
    """
    Near-real-time rollup builder (PRD §11 runtime step 2).
    Uses per-ticker DB watermarks to avoid reprocessing large windows.
    """

    def __init__(
        self,
        *,
        tickers: Iterable[str] | None = None,
        lookback_minutes_on_cold_start: int = 60,
        overlap_seconds: int = 120,
        loop_interval_seconds: float = 30.0,
    ):
        self.tickers = list(tickers) if tickers is not None else list(system_settings.static_watchlist)
        self.lookback_minutes_on_cold_start = lookback_minutes_on_cold_start
        self.overlap_seconds = overlap_seconds
        self.loop_interval_seconds = loop_interval_seconds

    def _wm_key(self, ticker: str) -> str:
        return f"rollups:{ticker}"

    async def run_once(self) -> None:
        now = datetime.now(timezone.utc)
        # The RollupBuilder needs a session to read data, but upsert_watermark can use db_write
        async with async_session_factory() as session:
            builder = RollupBuilder(session)

            for ticker in self.tickers:
                wm = await get_watermark(session, key=self._wm_key(ticker))
                if wm is None:
                    start = now - timedelta(minutes=self.lookback_minutes_on_cold_start)
                else:
                    start = wm - timedelta(seconds=self.overlap_seconds)

                latest_ts = await builder.build_rollups(ticker=ticker, start_time=start, end_time=now)

                if latest_ts is None:
                    continue

                await upsert_watermark(session, key=self._wm_key(ticker), last_seen_ts_utc=latest_ts)

    async def run_forever(self) -> None:
        logger.info(
            "Starting rollup job loop",
            extra={
                "event_type": "ROLLUP_JOB_START",
                "tickers": len(self.tickers),
                "interval_seconds": self.loop_interval_seconds,
            },
        )
        while True:
            t0 = asyncio.get_running_loop().time()
            try:
                await self.run_once()
            except Exception as e:
                logger.error(f"Rollup job iteration failed: {e}", extra={"event_type": "ROLLUP_JOB_ERROR"})

            elapsed = asyncio.get_running_loop().time() - t0
            await asyncio.sleep(max(1.0, self.loop_interval_seconds - elapsed))


if __name__ == "__main__":
    setup_logging()
    asyncio.run(RollupJob().run_forever())
