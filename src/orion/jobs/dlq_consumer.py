import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, List

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
from orion.shared.db_utils import db_query, db_write
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

    async def _mark_succeeded(self, msg_id: str) -> None:
        """Mark the DLQ message as succeeded."""

        async def update_status(session: Any) -> None:
            stmt = select(DeadLetterQueue).where(DeadLetterQueue.id == msg_id)
            result = await session.execute(stmt)
            msg = result.scalars().first()
            if msg:
                msg.status = "REPLAYED"
                msg.error_message = f"Replayed successfully at {datetime.now(timezone.utc)}"

        await db_write(update_status)

    async def run_once(self) -> None:
        """
        Process one batch of DLQ items.
        """
        logger.info("Checking DLQ for failed events...")

        batch_size = 10

        async def fetch_dlq_events(session: Any) -> List[Any]:
            stmt = (
                select(DeadLetterQueue)
                .where(DeadLetterQueue.status == "PENDING")
                .order_by(DeadLetterQueue.event_ts_utc.asc())
                .limit(batch_size)
            )
            result = await session.execute(stmt)
            return result.scalars().all()

        events = await db_query(fetch_dlq_events)

        if not events:
            logger.info("No retryable DLQ items found.")
            return

        async def process_and_update_dlq_items(session: Any) -> None:
            for task in events:
                logger.info(f"Replaying DLQ {task.id} (Type: {task.event_type})...")

                try:
                    success = await self._replay_event(session, task)

                    if success:
                        task.status = "REPLAYED"
                        task.error_message = f"Replayed successfully at {datetime.now(timezone.utc)}"
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
            received_ts_utc=datetime.now(timezone.utc),
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
                pass
            unique_events = [bronze]

        # Persist bronze + silver (idempotent via ON CONFLICT DO NOTHING).
        await persist_bronze_events(session, unique_events)
        await persist_silver_from_bronze(session, unique_events)
        await session.commit()

        # Re-run downstream feature + rule pipeline for supported event types.
        self.feature_engine.process_uw_flow(unique_events)

        uw_flow_events = [e for e in unique_events if e.event_type == "UW_FLOW"]
        if uw_flow_events:
            sigs = self.feature_engine.process_uw_flow_events(uw_flow_events)
            if sigs:
                # Preserve legacy behavior: persist feature rows to gold_feature_events (used by existing tests/pipeline).
                await self.feature_engine.persist_signal_batch(sigs, "v1_legacy")
                # If these are real SilverSignal objects, persist them + candidates.
                if all(hasattr(s, "signal_id") and hasattr(s, "ticker") for s in sigs):
                    await persist_silver_signals(session, sigs)
                    candidates = self.rule_engine.process_signals(sigs)
                    if candidates:
                        await persist_candidates(session, candidates)

        alpaca_events = [e for e in unique_events if e.event_type == "ALPACA_BAR_1M"]
        if alpaca_events:
            sigs = self.feature_engine.process_alpaca_bars(alpaca_events)
            if sigs:
                await self.feature_engine.persist_signal_batch(sigs, "v1_legacy")
                if all(hasattr(s, "signal_id") and hasattr(s, "ticker") for s in sigs):
                    await persist_silver_signals(session, sigs)
                    candidates = self.rule_engine.process_signals(sigs)
                    if candidates:
                        await persist_candidates(session, candidates)

        # For other event types, bronze/silver persistence is still useful even if we skip downstream steps.
        await session.commit()
        return True


if __name__ == "__main__":
    # Standalone run
    loop = asyncio.get_event_loop()
    consumer = DLQConsumer()
    loop.run_until_complete(consumer.run_once())
