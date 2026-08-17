"""Verify PositionMonitor enriches Gateway-loaded positions with entry context.

FOLLOWUPS #4: when a position is loaded from Alpaca via Gateway, the
PositionMonitor must join back to the originating Orion decision (via
orders.decision_id → strategy_decisions → candidate_trades) and populate
TrackedPosition with the entry-time market context (iv_rank, vix, gex,
market_tide, is_sweep, premium, dte, expiry_date). Without this, the
exit classifier sees None for all entry-context features.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from orion.execution.position_monitor import (
    PositionMonitor,
    _expiry_from_occ_symbol,
    _is_occ_option_symbol,
)
from orion.storage.db import async_session_factory
from orion.storage.models_execution import OrderRecord
from orion.storage.models_gold import CandidateTrade, StrategyDecision


@pytest.mark.unit
def test_is_occ_option_symbol_matches_real_contracts() -> None:
    assert _is_occ_option_symbol("QQQ260522P00721000")
    assert _is_occ_option_symbol("GDX260618C00086000")
    assert _is_occ_option_symbol("SPY240119C00500000")
    # Underlying ticker — not an OCC contract.
    assert not _is_occ_option_symbol("QQQ")
    assert not _is_occ_option_symbol("AAPL")
    # Malformed.
    assert not _is_occ_option_symbol("")
    assert not _is_occ_option_symbol("QQQ260522X00721000")  # X not C/P


@pytest.mark.asyncio
async def test_fetch_entry_context_populates_enrichment_fields_for_option_position() -> None:
    """Insert a fake Orion EXECUTE decision for an option contract, then verify
    PositionMonitor._fetch_entry_context joins it correctly and populates the
    enrichment fields (iv_rank, vix, gex, market_tide) from flow_enricher."""
    option_symbol = "AAPL260117C00200000"
    ticker = "AAPL"
    decision_ts = datetime.now(UTC) - timedelta(minutes=5)
    # 14 days after decision_ts so the Python-computed DTE lands at 14 → SWING bucket.
    expiration = decision_ts + timedelta(days=14)
    candidate_id = "test_candidate_" + uuid.uuid4().hex[:16]
    decision_id = "test_decision_" + uuid.uuid4().hex[:16]
    client_order_id = "orion_" + uuid.uuid4().hex

    async with async_session_factory() as session:
        session.add(
            CandidateTrade(
                candidate_id=candidate_id,
                ticker=ticker,
                timestamp_utc=decision_ts,
                rule_id="rule_bullish_sweep_v1",
                direction="LONG",
                confidence=0.7,
                source="UW",
                option_symbol=option_symbol,
                strike_price=200.0,
                expiration_date=expiration,
                option_type="C",
                underlying_price=195.0,
                premium=12500.0,
                evidence={
                    "premium": 12500.0,
                    "premium_usd": 12500.0,
                    "dte": 14,
                    "is_sweep": True,
                    "aggressor": "ASK",
                    "put_call": "C",
                    "event_id": "evt_test_123",
                    "event_ids": ["evt_test_123"],
                },
                execution_params={"limit_price": 1.25},
            )
        )
        session.add(
            StrategyDecision(
                decision_id=decision_id,
                candidate_id=candidate_id,
                timestamp_utc=decision_ts,
                ticker=ticker,
                strategy_version_id="test_v1",
                decision="EXECUTE",
                p_take=0.65,
                reason="test",
                executed_successfully="TRUE",
                decision_trace_json={"regime_size_multiplier": 1.0},
            )
        )
        session.add(
            OrderRecord(
                id=str(uuid.uuid4()),
                created_at_utc=decision_ts,
                decision_id=decision_id,
                candidate_id=candidate_id,
                ticker=ticker,
                side="buy",
                qty=1.0,
                limit_price=1.25,
                client_order_id=client_order_id,
                status="filled",
                system="orion",
                raw_json={},
            )
        )
        await session.commit()

    monitor = PositionMonitor()

    fake_enrichment = {
        "iv_rank_at_entry": 42.5,
        "vix_at_entry": 18.7,
        "gex_at_entry": 1.2e9,
        "market_tide_30m": -3.4e6,
        # Extra fields the enricher returns but we don't surface — should be ignored.
        "delta_at_entry": 0.45,
    }

    with patch(
        "orion.ml.flow_enricher.enrich_flow_for_scoring",
        new=AsyncMock(return_value=fake_enrichment),
    ) as mock_enrich:
        context = await monitor._fetch_entry_context(option_symbol)

    # DB-derived fields
    assert context["decision_id"] == decision_id
    assert context["option_symbol"] == option_symbol
    assert context["premium_usd"] == 12500.0
    assert context["dte"] == 14
    assert context["bucket"] == "SWING"  # dte=14 → SWING
    assert context["direction"] == "LONG"
    assert context["is_sweep"] is True
    # SQLite test backend may strip tzinfo on round-trip; compare the
    # naive components instead.
    assert context["expiry_date"] is not None
    assert context["expiry_date"].replace(tzinfo=None) == expiration.replace(tzinfo=None)
    assert context["entry_time"] is not None

    # Enricher-derived fields
    assert context["iv_rank_at_entry"] == 42.5
    assert context["vix_at_entry"] == 18.7
    assert context["gex_at_entry"] == 1.2e9
    assert context["market_tide_30m"] == -3.4e6

    # Enricher called with the expected derived args.
    assert mock_enrich.call_count == 1
    call_kwargs = mock_enrich.call_args.kwargs
    assert call_kwargs["ticker"] == ticker
    assert call_kwargs["put_call"] == "C"
    assert call_kwargs["dte"] == 14
    assert call_kwargs["premium_usd"] == 12500.0
    assert call_kwargs["event_id"] == "evt_test_123"
    assert call_kwargs["is_sweep"] is True
    assert call_kwargs["option_chain"] == option_symbol

    # Second call hits the in-memory cache — no extra DB or enricher work.
    with patch(
        "orion.ml.flow_enricher.enrich_flow_for_scoring",
        new=AsyncMock(return_value={"iv_rank_at_entry": 999.0}),
    ) as mock_enrich_2:
        cached = await monitor._fetch_entry_context(option_symbol)

    assert cached is context  # same dict instance
    assert mock_enrich_2.call_count == 0
    assert cached["iv_rank_at_entry"] == 42.5  # unchanged


@pytest.mark.asyncio
async def test_sync_positions_propagates_entry_context_to_tracked_position() -> None:
    """End-to-end: connector returns a position, PositionMonitor builds a
    TrackedPosition with all entry-context fields plumbed through."""
    option_symbol = "SPY260320P00450000"
    ticker = "SPY"
    decision_ts = datetime.now(UTC) - timedelta(minutes=10)
    # 30 days out → POSITION bucket.
    expiration = decision_ts + timedelta(days=30)
    candidate_id = "test_sync_cand_" + uuid.uuid4().hex[:16]
    decision_id = "test_sync_dec_" + uuid.uuid4().hex[:16]

    async with async_session_factory() as session:
        session.add(
            CandidateTrade(
                candidate_id=candidate_id,
                ticker=ticker,
                timestamp_utc=decision_ts,
                rule_id="rule_bearish_put_pressure_v1",
                direction="SHORT",
                confidence=0.65,
                source="UW",
                option_symbol=option_symbol,
                strike_price=450.0,
                expiration_date=expiration,
                option_type="P",
                underlying_price=460.0,
                premium=8400.0,
                evidence={
                    "premium_usd": 8400.0,
                    "is_sweep": False,
                    "put_call": "P",
                    "event_id": "evt_sync_456",
                },
                execution_params={"limit_price": 2.1},
            )
        )
        session.add(
            StrategyDecision(
                decision_id=decision_id,
                candidate_id=candidate_id,
                timestamp_utc=decision_ts,
                ticker=ticker,
                strategy_version_id="test_v1",
                decision="EXECUTE",
                p_take=0.6,
                reason="test sync",
                executed_successfully="TRUE",
                decision_trace_json={"regime_size_multiplier": 1.0},
            )
        )
        session.add(
            OrderRecord(
                id=str(uuid.uuid4()),
                created_at_utc=decision_ts,
                decision_id=decision_id,
                candidate_id=candidate_id,
                ticker=ticker,
                side="buy",
                qty=4.0,
                limit_price=2.1,
                client_order_id="orion_" + uuid.uuid4().hex,
                status="filled",
                system="orion",
                raw_json={},
            )
        )
        await session.commit()

    monitor = PositionMonitor()

    connector = SimpleNamespace(
        get_all_positions=lambda: [
            SimpleNamespace(
                symbol=option_symbol,
                current_price=2.5,
                avg_entry_price=2.1,
                qty=4.0,
                unrealized_plpc=0.19,
            )
        ]
    )

    fake_enrichment = {
        "iv_rank_at_entry": 67.3,
        "vix_at_entry": 22.4,
        "gex_at_entry": -8.5e8,
        "market_tide_30m": 5.5e6,
    }

    with patch(
        "orion.ml.flow_enricher.enrich_flow_for_scoring",
        new=AsyncMock(return_value=fake_enrichment),
    ):
        tracked = await monitor.sync_positions(connector)

    assert len(tracked) == 1
    pos = tracked[0]
    assert pos.symbol == option_symbol
    assert pos.decision_id == decision_id
    assert pos.option_symbol == option_symbol
    assert pos.premium_usd == 8400.0
    assert pos.is_sweep is False
    assert pos.expiry_date is not None
    assert pos.expiry_date.replace(tzinfo=None) == expiration.replace(tzinfo=None)
    assert pos.iv_rank_at_entry == 67.3
    assert pos.vix_at_entry == 22.4
    assert pos.gex_at_entry == -8.5e8
    assert pos.market_tide_30m == 5.5e6


@pytest.mark.asyncio
async def test_fetch_entry_context_returns_default_for_unknown_symbol() -> None:
    """Positions opened by other systems on the shared Alpaca account have no
    matching Orion decision row — fetch should fall back to a default derived
    from the OCC symbol's expiry (so bucket exits still arm correctly) and
    cache it so subsequent calls don't re-hit the DB."""
    from datetime import UTC, datetime, timedelta

    monitor = PositionMonitor()
    # Expiry far in the future → current DTE > 14 → POSITION bucket.
    far_expiry = (datetime.now(UTC) + timedelta(days=200)).strftime("%y%m%d")
    unknown = f"TSLA{far_expiry}C00300000"

    with patch(
        "orion.ml.flow_enricher.enrich_flow_for_scoring",
        new=AsyncMock(return_value={}),
    ) as mock_enrich:
        ctx_1 = await monitor._fetch_entry_context(unknown)
        ctx_2 = await monitor._fetch_entry_context(unknown)

    # Default fallback — no enricher call should happen because there's no
    # decision row to anchor the enrichment.
    assert ctx_1 == {"bucket": "POSITION"}
    assert ctx_2 is ctx_1
    assert mock_enrich.call_count == 0


@pytest.mark.asyncio
async def test_fetch_entry_context_swing_default_for_non_occ_symbol() -> None:
    """A non-OCC (equity) symbol has no embedded expiry — falls back to SWING."""
    monitor = PositionMonitor()

    with patch(
        "orion.ml.flow_enricher.enrich_flow_for_scoring",
        new=AsyncMock(return_value={}),
    ):
        ctx = await monitor._fetch_entry_context("TSLA")

    assert ctx == {"bucket": "SWING"}


def _one_position_connector(symbol: str, unrealized_plpc: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        get_all_positions=lambda: [
            SimpleNamespace(
                symbol=symbol,
                current_price=2.0 * (1 + unrealized_plpc),
                avg_entry_price=2.0,
                qty=1.0,
                unrealized_plpc=unrealized_plpc,
            )
        ]
    )


@pytest.mark.asyncio
async def test_entry_context_timeout_derives_bucket_from_occ_and_does_not_cache() -> None:
    """A timed-out entry-context fetch must not freeze the position as SWING.

    July 2026: 85 `entry_context_timeout` events, and every affected contract —
    0DTE included — ran SWING exit parameters for the life of the process
    because the timeout fallback was cached. The contract symbol carries the
    expiry, so the bucket and expiry are derivable without the database, and
    the real context must be retried on the next cycle rather than cached.
    """
    monitor = PositionMonitor()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01
    today = datetime.now(UTC).strftime("%y%m%d")
    symbol = f"SPY{today}C00500000"

    attempts: list[str] = []

    async def _hang(sym: str) -> dict:
        attempts.append(sym)
        await asyncio.sleep(5)
        return {}

    monitor._fetch_entry_context = _hang  # type: ignore[method-assign]

    tracked = await monitor.sync_positions(_one_position_connector(symbol))

    assert attempts == [symbol]
    assert len(tracked) == 1
    pos = tracked[0]
    assert pos.bucket == "0DTE"
    assert pos.expiry_date == _expiry_from_occ_symbol(symbol)
    assert symbol not in monitor._entry_context_cache, "timeout fallback must not be cached"

    # Next cycle re-attempts the fetch instead of reusing the fallback.
    await monitor.sync_positions(_one_position_connector(symbol))

    assert attempts == [symbol, symbol]


@pytest.mark.asyncio
async def test_entry_context_arriving_after_timeout_is_applied_to_tracked_position() -> None:
    """When the retried fetch succeeds, the already-tracked position picks up the
    real context (bucket, entry time, decision id) and keeps its running stats."""
    monitor = PositionMonitor()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01
    today = datetime.now(UTC).strftime("%y%m%d")
    symbol = f"SPY{today}C00500000"
    real_entry_ts = datetime.now(UTC) - timedelta(hours=3)
    real_context = {
        "decision_id": "dec-late",
        "option_symbol": symbol,
        "premium_usd": 1200.0,
        "dte": 5,
        "bucket": "SWING",
        "direction": "LONG",
        "entry_time": real_entry_ts,
        "expiry_date": None,
    }
    calls = 0

    async def _slow_then_ok(sym: str) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(5)
        monitor._entry_context_cache[sym] = real_context
        return real_context

    monitor._fetch_entry_context = _slow_then_ok  # type: ignore[method-assign]

    (pos,) = await monitor.sync_positions(_one_position_connector(symbol, unrealized_plpc=0.30))
    assert pos.bucket == "0DTE"
    assert pos.decision_id is None
    assert pos.max_return_pct == pytest.approx(30.0)

    (same_pos,) = await monitor.sync_positions(_one_position_connector(symbol, unrealized_plpc=0.10))

    assert same_pos is pos
    assert pos.bucket == "SWING"
    assert pos.decision_id == "dec-late"
    assert pos.entry_time == real_entry_ts
    assert pos.premium_usd == 1200.0
    assert pos.expiry_date == _expiry_from_occ_symbol(symbol)
    # Running envelope survives the late context.
    assert pos.max_return_pct == pytest.approx(30.0)
