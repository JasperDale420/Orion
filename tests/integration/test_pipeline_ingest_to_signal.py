import uuid
from datetime import UTC, datetime, timedelta

import pytest

from orion.config import risk_settings, system_settings
from orion.execution.risk.manager import RiskManager
from orion.execution.signal_preflight import preflight_live_signal
from orion.processing.feature_engine import FeatureEngine
from orion.processing.ingest_pipeline import ingest_bronze_events
from orion.processing.persistence import (
    persist_bronze_events,
    persist_candidates,
    persist_silver_from_bronze,
    persist_silver_signals,
)
from orion.processing.rollup_builder import RollupBuilder
from orion.processing.rule_engine import RuleEngine
from orion.processing.signal_engine import SignalEngine
from orion.storage.db import async_session_factory
from orion.storage.models import BronzeEvent
from orion.storage.models_signals import SignalLive
from orion.storage.models_solvers import Solver, SolverMetrics


@pytest.mark.slow
@pytest.mark.asyncio
async def test_pipeline_ingest_to_signal_live_row(monkeypatch: pytest.MonkeyPatch):
    # Make risk permissive so preflight + sizing doesn't reject in tests.
    monkeypatch.setattr(risk_settings, "max_order_size_usd", 1e9)
    monkeypatch.setattr(risk_settings, "max_ticker_exposure_usd", 1e9)
    monkeypatch.setattr(risk_settings, "max_positions", 50)
    monkeypatch.setattr(risk_settings, "max_daily_loss", 1e9)

    monkeypatch.setattr(system_settings, "orion_stage", "paper")
    monkeypatch.setattr(system_settings, "baseline_solver_id", "baseline_solver")
    monkeypatch.setattr(system_settings, "require_rollups_for_signals_live", True)
    monkeypatch.setattr(system_settings, "max_data_lag_seconds", 10_000)
    monkeypatch.setattr(system_settings, "ml_prefilter_threshold", 0.0)
    monkeypatch.setattr(system_settings, "ml_stale_model_policy", "warn")

    now = datetime.now(UTC).replace(second=0, microsecond=0)

    async with async_session_factory() as session:
        session.add(
            Solver(
                solver_id="baseline_solver",
                family_name="Baseline",
                name="Baseline",
                version=1,
                status="active",
                stage="paper",
                is_active=True,
                created_by="human",
                config={"version_id": "baseline_solver"},
                definition_json={"version_id": "baseline_solver"},
            )
        )
        session.add(
            SolverMetrics(
                id=str(uuid.uuid4()),
                solver_id="baseline_solver",
                sector="ALL",
                dataset_tag="test",
                num_runs=1,
                num_trades=1,
                sharpe_ratio=0.0,
                info_ratio=0.0,
                profit_factor=1.0,
                max_dd_pct=0.0,
                stability_score=0.0,
                metrics_json={},
                oos_expect_bp=10.0,
            )
        )
        await session.commit()

    # One Alpaca bar + one UW flow, same minute bucket.
    bar_raw = {
        "t": now.isoformat(),
        "o": 500.0,
        "h": 501.0,
        "l": 499.0,
        "c": 500.5,
        "v": 1000,
        "vw": 500.2,
        "symbol": "SPY",
    }
    flow_raw = {
        "ticker": "SPY",
        "timestamp": now.isoformat(),
        "put_call": "C",
        "expiry": (now.date() + timedelta(days=14)).isoformat(),
        "strike_price": 500.0,
        "price": 1.0,
        "size": 100,
        "bid": 0.9,
        "ask": 1.1,
        "underlying_price": 500.0,
        "aggressor": "ASK",
        "sweep": True,
        "trade_type": "SWEEP",
        "open_interest": 1000,
        "volume": 100,
        "premium": 100000.0,
        "multi_leg": False,
        "id": "flow_1",
    }

    events = [
        BronzeEvent(
            event_id="evt_bar_1",
            source="ALPACA",
            source_event_id="alpaca_1",
            event_type="ALPACA_BAR_1M",
            ticker="SPY",
            trading_date=None,
            session=None,
            schema_version="v1",
            event_ts_utc=now,
            received_ts_utc=now,
            payload=bar_raw,
            ingest={},
        ),
        BronzeEvent(
            event_id="evt_flow_1",
            source="UW",
            source_event_id="uw_1",
            event_type="UW_FLOW",
            ticker="SPY",
            trading_date=None,
            session=None,
            schema_version="v1",
            event_ts_utc=now,
            received_ts_utc=now,
            payload=flow_raw,
            ingest={},
        ),
    ]

    run_id = "test_run"
    trace_id = "test_trace"

    async with async_session_factory() as session:
        unique = await ingest_bronze_events(session, events, run_id=run_id, trace_id=trace_id)
        await persist_bronze_events(session, unique)
        await persist_silver_from_bronze(session, unique)
        await session.commit()

    # Build OHLCV signals + rollups
    fe = FeatureEngine()
    alpaca_events = [e for e in unique if e.event_type == "ALPACA_BAR_1M"]
    ohlcv_signals = fe.process_alpaca_bars(alpaca_events)
    assert ohlcv_signals

    async with async_session_factory() as session:
        await persist_silver_signals(session, ohlcv_signals)
        await session.commit()
        builder = RollupBuilder(session)
        await builder.build_rollups(
            ticker="SPY", start_time=now - timedelta(minutes=1), end_time=now + timedelta(minutes=1)
        )

    # Build UW flow signals -> candidates
    uw_signals = fe.process_uw_flow_events([e for e in unique if e.event_type == "UW_FLOW"])
    assert uw_signals

    re = RuleEngine()
    candidates = re.process_signals(uw_signals)
    assert candidates

    async with async_session_factory() as session:
        await persist_candidates(session, candidates)
        await session.commit()

    # Decide + preflight + persist signals_live
    se = SignalEngine()
    decision = await se.decide(candidates[0])
    assert decision.decision == "EXECUTE"

    async with async_session_factory() as session:
        pre = await preflight_live_signal(
            session, candidate=candidates[0], decision=decision, risk_manager=RiskManager()
        )
        assert pre.ok is True

        sig = SignalLive(
            signal_id=f"sig_{candidates[0].candidate_id}",
            timestamp_utc=now,
            ticker="SPY",
            direction=candidates[0].direction,
            rule_id=candidates[0].rule_id,
            model_version=decision.model_version,
            expected_return=float(decision.decision_trace_json.get("expected_return_bp")),
            p_take=float(decision.p_take),
            risk_score=float(decision.decision_trace_json.get("risk_score")),
            entry_logic={"order_type": "LIMIT", "time_in_force": "DAY", "limit_price": 500.0},
            exit_rules={"stop_loss_pct": 0.02, "take_profit_pct": 0.04},
            evidence=candidates[0].evidence or {},
            decision_trace_json={"preflight": pre.extra},
        )
        session.add(sig)
        await session.commit()

        fetched = await session.get(SignalLive, sig.signal_id)
        assert fetched is not None
        assert (fetched.evidence or {}).get("event_ids") == ["evt_flow_1"]
