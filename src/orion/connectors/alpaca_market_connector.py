import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.storage.models import BronzeEvent

logger = logging.getLogger(__name__)


class AlpacaMarketConnector:
    """
    Connects to Alpaca Market Data API to poll for 1-minute bars.
    """

    def __init__(self, api_key: str, secret_key: str, base_url: Optional[str] = None, paper: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        # Note: StockHistoricalDataClient typically uses standardized base URLs via enums,
        # but allows url_override if needed. We'll pass it if it differs from defaults.
        self.client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key, url_override=base_url)
        self.last_seen_ts = {}  # {ticker: timestamp}

    def _generate_event_id(self, ticker: str, bar_data: dict) -> str:
        """
        Generates a deterministic event ID based on event content.
        """
        # For bars, (ticker + timestamp) is unique. source=ALPACA, type=ALPACA_BAR_1M
        raw_str = f"ALPACA_BAR_1M_{ticker}_{bar_data.get('t', '')}_{bar_data.get('v', '')}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def fetch_bars(
        self, tickers: List[str], start_time: datetime, end_time: Optional[datetime] = None
    ) -> List[BronzeEvent]:
        """
        Fetches bars for the given tickers and time range.
        Converts them to BronzeEvent objects.
        """
        if not tickers:
            return []

        # Ensure start_time is set
        req = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=TimeFrame.Minute,
            start=start_time,
            end=end_time,
            limit=10000,  # Maximize page size
            feed="sip",  # User requested non-IEX exchange
        )

        try:
            # client.get_stock_bars returns a BarSet (dict-like)
            logger.info(f"Polling Alpaca for {len(tickers)} tickers. Start: {start_time}, End: {end_time}")
            bar_set = self.client.get_stock_bars(req)

            logger.info(f"Alpaca returned data for {len(bar_set.data)} tickers.")

            events = []

            # bar_set.data is Dict[str, List[Bar]]
            for ticker, bars in bar_set.data.items():
                for bar in bars:
                    event = self._process_bar(ticker, bar)
                    if event:
                        events.append(event)

            return events

        except Exception as e:
            logger.error(f"Error fetching Alpaca bars: {e}")
            raise e

    def _process_bar(self, ticker: str, bar: object) -> Optional[BronzeEvent]:
        """Helper to process a single bar into a BronzeEvent."""
        try:
            # Inspecting client code: Bar is a pydantic model.
            # We'll use model_dump(mode='json') if pydantic v2 or dict() if v1
            try:
                payload = bar.model_dump(mode="json")
            except AttributeError:
                payload = bar.dict()  # Fallback for v1 if needed

            # Ensure timestamp 't' is present and serializable
            # Alpaca v2 models often use 'timestamp' instead of 't'
            if "t" not in payload:
                if "timestamp" in payload:
                    payload["t"] = payload["timestamp"]
                elif hasattr(bar, "timestamp"):
                    payload["t"] = bar.timestamp.isoformat()

            # Get datetime object
            if hasattr(bar, "timestamp"):
                event_ts = bar.timestamp
            elif hasattr(bar, "t"):
                event_ts = bar.t
            else:
                # Fallback to parsing from payload
                ts_val = payload.get("t") or payload.get("timestamp")
                if ts_val:
                    if isinstance(ts_val, str):
                        event_ts = datetime.fromisoformat(ts_val)
                    else:
                        event_ts = ts_val
                else:
                    raise ValueError(f"No timestamp found in bar: {payload.keys()}")

            # Ensure UTC
            if event_ts.tzinfo is None:
                event_ts = event_ts.replace(tzinfo=timezone.utc)

            # Update watermark
            last_ts = self.last_seen_ts.get(ticker)
            if last_ts is None or event_ts > last_ts:
                self.last_seen_ts[ticker] = event_ts

            event_id = self._generate_event_id(ticker, payload)

            return BronzeEvent(
                event_id=event_id,
                source="ALPACA",
                event_type="ALPACA_BAR_1M",
                event_ts_utc=event_ts,
                received_ts_utc=datetime.now(timezone.utc),
                payload=payload,
            )
        except Exception as e:
            logger.warning(f"Failed to process bar for {ticker}: {e}")
            return None

    def poll(self, tickers: List[str], default_lookback_minutes: int = 15) -> List[BronzeEvent]:
        """
        Polls for new bars for the given tickers.
        Uses watermark to fetch only new data.
        """
        now = datetime.now(timezone.utc)

        # Determine start time: oldest watermark or default lookback
        # If we have multiple tickers, we might ideally fetch per ticker or
        # just take the min watermark to be safe and let dedupe handle overlaps.
        # For simplicity in this batch call: take the min watermark of the requested tickers.

        watermarks = [self.last_seen_ts.get(t) for t in tickers if t in self.last_seen_ts]

        if not watermarks:
            # First run
            start_time = now - timedelta(minutes=default_lookback_minutes)
        else:
            # Use the oldest watermark to ensure we don't miss data for any ticker
            # But add a small buffer/overlap or just use strict > last_seen
            # Alpaca 'start' is inclusive.
            start_time = min(watermarks)

        return self.fetch_bars(tickers, start_time=start_time, end_time=now)

    def get_latest_price(self, ticker: str) -> float:
        """
        Fetches the latest trade price for a ticker.
        Returns 0.0 if failed.
        """
        from alpaca.data.requests import StockLatestTradeRequest

        req = StockLatestTradeRequest(symbol_or_symbols=ticker, feed="sip")
        try:
            trade = self.client.get_stock_latest_trade(req)
            if ticker in trade:
                return float(trade[ticker].price)
            # Handle single object return if different
            return float(trade.price)
        except Exception as e:
            logger.error(f"Failed to get latest price for {ticker}: {e}")
            return 0.0
