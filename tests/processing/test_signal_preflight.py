from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.execution.signal_preflight import preflight_live_signal
from orion.jobs import bucket_halt
from orion.jobs.bucket_halt import record_halt, reset_halt_cache
from orion.storage.db import async_session_factory, init_db
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


# ── Per-bucket entry halt from the nightly measurement loop ──────────────


def _swing_candidate(now: datetime) -> CandidateTrade:
    """A 10-DTE candidate — bucket SWING under bucket_for_dte."""
    return CandidateTrade(
        candidate_id="cand_halt",
        ticker="SPY",
        timestamp_utc=now - timedelta(seconds=1),
        rule_id="rule_swing_v2",
        direction="LONG",
        confidence=0.7,
        source="UW",
        expiration_date=now + timedelta(days=10),
        execution_params={"limit_price": 5.0},
        evidence={"rollup_ids": []},
    )


def _execute_decision() -> StrategyDecision:
    return StrategyDecision(
        decision_id="dec_halt",
        candidate_id="cand_halt",
        timestamp_utc=datetime.now(UTC),
        ticker="SPY",
        strategy_version_id="baseline",
        model_version=None,
        decision="EXECUTE",
        reason="ok",
        executed_successfully="PENDING",
        execution_params={"limit_price": 5.0, "stop_loss_pct": 0.02},
        decision_trace_json={},
    )


async def _preflight_swing(now: datetime):
    from orion.config import system_settings

    system_settings.require_rollups_for_signals_live = False
    async with async_session_factory() as session:
        return await preflight_live_signal(
            session,
            candidate=_swing_candidate(now),
            decision=_execute_decision(),
            risk_manager=StubRiskManager(ok=True),
            now_utc=now,
        )


@pytest.mark.asyncio
async def test_preflight_skips_a_halted_bucket():
    await init_db()
    now = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)
    write = await record_halt("SWING", profit_factor=0.42, n_closed=63, now=now)

    res = await _preflight_swing(now)

    assert res.ok is False
    assert res.reason == (
        f"Bucket halted by measurement loop: SWING PF=0.42 n=63 until {write.halt.expires_after_session.isoformat()}"
    )
    assert res.extra["bucket"] == "SWING"


@pytest.mark.asyncio
async def test_preflight_passes_once_the_halt_has_expired():
    await init_db()
    halted_at = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)
    await record_halt("SWING", profit_factor=0.42, n_closed=63, now=halted_at)

    # 2026-08-31 is the first trading date past a 10-session window from 08-14.
    res = await _preflight_swing(datetime(2026, 8, 31, 14, 0, tzinfo=UTC))

    assert res.ok is True


@pytest.mark.asyncio
async def test_preflight_passes_when_another_bucket_is_halted():
    await init_db()
    now = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)
    await record_halt("0DTE", profit_factor=0.42, n_closed=63, now=now)

    res = await _preflight_swing(now)

    assert res.ok is True


@pytest.mark.asyncio
async def test_preflight_passes_when_the_halt_read_fails_and_warns():
    """A DB blip must never silently halt trading — the halt is an active
    verdict, not a kill switch."""
    await init_db()
    now = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)
    await record_halt("SWING", profit_factor=0.42, n_closed=63, now=now)
    reset_halt_cache()

    warned = MagicMock()
    with (
        patch.object(bucket_halt, "_load_halts", AsyncMock(side_effect=RuntimeError("db down"))),
        patch.object(bucket_halt.logger, "warning", warned),
    ):
        res = await _preflight_swing(now)

    assert res.ok is True
    warned.assert_called_once()
