import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion.processing.feature_engine import FeatureEngine
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_compute_does_not_block_on_persistence():
    """
    Verifies that FeatureEngine.compute returns quickly locally even if persist_features takes time.
    """
    engine = FeatureEngine()

    # Mock history for a ticker
    engine.history = {"AAPL": MagicMock()}
    # Just ensure it doesn't crash on logical lookups
    # actually mock history access to return something valid or ensure exception is caught?
    # The code: if ticker in self.history -> lookups.
    # If not in history -> empty dict returned (unless exception).
    # We want valid return to reach the persistence call.
    engine.history = {}  # effectively empty features returned, which is fine

    # Mock `persist_features` with a delay
    delay_sec = 0.5

    async def slow_persist(*args, **kwargs):
        await asyncio.sleep(delay_sec)

    engine.persist_features = AsyncMock(side_effect=slow_persist)

    # Create Candidate
    candidate = CandidateTrade(
        candidate_id="c1",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        rule_id="r1",
        direction="LONG",
        confidence=1.0,
        evidence={},
    )

    # Timing
    t0 = time.time()
    await engine.compute(candidate)
    t1 = time.time()
    duration = t1 - t0

    # Assertion
    # If blocking, duration >= 0.5s
    # If non-blocking, duration < 0.2s (allowing for overhead)

    print(f"Compute Duration: {duration:.4f}s")

    assert duration < 0.3, f"Compute blocked for {duration:.4f}s (Threshold 0.3s, Delay 0.5s)"

    # Also verify persist was called (eventually)
    # Since it's background (or should be), we might need to wait for pending tasks if we want to ensure it acts?
    # In the blocking version, it's already called.
    engine.persist_features.assert_called_once()
