"""
Gateway WebSocket Streaming Client.

Connects to the Data Gateway's WebSocket endpoint for real-time market data,
leveraging the Gateway's multiplexer to avoid connection limit issues.
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed

from orion.storage.models import BronzeEvent

logger = logging.getLogger(__name__)

# Reconnection settings
MAX_RECONNECT_ATTEMPTS = 10
INITIAL_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 16.0


class GatewayStreamClient:
    """
    WebSocket client for Data Gateway streaming.

    Connects to the Gateway's /ws endpoint and handles:
    - Authentication handshake
    - Symbol subscription management
    - Event normalization to BronzeEvent
    - Heartbeat responses
    - Automatic reconnection with exponential backoff
    """

    def __init__(
        self,
        gateway_url: str,
        api_key: str,
        on_bar_callback: Optional[Callable[[BronzeEvent], None]] = None,
    ):
        # Normalize URL to WebSocket
        if gateway_url.startswith("http://"):
            self.ws_url = gateway_url.replace("http://", "ws://") + "/ws"
        elif gateway_url.startswith("https://"):
            self.ws_url = gateway_url.replace("https://", "wss://") + "/ws"
        elif gateway_url.startswith(("ws://", "wss://")):
            self.ws_url = gateway_url if gateway_url.endswith("/ws") else gateway_url + "/ws"
        else:
            self.ws_url = f"ws://{gateway_url}/ws"

        self.api_key = api_key
        self.on_bar_callback = on_bar_callback

        # Connection state
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._subscribed_symbols: Set[str] = set()
        self._running = False
        self._authenticated = False

        # Event queue for buffering
        self._event_queue: asyncio.Queue[BronzeEvent] = asyncio.Queue()

        # Background tasks
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    def _generate_event_id(self, symbol: str, payload: Dict[str, Any]) -> str:
        """Generate deterministic event ID for deduplication."""
        raw_str = f"ALPACA_BAR_1M_{symbol}_{payload.get('t', '')}_{payload.get('v', '')}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    async def connect(self) -> bool:
        """Establish WebSocket connection and authenticate."""
        try:
            logger.info(f"Connecting to Gateway WebSocket: {self.ws_url}")
            self._websocket = await websockets.connect(
                self.ws_url,
                ping_interval=20,
                ping_timeout=10,
            )

            # Send authentication
            await self._websocket.send_json(
                {
                    "action": "auth",
                    "key": self.api_key,
                }
            )

            # Wait for auth response
            response = await asyncio.wait_for(
                self._websocket.recv(),
                timeout=10.0,
            )

            import json

            auth_result = json.loads(response)

            if auth_result.get("status") == "ok":
                self._authenticated = True
                logger.info("Gateway WebSocket authenticated successfully")
                return True
            else:
                error_msg = auth_result.get("message", "Unknown error")
                logger.error(f"Gateway authentication failed: {error_msg}")
                return False

        except Exception as e:
            logger.error(f"Gateway connection failed: {e}", exc_info=True)
            return False

    async def _reconnect_with_backoff(self) -> bool:
        """Reconnect with exponential backoff."""
        delay = INITIAL_RECONNECT_DELAY

        for attempt in range(MAX_RECONNECT_ATTEMPTS):
            logger.info(f"Reconnection attempt {attempt + 1}/{MAX_RECONNECT_ATTEMPTS}, delay={delay}s")
            await asyncio.sleep(delay)

            if await self.connect():
                # Resubscribe to previous symbols
                if self._subscribed_symbols:
                    await self._send_subscribe(list(self._subscribed_symbols))
                return True

            delay = min(delay * 2, MAX_RECONNECT_DELAY)

        logger.error("Max reconnection attempts reached")
        return False

    async def _send_subscribe(self, symbols: List[str]) -> bool:
        """Send subscribe message to Gateway."""
        if not self._websocket or not self._authenticated:
            logger.warning("Cannot subscribe: not connected or authenticated")
            return False

        try:
            import json

            await self._websocket.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "provider": "alpaca",
                        "feed": "bars",
                        "symbols": symbols,
                    }
                )
            )

            # Wait for ack
            response = await asyncio.wait_for(
                self._websocket.recv(),
                timeout=5.0,
            )
            result = json.loads(response)

            if result.get("type") == "subscription_ack":
                subscribed = result.get("subscribed", [])
                self._subscribed_symbols.update(subscribed)
                logger.info(f"Subscribed to {len(subscribed)} symbols via Gateway")
                return True
            else:
                logger.warning(f"Unexpected subscribe response: {result}")
                return False

        except Exception as e:
            logger.error(f"Subscribe failed: {e}", exc_info=True)
            return False

    async def _send_unsubscribe(self, symbols: List[str]) -> bool:
        """Send unsubscribe message to Gateway."""
        if not self._websocket or not self._authenticated:
            return False

        try:
            import json

            await self._websocket.send(
                json.dumps(
                    {
                        "action": "unsubscribe",
                        "provider": "alpaca",
                        "feed": "bars",
                        "symbols": symbols,
                    }
                )
            )

            response = await asyncio.wait_for(
                self._websocket.recv(),
                timeout=5.0,
            )
            result = json.loads(response)

            if result.get("type") == "unsubscription_ack":
                unsubscribed = result.get("unsubscribed", [])
                self._subscribed_symbols -= set(unsubscribed)
                logger.info(f"Unsubscribed from {len(unsubscribed)} symbols")
                return True
            return False

        except Exception as e:
            logger.error(f"Unsubscribe failed: {e}", exc_info=True)
            return False

    async def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to bar updates for the given symbols."""
        if not symbols:
            return

        new_symbols = [s for s in symbols if s not in self._subscribed_symbols]
        if new_symbols:
            await self._send_subscribe(new_symbols)

    async def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from bar updates for the given symbols."""
        if not symbols:
            return

        to_remove = [s for s in symbols if s in self._subscribed_symbols]
        if to_remove:
            await self._send_unsubscribe(to_remove)

    async def _receive_loop(self) -> None:
        """Main loop for receiving messages from Gateway."""
        import json

        while self._running:
            try:
                if not self._websocket:
                    if not await self._reconnect_with_backoff():
                        self._running = False
                        break
                    continue

                message = await self._websocket.recv()
                data = json.loads(message)

                msg_type = data.get("type") or data.get("event_type")

                # Handle heartbeat
                if msg_type == "heartbeat":
                    await self._websocket.send(json.dumps({"action": "heartbeat"}))
                    continue

                # Handle market data
                if msg_type in ("ALPACA_BAR_1M", "bar"):
                    await self._process_bar_message(data)

            except ConnectionClosed:
                logger.warning("Gateway WebSocket connection closed")
                self._websocket = None
                self._authenticated = False

                if self._running and not await self._reconnect_with_backoff():
                    self._running = False
                    break

            except Exception as e:
                logger.error(f"Receive loop error: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _process_bar_message(self, data: Dict[str, Any]) -> None:
        """Process incoming bar data and convert to BronzeEvent."""
        try:
            # Extract payload (Gateway EventEnvelope format)
            payload = data.get("payload", data)
            symbol = data.get("instrument_key", "").replace("equity:", "") or payload.get("S", "")

            if not symbol:
                return

            # Validate data quality
            close_price = payload.get("c") or payload.get("close")
            if not close_price or close_price <= 0:
                logger.warning(f"Rejecting invalid bar for {symbol}: close={close_price}")
                return

            # Parse timestamp
            timestamp_str = payload.get("t") or payload.get("timestamp")
            if timestamp_str:
                if isinstance(timestamp_str, str):
                    event_ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                else:
                    event_ts = timestamp_str
            else:
                event_ts = datetime.now(timezone.utc)

            event_id = self._generate_event_id(symbol, payload)

            event = BronzeEvent(
                event_id=event_id,
                source="ALPACA",
                event_type="ALPACA_BAR_1M",
                event_ts_utc=event_ts,
                received_ts_utc=datetime.now(timezone.utc),
                payload=payload,
            )

            if self.on_bar_callback:
                self.on_bar_callback(event)
            else:
                await self._event_queue.put(event)

            logger.debug(f"Received bar for {symbol} via Gateway")

        except Exception as e:
            logger.error(f"Error processing bar message: {e}", exc_info=True)

    async def start(self) -> None:
        """Start the WebSocket client."""
        if self._running:
            logger.warning("Gateway stream client already running")
            return

        self._running = True

        if not await self.connect():
            raise ConnectionError("Failed to connect to Gateway WebSocket")

        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("Gateway stream client started")

    async def stop(self) -> None:
        """Stop the WebSocket client."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping Gateway stream client")

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass  # Expected when stopping - no need to re-raise here as this is the stop() method

        if self._websocket:
            await self._websocket.close()
            self._websocket = None

    def drain_events(self, max_events: int = 1000) -> List[BronzeEvent]:
        """Drain buffered events from the queue."""
        events = []
        while len(events) < max_events:
            try:
                event = self._event_queue.get_nowait()
                events.append(event)
            except asyncio.QueueEmpty:
                break
        return events

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def subscribed_symbols(self) -> Set[str]:
        return self._subscribed_symbols.copy()


def create_gateway_stream_client(
    on_bar_callback: Optional[Callable[[BronzeEvent], None]] = None,
) -> GatewayStreamClient:
    """Create GatewayStreamClient from environment variables."""
    gateway_url = os.getenv("DATA_GATEWAY_URL")
    api_key = os.getenv("DATA_GATEWAY_API_KEY")

    if not gateway_url:
        raise ValueError("DATA_GATEWAY_URL environment variable not set")
    if not api_key:
        raise ValueError("DATA_GATEWAY_API_KEY environment variable not set")

    return GatewayStreamClient(
        gateway_url=gateway_url,
        api_key=api_key,
        on_bar_callback=on_bar_callback,
    )
