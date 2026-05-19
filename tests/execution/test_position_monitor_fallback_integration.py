"""Integration tests for position_monitor's fallback-then-ML wiring.

Verifies the contract established in Phase 2 Task 2.3:
- Fallback rules run FIRST and short-circuit the ML classifier.
- A raising fallback rule does NOT kill the eval loop — the position
  falls through to the ML branch, and other positions in the loop
  are unaffected.
"""

from datetime import UTC, datetime, timedelta

from orion.execution.position_monitor import PositionMonitor, TrackedPosition


def _tracked_position(
    *,
    symbol: str,
    return_pct_value: float,  # PERCENT (TrackedPosition stores in percent)
    max_return_pct_value: float = 0.0,
    bucket: str = "SHORT_SWING",
    entry_offset_days: int = 2,
    dte_at_entry: int = 5,
) -> TrackedPosition:
    """Build a real TrackedPosition (not a SimpleNamespace) for integration tests."""
    return TrackedPosition(
        symbol=symbol,
        qty=5,
        entry_price=10.0,
        current_price=10.0 * (1 + return_pct_value / 100.0),
        unrealized_pnl_pct=return_pct_value,
        entry_time=datetime.now(UTC) - timedelta(days=entry_offset_days),
        bucket=bucket,
        max_return_pct=max_return_pct_value,
        max_drawdown_pct=0.0,
        premium_usd=5000.0,
        dte_at_entry=dte_at_entry,
        option_symbol=symbol + "_OPT",
    )


def _build_monitor_with_classifier(classifier) -> PositionMonitor:
    """Construct a PositionMonitor without going through __init__'s side effects.

    Bypasses the connector + heber_reader wiring; we're only testing
    evaluate_exits's branching logic.
    """
    monitor = PositionMonitor.__new__(PositionMonitor)
    monitor.tracked_positions = {}
    monitor.exit_classifier = classifier
    return monitor


# --- Test 1: fallback short-circuits the classifier --------------------------


def test_fallback_short_circuits_classifier_on_profit_target():
    """+150% return must trigger the profit-target fallback; ML must NOT run."""

    class _ClassifierBoom:
        def predict(self, _features):
            raise AssertionError("ML classifier MUST NOT be called when a fallback fires")

    monitor = _build_monitor_with_classifier(_ClassifierBoom())
    monitor.tracked_positions = {
        "QQQ_PUT": _tracked_position(
            symbol="QQQ_PUT",
            return_pct_value=150.0,  # +150% — above the default 100% profit target
            max_return_pct_value=150.0,
        )
    }

    signals = monitor.evaluate_exits()

    assert len(signals) == 1
    pos, pred = signals[0]
    assert pos.symbol == "QQQ_PUT"
    assert pred.should_exit is True
    assert pred.rule_id == "profit_target_v1"


# --- Test 2: a raising fallback does NOT kill the loop -----------------------


def test_fallback_exception_falls_through_to_ml_classifier(monkeypatch, caplog):
    """If evaluate_fallback_rules raises, the position must still get an ML eval,
    AND other positions in the loop must be unaffected."""

    # Track which positions had `predict` called on them.
    predict_called_for: list[str] = []

    class _ClassifierBenign:
        def predict(self, features):
            # Capture the bucket so we can correlate to a position symbol.
            predict_called_for.append(features.bucket)
            # Return "don't exit" so we don't trigger the ML-fired branch.
            from types import SimpleNamespace

            return SimpleNamespace(
                should_exit=False,
                confidence=0.0,
                reasoning="ml says hold",
            )

    monitor = _build_monitor_with_classifier(_ClassifierBenign())
    monitor.tracked_positions = {
        "RAISING_POS": _tracked_position(
            symbol="RAISING_POS",
            return_pct_value=10.0,
            bucket="POSITION",  # bucket value we'll grep for
        ),
        "NORMAL_POS": _tracked_position(
            symbol="NORMAL_POS",
            return_pct_value=10.0,
            bucket="SWING",  # different bucket so we can distinguish
        ),
    }

    # Monkeypatch evaluate_fallback_rules at the IMPORT SITE inside
    # position_monitor.evaluate_exits. The function imports it locally, so we
    # patch the symbol on the exit_fallback_rules module — the local
    # import statement inside evaluate_exits re-fetches the attribute each call.
    from orion.execution import exit_fallback_rules as efr

    real_fn = efr.evaluate_fallback_rules

    def _raising_or_real(position, **kwargs):
        if getattr(position, "symbol", "") == "RAISING_POS":
            raise RuntimeError("synthetic rule failure")
        return real_fn(position, **kwargs)

    monkeypatch.setattr(efr, "evaluate_fallback_rules", _raising_or_real)

    import logging

    caplog.set_level(logging.ERROR, logger="orion.execution.position_monitor")

    signals = monitor.evaluate_exits()

    # 1. Both positions reached the ML branch (no exit signals because ML
    #    returned should_exit=False, but predict was called on each).
    assert "POSITION" in predict_called_for, "RAISING_POS must fall through to ML after fallback raised"
    assert "SWING" in predict_called_for, (
        "NORMAL_POS must also be evaluated — exception in one position must not kill the whole loop"
    )
    # 2. No exit signals — ML said hold for both.
    assert signals == []
    # 3. The exception was logged with the expected event tag.
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        "exit_fallback_error" in (getattr(r, "event", "") or str(getattr(r, "extra", {})))
        or "Exit fallback evaluation raised" in r.getMessage()
        for r in error_records
    ), f"Expected exit_fallback_error log, got: {[r.getMessage() for r in error_records]}"
