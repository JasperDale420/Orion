"""
Alpaca WebSocket Streaming Connector.

Routes real-time bar data through the Data Gateway's WebSocket multiplexer
to avoid Alpaca connection limit issues. Falls back to direct alpaca-py
connection if Gateway is unavailable.
"""

import asyncio
import contextlib
import hashlib
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from orion.config import system_settings
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import ensure_utc
from orion.storage.models import BronzeEvent

logger = setup_struct_logger(__name__)


class AlpacaStreamConnector:
    """
    Real-time WebSocket streaming connector for Alpaca market data.

    By default, connects to the Data Gateway's WebSocket endpoint which
    multiplexes connections to avoid Alpaca's 1-connection-per-stream limit.

    Set ORION_USE_GATEWAY=false to use direct alpaca-py connection (not recommended).
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        feed: str = "sip",
        on_bar_callback: Callable[[BronzeEvent], None] | None = None,
        use_gateway: bool | None = None,
    ):
        self._use_gateway = use_gateway if use_gateway is not None else bool(system_settings.orion_use_gateway)
        self.on_bar_callback = on_bar_callback
        self.feed = feed

        # Gateway client (lazy initialized)
        self._gateway_client = None

        # Direct alpaca-py client (legacy fallback)
        self._direct_stream = None
        self._api_key = api_key
        self._secret_key = secret_key

        # Track subscribed tickers
        self._subscribed_tickers: set[str] = set()

        # Event queue for buffering bars
        self._event_queue: asyncio.Queue[BronzeEvent] = asyncio.Queue()

        # Running state
        self._running = False
        self._stream_task: asyncio.Task | None = None

        if self._use_gateway:
            logger.info("AlpacaStreamConnector initialized in Gateway mode")
        else:
            logger.info("AlpacaStreamConnector initialized in Direct mode (legacy)")

    def _get_gateway_client(self):
        """Lazy initialization of Gateway stream client."""
        if self._gateway_client is None:
            from orion.connectors.gateway_stream_client import create_gateway_stream_client

            self._gateway_client = create_gateway_stream_client(
                on_bar_callback=self._handle_gateway_event,
            )
        return self._gateway_client

    def _get_direct_stream(self):
        """Lazy initialization of direct alpaca-py stream (legacy)."""
        if self._direct_stream is None:
            from alpaca.data.live import StockDataStream

            api_key = self._api_key or os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
            secret_key = self._secret_key or os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")

            if not api_key or not secret_key:
                raise ValueError("Alpaca API credentials not found")

            self._direct_stream = StockDataStream(api_key, secret_key, feed=self.feed)
        return self._direct_stream

    def _handle_gateway_event(self, event: BronzeEvent) -> None:
        """Handle events from Gateway client (callback mode)."""
        if self.on_bar_callback:
            self.on_bar_callback(event)
        else:
            # Queue for later drain
            try:
                self._event_queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Event queue full, dropping event")

    def _generate_event_id(self, ticker: str, bar_data: dict[str, Any]) -> str:
        """Generates a deterministic event ID based on event content."""
        raw_str = f"ALPACA_BAR_1M_{ticker}_{bar_data.get('timestamp', '')}_{bar_data.get('volume', '')}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    async def _handle_direct_bar(self, bar) -> None:
        """Callback for incoming bar data from direct alpaca-py stream (legacy)."""
        try:
            ticker = bar.symbol

            # Convert to dict
            try:
                payload = bar.model_dump(mode="json")
            except AttributeError:
                payload = bar.dict()

            # Data quality validation
            if not bar.close or bar.close <= 0:
                logger.warning(f"Rejecting invalid bar for {ticker}: close={bar.close}")
                return
            if not bar.open or bar.open <= 0:
                logger.warning(f"Rejecting invalid bar for {ticker}: open={bar.open}")
                return
            if bar.volume is None or bar.volume < 0:
                logger.warning(f"Rejecting invalid bar for {ticker}: volume={bar.volume}")
                return

            # Ensure timestamp is in payload
            if "timestamp" not in payload and hasattr(bar, "timestamp"):
                payload["timestamp"] = bar.timestamp.isoformat()

            event_ts = ensure_utc(bar.timestamp)
            event_id = self._generate_event_id(ticker, payload)

            event = BronzeEvent(
                event_id=event_id,
                source="ALPACA",
                event_type="ALPACA_BAR_1M",
                event_ts_utc=event_ts,
                received_ts_utc=datetime.now(UTC),
                payload=payload,
            )

            if self.on_bar_callback:
                self.on_bar_callback(event)
            else:
                await self._event_queue.put(event)

            logger.debug(
                f"Received bar for {ticker} @ {event_ts}",
                extra={"ticker": ticker, "close": bar.close, "volume": bar.volume},
            )

        except Exception as e:
            logger.error(f"Error processing streaming bar: {e}", exc_info=True)

    async def subscribe(self, tickers: list[str]) -> None:
        """Subscribe to bar updates for the given tickers."""
        if not tickers:
            return

        new_tickers = set(tickers) - self._subscribed_tickers
        if not new_tickers:
            return

        logger.info(f"Subscribing to {len(new_tickers)} new tickers for streaming bars")

        if self._use_gateway:
            client = self._get_gateway_client()
            await client.subscribe(list(new_tickers))
        else:
            stream = self._get_direct_stream()
            stream.subscribe_bars(self._handle_direct_bar, *new_tickers)

        self._subscribed_tickers.update(new_tickers)

    async def unsubscribe(self, tickers: list[str]) -> None:
        """Unsubscribe from bar updates for the given tickers."""
        if not tickers:
            return

        to_remove = set(tickers) & self._subscribed_tickers
        if not to_remove:
            return

        logger.info(f"Unsubscribing from {len(to_remove)} tickers")

        if self._use_gateway:
            client = self._get_gateway_client()
            await client.unsubscribe(list(to_remove))
        else:
            stream = self._get_direct_stream()
            stream.unsubscribe_bars(*to_remove)

        self._subscribed_tickers -= to_remove

    async def start(self) -> None:
        """Start the WebSocket stream."""
        if self._running:
            logger.warning("Stream already running")
            return

        self._running = True
        logger.info("Starting Alpaca WebSocket stream")

        if self._use_gateway:
            client = self._get_gateway_client()
            await client.start()
        else:
            # Direct mode: run stream in background task
            async def _run_direct_stream():
                try:
                    stream = self._get_direct_stream()
                    await stream._run_forever()
                except Exception as e:
                    logger.error(f"Direct stream error: {e}", exc_info=True)
                    self._running = False

            self._stream_task = asyncio.create_task(_run_direct_stream())

    async def stop(self) -> None:
        """Stop the WebSocket stream."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping Alpaca WebSocket stream")

        if self._use_gateway:
            if self._gateway_client:
                await self._gateway_client.stop()
        else:
            try:
                if self._direct_stream:
                    await self._direct_stream.stop()
            except Exception as e:
                logger.warning(f"Error stopping direct stream: {e}")

            if self._stream_task:
                self._stream_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._stream_task

    async def drain_events(self, max_events: int = 1000) -> list[BronzeEvent]:
        """
        Drain buffered events from the queue.
        Used when not using callback mode.
        """
        if self._use_gateway and self._gateway_client:
            return self._gateway_client.drain_events(max_events)

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
    def subscribed_tickers(self) -> set[str]:
        return self._subscribed_tickers.copy()


def create_alpaca_stream_connector(
    on_bar_callback: Callable[[BronzeEvent], None] | None = None,
) -> AlpacaStreamConnector:
    """Create AlpacaStreamConnector using environment configuration."""
    return AlpacaStreamConnector(
        on_bar_callback=on_bar_callback,
    )
