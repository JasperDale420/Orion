from datetime import UTC, datetime, timedelta

import pytest

from orion.execution.signal_preflight import preflight_live_signal
from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateTrade, GoldTickerRollup, StrategyDecision


class StubRiskManager:
    def __init__(self, *, ok: bool = True):
        self._ok = ok

    def calculate_size(self, *, entry_price: float, stop_loss_pct=None, account_equity=None) -> float:
        return 1.0

    def check_order(
        self, ticker: str, quantity: float, price: float, side: str, timestamp=None, risk_override=None
    ) -> bool:
        return self._ok


@pytest.mark.asyncio
async def test_preflight_rejects_when_risk_manager_rejects(monkeypatch):
    from orion.config import system_settings

    system_settings.require_rollups_for_signals_live = False

    now = datetime.now(UTC).replace(microsecond=0)
    cand = CandidateTrade(
        candidate_id="cand_1",
        ticker="SPY",
        timestamp_utc=now - timedelta(seconds=1),
        rule_id="rule_bullish_sweep_v1",
        direction="LONG",
        confidence=0.7,
        source="UW",
        execution_params={"limit_price": 500.0},
        evidence={"rollup_ids": []},
    )
    decision = StrategyDecision(
        decision_id="dec_1",
        candidate_id="cand_1",
        timestamp_utc=now,
        ticker="SPY",
        strategy_version_id="baseline",
        model_version=None,
        decision="EXECUTE",
        reason="ok",
        executed_successfully="PENDING",
        execution_params={"limit_price": 500.0, "stop_loss_pct": 0.02},
        decision_trace_json={"expected_return_bp": 1.0, "risk_score": 0.1},
    )

    async with async_session_factory() as session:
        res = await preflight_live_signal(
            session,
            candidate=cand,
            decision=decision,
            risk_manager=StubRiskManager(ok=False),
            now_utc=now,
        )
        assert res.ok is False
        assert res.reason == "Risk Rejection"


@pytest.mark.asyncio
async def test_preflight_includes_rollup_snapshot(monkeypatch):
    from orion.config import system_settings

    system_settings.require_rollups_for_signals_live = True

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    rollup_ts_5m = now.replace(minute=(now.minute // 5) * 5)
    rollup_id_5m = f"SPY|5m|{rollup_ts_5m.isoformat()}"

    cand = CandidateTrade(
        candidate_id="cand_2",
        ticker="SPY",
        timestamp_utc=now,
        rule_id="rule_bullish_sweep_v1",
        direction="LONG",
        confidence=0.7,
        source="UW",
        execution_params={"limit_price": 500.0},
        evidence={"rollup_ids": [rollup_id_5m]},
    )
    decision = StrategyDecision(
        decision_id="dec_2",
        candidate_id="cand_2",
        timestamp_utc=now,
        ticker="SPY",
        strategy_version_id="baseline",
        model_version=None,
        decision="EXECUTE",
        reason="ok",
        executed_successfully="PENDING",
        execution_params={"limit_price": 500.0, "stop_loss_pct": 0.02},
        decision_trace_json={"expected_return_bp": 1.0, "risk_score": 0.1},
    )

    async with async_session_factory() as session:
        session.add(
            GoldTickerRollup(
                ticker="SPY",
                period="5m",
                timestamp_utc=rollup_ts_5m,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=100.0,
                vwap=1.4,
            )
        )
        await session.commit()

        res = await preflight_live_signal(
            session,
            candidate=cand,
            decision=decision,
            risk_manager=StubRiskManager(ok=True),
            now_utc=now,
        )
        assert res.ok is True
        assert "rollups" in res.extra
        assert "5m" in res.extra["rollups"]
        assert res.extra["rollups"]["5m"]["ticker"] == "SPY"
