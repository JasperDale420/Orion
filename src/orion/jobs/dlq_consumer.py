import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from orion.processing.feature_engine import FeatureEngine
from orion.processing.ingest_pipeline import ingest_bronze_events
from orion.processing.persistence import (
    persist_bronze_events,
    persist_candidates,
    persist_silver_from_bronze,
    persist_silver_signals,
)
from orion.processing.rule_engine import RuleEngine
from orion.shared.db_utils import db_write
from orion.shared.utils import ensure_utc
from orion.storage.models import BronzeEvent
from orion.storage.models_dlq import DeadLetterQueue

logger = logging.getLogger(__name__)


class DLQConsumer:
    """
    PRD 15.3: DLQ Replay Tool.
    Reads 'FAILED' events from DLQ table and attempts to re-process them.
    """

    def __init__(self) -> None:
        self.feature_engine = FeatureEngine()
        self.rule_engine = RuleEngine()

    async def run_once(self) -> None:
        """
        Process one batch of DLQ items.
        """
        logger.info("Checking DLQ for failed events...")

        batch_size = 10

        # Fetch and mutate in the SAME write session. The previous version
        # fetched rows via db_query (session A) and mutated them inside a
        # separate db_write (session B): the instances were detached, the
        # commit was a no-op, and DLQ rows never left PENDING — every batch
        # was replayed again on the next run.
        async def process_and_update_dlq_items(session: Any) -> None:
            stmt = (
                select(DeadLetterQueue)
                .where(DeadLetterQueue.status == "PENDING")
                .order_by(DeadLetterQueue.event_ts_utc.asc())
                .limit(batch_size)
            )
            result = await session.execute(stmt)
            events = result.scalars().all()

            if not events:
                logger.info("No retryable DLQ items found.")
                return

            for task in events:
                logger.info(f"Replaying DLQ {task.id} (Type: {task.event_type})...")

                try:
                    success = await self._replay_event(session, task)

                    if success:
                        task.status = "REPLAYED"
                        task.error_message = f"Replayed successfully at {datetime.now(UTC)}"
                        logger.info(f"DLQ {task.id} Success.")
                    else:
                        task.retry_count += 1
                        task.error_message = f"Retry {task.retry_count} failed."
                        logger.warning(f"DLQ {task.id} Failed Retry.")

                except Exception as e:
                    task.retry_count += 1
                    task.error_message = f"Retry Crash: {str(e)}"
                    logger.error(f"DLQ {task.id} Crash: {e}")

        await db_write(process_and_update_dlq_items)

    async def _persist_signals_and_candidates(self, session: Any, sigs: list[Any]) -> None:
        if not sigs:
            return
        await self.feature_engine.persist_signal_batch(sigs, "v1_legacy")
        if all(hasattr(s, "signal_id") and hasattr(s, "ticker") for s in sigs):
            await persist_silver_signals(session, sigs)
            candidates = self.rule_engine.process_signals(sigs)
            if candidates:
                await persist_candidates(session, candidates)

    async def _replay_event(self, session: Any, task: DeadLetterQueue) -> bool:
        """
        Route to appropriate handler based on event_type.
        """
        if not task.payload:
            return False

        # Reconstruct BronzeEvent using canonical identity where possible (PRD 15.3).
        event_id = (
            task.event_id
            or (task.payload.get("event_id") if isinstance(task.payload, dict) else None)
            or f"dlq_{task.id}"
        )
        source_event_id = task.source_event_id or (
            task.payload.get("source_event_id") if isinstance(task.payload, dict) else None
        )
        ticker = task.ticker or (task.payload.get("ticker") if isinstance(task.payload, dict) else None)
        event_ts = task.timestamp_utc
        if isinstance(getattr(task, "event_ts_utc", None), datetime):
            event_ts = task.event_ts_utc
        if event_ts and event_ts.tzinfo is None:
            event_ts = ensure_utc(event_ts)

        bronze = BronzeEvent(
            event_id=str(event_id),
            source=str(task.source or "REPLAY"),
            source_event_id=str(source_event_id) if source_event_id is not None else None,
            event_type=str(task.event_type or "UNKNOWN"),
            ticker=ticker,
            event_ts_utc=event_ts,
            received_ts_utc=datetime.now(UTC),
            payload=task.payload if isinstance(task.payload, dict) else {"raw_content": str(task.payload)},
            ingest={
                "connector": "dlq_consumer",
                "run_id": task.run_id,
                "trace_id": task.trace_id,
                "attempt": int(task.retry_count or 0) + 1,
            },
            session="CLOSED",
        )

        trace_id = task.trace_id or str(uuid.uuid4())
        run_id = task.run_id or "dlq_replay"

        # Re-ingest through the same normalize/dedupe path.
        unique_events = await ingest_bronze_events(session, [bronze], run_id=run_id, trace_id=trace_id)
        if not unique_events:
            # Already processed at the bronze layer (idempotent replay).
            # Still attempt downstream feature/candidate regeneration using a normalized event; downstream tables are idempotent.
            try:
                from orion.core.timekeeping import derive_trading_date_and_session
                from orion.processing.normalizer import NormalizationEngine

                bronze.payload = NormalizationEngine.normalize_event(bronze.source, bronze.event_type, bronze.payload)
                if bronze.event_ts_utc and bronze.event_ts_utc.tzinfo is None:
                    bronze.event_ts_utc = ensure_utc(bronze.event_ts_utc)
                if bronze.event_ts_utc:
                    td, sess = derive_trading_date_and_session(bronze.event_ts_utc)
                    bronze.trading_date = td
                    bronze.session = sess
            except Exception:
                logger.error(f"DLQ event normalization failed for event_id={bronze.event_id}", exc_info=True)
            unique_events = [bronze]

        # Persist bronze + silver (idempotent via ON CONFLICT DO NOTHING).
        # No commit here: the outer db_write commits the whole batch once, so
        # replay writes and the DLQ status update land (or roll back) together
        # — a mid-batch commit left a replayed-but-still-PENDING window.
        await persist_bronze_events(session, unique_events)
        await persist_silver_from_bronze(session, unique_events)

        # Re-run downstream feature + rule pipeline for supported event types.
        self.feature_engine.process_uw_flow(unique_events)

        uw_flow_events = [e for e in unique_events if e.event_type == "UW_FLOW"]
        if uw_flow_events:
            sigs = self.feature_engine.process_uw_flow_events(uw_flow_events)
            await self._persist_signals_and_candidates(session, sigs)

        alpaca_events = [e for e in unique_events if e.event_type == "ALPACA_BAR_1M"]
        if alpaca_events:
            sigs = self.feature_engine.process_alpaca_bars(alpaca_events)
            await self._persist_signals_and_candidates(session, sigs)

        # For other event types, bronze/silver persistence is still useful even
        # if we skip downstream steps. Commit happens once in the outer db_write.
        return True


if __name__ == "__main__":
    # Standalone run
    asyncio.run(DLQConsumer().run_once())
