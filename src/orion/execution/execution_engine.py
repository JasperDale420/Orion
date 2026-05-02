import asyncio
import os
import socket
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from orion.config import risk_settings, system_settings
from orion.core.enums import DecisionStatus, OrderSide, TradeDirection
from orion.execution.attribution import (
    ORDER_ID_PREFIX,
    mint_orion_order_id,
    orion_order_id_sql_pattern,
)
from orion.execution.fill_processor import FillProcessor, maybe_snapshot_positions
from orion.execution.persistence import persist_exit_decision, persist_order_record
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
# `engine.initialize()`. Two concurrent instances of the same service then
# refuse to start (the second sees a fresh lease owned by another run_id and
# raises). Stale leases (no renewal for SERVICE_LEASE_STALE_SECONDS) are
# treated as crashed-prior-runs and overwritten.
SERVICE_LEASE_KEY_PREFIX = "service_lease_"
SERVICE_LEASE_STALE_SECONDS = 120


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

    @staticmethod
    def _service_lease_key(service_id: str) -> str:
        return f"{SERVICE_LEASE_KEY_PREFIX}{service_id}"

    async def acquire_service_lease(self, service_id: str) -> None:
        """Refuse to start if another instance of `service_id` holds a fresh lease.

        Backed by a SystemStatus row at `service_lease_<service_id>`. A row is
        considered "fresh" if its `last_updated_utc` is within the last
        SERVICE_LEASE_STALE_SECONDS — anything older is presumed to be a
        prior crashed instance and overwritten.

        Lease identity uses `ORION_RUN_ID` when set (typically a stable
        deployment identifier like ``docker_persistent_exec``) so a container
        restart re-acquires its own lease without waiting out the stale
        window. Falls back to a per-process uuid4 when unset, preserving the
        original "two simultaneous starts both new" guard for ad-hoc CLI
        invocations. Two concurrent instances with the same ORION_RUN_ID will
        both think they own the lease — acceptable under docker-compose
        which serializes container start/stop; not safe for hand-rolled
        multi-process deployments.

        Soft guard, not a distributed lock: a check-then-write race between
        two simultaneous starts can let both through. Acceptable for Orion's
        single-Docker-process deployment; the goal is to catch the common
        accidental cases (forgotten canary, local-while-prod, double-deploy).

        Raises RuntimeError if a fresh lease is owned by a different run_id.
        Caller (typically a `main_*.py`) should propagate the failure so the
        process exits non-zero.
        """
        from orion.storage.models import SystemStatus

        # Use a stable owner id when the deployment supplies one via
        # ``ORION_LEASE_OWNER_ID``. This lets a container with a fixed
        # owner id (e.g. ``docker_persistent_exec``) reclaim its own lease
        # across restarts within the stale window — without this, a fresh
        # uuid4 per process incarnation made docker-compose `up -d` cycles
        # dead-loop the container for ~120s waiting out the previous
        # instance's stale window. Two different deployments with distinct
        # owner ids still mutually exclude (the original guard).
        env_owner = os.environ.get("ORION_LEASE_OWNER_ID", "").strip()
        run_id = env_owner or str(uuid.uuid4())
        host = socket.gethostname()
        pid = os.getpid()
        details = f"run_id={run_id} host={host} pid={pid}"
        key = self._service_lease_key(service_id)

        async def _claim(session: Any) -> None:
            stmt = select(SystemStatus).where(SystemStatus.key == key)
            existing = (await session.execute(stmt)).scalars().first()

            now = datetime.now(UTC)
            if existing is not None:
                last = existing.last_updated_utc
                if last is not None:
                    age = (now - ensure_utc(last)).total_seconds()
                    is_fresh = age < SERVICE_LEASE_STALE_SECONDS
                    is_other = f"run_id={run_id}" not in (existing.details or "")
                    if is_fresh and is_other:
                        raise RuntimeError(
                            f"Another '{service_id}' instance holds a fresh lease "
                            f"(age={age:.1f}s, details={existing.details!r}). "
                            f"Refusing to start to preserve the single-process invariant."
                        )
                existing.status = "RUNNING"
                existing.details = details
                existing.last_updated_utc = now
            else:
                session.add(
                    SystemStatus(
                        key=key,
                        status="RUNNING",
                        details=details,
                        last_updated_utc=now,
                    )
                )

        async with async_session_factory() as session:
            await _claim(session)
            await session.commit()

        self._lease_service_id = service_id
        self._lease_run_id = run_id
        logger.info(
            "service_lease_acquired",
            extra={"event_type": "SERVICE_LEASE_ACQUIRED", "service_id": service_id, "run_id": run_id},
        )

    async def renew_service_lease(self) -> None:
        """Refresh the lease's `last_updated_utc` so other processes treat it as live.

        No-op if `acquire_service_lease` was never called. Errors are logged
        but do not propagate — a transient DB blip should not abort the main
        execution loop. The next renewal cycle retries. If renewal fails
        repeatedly, the lease eventually goes stale and another process can
        legitimately take over (defensive correctness, not perfect liveness).
        """
        if not self._lease_service_id or not self._lease_run_id:
            return

        from orion.storage.models import SystemStatus

        details = f"run_id={self._lease_run_id} host={socket.gethostname()} pid={os.getpid()}"
        key = self._service_lease_key(self._lease_service_id)

        try:
            async with async_session_factory() as session:
                stmt = select(SystemStatus).where(SystemStatus.key == key)
                existing = (await session.execute(stmt)).scalars().first()
                if existing is not None:
                    # Defensive: only renew if the row still belongs to us.
                    # If another process has taken over (we crashed and
                    # restarted with a new run_id), don't fight them.
                    if f"run_id={self._lease_run_id}" not in (existing.details or ""):
                        logger.warning(
                            "service_lease_lost",
                            extra={
                                "event_type": "SERVICE_LEASE_LOST",
                                "service_id": self._lease_service_id,
                                "current_details": existing.details,
                            },
                        )
                        return
                    existing.last_updated_utc = datetime.now(UTC)
                    existing.details = details
                else:
                    session.add(
                        SystemStatus(
                            key=key,
                            status="RUNNING",
                            details=details,
                            last_updated_utc=datetime.now(UTC),
                        )
                    )
                await session.commit()
        except Exception as exc:
            logger.warning(
                "service_lease_renew_failed",
                extra={"event_type": "SERVICE_LEASE_RENEW_FAILED", "error": str(exc)},
            )

    def _get_gateway_client(self) -> Any:
        """Lazy-initialize Gateway trading client singleton."""
        if self._gateway_client is None:
            from orion.clients.gateway_trading_client import get_gateway_trading_client

            self._gateway_client = get_gateway_trading_client()
        return self._gateway_client

    async def _check_gateway_available(self) -> bool:
        """Check if Data Gateway is reachable. Caches result for 60s."""
        if hasattr(self, "_gateway_check_ts") and self._gateway_check_ts:
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
        """Return the set of tickers that Orion has active orders for."""
        from orion.storage.models_execution import OrderRecord

        async def query_tickers(session: Any) -> set[str]:
            stmt = (
                select(OrderRecord.ticker)
                .where(OrderRecord.client_order_id.like(orion_order_id_sql_pattern()))
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
                last_equity = float(account.get("last_equity", 0) or account.get("equity", 0) or 0)

                if equity > 0:
                    self.risk_manager.current_equity = equity
                    self.risk_manager.starting_equity = last_equity

                    # Daily loss is Orion-attributed only: driven by update_post_fill
                    # from Orion-owned fills (client_order_id prefix "orion_"), not
                    # by account-wide equity delta. The paper account is shared with
                    # 3Roses/Cerberus/Kairos/Orbit/WhaleHunter, so last_equity-equity
                    # would include their P&L and falsely trip Orion's kill switch.
                    # We DO NOT overwrite self.risk_manager.current_daily_loss here.

                    # Seed peak_equity from the real Gateway account on first
                    # run only — replacing the older magic-default check
                    # (`peak_equity == 100000.0`) that overwrote a legitimately-
                    # loaded peak which happened to equal the hardcoded default.
                    # Subsequent syncs must not reset peak_equity; the high-
                    # water mark is updated by `_evaluate_drawdown_kill_switch`
                    # whenever current_equity > peak_equity.
                    if not getattr(self.risk_manager, "_peak_equity_seeded", False):
                        self.risk_manager.peak_equity = max(equity, last_equity)
                        self.risk_manager._peak_equity_seeded = True

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
            if positions:
                self.risk_manager.positions = {}
                self.risk_manager.ticker_exposures = {}
                skipped = 0
                for p in positions:
                    symbol = p.get("symbol", "")
                    # Only load positions Orion has ever placed an order for.
                    # Empty orion_tickers means Orion owns no positions at all —
                    # skip everything (None is the error sentinel, handled above).
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
        client = self._get_gateway_client()
        chain_result = await client.get_option_chain(candidate.ticker)

        if "error" not in chain_result and candidate.option_symbol:
            contracts = chain_result.get("contracts", [])
            for contract in contracts:
                # Gateway returns `contract_symbol`; `symbol` is for the underlying.
                if contract.get("contract_symbol") == candidate.option_symbol:
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
        if not self.risk_manager.check_order(candidate.ticker, num_contracts, option_price * 100, side_value):
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

        await self._submit_options_order(decision, candidate, num_contracts, option_price)

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
        self, decision: Any, candidate: Any, num_contracts: int, option_price: float
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
                raise RuntimeError(f"Gateway options order failed: {result['error']}")

            await persist_order_record(
                decision=decision,
                candidate=candidate,
                client_order_id=client_order_id,
                side=side,
                qty=num_contracts,
                limit_price=option_price,
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

            await persist_order_record(
                decision=decision,
                candidate=candidate,
                client_order_id=client_order_id,
                side=side,
                qty=num_contracts,
                limit_price=option_price,
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
    ) -> bool:
        """Close a position based on exit signal via Data Gateway."""
        if not await self._check_gateway_available():
            logger.warning(
                "Data Gateway unavailable. Cannot close position.",
                extra={"event_type": "CLOSE_POSITION_NOOP", "ticker": ticker},
            )
            return False

        try:
            client = self._get_gateway_client()
            client_order_id = mint_orion_order_id()

            if use_market_order or exit_signal.urgency == "IMMEDIATE":
                result = await client.close_position(ticker, qty=qty)

                if "error" in result:
                    raise RuntimeError(f"Gateway close_position failed: {result['error']}")

                logger.info(
                    f"EXIT MARKET ORDER: {ticker} x{qty} - Reason: {exit_signal.reason}",
                    extra={
                        "event_type": "EXIT_ORDER_SUBMITTED",
                        "ticker": ticker,
                        "qty": qty,
                        "order_type": "MARKET",
                        "rule_id": exit_signal.rule_id,
                        "reason": exit_signal.reason,
                    },
                )
            else:
                snapshot = await client.get_stock_snapshot(ticker)
                current_price = 0.0
                if "error" not in snapshot:
                    latest_trade = snapshot.get("latestTrade", {}) or snapshot.get("latest_trade", {})
                    if latest_trade:
                        current_price = float(latest_trade.get("p", 0) or latest_trade.get("price", 0) or 0)

                if current_price <= 0:
                    logger.error(f"Cannot close {ticker}: Failed to get current price")
                    return False

                exit_buffer_bps = 5
                limit_price = round(current_price * (1 - exit_buffer_bps / 10000.0), 2)

                close_side = OrderSide.BUY if str(direction).upper() == TradeDirection.SHORT else OrderSide.SELL

                result = await client.create_order(
                    symbol=ticker,
                    qty=qty,
                    side=close_side,
                    order_type="limit",
                    limit_price=limit_price,
                    time_in_force="day",
                    client_order_id=client_order_id,
                )

                if "error" in result:
                    raise RuntimeError(f"Gateway exit limit order failed: {result['error']}")

                logger.info(
                    f"EXIT LIMIT ORDER: {ticker} x{qty} @ {limit_price} - Reason: {exit_signal.reason}",
                    extra={
                        "event_type": "EXIT_ORDER_SUBMITTED",
                        "ticker": ticker,
                        "qty": qty,
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

        try:
            client = self._get_gateway_client()

            account = await client.get_account()
            if "error" not in account:
                equity = float(account.get("equity", 0) or 0)
                if equity > 0:
                    self.risk_manager.current_equity = equity
                    # Do not overwrite current_daily_loss with account-wide delta;
                    # shared Alpaca account means other systems' P&L would false-trip
                    # Orion's kill switch. Orion-only daily loss is maintained by
                    # update_post_fill from orion_-prefixed fills.

            self._last_fill_poll_ts = datetime.now(UTC)

        except Exception as e:
            logger.warning(
                "Fill polling via Gateway failed",
                extra={"event_type": "FILL_POLL_ERROR", "error": str(e)},
            )

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
