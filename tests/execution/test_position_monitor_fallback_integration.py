"""Integration tests for position_monitor's fallback-then-ML wiring.

Verifies the contract established in Phase 2 Task 2.3:
- Fallback rules run FIRST and short-circuit the ML classifier.
- A raising fallback rule does NOT kill the eval loop — the position
  falls through to the ML branch, and other positions in the loop
  are unaffected.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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
        # A loaded model per bucket — the classifier is only consulted for
        # buckets that have one.
        models = {"POSITION": object(), "SWING": object()}

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


# --- Test 3: smoke test — fallback rules called for EVERY tracked position ---


def test_fallback_rules_called_once_per_tracked_position(monkeypatch):
    """Phase 4 of exit-pipeline RCA: future-proofs the Phase 2 wire-in.

    The earlier tests would BOTH pass if someone accidentally deleted
    the `evaluate_fallback_rules` call from `evaluate_exits`:
    test_fallback_short_circuits_classifier_on_profit_target would
    pass because ML wouldn't be called (the classifier-boom would
    re-raise as AssertionError… wait, actually that one WOULD catch
    it). But test_fallback_exception_falls_through wouldn't, because
    a benign ML classifier covers both code paths.

    This smoke test spies on `evaluate_fallback_rules` and asserts
    the call count equals the tracked-position count exactly once
    per loop. If a future refactor removes the call (or accidentally
    short-circuits past it), this test fails immediately.
    """
    from types import SimpleNamespace

    from orion.execution import exit_fallback_rules as efr

    call_args: list[str] = []  # capture position symbols passed to each call
    real_fn = efr.evaluate_fallback_rules

    def _spy(position, **kwargs):
        call_args.append(position.symbol)
        return real_fn(position, **kwargs)

    monkeypatch.setattr(efr, "evaluate_fallback_rules", _spy)

    class _ClassifierBenign:
        models = {"SWING": object()}

        def predict(self, _features):
            return SimpleNamespace(should_exit=False, confidence=0.0, reasoning="hold")

    monitor = _build_monitor_with_classifier(_ClassifierBenign())
    # Three positions, none at the fallback threshold (10% gain is
    # well under the default 100% profit target and the 5-day DTE is
    # well above the min_dte=1 cliff). All fallback rules return None,
    # falling through to the benign ML classifier.
    monitor.tracked_positions = {
        f"POS_{i}": _tracked_position(
            symbol=f"POS_{i}",
            return_pct_value=10.0,
            bucket="SWING",
            dte_at_entry=5,
        )
        for i in range(3)
    }

    monitor.evaluate_exits()

    # Every position must have been passed to evaluate_fallback_rules
    # exactly once. A future regression that removes the call (or
    # gates it behind some condition) fails this assertion loudly.
    assert sorted(call_args) == ["POS_0", "POS_1", "POS_2"], (
        f"evaluate_fallback_rules must be called once per tracked position; "
        f"got call_args={call_args}. If this fails, check that the deferred "
        f"`from orion.execution.exit_fallback_rules import evaluate_fallback_rules` "
        f"inside `PositionMonitor.evaluate_exits` still wraps every position."
    )


# --- Test 4: the session close reaches the 0DTE flatten rule ----------------


def test_session_close_resolved_once_and_passed_to_fallback_rules(monkeypatch):
    """The 0DTE flatten deadline is derived from the session close, so an
    early-close day flattens before 13:00 ET instead of never. If the monitor
    stops passing it, every 0DTE reverts to the 16:00-close assumption.
    """
    from types import SimpleNamespace

    from orion.execution import exit_fallback_rules as efr

    session_close = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)  # 13:00 ET half day
    resolve_calls: list[object] = []
    seen_session_close: list[object] = []

    def _fake_resolve(*args, **kwargs):
        resolve_calls.append(args)
        return session_close

    real_fn = efr.evaluate_fallback_rules

    def _spy(position, **kwargs):
        seen_session_close.append(kwargs.get("session_close"))
        return real_fn(position, **kwargs)

    monkeypatch.setattr("orion.core.market_schedule.resolve_session_close", _fake_resolve)
    monkeypatch.setattr(efr, "evaluate_fallback_rules", _spy)

    class _ClassifierBenign:
        models = {"SWING": object()}

        def predict(self, _features):
            return SimpleNamespace(should_exit=False, confidence=0.0, reasoning="hold")

    monitor = _build_monitor_with_classifier(_ClassifierBenign())
    monitor.tracked_positions = {
        f"POS_{i}": _tracked_position(symbol=f"POS_{i}", return_pct_value=10.0, bucket="SWING") for i in range(3)
    }

    monitor.evaluate_exits()

    assert seen_session_close == [session_close] * 3
    # One calendar lookup per cycle, not per position.
    assert len(resolve_calls) == 1


# --- Test 5-7: without a loaded exit model, the barriers ARE the policy -------
#
# No `*_exit.pkl` has been deployed since 2026-07-01, so `BucketExitClassifier.predict`
# always ran its heuristic — whose SWING stop (-20%) is tighter than the
# documented -40% barrier and therefore always won. July: 9 heuristic stops,
# 5 of them within 30 minutes of entry on bid/ask noise.


class _NoModelClassifier:
    """The production shape since 2026-07-01: a classifier with nothing loaded."""

    models: dict = {}

    def predict(self, _features):
        raise AssertionError("classifier MUST NOT be consulted when no exit model is loaded for the bucket")


def test_evaluate_exits_no_heuristic_stop_when_no_exit_model():
    """SWING at -22%: inside the documented -40% stop, so NO exit — the
    heuristic's -20% stop must not pre-empt the barrier."""
    monitor = _build_monitor_with_classifier(_NoModelClassifier())
    monitor.tracked_positions = {
        "SWING_POS": _tracked_position(symbol="SWING_POS", return_pct_value=-22.0, bucket="SWING"),
    }

    assert monitor.evaluate_exits() == []


def test_evaluate_exits_deterministic_barrier_still_fires():
    """Same position at -45%: the documented stop fires, with its own identity."""
    monitor = _build_monitor_with_classifier(_NoModelClassifier())
    monitor.tracked_positions = {
        "SWING_POS": _tracked_position(symbol="SWING_POS", return_pct_value=-45.0, bucket="SWING"),
    }

    signals = monitor.evaluate_exits()

    assert len(signals) == 1
    pos, pred = signals[0]
    assert pos.symbol == "SWING_POS"
    assert pred.should_exit is True
    assert pred.rule_id == "stop_loss_v1"
    assert pred.urgency == "IMMEDIATE"


def test_evaluate_exits_uses_model_when_loaded():
    """A loaded model for the bucket keeps the classifier in the loop after the
    barriers pass — the model's verdict is what fires."""
    predict_called_for: list[str] = []

    class _LoadedModelClassifier:
        models = {"SWING": object()}

        def predict(self, features):
            predict_called_for.append(features.bucket)
            return SimpleNamespace(should_exit=True, confidence=0.9, reasoning="ML exit score: 0.90")

    monitor = _build_monitor_with_classifier(_LoadedModelClassifier())
    monitor.tracked_positions = {
        "SWING_POS": _tracked_position(symbol="SWING_POS", return_pct_value=-22.0, bucket="SWING"),
        # No model for SHORT_SWING: barriers only, and -22% is inside its -35% stop.
        "SS_POS": _tracked_position(symbol="SS_POS", return_pct_value=-22.0, bucket="SHORT_SWING"),
    }

    signals = monitor.evaluate_exits()

    assert predict_called_for == ["SWING"]
    assert [(pos.symbol, pred.reasoning) for pos, pred in signals] == [("SWING_POS", "ML exit score: 0.90")]
