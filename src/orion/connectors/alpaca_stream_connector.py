"""
Alpaca WebSocket Streaming Connector.

Uses alpaca-py StockDataStream for real-time bar data with sub-second latency.
Replaces polling-based approach that had 5-30 minute lag due to Historical API delays.
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from alpaca.data.live import StockDataStream
from alpaca.data.models import Bar

from orion.shared.utils import ensure_utc
from orion.storage.models import BronzeEvent

logger = logging.getLogger(__name__)


class AlpacaStreamConnector:
    """
    Real-time WebSocket streaming connector for Alpaca market data.
    
    Provides sub-second latency for 1-minute bars compared to 5-30 minute
    lag from the Historical API polling approach.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        feed: str = "sip",
        on_bar_callback: Optional[Callable[[BronzeEvent], None]] = None,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.feed = feed
        self.on_bar_callback = on_bar_callback
        
        # Initialize the streaming client
        self.stream = StockDataStream(api_key, secret_key, feed=feed)
        
        # Track subscribed tickers
        self._subscribed_tickers: Set[str] = set()
        
        # Event queue for buffering bars
        self._event_queue: asyncio.Queue[BronzeEvent] = asyncio.Queue()
        
        # Running state
        self._running = False
        self._stream_task: Optional[asyncio.Task] = None

    def _generate_event_id(self, ticker: str, bar_data: Dict[str, Any]) -> str:
        """Generates a deterministic event ID based on event content."""
        raw_str = f"ALPACA_BAR_1M_{ticker}_{bar_data.get('timestamp', '')}_{bar_data.get('volume', '')}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    async def _handle_bar(self, bar: Bar) -> None:
        """
        Callback for incoming bar data from WebSocket.
        Converts to BronzeEvent and either queues or invokes callback.
        """
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
                received_ts_utc=datetime.now(timezone.utc),
                payload=payload,
            )

            # Either use callback or queue
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

    async def subscribe(self, tickers: List[str]) -> None:
        """Subscribe to bar updates for the given tickers."""
        if not tickers:
            return

        new_tickers = set(tickers) - self._subscribed_tickers
        if not new_tickers:
            return

        logger.info(f"Subscribing to {len(new_tickers)} new tickers for streaming bars")
        
        # Register handler if not already done
        self.stream.subscribe_bars(self._handle_bar, *new_tickers)
        self._subscribed_tickers.update(new_tickers)

    async def unsubscribe(self, tickers: List[str]) -> None:
        """Unsubscribe from bar updates for the given tickers."""
        if not tickers:
            return

        to_remove = set(tickers) & self._subscribed_tickers
        if not to_remove:
            return

        logger.info(f"Unsubscribing from {len(to_remove)} tickers")
        self.stream.unsubscribe_bars(*to_remove)
        self._subscribed_tickers -= to_remove

    async def start(self) -> None:
        """Start the WebSocket stream in a background task."""
        if self._running:
            logger.warning("Stream already running")
            return

        self._running = True
        logger.info("Starting Alpaca WebSocket stream")

        # Run the stream in a background task
        async def _run_stream():
            try:
                await self.stream._run_forever()
            except Exception as e:
                logger.error(f"Stream error: {e}", exc_info=True)
                self._running = False

        self._stream_task = asyncio.create_task(_run_stream())

    async def stop(self) -> None:
        """Stop the WebSocket stream."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping Alpaca WebSocket stream")

        try:
            await self.stream.stop()
        except Exception as e:
            logger.warning(f"Error stopping stream: {e}")

        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

    async def drain_events(self, max_events: int = 1000) -> List[BronzeEvent]:
        """
        Drain buffered events from the queue.
        Used when not using callback mode.
        """
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
    def subscribed_tickers(self) -> Set[str]:
        return self._subscribed_tickers.copy()


# Factory function for easy initialization
def create_alpaca_stream_connector(
    on_bar_callback: Optional[Callable[[BronzeEvent], None]] = None,
) -> AlpacaStreamConnector:
    """Create AlpacaStreamConnector from environment variables."""
    api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
    
    if not api_key or not secret_key:
        raise ValueError("Alpaca API credentials not found in environment")
    
    return AlpacaStreamConnector(
        api_key=api_key,
        secret_key=secret_key,
        feed="sip",
        on_bar_callback=on_bar_callback,
    )
