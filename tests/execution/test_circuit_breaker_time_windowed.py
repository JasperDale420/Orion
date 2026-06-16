"""Tests for the time-windowed broker-error circuit breaker.

H-08 from predict 260430-1751-execution-reliability flagged the previous
deque-based breaker (`order_history: deque[bool]`, maxlen=20, hardcoded 3%
threshold) as having no time component: a single failure stayed in the deque
all day during low-volume periods, and the deliberate empty-on-restart
decision meant a single early failure could trip the breaker.

The fix replaces the entry-count window with a time window and adds a
minimum-samples threshold to suppress trivial first-failure trips. Both
threshold and window are now configurable via SystemSettings.
"""

from __future__ import annotations

import time
from collections import deque
from unittest.mock import Mock

import pytest

from orion.config import system_settings
from orion.execution.execution_engine import ExecutionEngine


def _make_engine_for_breaker_test() -> ExecutionEngine:
    """Build a stub engine that bypasses the full __init__ — we only need
    the breaker plumbing. No risk_manager / Gateway / ledger required."""
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine.order_history = deque()
    return engine


@pytest.mark.unit
class TestEmptyAndUnderSampledHistory:
    def test_empty_history_does_not_trip(self) -> None:
        engine = _make_engine_for_breaker_test()
        assert engine._check_circuit_breaker() is False

    def test_below_min_samples_does_not_trip_even_on_all_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The deliberate 'do not seed history on restart' policy means we must
        not trip on the very first failure — give the engine min_samples
        broker round-trips before the breaker has signal."""
        monkeypatch.setattr(system_settings, "circuit_breaker_min_samples", 5)
        engine = _make_engine_for_breaker_test()
        for _ in range(4):
            engine._record_result(False)
        assert engine._check_circuit_breaker() is False


@pytest.mark.unit
class TestThreshold:
    def test_at_min_samples_low_error_rate_does_not_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(system_settings, "circuit_breaker_min_samples", 5)
        monkeypatch.setattr(system_settings, "circuit_breaker_error_rate", 0.03)
        engine = _make_engine_for_breaker_test()
        # 0 / 5 = 0% — well under threshold
        for _ in range(5):
            engine._record_result(True)
        assert engine._check_circuit_breaker() is False

    def test_at_min_samples_high_error_rate_trips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(system_settings, "circuit_breaker_min_samples", 5)
        monkeypatch.setattr(system_settings, "circuit_breaker_error_rate", 0.03)
        engine = _make_engine_for_breaker_test()
        # 1 / 5 = 20% — over threshold
        engine._record_result(False)
        for _ in range(4):
            engine._record_result(True)
        assert engine._check_circuit_breaker() is True

    def test_threshold_boundary_is_exclusive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Trip is `> threshold`, not `>=` — at exactly the threshold, no trip."""
        monkeypatch.setattr(system_settings, "circuit_breaker_min_samples", 4)
        monkeypatch.setattr(system_settings, "circuit_breaker_error_rate", 0.25)
        engine = _make_engine_for_breaker_test()
        # 1 / 4 = 25% = threshold exactly → should NOT trip
        engine._record_result(False)
        for _ in range(3):
            engine._record_result(True)
        assert engine._check_circuit_breaker() is False


@pytest.mark.unit
class TestTimeWindow:
    def test_old_failures_pruned_out_of_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failure outside the configured window must not count toward the
        rate. Without this, low-volume days kept a single failure tripping the
        breaker until 20 more orders pushed it out."""
        monkeypatch.setattr(system_settings, "circuit_breaker_min_samples", 1)
        monkeypatch.setattr(system_settings, "circuit_breaker_error_rate", 0.5)
        monkeypatch.setattr(system_settings, "circuit_breaker_window_seconds", 0.1)

        engine = _make_engine_for_breaker_test()

        # Inject an old failure 1 second ago (outside 100ms window)
        engine.order_history.append((time.monotonic() - 1.0, False))
        # And a recent success
        engine._record_result(True)

        # The old failure must be pruned; remaining history is 1 success → 0% → no trip
        assert engine._check_circuit_breaker() is False
        # Pruning leaves only the recent entry in the deque
        assert len(engine.order_history) == 1
        assert engine.order_history[0][1] is True

    def test_recent_burst_of_failures_trips_even_after_old_successes_age_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(system_settings, "circuit_breaker_min_samples", 3)
        monkeypatch.setattr(system_settings, "circuit_breaker_error_rate", 0.3)
        monkeypatch.setattr(system_settings, "circuit_breaker_window_seconds", 0.1)

        engine = _make_engine_for_breaker_test()
        # Old successes 1 second ago — should be pruned
        for _ in range(10):
            engine.order_history.append((time.monotonic() - 1.0, True))
        # Recent failures within window
        engine._record_result(False)
        engine._record_result(False)
        engine._record_result(True)

        # After pruning: 2 failures / 3 = 67% → trips
        assert engine._check_circuit_breaker() is True

    def test_prune_keeps_entries_inside_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(system_settings, "circuit_breaker_window_seconds", 60.0)
        engine = _make_engine_for_breaker_test()

        now = time.monotonic()
        engine.order_history.append((now - 30.0, True))  # inside window
        engine.order_history.append((now - 10.0, False))  # inside window
        engine._prune_order_history()
        assert len(engine.order_history) == 2


@pytest.mark.unit
class TestConfigurability:
    def test_lower_threshold_makes_breaker_more_sensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(system_settings, "circuit_breaker_min_samples", 10)
        engine = _make_engine_for_breaker_test()
        # 1 / 10 = 10%
        engine._record_result(False)
        for _ in range(9):
            engine._record_result(True)

        # 10% rate; threshold 0.05 → trip
        monkeypatch.setattr(system_settings, "circuit_breaker_error_rate", 0.05)
        assert engine._check_circuit_breaker() is True

        # Same history; threshold 0.20 → no trip
        monkeypatch.setattr(system_settings, "circuit_breaker_error_rate", 0.20)
        assert engine._check_circuit_breaker() is False

    def test_record_result_stores_timestamp_and_outcome(self) -> None:
        engine = _make_engine_for_breaker_test()
        before = time.monotonic()
        engine._record_result(True)
        after = time.monotonic()

        assert len(engine.order_history) == 1
        ts, success = engine.order_history[0]
        assert before <= ts <= after
        assert success is True

    def test_legacy_assignment_to_empty_list_does_not_break_check(self) -> None:
        """Existing tests assign `engine.order_history = []`. Verify the breaker
        gracefully handles a list (vs deque) and an empty container without
        crashing on prune or count."""
        engine = ExecutionEngine.__new__(ExecutionEngine)
        engine.order_history = []  # type: ignore[assignment]
        # Should not raise
        assert engine._check_circuit_breaker() is False
