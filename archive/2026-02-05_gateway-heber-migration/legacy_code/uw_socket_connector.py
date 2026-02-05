import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

import websockets
from orion.core.errors import ErrorCode, ProviderError
from orion.storage.models import BronzeEvent

logger = logging.getLogger(__name__)


class UWWebsocketConnector:
    """
    Connects to the Unusual Whales Websocket API (V2).
    Provides real-time streaming of options flow events.

    NOTE: This is currently a latent capability. The system uses Polling (V1) by default.
    """

    WS_URL = os.getenv("UW_WS_URL", "wss://api.unusualwhales.com/socket")

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("UW_API_KEY")
        if not self.api_key:
            raise ProviderError("UW_API_KEY is required.", code=ErrorCode.PROVIDER_AUTH_FAILED)

    def _generate_event_id(self, event_data: Dict[str, Any]) -> str:
        """
        Generates deterministic event ID.
        Matches logic in Poll Connector for consistency.
        """
        if "id" in event_data:
            unique_str = f"UW_FLOW_{event_data['id']}"
        else:
            stable_payload = f"{event_data.get('ticker')}_{event_data.get('timestamp')}_{event_data.get('premium')}"
            unique_str = f"UW_FLOW_HASH_{stable_payload}"

        return hashlib.sha256(unique_str.encode()).hexdigest()

    async def _authenticate(self, ws: Any) -> None:
        """
        Sends auth message if required by protocol.
        """
        # Assuming standard Bearer token auth via message or query param.
        # If URL doesn't support query param auth, we send a message.
        # For now, we'll try sending an auth frame.
        auth_msg = {"type": "auth", "token": self.api_key}
        await ws.send(json.dumps(auth_msg))
        logger.info("Sent Websocket Auth")

    async def stream(self, channels: List[str] | None = None) -> AsyncGenerator[BronzeEvent, None]:
        """
        Yields BronzeEvents from the websocket stream.
        Handles reconnection automatically via explicit loop.
        """
        channels = channels or ["flow"]
        backoff = 2

        logger.info(f"Connecting to UW Websocket: {self.WS_URL}")

        while True:
            try:
                async with websockets.connect(self.WS_URL) as websocket:
                    # Reset backoff on successful connection
                    backoff = 2

                    await self._authenticate(websocket)

                    # Subscribe
                    sub_msg = {"type": "subscribe", "channels": channels}
                    await websocket.send(json.dumps(sub_msg))
                    logger.info(f"Subscribed to {channels}")

                    while True:
                        msg = await websocket.recv()
                        data = json.loads(msg)

                        # Handle Heartbeats/System messages
                        if data.get("type") in ["ping", "heartbeat"]:
                            continue

                        if data.get("type") == "error":
                            logger.error(f"Websocket Error: {data}")
                            continue

                        # Process Flow Event
                        event_payload = data.get("data", data)

                        # Basic validation
                        if not isinstance(event_payload, dict):
                            continue

                        now = datetime.now(timezone.utc)

                        # Attempt timestamp parse
                        ts_str = event_payload.get("timestamp")
                        try:
                            # Parse ISO format or just use now if missing/invalid
                            event_ts = datetime.fromisoformat(str(ts_str)) if ts_str else now
                        except ValueError:
                            event_ts = now

                        if event_ts.tzinfo is None:
                            event_ts = event_ts.replace(tzinfo=timezone.utc)

                        event_id = self._generate_event_id(event_payload)

                        bronze = BronzeEvent(
                            event_id=event_id,
                            source="UW",
                            event_type="UW_FLOW",
                            event_ts_utc=event_ts,
                            received_ts_utc=now,
                            payload=event_payload,
                        )

                        yield bronze

            except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                logger.warning(f"Websocket connection lost: {e}. Retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception as e:
                logger.error(f"Unexpected Websocket Error: {e}. Retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
