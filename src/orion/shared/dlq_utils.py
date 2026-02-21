from orion.shared.logger import setup_struct_logger
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from orion.config import system_settings
from orion.storage import db  # Import module for dynamic access
from orion.storage.models_dlq import DeadLetterQueue

logger = setup_struct_logger("orion.shared.dlq_utils")


class DLQWriter:
    """
    Shared utility for writing to the Dead Letter Queue (DLQ).
    """

    @staticmethod
    async def write_to_dlq(
        error: Exception,
        event_type: str,
        source: str,
        payload: Optional[Union[Dict[str, Any], str]] = None,
        context: Optional[str] = None,
        *,
        event_id: Optional[str] = None,
        source_event_id: Optional[str] = None,
        ticker: Optional[str] = None,
        event_ts_utc: Optional[datetime] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        """
        Asynchronously writes an error entry to the DLQ table.
        Safe to call from anywhere; catches its own exceptions to prevent cascading failures.
        """
        try:
            # Ensure payload is a dict if possible, or wrap it
            safe_payload = {}
            if isinstance(payload, dict):
                safe_payload = payload
            elif payload is not None:
                safe_payload = {"raw_content": str(payload)}

            if context:
                safe_payload["error_context"] = context

            error_msg = str(error)
            stack = traceback.format_exc()

            # Best-effort envelope extraction from payload, if present
            if isinstance(payload, dict):
                event_id = event_id or payload.get("event_id")
                source_event_id = source_event_id or payload.get("source_event_id")
                ticker = ticker or payload.get("ticker")

            # Correlation defaults
            run_id = run_id or system_settings.run_id
            trace_id = trace_id or safe_payload.get("trace_id")

            async with db.async_session_factory() as session:
                dlq_entry = DeadLetterQueue(
                    id=str(uuid.uuid4()),
                    event_id=event_id,
                    source_event_id=source_event_id,
                    event_type=event_type,
                    source=source,
                    ticker=ticker,
                    event_ts_utc=event_ts_utc,
                    run_id=run_id,
                    trace_id=trace_id,
                    error_message=error_msg,
                    stack_trace=stack,
                    payload=safe_payload,
                    timestamp_utc=datetime.now(timezone.utc),
                    status="FAILED",
                )
                session.add(dlq_entry)
                await session.commit()

            logger.error(f"DLQ Entry Written: {source} / {event_type} - {error_msg}")

        except Exception as e:
            # Fallback log if DB write fails - CRITICAL
            logger.critical(f"CRITICAL: Failed to write to DLQ! Original Error: {error}. DLQ Error: {e}")
