"""
Gateway WebSocket Streaming Client.

Connects to the Data Gateway's WebSocket endpoint for real-time market data,
leveraging the Gateway's multiplexer to avoid connection limit issues.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed

from orion.config import system_settings
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
        raw_str = (
            f"ALPACA_BAR_1M_{symbol}_"
            f"{payload.get('t', payload.get('timestamp', ''))}_"
            f"{payload.get('v', payload.get('volume', ''))}"
        )
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
            await self._websocket.send(
                json.dumps(
                    {
                        "action": "auth",
                        "key": self.api_key,
                    }
                )
            )

            # Wait for auth response
            response = await asyncio.wait_for(
                self._websocket.recv(),
                timeout=10.0,
            )

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

            # We do not wait for ack here because receive loop may already be active.
            self._subscribed_symbols.update(symbols)
            logger.info(f"Sent subscribe for {len(symbols)} symbols via Gateway")
            return True

        except Exception as e:
            logger.error(f"Subscribe failed: {e}", exc_info=True)
            return False

    async def _send_unsubscribe(self, symbols: List[str]) -> bool:
        """Send unsubscribe message to Gateway."""
        if not self._websocket or not self._authenticated:
            return False

        try:
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

            # Do not block on ack while receive loop may be active.
            self._subscribed_symbols -= set(symbols)
            logger.info(f"Sent unsubscribe for {len(symbols)} symbols")
            return True

        except Exception as e:
            logger.error(f"Unsubscribe failed: {e}", exc_info=True)
            return False

    async def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to bar updates for the given symbols."""
        if not symbols:
            return

        new_symbols = [s for s in symbols if s not in self._subscribed_symbols]
        if not new_symbols:
            return

        # Track desired subscriptions even before connection is live.
        self._subscribed_symbols.update(new_symbols)

        if self._websocket and self._authenticated:
            await self._send_subscribe(new_symbols)
        else:
            logger.debug(f"Queued {len(new_symbols)} subscriptions until Gateway connection is ready")

    async def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from bar updates for the given symbols."""
        if not symbols:
            return

        to_remove = [s for s in symbols if s in self._subscribed_symbols]
        if not to_remove:
            return

        # Remove from desired set immediately.
        self._subscribed_symbols -= set(to_remove)

        if self._websocket and self._authenticated:
            await self._send_unsubscribe(to_remove)
        else:
            logger.debug(f"Removed {len(to_remove)} queued subscriptions before Gateway connection")

    @staticmethod
    def _loads_message(message: Any) -> Dict[str, Any]:
        """Decode a websocket frame into a JSON object."""
        if isinstance(message, bytes):
            return json.loads(message.decode("utf-8"))
        if isinstance(message, str):
            return json.loads(message)
        if isinstance(message, dict):
            return message
        raise ValueError(f"Unsupported websocket frame type: {type(message)}")

    @staticmethod
    def _is_bar_message(data: Dict[str, Any]) -> bool:
        """Return True when message contains bar data in any supported Gateway shape."""
        msg_type = str(data.get("type") or data.get("event_type") or "").lower()
        feed = str(data.get("feed") or "").lower()

        if msg_type in ("alpaca_bar_1m", "bar"):
            return True

        if msg_type == "data" and feed in ("bars", "stock_bars", "bar", "alpaca_bar_1m"):
            return True

        return False

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        """Parse provider timestamp values to UTC datetime."""
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.now(timezone.utc)

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        """Best-effort numeric parsing."""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def _receive_loop(self) -> None:
        """Main loop for receiving messages from Gateway."""
        while self._running:
            try:
                if not self._websocket:
                    if not await self._reconnect_with_backoff():
                        self._running = False
                        break
                    continue

                message = await self._websocket.recv()
                data = self._loads_message(message)

                msg_type = data.get("type") or data.get("event_type")

                # Handle heartbeat
                if msg_type == "heartbeat":
                    await self._websocket.send(json.dumps({"action": "heartbeat"}))
                    continue

                # Ack/system messages
                if msg_type in ("auth_result", "subscription_ack", "unsubscription_ack", "heartbeat_ack", "pong"):
                    continue

                # Handle market data
                if self._is_bar_message(data):
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
            envelope = data.get("envelope", {})
            if not isinstance(envelope, dict):
                envelope = {}

            # Gateway wraps market data as type=data with raw bar under "data".
            payload = data.get("data")
            if not isinstance(payload, dict):
                payload = data.get("payload")
            if not isinstance(payload, dict):
                payload = envelope.get("payload")
            if not isinstance(payload, dict):
                payload = data

            instrument_key = (
                data.get("instrument_key")
                or envelope.get("instrument_key")
                or payload.get("instrument_key")
                or ""
            )
            top_symbol = data.get("symbol") or envelope.get("symbol")
            payload_symbol = payload.get("symbol") or payload.get("S")
            symbol = top_symbol or payload_symbol or instrument_key.replace("equity:", "")

            if not symbol:
                return

            # Validate data quality
            close_price = self._to_float(payload.get("c", payload.get("close")))
            if close_price is None or close_price <= 0:
                logger.warning(f"Rejecting invalid bar for {symbol}: close={close_price}")
                return

            # Parse timestamp
            timestamp_value = (
                payload.get("t")
                or payload.get("timestamp")
                or envelope.get("ts_event")
                or data.get("ts_event")
            )
            event_ts = self._parse_timestamp(timestamp_value)

            # Use Gateway envelope idempotency id when available.
            event_id = data.get("event_id") or envelope.get("event_id") or self._generate_event_id(symbol, payload)

            # Add normalized symbol keys for downstream normalizer compatibility.
            normalized_payload = dict(payload)
            normalized_payload.setdefault("symbol", symbol)
            normalized_payload.setdefault("ticker", symbol)
            if instrument_key:
                normalized_payload.setdefault("instrument_key", instrument_key)

            event = BronzeEvent(
                event_id=event_id,
                source="ALPACA",
                event_type="ALPACA_BAR_1M",
                event_ts_utc=event_ts,
                received_ts_utc=datetime.now(timezone.utc),
                payload=normalized_payload,
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

        # Flush queued subscriptions that were requested before startup.
        if self._subscribed_symbols:
            await self._send_subscribe(list(self._subscribed_symbols))

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
    """Create GatewayStreamClient from centralized system settings."""
    gateway_url = system_settings.data_gateway_url
    api_key = system_settings.data_gateway_api_key

    if not gateway_url:
        raise ValueError("DATA_GATEWAY_URL/GATEWAY_URL setting not configured")
    if not api_key:
        raise ValueError("DATA_GATEWAY_API_KEY/GATEWAY_API_KEY setting not configured")

    return GatewayStreamClient(
        gateway_url=gateway_url,
        api_key=api_key,
        on_bar_callback=on_bar_callback,
    )
