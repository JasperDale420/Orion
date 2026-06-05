import asyncio
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from orion.config import risk_settings, system_settings
from orion.core.enums import DecisionStatus, OrderSide
from orion.execution.attribution import (
    ORDER_ID_PREFIX,
    is_occ_option_symbol,
    mint_orion_order_id,
    orion_order_id_sql_pattern,
)
from orion.execution.fill_processor import FillProcessor, maybe_snapshot_positions
from orion.execution.persistence import (
    persist_exit_decision,
    persist_exit_order_rejection,
    persist_order_finalize,
    persist_order_status_update,
    persist_pending_order,
)
from orion.execution.rate_limiter import get_order_rate_limiter
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import ensure_utc
from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateTrade, StrategyDecision

logger = setup_struct_logger(__name__)

# `ORDER_ID_PREFIX` is re-exported from orion.execution.attribution so
# existing imports (`from orion.execution.execution_engine import ORDER_ID_PREFIX`)
# continue to work. New code should import directly from `attribution`.
__all__ = ["ExecutionEngine", "ORDER_ID_PREFIX", "async_session_factory"]

# Per-service lease — a soft single-process guard backed by SystemStatus.
# Each service that creates an ExecutionEngine and wants single-instance
# enforcement calls `engine.acquire_service_lease("<service-id>")` before
# `engine.initialize()`. The actual implementation lives in
# `orion.core.service_lease`; these methods are thin wrappers that retain
# the engine instance state (`_lease_service_id`, `_lease_run_id`) so
# `renew_service_lease()` can be called with no arguments from the
# execution main loop. New code outside of ExecutionEngine should call
# the free functions in `orion.core.service_lease` directly.
#
# Constants are re-exported below so existing tests that import them
# from this module continue to work.
from orion.core.service_lease import (
    SERVICE_LEASE_KEY_PREFIX,
    SERVICE_LEASE_STALE_SECONDS,
    acquire_service_lease as _acquire_service_lease,
    renew_service_lease as _renew_service_lease,
)

__all__ += ["SERVICE_LEASE_KEY_PREFIX", "SERVICE_LEASE_STALE_SECONDS"]


def round_to_options_tick(price: float) -> float:
    """Round a price to Alpaca's options tick increment.

    Alpaca rejects options orders whose limit_price doesn't match the
    minimum tick:
      - price >= $3.00 -> $0.10 increments
      - price <  $3.00 -> $0.05 increments

    Sub-penny prices (e.g., a mid of $0.605, or float-precision artefacts
    like 5.789999999999999) produce 422 Unprocessable Entity at the
    broker, which we observed as `broker_order_id IS NULL` rows in the
    `orders` table. Rounding here keeps internal sizing math and the
    submitted price consistent (errors stay below one tick per contract).
    """
    if price <= 0:
        return 0.0
    tick = 0.10 if price >= 3.0 else 0.05
    return round(round(price / tick) * tick, 2)


def _extract_contract_greeks(contract: dict[str, Any]) -> dict[str, float] | None:
    """Pull per-contract (per-share) greeks off a Gateway chain contract.

    Returns delta/gamma/theta/vega, or None if delta/gamma/vega are missing or
    unparseable (theta is optional — tracking only). A real 0.0 is present data,
    not missing. Decimal/str/None are coerced the same way as bid/ask above.
    """

    def _coerce(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    delta = _coerce(contract.get("delta"))
    gamma = _coerce(contract.get("gamma"))
    vega = _coerce(contract.get("vega"))
    if delta is None or gamma is None or vega is None:
        return None
    theta = _coerce(contract.get("theta")) or 0.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def _project_position_greeks(contract_greeks: dict[str, float] | None, num_contracts: int) -> dict[str, float] | None:
    """Scale per-share contract greeks to share-equivalent position greeks:
    per-share greek × 100 shares/contract × num_contracts."""
    if contract_greeks is None:
        return None
    mult = 100.0 * num_contracts
    return {
        "delta": contract_greeks["delta"] * mult,
        "gamma": contract_greeks["gamma"] * mult,
        "theta": contract_greeks["theta"] * mult,
        "vega": contract_greeks["vega"] * mult,
    }


def _greeks_gate_blocks_on_missing(greeks_enabled: bool) -> bool:
    """Fail-safe policy when per-contract greeks are unavailable.

    Block only in real-money stages (anything other than paper/test) and only
    when greek checks are enabled. paper/test fail open (the caller logs a WARN
    and skips the greek gate). Operators can disable the gate entirely with
    ORION_RISK_ENABLE_GREEKS_CHECKS=False.
    """
    if not greeks_enabled:
        return False
    return system_settings.orion_stage.lower() not in ("paper", "test")


class ExecutionEngine:
    """
    Translates Agent decisions into broker orders.

    Trading is routed through the Data Gateway which proxies to Alpaca.
    Options-only: candidates without an option_symbol are rejected.

    Paper mode is controlled by the Data Gateway's Alpaca configuration.
    """

    def __init__(self) -> None:
        from orion.execution.risk.manager import RiskManager

        self.risk_manager = RiskManager()

        # Data Gateway trading client
        self._gateway_client = None
        self._gateway_available = False

        # Time-windowed broker-result history for the per-process circuit
        # breaker. Each entry is (monotonic_ts, success). Bounded by the
        # configured window, not by entry count — see _check_circuit_breaker
        # and _prune_order_history.
        self.order_history: deque[tuple[float, bool]] = deque()
        self.last_positions_snapshot_ts: datetime | None = None
        self._last_fill_poll_ts: datetime | None = None
        self._last_order_poll_ts: datetime | None = None
        self._last_position_sync_ts: datetime | None = None

        # TTL cache for _check_system_health (avoids N identical DB queries per cycle)
        self._health_cache: tuple[bool, float] | None = None
        self._health_cache_ttl: float = 10.0

        # Per-service single-instance lease (None until acquire_service_lease is called).
        self._lease_service_id: str | None = None
        self._lease_run_id: str | None = None

        # Empire-core ledger for EmpireUI trade recording
        self._ledger = None
        try:
            from empire_core.ledger import LedgerWriter
            from orion.core.ledger_adapter import OrionLedgerAdapter

            writer = LedgerWriter(db_path="ledger.db", system="orion")
            self._ledger = OrionLedgerAdapter(writer)
            logger.info("Ledger adapter initialized")
        except Exception as exc:
            logger.warning("Ledger adapter init failed (non-fatal)", error=str(exc))

        # Fill processor (handles partial fills, persistence)
        self._fill_processor = FillProcessor(ledger=self._ledger)

    # ── Service lease (single-process enforcement) ───────────────────────
    #
    # Thin wrappers over `orion.core.service_lease` that retain the lease
    # identity on the engine instance so `renew_service_lease()` can be
    # called argument-less from the main execution loop. New callers
    # outside ExecutionEngine should use the free functions directly.

    @staticmethod
    def _service_lease_key(service_id: str) -> str:
        return f"{SERVICE_LEASE_KEY_PREFIX}{service_id}"

    async def acquire_service_lease(self, service_id: str) -> None:
        """Acquire a single-instance lease for ``service_id``.

        Delegates to ``orion.core.service_lease.acquire_service_lease``
        and stashes the returned run_id on the instance so subsequent
        ``renew_service_lease()`` calls don't need it passed in.

        Raises RuntimeError if another fresh lease is owned by a
        different run_id; see the free function for the full semantics.
        """
        run_id = await _acquire_service_lease(service_id)
        self._lease_service_id = service_id
        self._lease_run_id = run_id

    async def renew_service_lease(self) -> None:
        """Refresh the lease's ``last_updated_utc`` so other processes treat it as live.

        No-op if ``acquire_service_lease`` was never called. Errors are
        logged but do not propagate — a transient DB blip should not
        abort the main execution loop.
        """
        if not self._lease_service_id or not self._lease_run_id:
            return
        await _renew_service_lease(self._lease_service_id, self._lease_run_id)

    def _get_gateway_client(self) -> Any:
        """Lazy-initialize Gateway trading client singleton."""
        if self._gateway_client is None:
            from orion.clients.gateway_trading_client import get_gateway_trading_client

            self._gateway_client = get_gateway_trading_client()
        return self._gateway_client

    # Close is a critical path — a transient Gateway blip must not abandon an
    # exit for a full monitor cycle. The close path retries a fresh
    # (cache-bypassing) availability probe a few times before giving up.
    _CLOSE_GATEWAY_RETRY_ATTEMPTS: int = 3
    _CLOSE_GATEWAY_RETRY_BACKOFF_SECONDS: float = 1.0

    async def _check_gateway_available(self, force: bool = False) -> bool:
        """Check if Data Gateway is reachable. Caches result for 60s unless
        `force` is set (the critical close path bypasses a possibly-stale
        cached blip)."""
        if not force and hasattr(self, "_gateway_check_ts") and self._gateway_check_ts:
            elapsed = (datetime.now(UTC) - self._gateway_check_ts).total_seconds()
            if elapsed < 60:
                return self._gateway_available

        try:
            client = self._get_gateway_client()
            result = await client.get_clock()
            self._gateway_available = "error" not in result
        except Exception:
            logger.error("Gateway availability check failed", exc_info=True)
            self._gateway_available = False

        self._gateway_check_ts = datetime.now(UTC)

        if not self._gateway_available:
            logger.warning(
                "Data Gateway unavailable; execution engine in degraded mode",
                extra={"event_type": "GATEWAY_UNAVAILABLE"},
            )
        else:
            logger.info(
                "Data Gateway connection verified",
                extra={"event_type": "GATEWAY_AVAILABLE"},
            )

        return self._gateway_available

    async def _gateway_available_for_close(self, ticker: str) -> bool:
        """Critical-path availability check for closes: retry a fresh probe
        with backoff before giving up so a transient blip doesn't abandon an
        exit for a full monitor cycle."""
        for attempt in range(1, self._CLOSE_GATEWAY_RETRY_ATTEMPTS + 1):
            if await self._check_gateway_available(force=True):
                return True
            if attempt < self._CLOSE_GATEWAY_RETRY_ATTEMPTS:
                logger.warning(
                    "close_gateway_unavailable_retrying",
                    extra={
                        "event_type": "CLOSE_GATEWAY_RETRY",
                        "ticker": ticker,
                        "attempt": attempt,
                        "max_attempts": self._CLOSE_GATEWAY_RETRY_ATTEMPTS,
                    },
                )
                await asyncio.sleep(self._CLOSE_GATEWAY_RETRY_BACKOFF_SECONDS)
        return False

    async def _live_position_qty(self, ticker: str) -> float | None:
        """Signed live broker qty for `ticker`: 0.0 if flat, None if it could
        not be determined (caller must fail safe and NOT submit a close —
        submitting against an unknown position risks opening a naked short)."""
        try:
            pos = await self._get_gateway_client().get_position(ticker)
        except Exception as e:
            logger.warning(
                f"Live position fetch failed for {ticker}: {e}",
                extra={"event_type": "LIVE_POSITION_FETCH_ERROR", "ticker": ticker, "error": str(e)},
            )
            return None
        if not isinstance(pos, dict):
            return None
        if "error" in pos:
            return 0.0  # broker reports no such position → flat, nothing to reduce
        raw = pos.get("qty")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    _ACCOUNT_CACHE_TTL_SECONDS = 30.0

    async def _get_cached_account(self) -> dict[str, Any]:
        """Account snapshot from the Gateway, cached for a short TTL so the
        per-order buying-power gate doesn't add a round-trip to every order."""
        now = time.monotonic()
        ts = getattr(self, "_account_cache_ts", None)
        cached = getattr(self, "_account_cache", None)
        if cached is not None and ts is not None and (now - ts) < self._ACCOUNT_CACHE_TTL_SECONDS:
            return cached
        acct = await self._get_gateway_client().get_account()
        self._account_cache = acct
        self._account_cache_ts = now
        return acct

    _DTBP_BACKOFF_SECONDS = 120.0

    def _in_dtbp_backoff(self) -> bool:
        """True while inside the cooldown set after a CONFIRMED broker DTBP
        rejection (40310000), so Orion stops submitting opening orders after the
        first confirmed wall instead of hammering — even when the proactive
        account check fails open on a degraded/malformed read (adversarial
        review)."""
        return time.monotonic() < getattr(self, "_dtbp_backoff_until", 0.0)

    def _note_dtbp_rejection(self) -> None:
        """Arm the DTBP backoff after a broker 40310000 rejection."""
        self._dtbp_backoff_until = time.monotonic() + self._DTBP_BACKOFF_SECONDS

    async def _has_daytrading_buying_power(self, estimated_cost: float) -> bool:
        """True unless we can positively read that the shared account's
        day-trading buying power can't cover ``estimated_cost``.

        Fails OPEN (returns True) on any read/parse problem — a missing field or
        a transient account-read error must never block trading. This only
        exists to stop Orion hammering the Gateway with opening orders Alpaca
        will reject 40310000 when the shared paper account's day-trading buying
        power is exhausted (2026-06-02: 193 rejected buys in one day).
        """
        try:
            acct = await self._get_cached_account()
        except Exception:
            return True
        if not isinstance(acct, dict) or "error" in acct:
            return True
        raw = acct.get("daytrading_buying_power")
        if raw is None:
            return True  # field absent → don't gate
        try:
            dtbp = float(raw)
        except (TypeError, ValueError):
            return True
        return dtbp >= estimated_cost

    async def initialize(self) -> None:
        """
        Loads the last 20 execution attempts and initializes RiskManager state.
        """
        if hasattr(self.risk_manager, "initialize"):
            await self.risk_manager.initialize()

        await self._sync_risk_from_gateway()

        # NOTE: We deliberately do NOT seed `order_history` from past
        # `strategy_decisions` rows. `executed_successfully=FALSE` in that
        # table covers every pre-flight rejection (DTE gate, data-lag gate,
        # health gate, risk rejection, shorting disabled, etc.) — not just
        # broker-submission failures, which is what `_check_circuit_breaker`
        # actually wants to monitor. Backfilling created a self-reinforcing
        # poison pill: yesterday's session had 104 EXECUTEs all rejected at
        # pre-flight (system-status STALE bug), which on restart filled the
        # 20-slot deque with [False]*20, immediately tripped the 3% error-
        # rate breaker, and prevented any new orders from being attempted —
        # which then prevented any True from landing to clear the history.
        # The runtime tracking at _record_result() (lines 601 / 639 / 784 /
        # 792) only logs *actual* broker round-trips, which is what the
        # breaker is meant to measure. Start the deque empty.
        logger.info(
            "ExecutionEngine initialized",
            extra={"event_type": "EXECUTION_INIT", "loaded_history_count": 0},
        )

    async def _fetch_orion_tickers(self) -> set[str] | None:
        """Return the set of broker symbols Orion has actually filled.

        Sourced from ``fills`` (not ``orders``) so that for options the
        set contains full OCC contract symbols, enabling per-contract
        attribution against the broker's position symbols. See
        ``position_monitor._fetch_orion_attributed_tickers`` for the
        full rationale (codex review 2026-05-26 CRITICAL on commit
        39174f8).
        """
        from orion.storage.models_execution import FillRecord

        async def query_tickers(session: Any) -> set[str]:
            stmt = (
                select(FillRecord.ticker)
                .where(FillRecord.client_order_id.like(orion_order_id_sql_pattern()))
                .distinct()
            )
            result = await session.execute(stmt)
            return {row[0] for row in result.all()}

        try:
            return await db_query(query_tickers)
        except Exception as exc:
            logger.error(
                "Failed to fetch Orion-owned tickers for shared-account position filtering",
                extra={"event_type": "ORION_TICKER_LOOKUP_FAILED", "error": str(exc)},
                exc_info=True,
            )
            return None

    async def _sync_risk_from_gateway(self) -> None:
        """Sync risk manager state from Data Gateway (account + positions).

        Only positions for tickers that Orion has traded are loaded into the
        risk manager.  The Alpaca paper account is shared by multiple systems
        via Data-Gateway, so we must filter to avoid counting other systems'
        positions in Orion's risk calculations.
        """
        # Prune stale pending orders (DB-only, gateway-independent). Expired
        # day orders never fire a fill, so they linger as phantom pending
        # exposure until restart unless swept at runtime (RCA 2026-06-05).
        prune = getattr(self.risk_manager, "prune_stale_pending_orders", None)
        if prune is not None:
            try:
                await prune()
            except Exception as e:
                logger.debug(f"pending order prune skipped: {e}")

        if not await self._check_gateway_available():
            logger.info(
                "Skipping Gateway risk sync; server unavailable. Using persisted risk state.",
                extra={"event_type": "GATEWAY_RISK_SYNC_SKIPPED"},
            )
            return

        try:
            client = self._get_gateway_client()

            account = await client.get_account()
            if "error" not in account:
                equity = float(account.get("equity", 0) or 0)
                float(account.get("last_equity", 0) or account.get("equity", 0) or 0)

                if equity > 0:
                    # The paper Alpaca account is shared with
                    # 3Roses/Cerberus/Kairos/Orbit/WhaleHunter, so the
                    # account-wide `equity` and `last_equity` include
                    # their P&L. Overwriting Orion-only running totals
                    # with that pool falsely trips Orion's drawdown /
                    # daily-loss kill switches when other systems lose.
                    #
                    # Seed-once pattern (mirrors current_daily_loss /
                    # peak_equity): take the first Gateway equity as the
                    # Orion baseline; afterwards `current_equity` only
                    # moves from Orion-attributed fills via
                    # `update_post_fill` (manager.py line 590), and
                    # `peak_equity` is bumped by
                    # `_evaluate_drawdown_kill_switch` when
                    # current_equity rises above it.
                    # Seed the Orion equity baseline ONCE, capped to the
                    # allocated slice (RiskManager.seed_equity_baseline). The
                    # shared account reports pooled equity across all systems;
                    # peak seeds to the same capped baseline so drawdown starts
                    # at 0% (using max(equity, last_equity) historically pulled
                    # a stale cross-system high and instantly tripped drawdown).
                    self.risk_manager.seed_equity_baseline(equity)

                    # Daily loss is Orion-attributed only: driven by update_post_fill
                    # from Orion-owned fills (client_order_id prefix "orion_"), not
                    # by account-wide equity delta. We DO NOT overwrite
                    # self.risk_manager.current_daily_loss here.

                    logger.info(
                        "Risk state synced from Gateway account",
                        extra={
                            "event_type": "GATEWAY_RISK_SYNC",
                            "equity": equity,
                            "peak_equity": self.risk_manager.peak_equity,
                            "daily_loss": self.risk_manager.current_daily_loss,
                        },
                    )

            # Filter positions to Orion-owned tickers only
            orion_tickers = await self._fetch_orion_tickers()
            if orion_tickers is None:
                logger.error(
                    "Skipping Gateway position sync because Orion-owned tickers could not be determined",
                    extra={"event_type": "GATEWAY_POSITIONS_SYNC_ABORTED"},
                )
                return

            positions = await client.get_positions()
            # CRITICAL: This block ALWAYS runs, including when `positions` is
            # an empty list. The previous `if positions:` guard meant that
            # when the broker returned 0 orion-attributable positions, the
            # risk_manager's `open_positions` count stayed at whatever was
            # loaded from `risk_state.open_positions_count` — observed live
            # on 2026-05-21 stuck at 5 ghost positions, causing every
            # EXECUTE to fail preflight with "RISK REJECT: Max Positions 5
            # Reached" despite the broker reporting zero. The risk
            # manager's view of "how many positions am I holding" MUST be
            # ground-truthed against the broker on every sync.
            self.risk_manager.positions = {}
            self.risk_manager.ticker_exposures = {}
            skipped = 0
            for p in positions:
                symbol = p.get("symbol", "")
                # Only load positions Orion has actually filled. Empty
                # orion_tickers means Orion owns no positions at all —
                # skip everything (None is the error sentinel, handled
                # above).
                #
                # `orion_tickers` is sourced from `fills.ticker`, which
                # stores the full OCC contract for options and the
                # underlying for equity — both match the broker's symbol
                # verbatim. Per-contract attribution, fixes codex review
                # 2026-05-26 CRITICAL on commit 39174f8 (underlying-only
                # matching let sibling systems' same-underlying options
                # leak into Orion's risk and exit pipelines).
                if symbol not in orion_tickers:
                    skipped += 1
                    continue

                qty = float(p.get("qty", 0) or 0)
                avg_entry = float(p.get("avg_entry_price", 0) or 0)
                market_value = float(p.get("market_value", 0) or 0)

                self.risk_manager.positions[symbol] = {"qty": qty, "avg_entry": avg_entry}
                self.risk_manager.ticker_exposures[symbol] = abs(market_value)

            self.risk_manager.open_positions = len(
                [p for p in self.risk_manager.positions.values() if abs(p["qty"]) > 1e-9]
            )
            logger.info(
                "Positions synced from Gateway (Orion-only)",
                extra={
                    "event_type": "GATEWAY_POSITIONS_SYNC",
                    "open_positions": self.risk_manager.open_positions,
                    "skipped_non_orion": skipped,
                    "total_account_positions": len(positions),
                },
            )

            if hasattr(self.risk_manager, "evaluate_drawdown_kill_switch"):
                await self.risk_manager.evaluate_drawdown_kill_switch()

        except Exception as e:
            logger.error(
                "Failed to sync risk state from Gateway",
                extra={"event_type": "GATEWAY_RISK_SYNC_ERROR", "error": str(e)},
                exc_info=True,
            )

    # ── Order execution ──────────────────────────────────────────────────

    async def execute_order(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        if not await self._check_gateway_available():
            logger.warning(
                "Data Gateway unavailable. Order logged but not submitted.",
                extra={
                    "event_type": "EXECUTION_NOOP",
                    "ticker": candidate.ticker,
                    "decision": decision.decision,
                },
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Data Gateway Unavailable"
            return

        action = decision.decision.upper()
        if action != "EXECUTE":
            logger.info("decision_skipped", ticker=candidate.ticker, action=action)
            return

        if isinstance(candidate.direction, str):
            candidate.direction = candidate.direction.upper()

        if not candidate.option_symbol:
            logger.warning(
                "execution_rejected_no_option_symbol",
                ticker=candidate.ticker,
                reason="Options only — no option_symbol on candidate",
            )
            decision.executed_successfully = DecisionStatus.SKIPPED
            decision.reason = "Options only"
            return

        await self._execute_options_order(decision, candidate)

    async def _remove_pending_order_compat(self, order_id: str) -> None:
        """Support both sync and async remove_pending_order implementations."""
        if not order_id or not hasattr(self.risk_manager, "remove_pending_order"):
            return
        maybe_result = self.risk_manager.remove_pending_order(order_id)
        if asyncio.iscoroutine(maybe_result):
            await maybe_result

    async def _execute_options_order(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        """Execute an options order via Data Gateway."""

        if not await self._pre_flight_checks(decision, candidate):
            return

        dte: int | None = None
        if candidate.expiration_date:
            now = datetime.now(UTC)
            dte = (candidate.expiration_date - now).days
            if dte < risk_settings.min_dte:
                logger.warning(
                    "options_blocked_dte_low", dte=dte, min_dte=risk_settings.min_dte, ticker=candidate.ticker
                )
                decision.executed_successfully = DecisionStatus.FALSE
                decision.reason = f"DTE Too Low ({dte} days)"
                return

        # Always fetch live option chain for current pricing — candidate.premium
        # is signal-time data and may be stale. Live mid/ask is needed for
        # accurate order sizing, risk checks, and limit price.
        option_price = None
        contract_greeks: dict[str, float] | None = None
        client = self._get_gateway_client()
        chain_result = await client.get_option_chain(candidate.ticker)

        if "error" not in chain_result and candidate.option_symbol:
            contracts = chain_result.get("contracts", [])
            for contract in contracts:
                # Gateway returns `contract_symbol`; `symbol` is for the underlying.
                if contract.get("contract_symbol") == candidate.option_symbol:
                    # Same chain response carries per-contract greeks — capture
                    # them here so the risk gate below has no extra round-trip.
                    contract_greeks = _extract_contract_greeks(contract)
                    bid = contract.get("bid")
                    ask = contract.get("ask")
                    try:
                        bid_f = float(bid) if bid not in (None, "") else 0.0
                        ask_f = float(ask) if ask not in (None, "") else 0.0
                    except (TypeError, ValueError):
                        bid_f = ask_f = 0.0
                    if bid_f > 0 and ask_f > 0:
                        option_price = (bid_f + ask_f) / 2
                    elif ask_f > 0:
                        option_price = ask_f
                    elif bid_f > 0:
                        option_price = bid_f
                    else:
                        last = contract.get("last")
                        try:
                            last_f = float(last) if last not in (None, "") else 0.0
                        except (TypeError, ValueError):
                            last_f = 0.0
                        if last_f > 0:
                            option_price = last_f
                    break

        # NOTE: `candidate.premium` is the UW-flow event's aggregate premium
        # (sum of all contracts in the sweep) — NOT a per-contract price. Using
        # it as option_price produced `options_calculated_0_contracts` because
        # risk_dollars / (34075 * 100) rounds to 0. We now fail closed instead.
        if not option_price or option_price <= 0:
            logger.error("options_price_fetch_failed", option_symbol=candidate.option_symbol, ticker=candidate.ticker)
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Option Price Fetch Failed"
            return

        # Snap the price to Alpaca's options tick increment. Prior to this,
        # mid-quotes like (bid 0.60 + ask 0.61) / 2 = 0.605 and float-
        # precision artefacts (5.789999999999999) were rejected at the
        # broker as `422 Unprocessable Entity`, which surfaced as orders
        # with status='' / broker_order_id IS NULL.
        option_price = round_to_options_tick(option_price)
        if option_price <= 0:
            logger.error(
                "options_price_rounded_to_zero",
                option_symbol=candidate.option_symbol,
                ticker=candidate.ticker,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Option Price Rounded To Zero"
            return

        # Solver-driven sizing: use risk_per_trade_bps × regime_size_multiplier
        # when available, with max_option_premium_pct as safety ceiling.
        ep = decision.execution_params or {}
        risk_bps = float(ep.get("risk_per_trade_bps", 0))
        regime_mult = float(ep.get("regime_size_multiplier", 1.0))
        max_premium = self.risk_manager.current_equity * risk_settings.max_option_premium_pct

        if risk_bps > 0:
            risk_dollars = (self.risk_manager.current_equity * risk_bps / 10000.0) * regime_mult
            risk_dollars = min(risk_dollars, max_premium)
        else:
            risk_dollars = max_premium

        num_contracts = max(0, int(risk_dollars / (option_price * 100)))

        if num_contracts <= 0:
            logger.warning(
                "options_calculated_0_contracts",
                option_symbol=candidate.option_symbol,
                ticker=candidate.ticker,
                option_price=option_price,
            )
            decision.executed_successfully = DecisionStatus.SKIPPED
            decision.reason = "Size 0 Contracts"
            return

        if dte is not None and dte == 0:
            allowed, reason = self.risk_manager.check_zero_dte_winddown(dte)
            if not allowed:
                logger.warning(
                    "options_blocked_zero_dte_winddown",
                    ticker=candidate.ticker,
                    option_symbol=candidate.option_symbol,
                    reason=reason,
                )
                decision.executed_successfully = DecisionStatus.FALSE
                decision.reason = f"0DTE Wind-Down: {reason}"
                return

            multiplier = self.risk_manager.get_zero_dte_size_multiplier(dte)
            if multiplier < 1.0:
                original_contracts = num_contracts
                num_contracts = max(1, int(num_contracts * multiplier))
                logger.info(
                    "zero_dte_size_reduced",
                    ticker=candidate.ticker,
                    original_contracts=original_contracts,
                    reduced_contracts=num_contracts,
                    multiplier=multiplier,
                )

        # Options-open is always BUY (calls on LONG bet, puts on SHORT bet);
        # SHORT does not mean shorting the contract. Exit uses the inverted
        # side in the position_monitor close path.
        side_value = OrderSide.BUY
        notional = option_price * 100

        # Greeks gate. Per-contract greeks → share-equivalent position greeks
        # (per-share × 100 × num_contracts). When available, enforce the
        # configured portfolio/position limits via check_options_order; when
        # unavailable, behavior is stage-gated (block in live, warn+skip in
        # paper/test) per _greeks_gate_blocks_on_missing.
        position_greeks = _project_position_greeks(contract_greeks, num_contracts)
        # Bind to the config the risk manager actually enforces greeks under so
        # the fail-safe decision matches check_greeks_limits' own toggle.
        greeks_enabled = getattr(
            getattr(self.risk_manager, "config", None), "enable_greeks_checks", risk_settings.enable_greeks_checks
        )

        if position_greeks is not None:
            risk_ok = self.risk_manager.check_options_order(
                candidate.ticker,
                num_contracts,
                notional,
                side_value,
                delta=position_greeks["delta"],
                gamma=position_greeks["gamma"],
                vega=position_greeks["vega"],
            )
        elif _greeks_gate_blocks_on_missing(greeks_enabled):
            logger.error(
                "options_blocked_greeks_unavailable",
                ticker=candidate.ticker,
                option_symbol=candidate.option_symbol,
                stage=system_settings.orion_stage,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Greeks Unavailable"
            return
        else:
            if greeks_enabled:
                logger.warning(
                    "greeks_unavailable_skipping_gate",
                    ticker=candidate.ticker,
                    option_symbol=candidate.option_symbol,
                    stage=system_settings.orion_stage,
                )
            risk_ok = self.risk_manager.check_order(candidate.ticker, num_contracts, notional, side_value)

        if not risk_ok:
            logger.error(
                "options_execution_blocked_by_risk",
                ticker=candidate.ticker,
                option_symbol=candidate.option_symbol,
                contracts=num_contracts,
                option_price=option_price,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Risk Rejection"
            return

        if self._check_circuit_breaker():
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "High Error Rate"
            return

        await self._submit_options_order(decision, candidate, num_contracts, option_price, position_greeks)

    async def _pre_flight_checks(self, decision: StrategyDecision, candidate: CandidateTrade) -> bool:
        """System Health, Data Lag, Shorting Checks"""
        if not await self._check_system_health():
            msg = "EXECUTION BLOCKED: System Status is UNHEALTHY."
            logger.critical(msg, extra={"event_type": "EXECUTION_BLOCKED", "ticker": candidate.ticker})
            decision.executed_successfully = DecisionStatus.FALSE
            decision.execution_log = msg
            return False

        # Options-open is always BUY (see _execute_options_candidate comment);
        # the shorting-disabled gate therefore never fires on the open path.
        # Leave it in place so anyone wiring a future equity short-sale flow
        # through this check gets blocked by default.
        side = OrderSide.BUY
        exposure = self.risk_manager.ticker_exposures.get(candidate.ticker, 0.0)
        is_short_opening = side == OrderSide.SELL and exposure <= 0

        if is_short_opening and not self.risk_manager.config.enable_shorting:
            logger.warning("Execution BLOCKED: Shorting is disabled")
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Shorting Disabled"
            return False

        now_utc = datetime.now(UTC)
        lag = (now_utc - ensure_utc(candidate.timestamp_utc)).total_seconds()

        if lag > system_settings.max_data_lag_seconds:
            logger.critical(
                "execution_blocked_data_lag",
                lag_seconds=lag,
                max_lag=system_settings.max_data_lag_seconds,
                ticker=candidate.ticker,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Data Lag"
            return False

        return True

    def _check_circuit_breaker(self) -> bool:
        """Trip when the broker-error rate exceeds threshold within the
        configured time window AND we've seen at least min_samples broker
        round-trips.

        Returns True iff trading should be blocked.
        """
        self._prune_order_history()
        total = len(self.order_history)
        min_samples = system_settings.circuit_breaker_min_samples
        if total < min_samples:
            # Avoid tripping on a single failure right after restart — the
            # deliberate "do not seed history" decision means the deque is
            # always empty post-restart, so the first ~min_samples broker
            # calls run without breaker pressure.
            return False
        failures = sum(1 for _, success in self.order_history if not success)
        rate = failures / total
        threshold = system_settings.circuit_breaker_error_rate
        if rate > threshold:
            if not system_settings.circuit_breaker_enabled:
                logger.warning(
                    "execution_circuit_breaker_would_trip_but_disabled",
                    error_rate=rate,
                    limit=threshold,
                    window_seconds=system_settings.circuit_breaker_window_seconds,
                    samples=total,
                    failures=failures,
                )
                return False
            logger.critical(
                "execution_blocked_error_rate",
                error_rate=rate,
                limit=threshold,
                window_seconds=system_settings.circuit_breaker_window_seconds,
                samples=total,
                failures=failures,
            )
            return True
        return False

    def _prune_order_history(self) -> None:
        """Drop history entries older than the configured window."""
        cutoff = time.monotonic() - system_settings.circuit_breaker_window_seconds
        while self.order_history and self.order_history[0][0] < cutoff:
            self.order_history.popleft()

    async def _submit_options_order(
        self,
        decision: Any,
        candidate: Any,
        num_contracts: int,
        option_price: float,
        position_greeks: dict[str, float] | None = None,
    ) -> None:
        """Submit an options order via Data Gateway."""
        logger.info(
            "options_execution_triggered",
            num_contracts=num_contracts,
            option_symbol=candidate.option_symbol,
            option_price=option_price,
            ticker=candidate.ticker,
        )

        client_order_id = mint_orion_order_id()
        decision.execution_params = decision.execution_params or {}
        decision.execution_params["client_order_id"] = client_order_id
        decision.execution_params["order_type"] = "OPTIONS"
        decision.execution_params["contracts"] = num_contracts

        # Pre-trade buying-power gate (RCA 2026-06-02). The shared Alpaca paper
        # account periodically exhausts day-trading buying power (Alpaca
        # 40310000) — mostly driven by sibling systems — after which every new
        # opening order is rejected. Orion contributed 193 rejected buys in a
        # single day by hammering straight through it. Back off early here, with
        # a clear reason, before reserving rate-limiter / risk / pending-order
        # state. Fails OPEN, so a transient account-read blip never blocks us.
        estimated_cost = num_contracts * option_price * 100.0
        if self._in_dtbp_backoff() or not await self._has_daytrading_buying_power(estimated_cost):
            logger.warning(
                "insufficient_daytrading_buying_power",
                ticker=candidate.ticker,
                option_symbol=candidate.option_symbol,
                estimated_cost=estimated_cost,
                backoff_active=self._in_dtbp_backoff(),
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Insufficient day-trading buying power (shared account)"
            return

        # Orion only opens options positions from candidates — it buys calls on
        # a LONG bet and buys puts on a SHORT bet. Both are BUYs at the broker.
        # The SHORT direction reflects a bearish view on the underlying, not a
        # short-sale of the contract. Exit/close flows (see position_monitor)
        # own the OrderSide.SELL path.
        side = OrderSide.BUY

        rate_limiter = get_order_rate_limiter()
        if not await rate_limiter.acquire(timeout=10.0):
            logger.warning(
                "rate_limit_reached",
                ticker=candidate.ticker,
                capacity=rate_limiter.available_capacity,
                max_capacity=rate_limiter.max_per_minute,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Rate limit exceeded"
            return

        if hasattr(self.risk_manager, "update_post_trade"):
            await self.risk_manager.update_post_trade(
                ticker=candidate.ticker,
                qty=num_contracts,
                price=option_price,
                side=side,
                order_id=client_order_id,
            )

        # Stash projected greeks so the fill that lands this order updates
        # portfolio greeks (set_intended_position_greeks → process_fill). Cleared
        # below if the submission fails (no fill will ever arrive).
        if position_greeks is not None and hasattr(self.risk_manager, "set_intended_position_greeks"):
            self.risk_manager.set_intended_position_greeks(
                candidate.ticker,
                delta=position_greeks["delta"],
                gamma=position_greeks["gamma"],
                theta=position_greeks["theta"],
                vega=position_greeks["vega"],
            )

        # Two-phase persistence: write the PENDING_SUBMIT tracking row BEFORE the
        # Gateway round-trip so a crash mid-call (SIGTERM, cancel, OOM) cannot
        # leave the broker with an order Orion's DB knows nothing about.
        # Forensic context: between 2026-05-12 and 2026-05-21, 37 broker positions
        # had ZERO matching `orders` rows because the original write happened
        # after `client.create_order()` returned and the docker_execution
        # container was crash-looping (380 restarts in 24h, lease-conflict).
        # The post-Gateway persist_order_finalize fills in broker_order_id + status.
        await persist_pending_order(
            decision=decision,
            candidate=candidate,
            client_order_id=client_order_id,
            side=side,
            qty=num_contracts,
            limit_price=option_price,
        )

        try:
            client = self._get_gateway_client()
            result = await client.create_order(
                symbol=candidate.option_symbol,
                qty=num_contracts,
                side=side,
                order_type="limit",
                limit_price=option_price,
                time_in_force="day",
                client_order_id=client_order_id,
            )

            if "error" in result:
                # Include the Gateway/Alpaca response body so the REJECTED
                # row records the real reason (e.g. 40310000 insufficient
                # day-trading buying power) instead of a bare "403 Forbidden".
                detail = result.get("detail")
                raise RuntimeError(
                    f"Gateway options order failed: {result['error']}" + (f" | {detail}" if detail else "")
                )

            await persist_order_finalize(
                client_order_id=client_order_id,
                broker_order=result,
                error_message=None,
            )

            # Record in empire-core ledger for EmpireUI
            if self._ledger is not None:
                try:
                    self._ledger.on_order_placed(
                        decision_id=str(getattr(decision, "decision_id", client_order_id)),
                        ticker=candidate.option_symbol,
                        side=side,
                        qty=num_contracts,
                        client_order_id=client_order_id,
                        limit_price=option_price,
                    )
                except Exception as exc:
                    logger.warning("ledger_order_write_failed", error=str(exc))

            premium_paid = num_contracts * option_price * 100
            logger.info(
                "options_execution_successful",
                client_order_id=client_order_id,
                premium_paid=premium_paid,
                option_symbol=candidate.option_symbol,
            )
            decision.executed_successfully = DecisionStatus.TRUE
            self._record_result(True)

            # Place bracket stop-loss / take-profit orders if enabled
            ep = decision.execution_params or {}
            if risk_settings.enable_bracket_orders:
                sl_pct = float(ep.get("stop_loss_pct", risk_settings.default_stop_loss_pct))
                tp_pct = float(ep.get("take_profit_pct", 0.50))
                bracket_result = await self._place_bracket_orders(
                    option_symbol=candidate.option_symbol,
                    qty=num_contracts,
                    entry_price=option_price,
                    stop_loss_pct=sl_pct,
                    take_profit_pct=tp_pct,
                    side=side,
                )
                ep["bracket_orders"] = bracket_result
                # Hoist protection state to top-level execution_params so a DB
                # query can find unprotected positions without parsing the
                # nested bracket_orders dict.
                if bracket_result.get("unprotected"):
                    ep["position_unprotected"] = True
                if bracket_result.get("partial_protection"):
                    ep["position_partial_protection"] = True

        except Exception as e:
            await self._remove_pending_order_compat(client_order_id)
            if hasattr(self.risk_manager, "clear_intended_position_greeks"):
                self.risk_manager.clear_intended_position_greeks(candidate.ticker)

            # Confirmed day-trading-buying-power wall → arm the backoff so we
            # stop submitting opening orders for a cooldown instead of flooding.
            if "40310000" in str(e):
                self._note_dtbp_rejection()

            # Finalize the PENDING_SUBMIT row to REJECTED in place; the row
            # already exists from persist_pending_order above.
            await persist_order_finalize(
                client_order_id=client_order_id,
                broker_order=None,
                error_message=str(e),
            )
            logger.error(
                "options_execution_failed",
                error=str(e),
                client_order_id=client_order_id,
                option_symbol=candidate.option_symbol,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = f"Options Broker Error: {e}"
            self._record_result(False)

    # ── Bracket orders (stop-loss / take-profit) ──────────────────────────

    async def _place_bracket_orders(
        self,
        option_symbol: str,
        qty: int,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        side: str,
    ) -> dict:
        """Place stop-loss and take-profit orders after a successful entry.

        Non-fatal at the broker level: bracket failures don't roll back the entry.
        But protection-state is tracked in the return dict so the caller can
        surface unprotected positions to operators (otherwise they're invisible
        outside the log stream).
        """
        exit_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        sl_price = round(entry_price * (1 - stop_loss_pct), 2)
        tp_price = round(entry_price * (1 + take_profit_pct), 2)
        sl_failure_reason: str | None = None
        tp_failure_reason: str | None = None
        result: dict = {"stop_loss": None, "take_profit": None}

        client = self._get_gateway_client()

        try:
            sl_order = await client.create_order(
                symbol=option_symbol,
                qty=qty,
                side=exit_side,
                order_type="stop",
                stop_price=sl_price,
                time_in_force="gtc",
            )
            result["stop_loss"] = {"order_id": sl_order.get("id"), "stop_price": sl_price}
            logger.info(
                "bracket_stop_loss_placed",
                option_symbol=option_symbol,
                stop_price=sl_price,
                order_id=sl_order.get("id"),
            )
        except Exception as e:
            sl_failure_reason = str(e)
            logger.error("bracket_stop_loss_failed", error=sl_failure_reason, option_symbol=option_symbol)

        try:
            tp_order = await client.create_order(
                symbol=option_symbol,
                qty=qty,
                side=exit_side,
                order_type="limit",
                limit_price=tp_price,
                time_in_force="gtc",
            )
            result["take_profit"] = {"order_id": tp_order.get("id"), "limit_price": tp_price}
            logger.info(
                "bracket_take_profit_placed",
                option_symbol=option_symbol,
                take_profit_price=tp_price,
                order_id=tp_order.get("id"),
            )
        except Exception as e:
            tp_failure_reason = str(e)
            logger.error("bracket_take_profit_failed", error=tp_failure_reason, option_symbol=option_symbol)

        # Surface protection state. `unprotected` means no automatic downside
        # exit was placed — the position depends entirely on PositionMonitor's
        # ML/rule exits to avoid full premium decay.
        sl_placed = result["stop_loss"] is not None
        tp_placed = result["take_profit"] is not None
        result["unprotected"] = not sl_placed
        result["partial_protection"] = sl_placed != tp_placed
        result["failure_reasons"] = [
            r
            for r in (
                sl_failure_reason and f"stop_loss: {sl_failure_reason}",
                tp_failure_reason and f"take_profit: {tp_failure_reason}",
            )
            if r
        ]

        if result["unprotected"]:
            logger.critical(
                "position_unprotected",
                event_type="POSITION_UNPROTECTED",
                option_symbol=option_symbol,
                qty=qty,
                entry_price=entry_price,
                stop_loss_failed=sl_failure_reason,
                take_profit_placed=tp_placed,
            )
        elif result["partial_protection"]:
            logger.warning(
                "position_partial_protection",
                event_type="POSITION_PARTIAL_PROTECTION",
                option_symbol=option_symbol,
                qty=qty,
                stop_loss_placed=sl_placed,
                take_profit_failed=tp_failure_reason,
            )

        return result

    async def close_position(
        self,
        ticker: str,
        qty: float,
        exit_signal: Any,
        direction: str = "LONG",
        use_market_order: bool = False,
        current_price: float | None = None,
    ) -> bool:
        """Close a position based on exit signal via Data Gateway.

        Options behavior (Phase 2 of exit-pipeline RCA):

        - Outside RTH (Alpaca 9:30-16:00 ET): logs and returns False
          WITHOUT submitting. Submitting an options order outside this
          window gets rejected with ``42210000``, producing noise and
          no fill. The PositionMonitor loop will re-attempt on the
          next iteration; when the market opens, the gate flips and
          the submit goes through.
        - Inside RTH: ALWAYS uses a marketable LIMIT order priced
          aggressively relative to ``current_price`` (passed by the
          caller from ``TrackedPosition.current_price``, the mark
          maintained by sync_positions). The Gateway does not expose a
          per-symbol option-quote endpoint, so we use the tracked mark
          rather than pulling a fresh quote. For LONG close (SELL),
          limit is 7.5% below mark; for SHORT close (BUY), 7.5% above.
          Both rounded to the options tick (`round_to_options_tick`).
        - Without ``current_price``: returns False with a log — does
          NOT fall back to a market order. Defensive: any options
          market order is going to be rejected by Alpaca anyway.

        Equity behavior unchanged: market for IMMEDIATE / use_market,
        limit otherwise. Equity has no RTH gate.
        """
        if not await self._gateway_available_for_close(ticker):
            logger.warning(
                "Data Gateway unavailable after retries. Cannot close position.",
                extra={"event_type": "CLOSE_POSITION_NOOP", "ticker": ticker},
            )
            return False

        if qty == 0:
            logger.warning(
                f"close_position called with qty=0 for {ticker}; nothing to close",
                extra={"event_type": "EXIT_ORDER_SKIPPED", "ticker": ticker, "qty": qty},
            )
            return False

        # Reduce-only safety: re-verify the LIVE broker position so a stale
        # tracked qty can't turn a "close" into an OPENING (naked) short. Only
        # ever reduce an existing position — never open or flip. The broker's
        # signed qty is authoritative for the close direction (overriding the
        # caller's `direction` hint, which can be stale or default "LONG" for
        # sibling-system positions on the shared account). 2026-05-29: the close
        # path oversold longs into naked short 0DTE puts (~3,235 rejected
        # orders/day) because it trusted the passed qty — see
        # test_close_reduce_only.
        broker_qty = await self._live_position_qty(ticker)
        if broker_qty is None:
            logger.warning(
                f"Skipping close for {ticker}: live position could not be verified",
                extra={"event_type": "EXIT_SKIPPED_UNVERIFIED", "ticker": ticker},
            )
            return False
        if abs(broker_qty) < 1e-9:
            logger.info(
                f"Skipping close for {ticker}: broker holds no position to reduce",
                extra={"event_type": "EXIT_SKIPPED_NO_POSITION", "ticker": ticker},
            )
            return False

        # `held_short` from the broker sign; cap the close to the held qty (and
        # to the caller's requested qty). Down-stream Gateway calls receive the
        # positive `abs_qty`.
        held_short = broker_qty < 0
        abs_qty = min(abs(qty), abs(broker_qty))
        if abs_qty < 1e-9:
            return False

        # Lazy-instantiate the market schedule the first time we need
        # it. Tests inject a stub via `engine._market_schedule = ...`
        # for hermetic control.
        from orion.core.market_schedule import MarketSchedule

        if not hasattr(self, "_market_schedule") or self._market_schedule is None:
            self._market_schedule = MarketSchedule()

        is_option = is_occ_option_symbol(ticker)

        # ── Options outside RTH: skip submission ─────────────────
        if is_option and not self._market_schedule.is_market_open_for_options():
            # DEBUG level so the every-5-second retry across ~50
            # positions doesn't flood WARN logs while market is closed.
            # Operator can still see the gating decision via
            # event_type filter, and the first attempt at the next
            # market open is logged separately when the submit happens.
            logger.debug(
                f"Skipping options close for {ticker}: market closed for options",
                extra={
                    "event_type": "EXIT_SKIPPED_MARKET_CLOSED",
                    "ticker": ticker,
                    "rule_id": getattr(exit_signal, "rule_id", None),
                },
            )
            return False

        # ── Options inside RTH: attributed LIMIT first, native escalation ──
        #
        # RCA 2026-06-05 + adversarial review: the close MUST stay
        # orion-attributed so its fill reaches RiskManager.process_fill and
        # feeds realized-PnL / daily-loss / the drawdown kill switch. The
        # Gateway's native DELETE /positions close carries NO orion_
        # client_order_id, so its fill is dropped by the orion_-prefix fill
        # filter — using it as the default would make the kill switch blind to
        # option-close PnL. So:
        #   1. cancel Orion's own resting orders on this symbol (avoid a
        #      self-wash against a filled/working entry, and a re-open after);
        #   2. submit an orion-attributed marketable LIMIT, floored strictly
        #      above our own avg entry so it can't self-wash (the MU bug was a
        #      stale mark putting the SELL below our $5.00 entry), with a
        #      42210000 retry that re-verifies the live position (reduce-only);
        #   3. only if the limit is rejected, ESCALATE to the native flatten as
        #      a last resort to avoid stranding the position — accepting the
        #      attribution gap only in that rare case.
        #
        # Close-side comes from `held_short` (broker signed qty), NOT the
        # caller's `direction` hint (Codex review 2026-05-21 Important #2).
        if is_option:
            client = self._get_gateway_client()
            close_side = OrderSide.BUY if held_short else OrderSide.SELL

            # Cancel our own resting orders first. If we cannot CONFIRM they're
            # cleared, defer this close (don't risk a wash/re-open) and retry
            # next cycle rather than escalating blind.
            if not await self._cancel_resting_orion_orders(ticker):
                logger.warning(
                    f"Deferring close for {ticker}: own resting orders not confirmed cancelled",
                    extra={"event_type": "EXIT_DEFERRED_RESTING_ORDERS", "ticker": ticker},
                )
                return False

            # No mark → can't price an attributed limit; escalate to native.
            if current_price is None or current_price <= 0:
                return await self._native_close_escalation(
                    client, ticker, abs_qty, close_side, exit_signal, "no mark for limit"
                )

            limit_price = self._compute_close_limit(current_price, held_short)
            if limit_price <= 0:
                return await self._native_close_escalation(
                    client,
                    ticker,
                    abs_qty,
                    close_side,
                    exit_signal,
                    f"limit priced <= 0 (mark={current_price})",
                )

            # 1) Primary: orion-attributed marketable LIMIT (keeps the fill
            #    orion-attributed → PnL / daily-loss / kill-switch accounting).
            client_order_id = mint_orion_order_id()
            result, client_order_id = await self._submit_close_limit(
                client=client,
                ticker=ticker,
                qty=abs_qty,
                close_side=close_side,
                limit_price=limit_price,
                client_order_id=client_order_id,
            )
            if "error" not in result:
                logger.info(
                    f"EXIT LIMIT ORDER (OPTION): {ticker} x{abs_qty} @ {limit_price} "
                    f"mark={current_price} - {getattr(exit_signal, 'reason', None)}",
                    extra={
                        "event_type": "EXIT_ORDER_SUBMITTED",
                        "ticker": ticker,
                        "qty": abs_qty,
                        "order_type": "LIMIT",
                        "limit_price": limit_price,
                        "mark_price": current_price,
                        "rule_id": getattr(exit_signal, "rule_id", None),
                        "reason": getattr(exit_signal, "reason", None),
                    },
                )
                await persist_exit_decision(ticker, exit_signal, client_order_id, result)
                self._record_result(True)
                return True

            # 2) Limit rejected → escalate to the native flatten (last resort).
            detail = str(result.get("detail") or result.get("error") or "")
            logger.warning(
                f"Limit close rejected for {ticker}, escalating to native flatten: {detail[:200]}",
                extra={"event_type": "EXIT_LIMIT_REJECTED_ESCALATE", "ticker": ticker, "error": detail[:200]},
            )
            return await self._native_close_escalation(
                client, ticker, abs_qty, close_side, exit_signal, f"limit rejected: {detail}"
            )

        # ── Equity: market for IMMEDIATE, limit otherwise ──
        #
        # Position side for equity comes from the qty SIGN (`held_short`),
        # not from the `direction` hint. Alpaca's market-close endpoint
        # accepts only positive qty and infers BUY-to-cover from the
        # existing short side, so the DELETE path just passes `abs_qty`.
        # The limit path mirrors that: BUY when held_short else SELL,
        # with the limit price crossing in the direction needed to fill
        # (above mark for BUY-to-cover, below for SELL-to-close).
        try:
            client = self._get_gateway_client()
            client_order_id = mint_orion_order_id()

            if use_market_order or exit_signal.urgency == "IMMEDIATE":
                result = await client.close_position(ticker, qty=abs_qty)

                if "error" in result:
                    raise RuntimeError(f"Gateway close_position failed: {result['error']}")

                logger.info(
                    f"EXIT MARKET ORDER: {ticker} x{abs_qty} - Reason: {exit_signal.reason}",
                    extra={
                        "event_type": "EXIT_ORDER_SUBMITTED",
                        "ticker": ticker,
                        "qty": abs_qty,
                        "order_type": "MARKET",
                        "rule_id": exit_signal.rule_id,
                        "reason": exit_signal.reason,
                    },
                )
            else:
                # Prefer caller-supplied current_price (mark from sync_positions);
                # fall back to a fresh snapshot only if the caller didn't supply.
                price = current_price
                if price is None or price <= 0:
                    snapshot = await client.get_stock_snapshot(ticker)
                    if "error" not in snapshot:
                        latest_trade = snapshot.get("latestTrade", {}) or snapshot.get("latest_trade", {})
                        if latest_trade:
                            price = float(latest_trade.get("p", 0) or latest_trade.get("price", 0) or 0)

                if price is None or price <= 0:
                    logger.error(f"Cannot close {ticker}: Failed to get current price")
                    return False

                exit_buffer_bps = 5
                if held_short:
                    # BUY-to-cover: pay slightly above mark to ensure fill.
                    limit_price = round(price * (1 + exit_buffer_bps / 10000.0), 2)
                    close_side = OrderSide.BUY
                else:
                    # SELL-to-close: hit slightly below mark.
                    limit_price = round(price * (1 - exit_buffer_bps / 10000.0), 2)
                    close_side = OrderSide.SELL

                result = await client.create_order(
                    symbol=ticker,
                    qty=abs_qty,
                    side=close_side,
                    order_type="limit",
                    limit_price=limit_price,
                    time_in_force="day",
                    client_order_id=client_order_id,
                    # Reduce-only intent: Alpaca will reject (not open a naked
                    # short) if no matching position exists — belt-and-suspenders
                    # alongside the live-position verification above.
                    position_intent=("buy_to_close" if close_side == OrderSide.BUY else "sell_to_close"),
                )

                if "error" in result:
                    raise RuntimeError(f"Gateway exit limit order failed: {result['error']}")

                logger.info(
                    f"EXIT LIMIT ORDER: {ticker} x{abs_qty} @ {limit_price} - Reason: {exit_signal.reason}",
                    extra={
                        "event_type": "EXIT_ORDER_SUBMITTED",
                        "ticker": ticker,
                        "qty": abs_qty,
                        "order_type": "LIMIT",
                        "limit_price": limit_price,
                        "rule_id": exit_signal.rule_id,
                        "reason": exit_signal.reason,
                    },
                )

            await persist_exit_decision(ticker, exit_signal, client_order_id, result)
            self._record_result(True)
            return True

        except Exception as e:
            logger.error(
                f"Failed to close position {ticker}: {e}",
                extra={"event_type": "EXIT_ORDER_FAILED", "ticker": ticker, "error": str(e)},
            )
            self._record_result(False)
            return False

    async def _cancel_resting_orion_orders(self, ticker: str) -> bool:
        """Cancel Orion's own OPEN orders on ``ticker`` before a close.

        Returns ``True`` only when it is SAFE to proceed — no Orion resting
        order remains that could self-wash the close or re-open exposure after
        it. Returns ``False`` if the open-order listing failed or ANY cancel was
        rejected, so the caller can defer and retry rather than close blind. The
        Gateway surfaces failures as ``{"error": ...}`` (not exceptions), so we
        inspect the cancel result rather than assuming success (adversarial
        review). Only Orion's own ``orion_``-prefixed orders are touched; on a
        successful cancel the order is dropped from risk pending exposure so it
        isn't double-counted until the hourly prune.
        """
        try:
            client = self._get_gateway_client()
            open_orders = await client.get_orders(status="open", limit=500)
        except Exception as e:
            logger.warning(
                f"Could not list open orders before closing {ticker}: {e}",
                extra={"event_type": "EXIT_CANCEL_RESTING_LIST_FAILED", "ticker": ticker},
            )
            return False
        if not isinstance(open_orders, list):
            return False

        safe = True
        for order in open_orders:
            if not isinstance(order, dict) or order.get("symbol") != ticker:
                continue
            coid = str(order.get("client_order_id") or "")
            if not coid.startswith(ORDER_ID_PREFIX):
                continue  # only Orion's own orders on the shared account
            order_id = order.get("id")
            if not order_id:
                continue
            result = await client.cancel_order(str(order_id))
            if isinstance(result, dict) and "error" in result:
                logger.warning(
                    f"Failed to cancel resting order {order_id} on {ticker}: "
                    f"{result.get('detail') or result.get('error')}",
                    extra={"event_type": "EXIT_CANCEL_RESTING_FAILED", "ticker": ticker, "order_id": str(order_id)},
                )
                safe = False
                continue
            # Cancel accepted → drop it from risk pending exposure.
            remove = getattr(self.risk_manager, "remove_pending_order", None)
            if remove is not None:
                try:
                    await remove(coid)
                except Exception:
                    pass
            logger.info(
                f"Cancelled resting Orion order {order_id} on {ticker} before close",
                extra={"event_type": "EXIT_CANCEL_RESTING", "ticker": ticker, "order_id": str(order_id)},
            )
        return safe

    def _compute_close_limit(self, mark: float, held_short: bool) -> float:
        """Marketable close limit: cross the spread by ~7.5% so the order
        actually fills. SELL-to-close hits the bid (below mark); BUY-to-cover
        lifts the offer (above mark). ``round_to_options_tick`` handles the
        $0.05/$0.10 increment requirement.

        Deliberately NOT floored against our own avg entry: an entry floor can
        push a loser's exit far from the market so Alpaca accepts a *resting*
        limit that never fills (adversarial review). Self-wash is handled
        instead by cancelling our own resting orders first, and by escalating to
        the native flatten if a residual wash rejects the limit.
        """
        buffer = 0.075
        if held_short:
            return round_to_options_tick(mark * (1 + buffer))  # BUY-to-cover lifts the offer
        return round_to_options_tick(mark * (1 - buffer))  # SELL-to-close hits the bid

    async def _submit_close_limit(
        self,
        *,
        client: Any,
        ticker: str,
        qty: float,
        close_side: Any,
        limit_price: float,
        client_order_id: str,
    ) -> tuple[dict[str, Any], str]:
        """Submit a single reduce-only close LIMIT with ``position_intent=
        *_to_close``.

        There is deliberately NO plain-limit retry. Alpaca rejects the forced
        intent with ``42210000`` when it infers the OPENING side, but retrying
        WITHOUT intent has an inherent reduce-only race: the live position can
        change between any pre-check and the ``create_order`` call, so a plain
        SELL/BUY with no broker-side reduce-only guard could open or flip
        exposure (adversarial review). Instead the rejection is returned to the
        caller, which escalates to the native flatten — reduce-only by
        construction and 404-safe.

        Returns ``(result, client_order_id)``.
        """
        intent = "buy_to_close" if close_side == OrderSide.BUY else "sell_to_close"
        result = await client.create_order(
            symbol=ticker,
            qty=qty,
            side=close_side,
            order_type="limit",
            limit_price=limit_price,
            time_in_force="day",
            client_order_id=client_order_id,
            position_intent=intent,
        )
        return result, client_order_id

    async def _native_close_escalation(
        self,
        client: Any,
        ticker: str,
        abs_qty: float,
        close_side: Any,
        exit_signal: Any,
        reason: str,
    ) -> bool:
        """Last-resort flatten via the Gateway native close (``DELETE
        /positions/{symbol}``), bounded to ``abs_qty``, used only when the
        attributed limit close can't be priced or is rejected.

        The native close fill is NOT orion-attributed (the endpoint takes no
        ``client_order_id``), so it does NOT feed ``RiskManager.process_fill`` —
        accepted only as an escape hatch to avoid stranding a position. A
        vanished position (404 / POSITION_NOT_FOUND / 40410000) counts as
        already-closed (do NOT submit an opposing order — that could open a
        naked short).
        """
        client_order_id = mint_orion_order_id()
        native = await client.close_position(ticker, qty=abs_qty)
        native_err = str(native.get("detail") or native.get("error") or "")
        if "error" not in native:
            logger.info(
                f"EXIT NATIVE CLOSE (escalation): {ticker} x{abs_qty} - {reason}",
                extra={
                    "event_type": "EXIT_ORDER_SUBMITTED",
                    "ticker": ticker,
                    "qty": abs_qty,
                    "order_type": "NATIVE_CLOSE",
                    "reason": getattr(exit_signal, "reason", None),
                },
            )
            await persist_exit_decision(ticker, exit_signal, client_order_id, native)
            self._record_result(True)
            return True
        if native.get("status_code") == 404 or "POSITION_NOT_FOUND" in native_err or "40410000" in native_err:
            logger.info(
                f"Close for {ticker}: broker reports no position (already closed)",
                extra={"event_type": "EXIT_SKIPPED_NO_POSITION", "ticker": ticker},
            )
            self._record_result(True)
            return True
        logger.error(
            f"Native close escalation failed for {ticker}: {native_err[:200]}",
            extra={"event_type": "EXIT_ORDER_FAILED", "ticker": ticker, "error": native_err[:200]},
        )
        await persist_exit_order_rejection(
            client_order_id=client_order_id,
            ticker=ticker,
            side=close_side,
            qty=abs_qty,
            limit_price=None,
            error_message=f"limit+native both failed ({reason}); native: {native_err}",
        )
        self._record_result(False)
        return False

    # ── System health ────────────────────────────────────────────────────

    async def _check_system_health(self) -> bool:
        """Queries SystemStatus table to ensure Global Health is OK and circuit breaker is not open.

        Results are cached for ``_health_cache_ttl`` seconds (default 10s) to
        avoid redundant DB queries when multiple candidates are evaluated in
        the same execution cycle.
        """
        if self._health_cache and (time.monotonic() - self._health_cache[1]) < self._health_cache_ttl:
            return self._health_cache[0]

        from orion.core.circuit_breaker import CircuitBreaker
        from orion.enrichment.heber_context import DEGRADED_DISCOVERY_KEY, DISCOVERY_STATUS_DEGRADED
        from orion.storage.models import SystemStatus

        try:

            async def fetch_health_records(session: Any) -> tuple[Any, Any, Any]:
                cb_stmt = select(SystemStatus).where(SystemStatus.key == CircuitBreaker.KEY)
                health_stmt = select(SystemStatus).where(SystemStatus.key == "global_health")
                discovery_stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
                cb_result = await session.execute(cb_stmt)
                health_result = await session.execute(health_stmt)
                discovery_result = await session.execute(discovery_stmt)
                return (
                    cb_result.scalars().first(),
                    health_result.scalars().first(),
                    discovery_result.scalars().first(),
                )

            cb_record, status_record, discovery_record = await db_query(fetch_health_records)

            if cb_record and cb_record.status == "OPEN":
                logger.critical(
                    "EXECUTION BLOCKED: Circuit breaker is OPEN",
                    extra={
                        "event_type": "HEALTH_CHECK_FAILED",
                        "reason": "Circuit Breaker Open",
                        "details": cb_record.details,
                    },
                )
                self._health_cache = (False, time.monotonic())
                return False

            # Discovery degradation: feature_enrichment writes DEGRADED when
            # ticker discovery has been falling back to the static SPY/QQQ/...
            # list past the warn-streak threshold. Block new trades — the
            # universe may be stale and emitting against the wrong tickers.
            if discovery_record and discovery_record.status == DISCOVERY_STATUS_DEGRADED:
                logger.critical(
                    "EXECUTION BLOCKED: Ticker discovery is DEGRADED",
                    extra={
                        "event_type": "HEALTH_CHECK_FAILED",
                        "reason": "Discovery Degraded",
                        "details": discovery_record.details,
                    },
                )
                self._health_cache = (False, time.monotonic())
                return False

            if not status_record:
                logger.error(
                    "System Health Record missing. Execution BLOCKED until health record is created.",
                    extra={"event_type": "HEALTH_CHECK_FAILED", "details": "Record Missing"},
                )
                self._health_cache = (False, time.monotonic())
                return False

            if status_record.status != "HEALTHY":
                logger.error(
                    f"System Health Check Failed: {status_record.status}",
                    extra={
                        "event_type": "HEALTH_CHECK_FAILED",
                        "status": status_record.status,
                        "details": status_record.details,
                    },
                )
                self._health_cache = (False, time.monotonic())
                return False

            now = datetime.now(UTC)
            if status_record.last_updated_utc:
                last_updated = ensure_utc(status_record.last_updated_utc)

                age = (now - last_updated).total_seconds()
                if age > system_settings.ingestion_heartbeat_max_age:
                    logger.error(
                        f"System Health Record STALE ({age:.1f}s). Ingestion likely dead.",
                        extra={"event_type": "HEALTH_CHECK_FAILED", "reason": "Stale", "age_seconds": age},
                    )
                    self._health_cache = (False, time.monotonic())
                    return False

            self._health_cache = (True, time.monotonic())
            return True
        except Exception as e:
            logger.error(
                f"Failed to check System Health: {e}",
                extra={"event_type": "HEALTH_CHECK_ERROR", "error_details": str(e)},
            )
            self._health_cache = (False, time.monotonic())
            return False

    # ── Fill polling (delegates to FillProcessor) ────────────────────────

    # Minimum interval between Gateway /alpaca/account polls. The execution
    # loop calls poll_fills every iteration (~1s when idle), but account
    # equity does not need second-by-second resolution. Throttling here
    # cuts ~3,600 redundant Gateway calls/hour during quiet periods.
    _ACCOUNT_POLL_MIN_INTERVAL_SECONDS: float = 15.0

    # Separate (faster) cadence for /alpaca/orders polling so fills land in
    # the local fills table before the position monitor evaluates exits.
    # Throttled vs the per-iteration loop to avoid spamming the Gateway.
    _ORDER_POLL_MIN_INTERVAL_SECONDS: float = 5.0

    # Cadence for re-grounding open_positions against the broker. Closes done
    # by the position monitor (direct to Gateway) and option expiries don't
    # flow back as Orion-attributed fills, so the in-memory count drifts high
    # over a long-running process — a periodic ground-truth resync corrects it.
    _POSITION_SYNC_MIN_INTERVAL_SECONDS: float = 120.0

    async def poll_fills(self) -> None:
        """Polls Data Gateway for account equity and updates RiskManager.

        Only account-level equity is synced (shared across all systems).
        Position-level data is filtered to Orion-only in _sync_risk_from_gateway.

        Also renews the service lease (no-op if `acquire_service_lease` was
        never called). Renewal is best-effort and never blocks fill polling.
        """
        await self.renew_service_lease()

        if not self._gateway_available:
            return

        client = self._get_gateway_client()

        now = datetime.now(UTC)
        account_poll_due = (
            self._last_fill_poll_ts is None
            or (now - self._last_fill_poll_ts).total_seconds() >= self._ACCOUNT_POLL_MIN_INTERVAL_SECONDS
        )

        if account_poll_due:
            try:
                account = await client.get_account()
                if "error" not in account:
                    equity = float(account.get("equity", 0) or 0)
                    if equity > 0:
                        # Same seed-once pattern as `_sync_risk_from_gateway`:
                        # account-wide equity includes other systems' P&L on
                        # the shared paper account. After the initial seed,
                        # current_equity moves only from Orion-attributed
                        # fills via update_post_fill — overwriting it here
                        # would falsely trip Orion's drawdown kill switch
                        # whenever 3Roses/Cerberus/Kairos/etc. take losses.
                        self.risk_manager.seed_equity_baseline(equity)

                self._last_fill_poll_ts = now
            except Exception as e:
                logger.warning(
                    "Fill polling via Gateway failed",
                    extra={"event_type": "FILL_POLL_ERROR", "error": str(e)},
                )

        # Order/fill poll — separately throttled from account-equity (5s vs 15s)
        # so fills land promptly without spamming /api/v1/alpaca/account.
        now2 = datetime.now(UTC)
        if (
            self._last_order_poll_ts is None
            or (now2 - self._last_order_poll_ts).total_seconds() >= self._ORDER_POLL_MIN_INTERVAL_SECONDS
        ):
            try:
                orders = await client.get_orders(status="all", limit=200)
            except Exception as e:
                logger.warning(
                    "Order poll via Gateway failed",
                    extra={"event_type": "ORDER_POLL_ERROR", "error": str(e)},
                )
                orders = []
                # Intentional: the throttle timestamp (set below) advances even on
                # failure to prevent a tight retry loop when Gateway is unhealthy.
                # Next attempt waits the full _ORDER_POLL_MIN_INTERVAL_SECONDS.
                # Cost: filled orders land up to 5s late after a transient
                # failure; acceptable vs spamming Gateway during a real outage.

            for order in orders:
                # Filter to orion-attributed orders only (shared account safety).
                coid = order.get("client_order_id", "") or ""
                if not coid.startswith(ORDER_ID_PREFIX):
                    continue

                # Update orders.status whenever the broker has a fresher state
                # (idempotent — UPDATE with the latest known values).
                broker_id = order.get("id") or order.get("broker_order_id")
                status = order.get("status")
                if broker_id and status:
                    try:
                        await persist_order_status_update(
                            broker_order_id=str(broker_id),
                            status=str(status),
                            filled_qty=order.get("filled_qty"),
                            filled_avg_price=order.get("filled_avg_price"),
                        )
                    except Exception as e:
                        logger.warning(
                            "Order status update failed",
                            extra={
                                "event_type": "ORDER_STATUS_UPDATE_ERROR",
                                "broker_order_id": broker_id,
                                "error": str(e),
                            },
                        )

                # If the order is filled (or partially), feed it through the fill
                # processor — ProcessedFill table guards against double-processing.
                filled_qty = float(order.get("filled_qty") or 0)
                if filled_qty > 0:
                    try:
                        await self._process_single_fill(order)
                    except Exception as e:
                        logger.warning(
                            "Fill processing failed for order",
                            extra={
                                "event_type": "FILL_PROCESS_ERROR",
                                "broker_order_id": broker_id,
                                "error": str(e),
                            },
                        )

            self._last_order_poll_ts = now2

        # Periodically re-ground open_positions against the broker. Closes done
        # by the position monitor (direct to Gateway) and option expiries don't
        # flow back as Orion-attributed fills, so the in-memory count drifts
        # high over a long-running process (2026-05-29: 15 vs broker 12) and
        # would keep entries blocked at the max_positions gate even after real
        # positions fall below the limit. Interval-gated so we don't hit the
        # Gateway every ~1s idle iteration.
        now3 = datetime.now(UTC)
        if (
            self._last_position_sync_ts is None
            or (now3 - self._last_position_sync_ts).total_seconds() >= self._POSITION_SYNC_MIN_INTERVAL_SECONDS
        ):
            self._last_position_sync_ts = now3
            try:
                await self._sync_risk_from_gateway()
            except Exception as e:
                logger.warning(
                    "periodic_risk_resync_failed",
                    extra={"event_type": "PERIODIC_RISK_RESYNC_FAILED", "error": str(e)},
                )

        # Persist a position snapshot for observability (self-throttles to its
        # own interval; the table was empty because this was never called).
        await self._maybe_snapshot_positions()

    async def _process_single_fill(self, fill: Any) -> None:
        """Delegates fill processing to FillProcessor."""
        await self._fill_processor.process_single_fill(fill, self.risk_manager, self._remove_pending_order_compat)

    # ── Position snapshots (delegates to fill_processor module) ──────────

    async def _maybe_snapshot_positions(self, min_interval_seconds: int = 60) -> None:
        """Persist positions snapshots via Data Gateway."""
        if not self._gateway_available:
            return

        client = self._get_gateway_client()
        result = await maybe_snapshot_positions(client, self.last_positions_snapshot_ts, min_interval_seconds)
        if result is not None:
            self.last_positions_snapshot_ts = result

    def _record_result(self, success: bool) -> None:
        """Record a broker round-trip outcome with its monotonic timestamp.

        The breaker uses a time window, so the timestamp determines whether a
        past failure is still counted. Pruning runs lazily on read in
        `_check_circuit_breaker` / `_prune_order_history`.
        """
        self.order_history.append((time.monotonic(), success))
