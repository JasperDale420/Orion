import time
import unittest
from unittest.mock import MagicMock

from orion.core.universe_manager import UniverseManager
from orion.storage.models import BronzeEvent


class TestUniverseManager(unittest.TestCase):
    def setUp(self):
        self.manager = UniverseManager()
        # Clear static universe for testing isolation (mocking config would be better but this works for unit)
        self.manager.static_universe = set()
        self.manager.held_tickers = set()
        self.manager.alert_tickers = {}

    def test_update_from_config(self):
        tickers = ["AAPL", "GOOG"]
        self.manager.update_from_config(tickers)
        self.assertEqual(len(self.manager.static_universe), 2)
        self.assertIn("AAPL", self.manager.static_universe)

        # Verify get_active_universe includes them
        active = self.manager.get_active_universe()
        self.assertEqual(len(active), 2)
        self.assertIn("GOOG", active)

    def test_update_from_event(self):
        # Create a mock BronzeEvent
        event = MagicMock(spec=BronzeEvent)
        event.ticker = "TSLA"
        event.payload = {}

        self.manager.update_from_event(event)

        self.assertIn("TSLA", self.manager.active_tickers)
        self.assertIn("TSLA", self.manager.get_active_universe())

        # Test payload fallback
        event2 = MagicMock(spec=BronzeEvent)
        event2.ticker = None
        event2.payload = {"ticker": "MSFT"}

        self.manager.update_from_event(event2)
        self.assertIn("MSFT", self.manager.active_tickers)

    def test_cleanup_ttl(self):
        # Add a ticker
        self.manager.active_tickers["OLD"] = time.time() - 1000  # 1000s ago
        self.manager.active_tickers["NEW"] = time.time()  # now

        # Cleanup with TTL=600
        self.manager.cleanup(ttl_seconds=600)

        active = self.manager.get_active_universe()
        self.assertNotIn("OLD", active)
        self.assertIn("NEW", active)

    def test_held_positions_pinned(self):
        self.manager.update_from_positions(["SPY", "AAPL"])
        self.manager.cleanup(ttl_seconds=1)
        active = self.manager.get_active_universe()
        self.assertIn("SPY", active)
        self.assertIn("AAPL", active)

    def test_alerts_have_longer_ttl(self):
        event = MagicMock(spec=BronzeEvent)
        event.ticker = "NVDA"
        event.payload = {}
        event.event_type = "UW_ALERT"

        self.manager.update_from_event(event)
        # Simulate time passing so activity expires but alert does not.
        self.manager.active_tickers["NVDA"] = time.time() - 10
        self.manager.alert_tickers["NVDA"] = time.time() - 3

        self.manager.cleanup(ttl_seconds=5, alert_ttl_seconds=20)
        active = self.manager.get_active_universe()
        self.assertIn("NVDA", active)

    def test_static_never_expires(self):
        self.manager.update_from_config(["SPY"])
        # Even if we try to "expire" it from active (it shouldn't be there, but hypothetically)

        # Cleanup should not touch static
        self.manager.cleanup(ttl_seconds=1)

        self.assertIn("SPY", self.manager.get_active_universe())
