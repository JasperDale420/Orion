"""
Gateway WebSocket Streaming Client.

Connects to the Data Gateway's WebSocket endpoint for real-time market data,
leveraging the Gateway's multiplexer to avoid connection limit issues.
"""

import asyncio
import contextlib
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import websockets
from websockets.exceptions import ConnectionClosed

from orion.config import system_settings
from orion.processing.flow_enrich import enrich_flow_payload
from orion.shared.logger import setup_struct_logger
from orion.storage.models import BronzeEvent

logger = setup_struct_logger(__name__)

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
        on_bar_callback: Callable[[BronzeEvent], None] | None = None,
        on_flow_callback: Callable[[BronzeEvent], None] | None = None,
    ):
        self.ws_url = self._normalize_ws_url(gateway_url)

        self.api_key = api_key
        self.on_bar_callback = on_bar_callback
        self.on_flow_callback = on_flow_callback

        # Connection state
        self._websocket: websockets.WebSocketClientProtocol | None = None
        self._subscribed_symbols: set[str] = set()
        # Flow subscription state. None means "not subscribed to flow at all";
        # an empty set means subscribed to ALL flow (firehose). A non-empty set
        # tracks the specific underlyings requested.
        self._flow_subscribed: bool = False
        self._subscribed_flow_symbols: set[str] = set()
        self._running = False
        self._authenticated = False

        # Event queue for buffering
        self._event_queue: asyncio.Queue[BronzeEvent] = asyncio.Queue()
        self._flow_event_queue: asyncio.Queue[BronzeEvent] = asyncio.Queue()

        # Background tasks
        self._receive_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

    @staticmethod
    def _normalize_ws_url(gateway_url: str) -> str:
        """
        Normalize gateway base URL to a websocket endpoint.

        Supports host-only, HTTP(S), and WS(S) inputs while ensuring `/api/v1`
        suffixes are not propagated into websocket route construction.
        """
        raw = (gateway_url or "").strip()
        if not raw:
            raise ValueError("gateway_url is required")

        if "://" not in raw:
            raw = f"ws://{raw}"

        parsed = urlparse(raw)

        scheme_map = {
            "http": "ws",
            "https": "wss",
            "ws": "ws",
            "wss": "wss",
        }
        ws_scheme = scheme_map.get(parsed.scheme, "ws")

        path = (parsed.path or "").rstrip("/")
        if path.endswith("/api/v1"):
            path = path[: -len("/api/v1")]

        if path.endswith("/ws"):
            ws_path = path
        else:
            ws_path = f"{path}/ws" if path else "/ws"

        return urlunparse((ws_scheme, parsed.netloc, ws_path, "", "", ""))

    async def _cleanup_failed_connection(self) -> None:
        """Best-effort websocket cleanup for failed handshake/connect paths."""
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                logger.warning("Websocket close error during cleanup", exc_info=True)
        self._websocket = None
        self._authenticated = False

    def _generate_event_id(self, symbol: str, payload: dict[str, Any]) -> str:
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
                ping_interval=30,  # Match Data Gateway's uvicorn --ws-ping-interval
                ping_timeout=90,  # Match Data Gateway's uvicorn --ws-ping-timeout
                # The Gateway serializes websocket.accept() behind a shared
                # asyncio.Lock on a single-worker event loop that is also
                # serving the REST trading proxy + broadcast fan-out, so the
                # handshake can exceed the websockets default 10s open_timeout
                # under market-open load (TimeoutError: timed out during opening
                # handshake, seen 2026-06-05/07). 30s tolerates the slow accept;
                # the real fix is Gateway-side (accept before lock + uvicorn
                # concurrency limits).
                open_timeout=30,
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
                await self._cleanup_failed_connection()
                return False

        except Exception as e:
            logger.error(f"Gateway connection failed: {e}", exc_info=True)
            await self._cleanup_failed_connection()
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
                # Re-send the flow subscription (ALL or specific underlyings)
                # so a reconnect restores push delivery.
                if self._flow_subscribed:
                    await self._send_subscribe_flow(list(self._subscribed_flow_symbols))
                return True

            delay = min(delay * 2, MAX_RECONNECT_DELAY)

        logger.error("Max reconnection attempts reached")
        return False

    async def _send_subscribe(self, symbols: list[str]) -> bool:
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

    async def _send_unsubscribe(self, symbols: list[str]) -> bool:
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

    async def _send_subscribe_flow(self, symbols: list[str]) -> bool:
        """Send the UW flow subscribe message (empty symbols ⇒ ALL flow)."""
        if not self._websocket or not self._authenticated:
            logger.warning("Cannot subscribe flow: not connected or authenticated")
            return False
        try:
            await self._websocket.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "provider": "uw",
                        "feeds": ["flow_alerts"],
                        "symbols": symbols,
                    }
                )
            )
            logger.info(
                "Sent UW flow subscribe via Gateway",
                extra={"flow_symbols": len(symbols) or "ALL"},
            )
            return True
        except Exception as e:
            logger.error(f"Flow subscribe failed: {e}", exc_info=True)
            return False

    async def subscribe_flow(self, symbols: list[str] | None = None) -> None:
        """Subscribe to UW flow push (empty/None ⇒ ALL flow / firehose).

        Tracks the desired subscription even before the connection is live so it
        is (re)sent by ``start``/reconnect/``restart``.
        """
        self._flow_subscribed = True
        self._subscribed_flow_symbols = {s for s in (symbols or []) if s}
        if self._websocket and self._authenticated:
            await self._send_subscribe_flow(list(self._subscribed_flow_symbols))
        else:
            logger.debug("Queued UW flow subscription until Gateway connection is ready")

    async def subscribe(self, symbols: list[str]) -> None:
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

    async def unsubscribe(self, symbols: list[str]) -> None:
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
    def _loads_message(message: Any) -> dict[str, Any]:
        """Decode a websocket frame into a JSON object."""
        if isinstance(message, bytes):
            return json.loads(message.decode("utf-8"))
        if isinstance(message, str):
            return json.loads(message)
        if isinstance(message, dict):
            return message
        raise ValueError(f"Unsupported websocket frame type: {type(message)}")

    @staticmethod
    def _is_bar_message(data: dict[str, Any]) -> bool:
        """Return True when message contains bar data in any supported Gateway shape."""
        msg_type = str(data.get("type") or data.get("event_type") or "").lower()
        feed = str(data.get("feed") or "").lower()

        if msg_type in ("alpaca_bar_1m", "bar"):
            return True

        if msg_type == "data" and feed in ("bars", "stock_bars", "bar", "alpaca_bar_1m"):
            return True

        # Current Gateway shape: bare EventEnvelope on the wire — no top-level
        # `type`, identified by `feed` plus envelope fields. `feeds` (plural,
        # used by subscription_ack) intentionally does not match.
        if not msg_type and feed in ("bars", "stock_bars") and ("instrument_key" in data or "symbol" in data):
            return True

        return False

    @staticmethod
    def _is_flow_message(data: dict[str, Any]) -> bool:
        """Return True when message carries a UW flow envelope.

        The Gateway flow fan-out wraps the envelope as
        ``{"type":"data","feed":"flow_alerts",...}`` (mirrors the bars shape).
        ``feeds`` (plural, on subscription_ack) intentionally does not match.
        """
        msg_type = str(data.get("type") or data.get("event_type") or "").lower()
        feed = str(data.get("feed") or "").lower()
        if feed not in ("flow", "flow_alerts"):
            return False
        if msg_type == "data":
            return True
        # Bare envelope fallback: no top-level type, flow feed + envelope fields.
        return not msg_type and ("instrument_key" in data or "symbol" in data or "envelope" in data)

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        """Parse provider timestamp values to UTC datetime."""
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.now(UTC)

    @staticmethod
    def _to_float(value: Any) -> float | None:
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
                elif self._is_flow_message(data):
                    await self._process_flow_message(data)

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

    async def _process_bar_message(self, data: dict[str, Any]) -> None:
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
                data.get("instrument_key") or envelope.get("instrument_key") or payload.get("instrument_key") or ""
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
                payload.get("t") or payload.get("timestamp") or envelope.get("ts_event") or data.get("ts_event")
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
                received_ts_utc=datetime.now(UTC),
                payload=normalized_payload,
            )

            if self.on_bar_callback:
                self.on_bar_callback(event)
            else:
                await self._event_queue.put(event)

            logger.debug(f"Received bar for {symbol} via Gateway")

        except Exception as e:
            logger.error(f"Error processing bar message: {e}", exc_info=True)

    async def _process_flow_message(self, data: dict[str, Any]) -> None:
        """Process a pushed UW flow envelope into a BronzeEvent.

        The output is shape-identical to the Heber-poll path's
        ``_heber_row_to_event`` (same event_id precedence, event_type, source,
        enriched payload keys via the shared ``enrich_flow_payload`` helper) so
        the two delivery paths are interchangeable and Orion's deduper collapses
        a push+poll duplicate pair on the Gateway-minted ``event_id``.
        """
        try:
            envelope = data.get("envelope", {})
            if not isinstance(envelope, dict):
                envelope = {}

            payload = data.get("data")
            if not isinstance(payload, dict):
                payload = envelope.get("payload")
            if not isinstance(payload, dict):
                payload = data
            payload = dict(payload)

            # The Gateway flow envelope's top-level `symbol` IS the underlying
            # ticker (wrap_event resolves underlying/ticker → symbol). Seed it so
            # the shared enrichment resolves the same ticker the poll path does
            # (underlying/symbol), even when the inner record only carries
            # `ticker`.
            top_symbol = data.get("symbol") or envelope.get("symbol")
            if top_symbol and not (payload.get("underlying") or payload.get("symbol")):
                payload["symbol"] = top_symbol

            now = datetime.now(UTC)
            ticker = enrich_flow_payload(payload, now)
            if not ticker:
                return

            # Same id precedence as bars: prefer the Gateway envelope id (the id
            # Heber writes to Silver), so push and poll carry byte-identical ids.
            event_id = data.get("event_id") or envelope.get("event_id") or payload.get("event_id")
            if not event_id:
                logger.warning(f"Dropping flow event without event_id for {ticker}")
                return
            event_id = str(event_id)

            # ts_event from the envelope (the same value Heber writes to the
            # Silver ts_event column the poll path reads).
            ts_value = (
                payload.get("timestamp") or envelope.get("ts_event") or data.get("ts_event") or payload.get("ts_event")
            )
            event_ts = self._parse_timestamp(ts_value)

            event = BronzeEvent(
                event_id=event_id,
                source="UW",
                event_type="UW_FLOW",
                event_ts_utc=event_ts,
                received_ts_utc=now,
                ticker=ticker,
                payload=payload,
            )

            if self.on_flow_callback:
                self.on_flow_callback(event)
            else:
                await self._flow_event_queue.put(event)

            logger.debug(f"Received UW flow push for {ticker} via Gateway")

        except Exception as e:
            logger.error(f"Error processing flow message: {e}", exc_info=True)

    async def start(self) -> None:
        """Start the WebSocket client."""
        if self._running:
            logger.warning("Gateway stream client already running")
            return

        self._running = True

        # Use reconnection logic for initial connection to handle transient failures
        if not await self._reconnect_with_backoff():
            self._running = False
            raise ConnectionError("Failed to connect to Gateway WebSocket after retries")

        # Flush queued subscriptions that were requested before startup.
        if self._subscribed_symbols:
            await self._send_subscribe(list(self._subscribed_symbols))
        if self._flow_subscribed:
            await self._send_subscribe_flow(list(self._subscribed_flow_symbols))

        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("Gateway stream client started")

    async def restart(self) -> bool:
        """Re-establish a dead connection after reconnect exhaustion.

        ``_receive_loop`` sets ``_running=False`` once it exhausts
        MAX_RECONNECT_ATTEMPTS, leaving the client permanently silent. This
        resets that state and attempts a single fresh connect (with the normal
        backoff budget), restarting the receive loop on success so the
        ingestion service can recover bar streaming without a process restart.
        Returns True if the connection and receive loop were re-established.
        """
        # Tear down any half-open state from the previous run.
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task
        self._receive_task = None
        await self._cleanup_failed_connection()

        self._running = True
        # _reconnect_with_backoff already resubscribes _subscribed_symbols on a
        # successful connect — do not resubscribe again here (a second
        # _send_subscribe would double-subscribe if Gateway isn't idempotent,
        # duplicating stream events and downstream pipeline writes).
        if not await self._reconnect_with_backoff():
            self._running = False
            return False

        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("Gateway stream client restarted")
        return True

    async def stop(self) -> None:
        """Stop the WebSocket client."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping Gateway stream client")

        if self._receive_task:
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task

        if self._websocket:
            await self._websocket.close()
            self._websocket = None

    def drain_events(self, max_events: int = 1000) -> list[BronzeEvent]:
        """Drain buffered bar events from the queue."""
        events = []
        while len(events) < max_events:
            try:
                event = self._event_queue.get_nowait()
                events.append(event)
            except asyncio.QueueEmpty:
                break
        return events

    def drain_flow_events(self, max_events: int = 5000) -> list[BronzeEvent]:
        """Drain buffered UW flow push events from the queue."""
        events = []
        while len(events) < max_events:
            try:
                event = self._flow_event_queue.get_nowait()
                events.append(event)
            except asyncio.QueueEmpty:
                break
        return events

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def subscribed_symbols(self) -> set[str]:
        return self._subscribed_symbols.copy()


def create_gateway_stream_client(
    on_bar_callback: Callable[[BronzeEvent], None] | None = None,
    on_flow_callback: Callable[[BronzeEvent], None] | None = None,
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
        on_flow_callback=on_flow_callback,
    )
