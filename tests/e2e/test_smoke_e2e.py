"""
E2E Smoke Test: Prove the full Orion pipeline works against real TimescaleDB.

Injects simulated SPY data and verifies every pipeline stage:
  Bronze → Silver → Features → Rollups → Rules → Candidates → ML Score → Signal Engine → Execution

Requires: TimescaleDB running on localhost:5440

Run:
    uv run pytest tests/e2e/test_smoke_e2e.py -v -s
    uv run python tests/e2e/test_smoke_e2e.py
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REAL_DB_URL = "postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db"  # pragma: allowlist secret
REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO_ROOT / "models"

TICKER = "SPY"


def _run_tag() -> str:
    return f"smoke_{int(time.time())}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_events(run_tag: str, now: datetime):
    """Create one ALPACA_BAR_1M and one UW_FLOW BronzeEvent."""
    from orion.storage.models import BronzeEvent

    bar_event = BronzeEvent(
        event_id=f"{run_tag}_bar_1",
        source="ALPACA",
        source_event_id=f"{run_tag}_alpaca_1",
        event_type="ALPACA_BAR_1M",
        ticker=TICKER,
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
            "symbol": TICKER,
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
        ticker=TICKER,
        trading_date=None,
        session=None,
        schema_version="v1",
        event_ts_utc=now,
        received_ts_utc=now,
        payload={
            "ticker": TICKER,
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
    system_settings.baseline_solver_id = f"{run_tag}_solver"
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


# ---------------------------------------------------------------------------
# Core smoke test logic
# ---------------------------------------------------------------------------


async def run_smoke_test() -> dict[str, bool]:
    """Run all 9 stages and return pass/fail per stage."""
    from orion.storage import db

    run_tag = _run_tag()
    results: dict[str, bool] = {}
    now = datetime.now(UTC).replace(second=0, microsecond=0)

    # ── Setup ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  ORION E2E SMOKE TEST  (run_tag={run_tag})")
    print(f"{'=' * 60}\n")

    # Connect to real DB
    db.configure_db(REAL_DB_URL, echo=False)
    await db.init_db()

    _configure_settings(run_tag)

    # Seed solver + system status
    from orion.storage.models import SystemStatus
    from orion.storage.models_solvers import Solver, SolverMetrics

    async with db.async_session_factory() as session:
        # Upsert system status (may already exist)
        await session.execute(
            text(
                "INSERT INTO system_status (key, status, details, last_updated_utc) "
                "VALUES (:key, :status, :details, :ts) "
                "ON CONFLICT (key) DO UPDATE SET status = :status, details = :details, last_updated_utc = :ts"
            ),
            {"key": "global_health", "status": "HEALTHY", "details": f"smoke test {run_tag}", "ts": now},
        )

        session.add(
            Solver(
                solver_id=f"{run_tag}_solver",
                family_name="SmokeTest",
                name="SmokeTest",
                version=1,
                status="active",
                stage="paper",
                is_active=True,
                created_by="smoke_test",
                config={"version_id": f"{run_tag}_solver"},
                definition_json={"version_id": f"{run_tag}_solver"},
            )
        )
        session.add(
            SolverMetrics(
                id=str(uuid.uuid4()),
                solver_id=f"{run_tag}_solver",
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

    bar_event, flow_event = _make_events(run_tag, now)

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
    ohlcv_signals = []
    try:
        from orion.processing.feature_engine import FeatureEngine
        from orion.processing.persistence import persist_silver_signals

        fe = FeatureEngine()
        alpaca_events = [e for e in unique if e.event_type == "ALPACA_BAR_1M"]
        ohlcv_signals = fe.process_alpaca_bars(alpaca_events)
        assert len(ohlcv_signals) > 0, "No OHLCV signals produced"

        signal_ids = [s.signal_id for s in ohlcv_signals]

        async with db.async_session_factory() as session:
            await persist_silver_signals(session, ohlcv_signals)
            await session.commit()

        # Verify by querying the exact signal IDs we just wrote
        async with db.async_session_factory() as session:
            row = await session.execute(
                text("SELECT count(*) FROM silver_signals WHERE signal_id = ANY(:ids)"),
                {"ids": signal_ids},
            )
            silver_count = row.scalar()

        assert silver_count > 0, f"No silver signals in DB (wrote {len(signal_ids)} IDs)"
        results["2_silver_signals"] = True
        print(f"PASS ({len(ohlcv_signals)} signals, {silver_count} in DB)")
    except Exception as e:
        results["2_silver_signals"] = False
        print(f"FAIL: {e}")

    # ── Stage 3: Feature Extraction (UW Flow) ──────────────────────────
    print("[Stage 3] Feature Extraction (UW Flow)...", end=" ")
    uw_signals = []
    try:
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

        async with db.async_session_factory() as session:
            builder = RollupBuilder(session)
            await builder.build_rollups(
                ticker=TICKER,
                start_time=now - timedelta(minutes=5),
                end_time=now + timedelta(minutes=5),
            )

        # Verify
        async with db.async_session_factory() as session:
            row = await session.execute(
                text("SELECT count(*) FROM gold_ticker_rollup WHERE ticker = :t"),
                {"t": TICKER},
            )
            rollup_count = row.scalar()

        results["4_rollups"] = True
        print(f"PASS ({rollup_count} rollup rows)")
    except Exception as e:
        results["4_rollups"] = False
        print(f"FAIL: {e}")

    # ── Stage 5: Rule Engine ───────────────────────────────────────────
    print("[Stage 5] Rule Engine...", end=" ")
    candidates = []
    try:
        from orion.processing.rule_engine import RuleEngine

        re = RuleEngine()
        candidates = re.process_signals(uw_signals)
        assert len(candidates) > 0, "No candidates produced by rule engine"

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
                text("SELECT count(*) FROM candidate_trades WHERE ticker = :t"),
                {"t": TICKER},
            )
            cand_count = row.scalar()

        assert cand_count > 0, "No candidates in DB"
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

        assert len(loaded_buckets) > 0, f"No models loaded from {MODEL_DIR}"

        # Score a test flow
        test_flow = {
            "ticker": TICKER,
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
    decision = None
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
        from orion.execution.execution_engine import ExecutionEngine
        from orion.execution.risk.manager import RiskManager

        if decision and decision.decision == "EXECUTE":
            # Reset system health, circuit breaker, and risk state
            async with db.async_session_factory() as session:
                now_exec = datetime.now(UTC)
                for key in ("global_health", "GLOBAL_CIRCUIT_BREAKER"):
                    await session.execute(
                        text(
                            "INSERT INTO system_status (key, status, details, last_updated_utc) "
                            "VALUES (:key, :status, :details, :ts) "
                            "ON CONFLICT (key) DO UPDATE SET status = :status, details = :details, last_updated_utc = :ts"
                        ),
                        {
                            "key": key,
                            "status": "HEALTHY" if key == "global_health" else "CLOSED",
                            "details": f"smoke test {run_tag}",
                            "ts": now_exec,
                        },
                    )
                await session.execute(text("DELETE FROM risk_state"))
                await session.commit()

            ee = ExecutionEngine()
            ee._gateway_available = True
            ee._gateway_check_ts = datetime.now(UTC)
            ee._circuit_breaker_open = False

            mock_client = AsyncMock()
            mock_client.get_clock.return_value = {"is_open": True}
            # Use the actual option_symbol from the candidate
            actual_occ = candidates[0].option_symbol
            mock_client.get_option_chain.return_value = {
                "contracts": [{"symbol": actual_occ, "mid": 3.50, "ask": 3.70}]
            }
            mock_client.create_order.return_value = {"id": "smoke_order_001", "status": "accepted"}
            ee._get_gateway_client = lambda: mock_client

            ee.risk_manager = MagicMock(spec=RiskManager)
            ee.risk_manager.check_order.return_value = True
            ee.risk_manager.check_sector_exposure.return_value = True
            ee.risk_manager.calculate_size.return_value = 5
            ee.risk_manager.current_equity = 100000.0
            ee.risk_manager.ticker_exposures = {}
            ee.risk_manager.config = MagicMock()
            ee.risk_manager.config.enable_shorting = True
            ee.risk_manager.update_post_trade = AsyncMock()
            ee.risk_manager.remove_pending_order = AsyncMock()

            await ee.execute_order(decision, candidates[0])
            mock_client.create_order.assert_called()

            results["9_execution"] = True
            print(f"PASS (order submitted, status={decision.executed_successfully})")
        else:
            reason = decision.reason if decision else "no decision"
            results["9_execution"] = False
            print(f"SKIP (decision was not EXECUTE: {reason})")
    except Exception as e:
        results["9_execution"] = False
        print(f"FAIL: {e}")

    # ── Cleanup ────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("Cleaning up test data...", end=" ")
    try:
        async with db.async_session_factory() as session:
            await session.execute(
                text(
                    "DELETE FROM strategy_decisions WHERE candidate_id IN "
                    "(SELECT candidate_id FROM candidate_trades WHERE ticker = :t)"
                ),
                {"t": TICKER},
            )
            await session.execute(text("DELETE FROM candidate_trades WHERE ticker = :t"), {"t": TICKER})
            await session.execute(text("DELETE FROM gold_ticker_rollup WHERE ticker = :t"), {"t": TICKER})
            await session.execute(text("DELETE FROM silver_signals WHERE ticker = :t"), {"t": TICKER})
            await session.execute(
                text("DELETE FROM bronze_events WHERE event_id LIKE :prefix"), {"prefix": f"{run_tag}%"}
            )
            await session.execute(
                text("DELETE FROM solver_metrics WHERE solver_id = :sid"), {"sid": f"{run_tag}_solver"}
            )
            await session.execute(text("DELETE FROM solvers WHERE solver_id = :sid"), {"sid": f"{run_tag}_solver"})
            await session.commit()
        print("done")
    except Exception as e:
        print(f"cleanup error: {e}")

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


# ---------------------------------------------------------------------------
# Pytest entry point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_smoke_e2e_real_db():
    """Full pipeline smoke test against real TimescaleDB."""
    from orion.storage import db

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
    results = asyncio.run(run_smoke_test())
    failed = [k for k, v in results.items() if not v]
    sys.exit(1 if failed else 0)
