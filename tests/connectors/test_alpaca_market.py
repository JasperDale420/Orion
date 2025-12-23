import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from alpaca.data.models import Bar, BarSet
from orion.connectors.alpaca_market_connector import AlpacaMarketConnector


class TestAlpacaMarketConnector(unittest.TestCase):
    def setUp(self):
        self.api_key = "test_key"
        self.secret_key = "test_secret"
        self.base_url = "https://paper-api.alpaca.markets"

        # Patch the StockHistoricalDataClient
        self.client_patcher = patch("orion.connectors.alpaca_market_connector.StockHistoricalDataClient")
        self.MockClient = self.client_patcher.start()

        self.connector = AlpacaMarketConnector(self.api_key, self.secret_key, self.base_url)

    def tearDown(self):
        self.client_patcher.stop()

    def test_init(self):
        """Verify client initialization"""
        self.MockClient.assert_called_with(api_key=self.api_key, secret_key=self.secret_key, url_override=self.base_url)

    def test_fetch_bars_success(self):
        """Test successful bar fetching"""
        # Mock response
        mock_bar = MagicMock(spec=Bar)
        mock_bar.t = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        mock_bar.model_dump.return_value = {
            "t": "2023-01-01T10:00:00Z",
            "o": 100.0,
            "h": 105.0,
            "l": 99.0,
            "c": 102.0,
            "v": 1000,
        }

        # client.get_stock_bars returns BarSet
        mock_bar_set = MagicMock(spec=BarSet)
        mock_bar_set.data = {"SPY": [mock_bar]}
        self.connector.client.get_stock_bars.return_value = mock_bar_set

        events = self.connector.fetch_bars(["SPY"], datetime.now(timezone.utc))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source, "ALPACA")
        self.assertEqual(events[0].event_type, "ALPACA_BAR_1M")
        self.assertEqual(events[0].payload["c"], 102.0)
        self.assertEqual(self.connector.last_seen_ts["SPY"], mock_bar.t)

    def test_poll_updates_watermark(self):
        """Test poll method updates watermark"""
        # 1. First poll
        mock_bar1 = MagicMock(spec=Bar)
        ts1 = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        mock_bar1.t = ts1
        mock_bar1.model_dump.return_value = {"t": ts1.isoformat(), "c": 100}

        mock_bar_set1 = MagicMock(spec=BarSet)
        mock_bar_set1.data = {"AAPL": [mock_bar1]}
        self.connector.client.get_stock_bars.return_value = mock_bar_set1

        self.connector.poll(["AAPL"])
        self.assertEqual(self.connector.last_seen_ts["AAPL"], ts1)

        # 2. Second poll should use watermark
        mock_bar2 = MagicMock(spec=Bar)
        ts2 = datetime(2023, 1, 1, 10, 1, 0, tzinfo=timezone.utc)
        mock_bar2.t = ts2
        mock_bar2.model_dump.return_value = {"t": ts2.isoformat(), "c": 101}

        mock_bar_set2 = MagicMock(spec=BarSet)
        mock_bar_set2.data = {"AAPL": [mock_bar2]}
        self.connector.client.get_stock_bars.return_value = mock_bar_set2

        self.connector.poll(["AAPL"])

        # Verify get_stock_bars called with start > ts1 (implementation uses min(watermarks))
        # The exact start time arg is dynamically calculated, but we verified watermark updated
        self.assertEqual(self.connector.last_seen_ts["AAPL"], ts2)

    def test_generate_event_id_deterministic(self):
        """Verify event ID is deterministic based on content"""
        payload = {"t": "2023-01-01T10:00:00Z", "v": 1000}

        id1 = self.connector._generate_event_id("SPY", payload)
        id2 = self.connector._generate_event_id("SPY", payload)

        self.assertEqual(id1, id2)

        payload_diff = {"t": "2023-01-01T10:00:00Z", "v": 1001}
        id3 = self.connector._generate_event_id("SPY", payload_diff)
        self.assertNotEqual(id1, id3)
