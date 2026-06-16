"""
E2E Smoke Test: Prove the full Orion pipeline works against real TimescaleDB.

Injects simulated market data and verifies every pipeline stage:
  Bronze → Silver → Features → Rollups → Rules → Candidates → ML Score → Signal Engine → Execution

Requires: TimescaleDB running on localhost:5440

Run:
    ORION_RUN_LIVE_DB_SMOKE=1 uv run pytest tests/e2e/test_smoke_e2e.py -v -s
    uv run python tests/e2e/test_smoke_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import and_, delete, or_, select, text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REAL_DB_URL = "postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db"  # pragma: allowlist secret
REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO_ROOT / "models"
TICKER_PREFIX = "SM"
LIVE_DB_SMOKE_ENV = "ORION_RUN_LIVE_DB_SMOKE"
SMOKE_STATUS_KEYS = ("global_health", "GLOBAL_CIRCUIT_BREAKER")


def _run_tag() -> str:
    return f"smoke_{int(time.time())}"


def _live_db_smoke_enabled() -> bool:
    return os.getenv(LIVE_DB_SMOKE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _require_live_db_smoke_enabled() -> None:
    if _live_db_smoke_enabled():
        return
    raise SystemExit(f"Live DB smoke test is opt-in. Set {LIVE_DB_SMOKE_ENV}=1 to run it.")


def _ticker_for_run(run_tag: str) -> str:
    digits = "".join(ch for ch in run_tag if ch.isdigit())[-4:] or "0000"
    return f"{TICKER_PREFIX}{digits}"[:6]


def _smoke_solver_id(run_tag: str) -> str:
    return f"{run_tag}_solver"


def _smoke_order_id(run_tag: str) -> str:
    return f"{run_tag}_order_001"


def _smoke_broker_order_id(run_tag: str) -> str:
    return f"{run_tag}_smoke_order_001"


def _smoke_test_now() -> datetime:
    # Use a future timestamp to avoid colliding with live intraday rows.
    return (datetime.now(UTC) + timedelta(days=30)).replace(second=0, microsecond=0)


async def _snapshot_runtime_state(session) -> dict[str, object]:
    snapshot: dict[str, object] = {"system_status": {}, "risk_state": []}

    for key in SMOKE_STATUS_KEYS:
        row = (
            (
                await session.execute(
                    text("SELECT key, status, details, last_updated_utc FROM system_status WHERE key = :key"),
                    {"key": key},
                )
            )
            .mappings()
            .first()
        )
        snapshot["system_status"][key] = dict(row) if row else None

    risk_rows = (
        (
            await session.execute(
                text(
                    "SELECT id, updated_at_utc, current_daily_loss, current_equity, "
                    "starting_equity, peak_equity, open_positions_count FROM risk_state"
                )
            )
        )
        .mappings()
        .all()
    )
    snapshot["risk_state"] = [dict(row) for row in risk_rows]
    return snapshot


async def _restore_runtime_state(session, run_tag: str | None, snapshot: dict[str, object]) -> None:
    if run_tag:
        await session.execute(
            text("DELETE FROM orders WHERE broker_order_id IN (:order_id, :legacy_order_id)"),
            {
                "order_id": _smoke_order_id(run_tag),
                "legacy_order_id": _smoke_broker_order_id(run_tag),
            },
        )
        await session.execute(
            text("DELETE FROM fills WHERE broker_order_id IN (:order_id, :legacy_order_id)"),
            {
                "order_id": _smoke_order_id(run_tag),
                "legacy_order_id": _smoke_broker_order_id(run_tag),
            },
        )

    await session.execute(text("DELETE FROM risk_state"))
    for row in snapshot["risk_state"]:
        await session.execute(
            text(
                "INSERT INTO risk_state "
                "(id, updated_at_utc, current_daily_loss, current_equity, starting_equity, peak_equity, open_positions_count) "
                "VALUES (:id, :updated_at_utc, :current_daily_loss, :current_equity, :starting_equity, :peak_equity, :open_positions_count)"
            ),
            row,
        )

    for key in SMOKE_STATUS_KEYS:
        previous = snapshot["system_status"].get(key)
        if previous is None:
            await session.execute(text("DELETE FROM system_status WHERE key = :key"), {"key": key})
            continue

        await session.execute(
            text(
                "INSERT INTO system_status (key, status, details, last_updated_utc) "
                "VALUES (:key, :status, :details, :last_updated_utc) "
                "ON CONFLICT (key) DO UPDATE SET "
                "status = :status, details = :details, last_updated_utc = :last_updated_utc"
            ),
            previous,
        )

    await session.commit()


async def _snapshot_operational_state(session) -> dict[str, object]:
    return await _snapshot_runtime_state(session)


async def _restore_operational_state(session, snapshot: dict[str, object]) -> None:
    await _restore_runtime_state(session, None, snapshot)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_events(run_tag: str, now: datetime, ticker: str):
    """Create one ALPACA_BAR_1M and one UW_FLOW BronzeEvent."""
    from orion.storage.models import BronzeEvent

    bar_event = BronzeEvent(
        event_id=f"{run_tag}_bar_1",
        source="ALPACA",
        source_event_id=f"{run_tag}_alpaca_1",
        event_type="ALPACA_BAR_1M",
        ticker=ticker,
        trading_date=None,
        session=None,
        schema_version="v1",
        event_ts_utc=now,
        received_ts_utc=now,
        payload={
            "t": now.isoformat(),
            "o": 560.0,
            "h": 561.50,
            "l": 559.25,
            "c": 560.75,
            "v": 12000,
            "vw": 560.40,
            "symbol": ticker,
        },
        ingest={},
    )

    # Crafted to trigger BullishSweepRule:
    #   put_call=C, sweep=True, aggressor=ASK, premium>=10k, DTE in [7,30]
    expiry = now.date() + timedelta(days=10)
    flow_event = BronzeEvent(
        event_id=f"{run_tag}_flow_1",
        source="UW",
        source_event_id=f"{run_tag}_uw_1",
        event_type="UW_FLOW",
        ticker=ticker,
        trading_date=None,
        session=None,
        schema_version="v1",
        event_ts_utc=now,
        received_ts_utc=now,
        payload={
            "ticker": ticker,
            "timestamp": now.isoformat(),
            "put_call": "C",
            "expiry": expiry.isoformat(),
            "strike_price": 560.0,
            "price": 3.50,
            "size": 200,
            "bid": 3.30,
            "ask": 3.70,
            "underlying_price": 560.75,
            "aggressor": "ASK",
            "sweep": True,
            "trade_type": "SWEEP",
            "open_interest": 5000,
            "volume": 800,
            "premium": 50000.0,
            "multi_leg": False,
            "id": f"{run_tag}_flow_1",
        },
        ingest={},
    )

    return bar_event, flow_event


def _configure_settings(run_tag: str):
    """Override system/risk settings for permissive smoke testing."""
    from orion.config import risk_settings, system_settings

    system_settings.orion_stage = "paper"
    system_settings.baseline_solver_id = _smoke_solver_id(run_tag)
    system_settings.max_data_lag_seconds = 100_000
    system_settings.ml_prefilter_threshold = 0.0
    system_settings.ml_stale_model_policy = "warn"
    system_settings.require_rollups_for_signals_live = False

    risk_settings.max_order_size_usd = 1e9
    risk_settings.max_ticker_exposure_usd = 1e9
    risk_settings.max_positions = 100
    risk_settings.max_daily_loss = 1e9


def _override_model_dir():
    """Point MLScorer at host-side models/ dir and reload singleton."""
    import orion.ml.scorer as scorer_mod

    scorer_mod.MODEL_DIR = MODEL_DIR
    scorer_mod.reload_scorer()


async def _cleanup_smoke_run_rows(
    session,
    *,
    run_tag: str,
    ticker: str,
    solver_metric_ids: list[str] | None = None,
    bronze_event_ids: list[str] | None = None,
    silver_signal_ids: list[str] | None = None,
    candidate_ids: list[str] | None = None,
    rollup_keys: list[tuple[str, datetime]] | None = None,
) -> None:
    from orion.storage.models import BronzeEvent
    from orion.storage.models_execution import FillRecord, OrderRecord
    from orion.storage.models_gold import CandidateTrade, GoldTickerRollup, StrategyDecision
    from orion.storage.models_signals import SignalLive
    from orion.storage.models_silver import SilverSignal
    from orion.storage.models_solvers import Solver, SolverMetrics
    from orion.storage.models_trade_journal import TradeJournalEntry

    order_ids = [_smoke_order_id(run_tag), _smoke_broker_order_id(run_tag)]

    await session.execute(delete(OrderRecord).where(OrderRecord.broker_order_id.in_(order_ids)))
    await session.execute(delete(FillRecord).where(FillRecord.broker_order_id.in_(order_ids)))

    if candidate_ids:
        signal_ids = [f"sig_{candidate_id}" for candidate_id in candidate_ids]
        await session.execute(delete(StrategyDecision).where(StrategyDecision.candidate_id.in_(candidate_ids)))
        await session.execute(delete(SignalLive).where(SignalLive.signal_id.in_(signal_ids)))
        await session.execute(delete(TradeJournalEntry).where(TradeJournalEntry.candidate_id.in_(candidate_ids)))
        await session.execute(delete(CandidateTrade).where(CandidateTrade.candidate_id.in_(candidate_ids)))

    if rollup_keys:
        rollup_filters = [
            and_(
                GoldTickerRollup.ticker == ticker,
                GoldTickerRollup.period == period,
                GoldTickerRollup.timestamp_utc == ts,
            )
            for period, ts in rollup_keys
        ]
        await session.execute(delete(GoldTickerRollup).where(or_(*rollup_filters)))

    if silver_signal_ids:
        await session.execute(delete(SilverSignal).where(SilverSignal.signal_id.in_(silver_signal_ids)))

    if bronze_event_ids:
        await session.execute(delete(BronzeEvent).where(BronzeEvent.event_id.in_(bronze_event_ids)))

    if solver_metric_ids:
        await session.execute(delete(SolverMetrics).where(SolverMetrics.id.in_(solver_metric_ids)))

    await session.execute(delete(Solver).where(Solver.solver_id == _smoke_solver_id(run_tag)))


async def _execute_mock_smoke_order(*, run_tag: str, decision, candidate) -> None:
    from orion.execution.execution_engine import ExecutionEngine
    from orion.execution.risk.manager import RiskManager

    execution_engine = ExecutionEngine()
    execution_engine._ledger = None
    execution_engine._gateway_available = True
    execution_engine._gateway_check_ts = datetime.now(UTC)
    execution_engine._circuit_breaker_open = False
    execution_engine._check_system_health = AsyncMock(return_value=True)

    mock_client = AsyncMock()
    mock_client.get_clock.return_value = {"is_open": True}
    # The engine's price fetch matches on contract_symbol (not symbol) and
    # reads bid/ask (not mid) — same contract the dedicated integration test
    # exercises; a stale shape here aborts with options_price_fetch_failed.
    mock_client.get_option_chain.return_value = {
        "contracts": [{"contract_symbol": candidate.option_symbol, "bid": 3.30, "ask": 3.70}]
    }
    mock_client.create_order.return_value = {"id": _smoke_order_id(run_tag), "status": "accepted"}
    execution_engine._get_gateway_client = lambda: mock_client

    execution_engine.risk_manager = MagicMock(spec=RiskManager)
    execution_engine.risk_manager.check_order.return_value = True
    execution_engine.risk_manager.check_sector_exposure.return_value = True
    execution_engine.risk_manager.calculate_size.return_value = 5
    execution_engine.risk_manager.current_equity = 100000.0
    execution_engine.risk_manager.ticker_exposures = {}
    execution_engine.risk_manager.config = MagicMock()
    execution_engine.risk_manager.config.enable_shorting = True
    execution_engine.risk_manager.update_post_trade = AsyncMock()
    execution_engine.risk_manager.remove_pending_order = AsyncMock()

    # Two-phase persistence: persist_pending_order fires BEFORE the Gateway
    # round-trip, persist_order_finalize after. Asserting only the pending
    # call is enough for the smoke check — finalize is exercised by the
    # dedicated forensic regression tests.
    with patch("orion.execution.execution_engine.persist_pending_order", new=AsyncMock()) as persist_mock:
        await execution_engine.execute_order(decision, candidate)
        persist_mock.assert_awaited()

    mock_client.create_order.assert_called_once()


# ---------------------------------------------------------------------------
# Core smoke test logic
# ---------------------------------------------------------------------------


async def run_smoke_test() -> dict[str, bool]:
    """Run all 9 stages and return pass/fail per stage."""
    from orion.storage import db

    run_tag = _run_tag()
    ticker = _ticker_for_run(run_tag)
    results: dict[str, bool] = {}
    now = _smoke_test_now()
    bronze_event_ids = [f"{run_tag}_bar_1", f"{run_tag}_flow_1"]
    silver_signal_ids: list[str] = []
    candidate_ids: list[str] = []
    rollup_keys: list[tuple[str, datetime]] = []
    solver_metric_ids: list[str] = []

    # ── Setup ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  ORION E2E SMOKE TEST  (run_tag={run_tag}, ticker={ticker})")
    print(f"{'=' * 60}\n")

    # Connect to real DB
    db.configure_db(REAL_DB_URL, echo=False)
    try:
        await db.init_db()

        _configure_settings(run_tag)

        # Seed smoke solver
        from orion.storage.models_solvers import Solver, SolverMetrics

        solver_metric_id = str(uuid.uuid4())
        solver_metric_ids.append(solver_metric_id)

        async with db.async_session_factory() as session:
            session.add(
                Solver(
                    solver_id=_smoke_solver_id(run_tag),
                    family_name="SmokeTest",
                    name="SmokeTest",
                    version=1,
                    status="active",
                    stage="paper",
                    is_active=True,
                    created_by="smoke_test",
                    config={"version_id": _smoke_solver_id(run_tag)},
                    definition_json={"version_id": _smoke_solver_id(run_tag)},
                )
            )
            session.add(
                SolverMetrics(
                    id=solver_metric_id,
                    solver_id=_smoke_solver_id(run_tag),
                    sector="ALL",
                    dataset_tag="smoke",
                    num_runs=1,
                    num_trades=10,
                    sharpe_ratio=1.5,
                    info_ratio=1.5,
                    profit_factor=2.0,
                    max_dd_pct=5.0,
                    stability_score=0.8,
                    metrics_json={},
                    oos_expect_bp=15.0,
                )
            )
            await session.commit()

        bar_event, flow_event = _make_events(run_tag, now, ticker)
        unique = []
        ohlcv_signals = []
        uw_signals = []
        candidates = []
        decision = None
        fe = None

        # ── Stage 1: Bronze Ingest ─────────────────────────────────────────
        print("[Stage 1] Bronze Ingest...", end=" ")
        try:
            from orion.processing.ingest_pipeline import ingest_bronze_events
            from orion.processing.persistence import persist_bronze_events, persist_silver_from_bronze

            async with db.async_session_factory() as session:
                unique = await ingest_bronze_events(
                    session, [bar_event, flow_event], run_id=run_tag, trace_id=f"{run_tag}_trace"
                )
                await persist_bronze_events(session, unique)
                await persist_silver_from_bronze(session, unique)
                await session.commit()

            # Verify
            async with db.async_session_factory() as session:
                row = await session.execute(
                    text("SELECT count(*) FROM bronze_events WHERE event_id LIKE :prefix"),
                    {"prefix": f"{run_tag}%"},
                )
                bronze_count = row.scalar()

            assert bronze_count == 2, f"Expected 2 bronze events, got {bronze_count}"
            results["1_bronze_ingest"] = True
            print(f"PASS ({bronze_count} rows)")
        except Exception as e:
            results["1_bronze_ingest"] = False
            print(f"FAIL: {e}")

        # ── Stage 2: Silver Signals (OHLCV) ────────────────────────────────
        print("[Stage 2] Silver Signals (OHLCV)...", end=" ")
        try:
            from orion.processing.feature_engine import FeatureEngine
            from orion.processing.persistence import persist_silver_signals

            fe = FeatureEngine()
            alpaca_events = [e for e in unique if e.event_type == "ALPACA_BAR_1M"]
            ohlcv_signals = fe.process_alpaca_bars(alpaca_events)
            assert len(ohlcv_signals) > 0, "No OHLCV signals produced"

            silver_signal_ids = [s.signal_id for s in ohlcv_signals]

            async with db.async_session_factory() as session:
                await persist_silver_signals(session, ohlcv_signals)
                await session.commit()

            # Verify by querying the exact signal IDs we just wrote
            async with db.async_session_factory() as session:
                row = await session.execute(
                    text("SELECT count(*) FROM silver_signals WHERE signal_id = ANY(:ids)"),
                    {"ids": silver_signal_ids},
                )
                silver_count = row.scalar()

            assert silver_count > 0, f"No silver signals in DB (wrote {len(silver_signal_ids)} IDs)"
            results["2_silver_signals"] = True
            print(f"PASS ({len(ohlcv_signals)} signals, {silver_count} in DB)")
        except Exception as e:
            results["2_silver_signals"] = False
            print(f"FAIL: {e}")

        # ── Stage 3: Feature Extraction (UW Flow) ──────────────────────────
        print("[Stage 3] Feature Extraction (UW Flow)...", end=" ")
        try:
            assert fe is not None
            uw_events = [e for e in unique if e.event_type == "UW_FLOW"]
            uw_signals = fe.process_uw_flow_events(uw_events)
            assert len(uw_signals) > 0, "No UW flow signals produced"

            # Check feature quality
            feat = uw_signals[0].features
            assert feat.get("premium") or feat.get("premium_usd"), "Missing premium feature"
            assert feat.get("is_sweep") is True, f"Expected is_sweep=True, got {feat.get('is_sweep')}"

            results["3_feature_extraction"] = True
            print(f"PASS ({len(uw_signals)} signals, {len(feat)} features)")
        except Exception as e:
            results["3_feature_extraction"] = False
            print(f"FAIL: {e}")

        # ── Stage 4: Rollups ───────────────────────────────────────────────
        print("[Stage 4] Rollup Building...", end=" ")
        try:
            from orion.processing.rollup_builder import RollupBuilder
            from orion.storage.models_gold import GoldTickerRollup

            async with db.async_session_factory() as session:
                builder = RollupBuilder(session)
                await builder.build_rollups(
                    ticker=ticker,
                    start_time=now - timedelta(minutes=5),
                    end_time=now + timedelta(minutes=5),
                )

            async with db.async_session_factory() as session:
                rollups = (
                    (
                        await session.execute(
                            select(GoldTickerRollup).where(
                                GoldTickerRollup.ticker == ticker,
                                GoldTickerRollup.timestamp_utc >= now - timedelta(minutes=5),
                                GoldTickerRollup.timestamp_utc <= now + timedelta(minutes=5),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

            rollup_keys = [(row.period, row.timestamp_utc) for row in rollups]
            results["4_rollups"] = True
            print(f"PASS ({len(rollups)} rollup rows)")
        except Exception as e:
            results["4_rollups"] = False
            print(f"FAIL: {e}")

        # ── Stage 5: Rule Engine ───────────────────────────────────────────
        print("[Stage 5] Rule Engine...", end=" ")
        try:
            from orion.processing.rule_engine import RuleEngine

            re = RuleEngine()
            candidates = re.process_signals(uw_signals)
            assert len(candidates) > 0, "No candidates produced by rule engine"

            candidate_ids = [c.candidate_id for c in candidates]
            results["5_rule_engine"] = True
            rule_ids = [c.rule_id for c in candidates]
            opt_syms = [c.option_symbol for c in candidates]
            print(f"PASS ({len(candidates)} candidates: {rule_ids}, option_symbols={opt_syms})")
        except Exception as e:
            results["5_rule_engine"] = False
            print(f"FAIL: {e}")

        # ── Stage 6: Candidate Persistence ─────────────────────────────────
        print("[Stage 6] Candidate Persistence...", end=" ")
        try:
            from orion.processing.persistence import persist_candidates

            # Verify rule engine now populates option fields
            for c in candidates:
                assert c.option_symbol, "Rule engine should set option_symbol, got None"
                assert c.strike_price, "Rule engine should set strike_price, got None"
                assert c.option_type, "Rule engine should set option_type, got None"

            async with db.async_session_factory() as session:
                await persist_candidates(session, candidates)
                await session.commit()

            # Verify
            async with db.async_session_factory() as session:
                row = await session.execute(
                    text("SELECT count(*) FROM candidate_trades WHERE candidate_id = ANY(:ids)"),
                    {"ids": candidate_ids},
                )
                cand_count = row.scalar()

            assert cand_count == len(candidate_ids), "Not all candidates were persisted"
            results["6_candidate_persist"] = True
            print(f"PASS ({cand_count} rows in DB)")
        except Exception as e:
            results["6_candidate_persist"] = False
            print(f"FAIL: {e}")

        # ── Stage 7: ML Model Load + Score ─────────────────────────────────
        print("[Stage 7] ML Model Load + Score...", end=" ")
        try:
            _override_model_dir()
            from orion.ml.scorer import get_scorer

            scorer = get_scorer()
            loaded_buckets = list(scorer.models.keys())

            # models/*.pkl are untracked from git (they churn on retrain), so
            # a CI checkout has an empty models/ dir. When pickles exist on
            # disk (local runs) they MUST load; when absent, the scorer's
            # heuristic fallback must engage so the scoring path is still
            # exercised end-to-end.
            pkl_on_disk = any(MODEL_DIR.glob("*.pkl"))
            if pkl_on_disk:
                assert len(loaded_buckets) > 0, f"No models loaded from {MODEL_DIR}"
            else:
                assert scorer.use_heuristic, f"No models in {MODEL_DIR} and heuristic fallback not engaged"

            # Score a test flow
            test_flow = {
                "ticker": ticker,
                "premium_usd": 50000.0,
                "dte": 10,
                "put_call": "C",
                "strike": 560.0,
                "underlying_price": 560.75,
                "is_sweep": True,
                "aggressor": "ASK",
                "volume": 800,
                "open_interest": 5000,
            }
            score = scorer.score(test_flow)
            assert 0.0 <= score <= 1.0, f"Score {score} out of range"
            is_heuristic = scorer.use_heuristic

            results["7_ml_scoring"] = True
            print(f"PASS (models: {loaded_buckets}, score={score:.4f}, heuristic={is_heuristic})")
        except Exception as e:
            results["7_ml_scoring"] = False
            print(f"FAIL: {e}")

        # ── Stage 8: Signal Engine Decision ────────────────────────────────
        print("[Stage 8] Signal Engine Decision...", end=" ")
        try:
            from orion.processing.signal_engine import SignalEngine

            se = SignalEngine()
            decision = await se.decide(candidates[0])

            results["8_signal_engine"] = True
            trace = decision.decision_trace_json or {}
            ml_score = trace.get("ml_prefilter", {}).get("ml_score") or trace.get("ml_score")
            print(
                f"PASS (decision={decision.decision}, p_take={decision.p_take}, "
                f"solver={decision.strategy_version_id}, ml_score={ml_score})"
            )
        except Exception as e:
            results["8_signal_engine"] = False
            print(f"FAIL: {e}")

        # ── Stage 9: Execution (mock broker) ───────────────────────────────
        print("[Stage 9] Execution Engine (mock broker)...", end=" ")
        try:
            if decision and decision.decision == "EXECUTE":
                await _execute_mock_smoke_order(run_tag=run_tag, decision=decision, candidate=candidates[0])
                results["9_execution"] = True
                print(f"PASS (order submitted, status={decision.executed_successfully})")
            else:
                reason = decision.reason if decision else "no decision"
                results["9_execution"] = False
                print(f"SKIP (decision was not EXECUTE: {reason})")
        except Exception as e:
            results["9_execution"] = False
            print(f"FAIL: {e}")

        # ── Summary ────────────────────────────────────────────────────────
        print(f"\n{'=' * 60}")
        print("  RESULTS")
        print(f"{'=' * 60}")
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        for stage, ok in results.items():
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {stage}")
        print(f"\n  {passed}/{total} stages passed")
        print(f"{'=' * 60}\n")

        return results
    finally:
        print(f"\n{'─' * 60}")
        print("Cleaning up test data...", end=" ")
        try:
            async with db.async_session_factory() as session:
                await _cleanup_smoke_run_rows(
                    session,
                    run_tag=run_tag,
                    ticker=ticker,
                    solver_metric_ids=solver_metric_ids,
                    bronze_event_ids=bronze_event_ids,
                    silver_signal_ids=silver_signal_ids,
                    candidate_ids=candidate_ids,
                    rollup_keys=rollup_keys,
                )
                await session.commit()
            print("done")
        except Exception as e:
            print(f"cleanup error: {e}")


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


def test_live_db_smoke_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORION_RUN_LIVE_DB_SMOKE", raising=False)
    assert _live_db_smoke_enabled() is False
    with pytest.raises(SystemExit, match=LIVE_DB_SMOKE_ENV):
        _require_live_db_smoke_enabled()

    monkeypatch.setenv("ORION_RUN_LIVE_DB_SMOKE", "1")
    assert _live_db_smoke_enabled() is True
    _require_live_db_smoke_enabled()


@pytest.mark.asyncio
async def test_restore_operational_state_restores_system_status_and_risk_rows() -> None:
    from orion.storage.db import async_session_factory
    from orion.storage.models import SystemStatus
    from orion.storage.models_risk import RiskState

    original_ts = datetime(2026, 4, 2, 20, 0, tzinfo=UTC)

    async with async_session_factory() as session:
        session.add_all(
            [
                SystemStatus(
                    key="global_health",
                    status="HEALTHY",
                    details="original health",
                    last_updated_utc=original_ts,
                ),
                SystemStatus(
                    key="GLOBAL_CIRCUIT_BREAKER",
                    status="CLOSED",
                    details="original breaker",
                    last_updated_utc=original_ts,
                ),
                RiskState(
                    id="global_risk_v1",
                    updated_at_utc=original_ts,
                    current_daily_loss=11.0,
                    current_equity=100_000.0,
                    starting_equity=100_100.0,
                    peak_equity=100_200.0,
                    open_positions_count=2,
                ),
            ]
        )
        await session.commit()

        snapshot = await _snapshot_operational_state(session)

        health = await session.get(SystemStatus, "global_health")
        breaker = await session.get(SystemStatus, "GLOBAL_CIRCUIT_BREAKER")
        assert health is not None
        assert breaker is not None
        health.details = "smoke test dirty"
        breaker.details = "smoke test dirty"
        await session.execute(RiskState.__table__.delete())
        await session.commit()

        await _restore_operational_state(session, snapshot)
        await session.commit()

    async with async_session_factory() as session:
        restored_health = await session.get(SystemStatus, "global_health")
        restored_breaker = await session.get(SystemStatus, "GLOBAL_CIRCUIT_BREAKER")
        restored_risk = (
            (await session.execute(select(RiskState).where(RiskState.id == "global_risk_v1"))).scalars().first()
        )

    assert restored_health is not None
    assert restored_health.details == "original health"
    assert restored_breaker is not None
    assert restored_breaker.details == "original breaker"
    assert restored_risk is not None
    assert restored_risk.current_daily_loss == 11.0


# ---------------------------------------------------------------------------
# Pytest entry point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_smoke_e2e_real_db():
    """Full pipeline smoke test against real TimescaleDB."""
    from orion.storage import db

    if not _live_db_smoke_enabled():
        pytest.skip(f"Live DB smoke test is opt-in. Set {LIVE_DB_SMOKE_ENV}=1 to run it.")

    results = await run_smoke_test()

    # CRITICAL: Restore SQLite engine before conftest teardown runs,
    # otherwise the autouse setup_test_db fixture will drop_all on the
    # real Postgres DB.
    db.configure_db("sqlite+aiosqlite:///:memory:", echo=False)

    # Reset ML scorer singleton and model dir so subsequent tests get a clean scorer
    import orion.ml.scorer as scorer_mod

    scorer_mod._scorer = None
    scorer_mod.MODEL_DIR = Path("/app/models")  # restore default

    # Restore settings to test defaults
    from orion.config import system_settings

    system_settings.model_dir = Path("/app/models")
    system_settings.ml_stale_model_policy = "skip"
    system_settings.ml_prefilter_threshold = 0.5
    system_settings.orion_stage = "test"

    failed = [k for k, v in results.items() if not v]
    assert not failed, f"Smoke test stages failed: {failed}"


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _require_live_db_smoke_enabled()
    results = asyncio.run(run_smoke_test())
    failed = [k for k, v in results.items() if not v]
    sys.exit(1 if failed else 0)
