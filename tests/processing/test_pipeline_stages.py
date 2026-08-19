"""
Tests for composable pipeline stages (RegimeGate, MLPreFilter, SolverEnsemble).

Each stage is tested in isolation to verify it correctly returns CONTINUE or SKIP
and populates PipelineContext fields for downstream stages.
"""

import asyncio
import itertools
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.analysis.regime import MarketRegimeSnapshot, RiskRegime, SessionRegime, TrendRegime, VIXRegime, VolRegime
from orion.processing.pipeline import PipelineContext, StageResult
from orion.processing.stages.ml_prefilter import MLPreFilter
from orion.processing.stages.regime_gate import RegimeGate
from orion.processing.stages.solver_ensemble import SolverEnsemble
from orion.storage.db import async_session_factory
from orion.storage.models import RegimeSnapshot
from orion.storage.models_gold import CandidateTrade

_regime_snapshot_id_seq = itertools.count(1)


async def _insert_regime_snapshot(*, ts_utc: datetime, vix_level: float | None, ticker: str = "SPY") -> None:
    # SQLite's rowid-alias autoincrement only kicks in for an `Integer` primary
    # key column; the model declares `BigInteger`, so the in-memory test DB
    # needs an explicit id (Postgres autoincrements this fine in prod).
    async with async_session_factory() as session:
        session.add(
            RegimeSnapshot(
                id=next(_regime_snapshot_id_seq),
                ts_utc=ts_utc,
                ticker=ticker,
                vix_level=vix_level,
                realized_vol=None,
            )
        )
        await session.commit()


def _make_candidate(**overrides) -> CandidateTrade:
    defaults = dict(
        candidate_id="test_cand_1",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        rule_id="rule_test",
        direction="LONG",
        confidence=0.8,
        evidence={},
    )
    defaults.update(overrides)
    return CandidateTrade(**defaults)


# --- RegimeGate ---


@pytest.mark.asyncio
async def test_regime_gate_continues_in_normal_conditions():
    gate = RegimeGate()

    # Mock detector to return a benign regime
    snapshot = MarketRegimeSnapshot(
        ts=datetime.now(UTC),
        trend=TrendRegime.FLAT,
        vol=VolRegime.NORMAL,
        risk=RiskRegime.NEUTRAL,
        session=SessionRegime.MIDDAY,
        vix_regime=VIXRegime.NORMAL,
    )
    gate.multi_axis_detector.detect = MagicMock(return_value=snapshot)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert ctx.regime_snapshot is snapshot
    assert ctx.regime_size_multiplier > 0


@pytest.mark.asyncio
async def test_regime_gate_skips_on_shock():
    gate = RegimeGate()

    # SHOCK should trigger SKIP
    snapshot = MarketRegimeSnapshot(
        ts=datetime.now(UTC),
        trend=TrendRegime.FLAT,
        vol=VolRegime.SHOCK,
        risk=RiskRegime.RISK_OFF,
        session=SessionRegime.MIDDAY,
        vix_regime=VIXRegime.EXTREME,
    )
    gate.multi_axis_detector.detect = MagicMock(return_value=snapshot)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "SKIP"
    assert "SHOCK" in result.reason
    assert result.trace.get("regime_blocked") is True


@pytest.mark.asyncio
async def test_regime_gate_populates_context():
    gate = RegimeGate()

    snapshot = MarketRegimeSnapshot(
        ts=datetime.now(UTC),
        trend=TrendRegime.UP,
        vol=VolRegime.LOW,
        risk=RiskRegime.RISK_ON,
        session=SessionRegime.POWER_HOUR,
        vix_regime=VIXRegime.LOW,
    )
    gate.multi_axis_detector.detect = MagicMock(return_value=snapshot)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert ctx.regime_snapshot.trend == TrendRegime.UP
    assert ctx.regime_snapshot.vol == VolRegime.LOW
    # Size multiplier should reflect favorable conditions
    assert ctx.regime_size_multiplier > 0


# --- RegimeGate: honest regime inputs (audit fix — detect() was called with
# no market data, so the documented SHOCK/extreme-VIX block could never fire) ---


@pytest.mark.asyncio
async def test_regime_gate_uses_fresh_snapshot_to_detect_real_shock():
    """A fresh regime_snapshots row (written by feature_enrichment) with a
    genuine extreme vix_level must feed detect() and trigger the SHOCK block —
    proving the gate is no longer blind to real market data when it exists."""
    gate = RegimeGate()  # real detector, not mocked — exercise the actual wiring

    await _insert_regime_snapshot(ts_utc=datetime.now(UTC), vix_level=40.0)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "SKIP"
    assert "SHOCK" in result.reason
    assert result.trace.get("regime_inputs") == "regime_snapshots"
    assert ctx.regime_snapshot.vol == VolRegime.SHOCK


@pytest.mark.asyncio
async def test_regime_gate_ignores_stale_snapshot():
    """A regime_snapshots row older than the documented freshness window must
    NOT be treated as real-time data — a stale extreme VIX reading (last
    written 30 minutes ago) must not silently drive today's decisions."""
    gate = RegimeGate()

    stale_ts = datetime.now(UTC) - timedelta(minutes=30)
    await _insert_regime_snapshot(ts_utc=stale_ts, vix_level=40.0)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert result.trace.get("regime_inputs") == "none"


@pytest.mark.asyncio
async def test_regime_gate_no_snapshot_reports_none_and_does_not_block():
    """No regime_snapshots row at all (fresh DB / no feature_enrichment writes
    yet) must fail toward not blocking, and the trace must say so honestly
    rather than implying real inputs were used."""
    gate = RegimeGate()

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert result.trace.get("regime_inputs") == "none"


@pytest.mark.asyncio
async def test_regime_gate_fresh_snapshot_with_null_vix_reports_none():
    """A fresh row that carries no vix_level (today's reality: feature_enrichment's
    upstream VIX read is disabled in prod) must not be reported as a real
    input just because the row itself is recent."""
    gate = RegimeGate()

    await _insert_regime_snapshot(ts_utc=datetime.now(UTC), vix_level=None)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert result.trace.get("regime_inputs") == "none"


@pytest.mark.asyncio
async def test_regime_gate_db_read_failure_falls_back_to_no_block(monkeypatch):
    """A DB error while reading regime_snapshots must never crash the pipeline
    or block trading — it must degrade to the same behavior as no source."""
    gate = RegimeGate()

    def _raise(*_args, **_kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("orion.processing.stages.regime_gate.async_session_factory", _raise)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert result.trace.get("regime_inputs") == "none"


@pytest.mark.asyncio
async def test_regime_gate_logs_inert_warning_exactly_once(caplog):
    """When no real input is available, the gate must warn once (not spam
    the logs once per candidate) so an operator can discover it — but the
    per-decision trace still reports `regime_inputs: none` every time."""
    gate = RegimeGate()

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            ctx = PipelineContext(candidate=_make_candidate())
            await gate.evaluate(ctx)

    # regime_gate's logger is structlog-based (setup_struct_logger), which
    # JSON-renders the event into LogRecord.msg before stdlib logging ever
    # sees it — so the event name is a substring of the rendered record,
    # not an exact match (unlike a plain logging.getLogger() call).
    inert_warnings = [r for r in caplog.records if "regime_gate_inert_no_inputs" in r.getMessage()]
    assert len(inert_warnings) == 1


@pytest.mark.asyncio
async def test_regime_gate_rejects_future_dated_snapshot():
    """Codex adversarial review (high): a future-dated row (clock skew, bad
    data) has a NEGATIVE age, which must not satisfy `age <= freshness` and
    be treated as fresh forever until real time catches up to it — that
    would let one bad row latch a SHOCK block indefinitely."""
    gate = RegimeGate()

    future_ts = datetime.now(UTC) + timedelta(minutes=10)
    await _insert_regime_snapshot(ts_utc=future_ts, vix_level=40.0)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert result.trace.get("regime_inputs") == "none"


@pytest.mark.asyncio
async def test_regime_gate_db_read_timeout_falls_back_to_no_block(monkeypatch):
    """Codex adversarial review (high): main_execution awaits
    SignalEngine.decide() sequentially per candidate, so a hung DB read
    inside the gate would stall the whole pipeline rather than degrading —
    the read must be wrapped in a hard timeout, same idiom as
    shared/liveness.py's publish call."""
    gate = RegimeGate()
    monkeypatch.setattr("orion.processing.stages.regime_gate.REGIME_INPUT_DB_TIMEOUT_SECONDS", 0.05)

    async def _hang():
        await asyncio.sleep(5)
        return None

    monkeypatch.setattr(gate, "_read_latest_snapshot", _hang)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await asyncio.wait_for(gate.evaluate(ctx), timeout=2.0)

    assert result.action == "CONTINUE"
    assert result.trace.get("regime_inputs") == "none"


@pytest.mark.asyncio
async def test_regime_gate_cache_never_outlives_row_freshness_deadline():
    """Codex adversarial review (medium): the 60s read-cache must not keep
    returning a row's real vix past that row's own 15-minute freshness
    deadline — a row read at 14:59 old must not still be "fresh" 61 seconds
    later. The cache expiry is capped at the row's own deadline, not just
    now + 60s."""
    gate = RegimeGate()

    almost_stale_ts = datetime.now(UTC) - timedelta(minutes=15) + timedelta(seconds=5)
    await _insert_regime_snapshot(ts_utc=almost_stale_ts, vix_level=40.0)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.trace.get("regime_inputs") == "regime_snapshots"
    assert result.action == "SKIP"

    # The cache must expire at (row_ts + 15min), not (now + 60s) — the row
    # deadline arrives in ~5s, well before the 60s TTL would.
    row_deadline = almost_stale_ts + timedelta(minutes=15)
    assert gate._cache_expires_at is not None
    assert gate._cache_expires_at <= row_deadline + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_regime_gate_caches_db_read_within_ttl():
    """The bounded read must be cached (<=60s) so a hot pipeline evaluating
    many candidates per minute doesn't hammer TimescaleDB once per candidate
    for a value that only changes every 5 minutes."""
    gate = RegimeGate()
    await _insert_regime_snapshot(ts_utc=datetime.now(UTC), vix_level=40.0)

    calls = 0
    real_factory = async_session_factory

    def _counting_factory():
        nonlocal calls
        calls += 1
        return real_factory()

    with patch("orion.processing.stages.regime_gate.async_session_factory", side_effect=_counting_factory):
        await gate.evaluate(PipelineContext(candidate=_make_candidate()))
        await gate.evaluate(PipelineContext(candidate=_make_candidate()))

    assert calls == 1


# --- MLPreFilter ---


@pytest.mark.asyncio
async def test_ml_prefilter_continues_above_threshold():
    stage = MLPreFilter()

    mock_scorer = MagicMock()
    mock_scorer.bypass_scoring = False
    mock_scorer.score_enriched = AsyncMock(return_value=0.8)

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            strike_price=200.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert ctx.ml_score == 0.8


@pytest.mark.asyncio
async def test_ml_prefilter_skips_below_threshold():
    stage = MLPreFilter()

    mock_scorer = MagicMock()
    mock_scorer.bypass_scoring = False
    mock_scorer.score_enriched = AsyncMock(return_value=0.3)

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        result = await stage.evaluate(ctx)

    assert result.action == "SKIP"
    assert "pre-filter" in result.reason


@pytest.mark.asyncio
async def test_ml_prefilter_bypasses_incomplete_context():
    """No premium/put_call → bypass ML check, CONTINUE."""
    stage = MLPreFilter()

    ctx = PipelineContext(candidate=_make_candidate(evidence={}))

    result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert "bypassed" in str(result.trace.get("ml_prefilter", ""))


@pytest.mark.asyncio
async def test_ml_prefilter_continues_on_scorer_error():
    """ML scorer failure → don't block, CONTINUE."""
    stage = MLPreFilter()

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", side_effect=RuntimeError("model load failed")):
        result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert "error" in str(result.trace)


@pytest.mark.asyncio
async def test_ml_prefilter_trace_reports_model_scoring_mode():
    """When the candidate's bucket has a trained model, the trace must say
    so explicitly — ``strategy_decisions.decision_trace_json`` needs to
    distinguish a real model score from the heuristic fallback, not just
    carry a bare number."""
    stage = MLPreFilter()

    mock_scorer = MagicMock()
    mock_scorer.bypass_scoring = False
    mock_scorer.use_heuristic = False
    mock_scorer.last_scoring_mode = "model"
    mock_scorer.score_enriched = AsyncMock(return_value=0.8)

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert result.trace.get("scoring_mode") == "model"


@pytest.mark.asyncio
async def test_ml_prefilter_trace_reports_heuristic_scoring_mode():
    """When no trained model loaded, the trace must say ``heuristic`` — this
    is the state that went invisible after PR #187 promoted sklearn's
    InconsistentVersionWarning to a load failure, silently switching every
    candidate onto the heuristic scorer with no signal in the decision trace."""
    stage = MLPreFilter()

    mock_scorer = MagicMock()
    mock_scorer.bypass_scoring = False
    mock_scorer.use_heuristic = True
    mock_scorer.last_scoring_mode = "heuristic"
    mock_scorer.score_enriched = AsyncMock(return_value=0.5)

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert result.trace.get("scoring_mode") == "heuristic"
    assert result.trace.get("threshold") == 0.40  # HEURISTIC_THRESHOLD


@pytest.mark.asyncio
async def test_ml_prefilter_skip_trace_also_reports_scoring_mode():
    """The SKIP branch's trace needs ``scoring_mode`` too — a rejected
    candidate is exactly the case an operator most wants to audit."""
    stage = MLPreFilter()

    mock_scorer = MagicMock()
    mock_scorer.bypass_scoring = False
    mock_scorer.use_heuristic = True
    mock_scorer.last_scoring_mode = "heuristic"
    mock_scorer.score_enriched = AsyncMock(return_value=0.1)

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        result = await stage.evaluate(ctx)

    assert result.action == "SKIP"
    assert result.trace.get("scoring_mode") == "heuristic"


@pytest.mark.asyncio
async def test_ml_prefilter_trace_reads_scorer_last_scoring_mode_not_global_use_heuristic():
    """Regression: models are bucket-specific and inference can fail per
    call, so a global ``use_heuristic is False`` (SOME bucket has a model,
    and no exception this run) does not mean THIS candidate was scored by a
    model. The trace must come from the scorer's actual per-call outcome
    (``last_scoring_mode``, set by score()/score_enriched() as its last
    step), not the global flag — using the flag here previously mislabeled
    a heuristically-scored candidate as ``model``."""
    stage = MLPreFilter()

    mock_scorer = MagicMock()
    mock_scorer.bypass_scoring = False
    mock_scorer.use_heuristic = False  # some OTHER bucket (e.g. SWING) has a model
    mock_scorer.last_scoring_mode = "heuristic"  # but THIS call fell back (bucket gap or exception)
    mock_scorer.score_enriched = AsyncMock(return_value=0.6)

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        result = await stage.evaluate(ctx)

    assert result.trace.get("scoring_mode") == "heuristic"


@pytest.mark.asyncio
async def test_ml_prefilter_applies_heuristic_threshold_to_heuristic_score_under_partial_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial bucket coverage: only SWING has a trained model, so the global
    ``use_heuristic`` flag reads False, but a SHORT_SWING candidate is still
    scored by the heuristic fallback. A heuristic score must be compared
    against the heuristic threshold (0.40), not the model-calibrated one —
    0.45 clears 0.40 and fails 0.5, so the two thresholds are distinguishable.
    """
    monkeypatch.setattr(
        "orion.processing.stages.ml_prefilter.system_settings.ml_prefilter_threshold", 0.5, raising=False
    )
    stage = MLPreFilter()

    mock_scorer = MagicMock()
    mock_scorer.bypass_scoring = False
    mock_scorer.use_heuristic = False  # SWING has a model, so the global flag says "model"
    mock_scorer.last_scoring_mode = "heuristic"  # ...but THIS candidate's bucket has none
    mock_scorer.score_enriched = AsyncMock(return_value=0.45)

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert result.trace.get("threshold") == 0.40


@pytest.mark.asyncio
async def test_ml_prefilter_keeps_model_threshold_for_model_scored_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The converse guard: a genuine model score is still held to the
    configured (stricter) threshold — the heuristic threshold must not leak
    onto model-scored candidates and loosen admission."""
    monkeypatch.setattr(
        "orion.processing.stages.ml_prefilter.system_settings.ml_prefilter_threshold", 0.5, raising=False
    )
    stage = MLPreFilter()

    mock_scorer = MagicMock()
    mock_scorer.bypass_scoring = False
    mock_scorer.use_heuristic = False
    mock_scorer.last_scoring_mode = "model"
    mock_scorer.score_enriched = AsyncMock(return_value=0.45)

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        result = await stage.evaluate(ctx)

    assert result.action == "SKIP"
    assert result.trace.get("threshold") == 0.5


def test_build_payload_dte_uses_calendar_days_not_wall_clock_hours():
    """DTE must be calendar-day arithmetic — ``expiration_date.date() -
    timestamp_utc.date()`` — matching ``count_open_journal_positions`` in
    ``orion/execution/persistence.py``. The old raw-datetime subtraction
    truncated a same-evening entry expiring at next midnight to 0 days
    (dte=0 → bucket 0DTE) even though it's a genuine 1-DTE SHORT_SWING: a
    Thursday 13:41 UTC candidate expiring Friday 00:00 UTC is 1 calendar
    day out, not 0."""
    ts = datetime(2026, 8, 13, 13, 41, tzinfo=UTC)
    expiry = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
    candidate = _make_candidate(timestamp_utc=ts, expiration_date=expiry)

    payload = MLPreFilter._build_payload(candidate)

    assert payload["dte"] == 1


# --- SolverEnsemble ---


@pytest.mark.asyncio
async def test_solver_ensemble_skips_when_router_empty():
    stage = SolverEnsemble()
    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=[])

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    assert result.action == "SKIP"
    assert "Fallback" in result.reason or "Router empty" in result.reason
    assert result.trace.get("fallback_triggered") is True


@pytest.mark.asyncio
async def test_solver_ensemble_skips_below_consensus():
    stage = SolverEnsemble()

    solver_obj = MagicMock()
    solver_obj.solver_id = "s1"
    solver_obj.info_ratio = 1.0
    solver_obj.oos_expect_bp = 50
    solver_obj.is_baseline = False
    solver_obj.config.rules = ["*"]
    solver_obj.config.exit_logic = None
    solver_obj.config.risk = None

    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=[solver_obj])
    stage.pipeline.execute = AsyncMock(return_value=(0.2, 1.0, {}))

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    assert result.action == "SKIP"
    assert "Rejected" in result.reason


@pytest.mark.asyncio
async def test_solver_ensemble_continues_above_consensus():
    stage = SolverEnsemble()

    solver_obj = MagicMock()
    solver_obj.solver_id = "s1"
    solver_obj.info_ratio = 2.0
    solver_obj.oos_expect_bp = 100
    solver_obj.is_baseline = False
    solver_obj.config.rules = ["*"]
    solver_obj.config.exit_logic = MagicMock()
    solver_obj.config.exit_logic.fixed_sl_pct = 0.03
    solver_obj.config.exit_logic.fixed_tp_pct = 0.06
    solver_obj.config.risk = MagicMock()
    solver_obj.config.risk.risk_per_trade_bps = 150

    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=[solver_obj])
    stage.pipeline.execute = AsyncMock(return_value=(0.85, 1.0, {"model": "v1"}))

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert ctx.consensus_score == 0.85
    assert ctx.primary_solver_id == "s1"
    assert ctx.stop_loss_pct == 0.03
    assert ctx.take_profit_pct == 0.06
    assert ctx.risk_per_trade_bps == 150.0


# --- Ensemble abstain semantics (FOLLOWUPS #1 — observed 2026-05-21) ---


def _mock_solver(*, solver_id: str, info_ratio: float = 1.0) -> MagicMock:
    s = MagicMock()
    s.solver_id = solver_id
    s.info_ratio = info_ratio
    s.oos_expect_bp = 50
    s.is_baseline = False
    s.config.rules = ["*"]
    s.config.exit_logic = MagicMock()
    s.config.exit_logic.fixed_sl_pct = 0.03
    s.config.exit_logic.fixed_tp_pct = 0.06
    s.config.risk = MagicMock()
    s.config.risk.risk_per_trade_bps = 150
    return s


@pytest.mark.asyncio
async def test_ensemble_treats_zero_p_take_as_abstention():
    """Three solvers; only one applies. The non-applicable solvers
    return p_take=0.0 by default (solver_executor.py:65 — initial
    value if predict_proba is not invoked). Pre-fix, the weighted
    formula counted those zeros into the denominator and EVERY
    flow-rule candidate was rejected at consensus≈0.25 even though
    the one applicable solver clearly said TAKE.

    With p_take=0.0 treated as abstention, only the active solver's
    vote contributes — consensus = 0.7 → CONTINUE.
    """
    stage = SolverEnsemble()

    s_active = _mock_solver(solver_id="bullish_sweep_paper_v1", info_ratio=1.5)
    s_abstain1 = _mock_solver(solver_id="swing_entry_paper_v1", info_ratio=1.4)
    s_abstain2 = _mock_solver(solver_id="bearish_put_paper_v1", info_ratio=1.3)

    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=[s_active, s_abstain1, s_abstain2])

    # Return different p_take per solver via side_effect
    stage.pipeline.execute = AsyncMock(
        side_effect=[
            (0.7, 1.0, {"model": "active"}),  # active solver
            (0.0, 1.0, {"model": "inactive1"}),  # abstain
            (0.0, 1.0, {"model": "inactive2"}),  # abstain
        ]
    )

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE", (
        "expected CONTINUE (consensus=0.7 from sole active solver), "
        "got SKIP — abstain logic broken; consensus would be 0.25"
    )
    # Consensus should be exactly the active solver's vote when others
    # abstain (1.5 * 0.7) / 1.5 = 0.7
    assert abs(ctx.consensus_score - 0.7) < 1e-9


@pytest.mark.asyncio
async def test_ensemble_skips_when_all_solvers_abstain():
    """If every solver votes p_take=0.0 (none apply), the ensemble
    should still SKIP — consensus is undefined / no positive signal."""
    stage = SolverEnsemble()

    solvers = [_mock_solver(solver_id=f"s{i}") for i in range(3)]
    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=solvers)
    stage.pipeline.execute = AsyncMock(side_effect=[(0.0, 1.0, {})] * 3)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    assert result.action == "SKIP"
    assert "Rejected" in result.reason or "abstain" in result.reason.lower()


@pytest.mark.asyncio
async def test_ensemble_keeps_low_but_nonzero_votes():
    """A solver that votes 0.05 (low but non-zero) is STILL voting,
    not abstaining. The fix must only exclude exact 0.0 (the default
    initialization). 0.7 + low NOs should still average below
    threshold and SKIP."""
    stage = SolverEnsemble()

    solvers = [
        _mock_solver(solver_id="s_yes", info_ratio=1.5),
        _mock_solver(solver_id="s_lownos1", info_ratio=1.4),
        _mock_solver(solver_id="s_lownos2", info_ratio=1.3),
    ]
    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=solvers)
    stage.pipeline.execute = AsyncMock(side_effect=[(0.7, 1.0, {}), (0.05, 1.0, {}), (0.05, 1.0, {})])

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    # consensus = (0.7*1.5 + 0.05*1.4 + 0.05*1.3) / (1.5+1.4+1.3)
    # = (1.05 + 0.07 + 0.065) / 4.2 ≈ 0.282 → SKIP (still below 0.5)
    assert result.action == "SKIP", "low-but-nonzero votes must count, not abstain"


@pytest.mark.asyncio
async def test_ensemble_counts_model_predicted_zero_as_vote_not_abstain():
    """Codex review 2026-05-21 Important #3: pre-fix, the ensemble
    treated ANY p_take==0.0 as abstention. That conflated two
    different signals — (a) solver_executor's rule-mismatch default
    (line 40-45 returns p_take=0.0 with trace.reason='Rule Mismatch'),
    and (b) a model that genuinely predicted 0.0 as a strong NO.

    The fix uses the trace's explicit `abstained` flag (set by
    solver_executor on rule mismatch) instead of `p_take > 0.0`.
    A model that returns 0.0 with no abstained flag is a real vote
    and contributes to the weighted denominator.

    Scenario: 1 active solver votes 0.7, 1 model genuinely predicts
    0.0 (strong NO with a real inference trace), 1 abstains (rule
    mismatch). Consensus should weight 0.7 against 0.0 and produce
    a low score (0.5 / 2.9 ≈ 0.36) → SKIP, NOT count only the 0.7.
    """
    stage = SolverEnsemble()
    s_yes = _mock_solver(solver_id="s_yes", info_ratio=1.5)
    s_strongno = _mock_solver(solver_id="s_strong_no", info_ratio=1.4)
    s_abstain = _mock_solver(solver_id="s_abstain", info_ratio=1.3)

    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=[s_yes, s_strongno, s_abstain])

    # Three traces with semantically different meanings:
    #   - real model vote (high) → trace from model inference
    #   - real model vote (zero) → trace from model inference, NO abstained flag
    #   - abstention → trace marked abstained=True
    stage.pipeline.execute = AsyncMock(
        side_effect=[
            (0.7, 1.0, {"stage": "model_inference_deterministic", "p_take_raw": 0.7}),
            (0.0, 1.0, {"stage": "model_inference_deterministic", "p_take_raw": 0.0}),
            (0.0, 1.0, {"reason": "Rule Mismatch", "abstained": True}),
        ]
    )

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    # Expected: weighted_vote = 0.7*1.5 + 0.0*1.4 = 1.05
    #           total_weight  = 1.5 + 1.4         = 2.9
    #           consensus     = 1.05 / 2.9        ≈ 0.362 → below 0.5 → SKIP
    assert result.action == "SKIP", (
        "strong-NO vote (0.0 with real model trace) must count in denominator; "
        "consensus should be ~0.36, NOT 0.7 (which would happen if the model "
        "vote were silently treated as abstention)"
    )
    # Verify via the details that the strong-NO solver is NOT marked abstained
    details = result.trace.get("ensemble_details", []) or result.trace.get("ensemble_solvers", [])
    strong_no_entry = next((d for d in details if d.get("solver_id") == "s_strong_no"), None)
    assert strong_no_entry is not None
    assert strong_no_entry.get("abstained") is False, (
        f"s_strong_no must be marked abstained=False (real model vote); got {strong_no_entry}"
    )
