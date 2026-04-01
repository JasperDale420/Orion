import asyncio
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from orion.config import risk_settings, system_settings
from orion.core.enums import DecisionStatus
from orion.core.errors import ErrorCode
from orion.execution.fill_processor import FillProcessor, maybe_snapshot_positions
from orion.execution.persistence import persist_exit_decision, persist_order_record
from orion.execution.rate_limiter import get_order_rate_limiter
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import ensure_utc
from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateTrade, StrategyDecision

logger = setup_struct_logger(__name__)

# Prefix for all Orion order IDs — used to identify Orion's positions
# in the shared Alpaca paper account.
ORDER_ID_PREFIX = "orion_"

__all__ = ["ExecutionEngine", "ORDER_ID_PREFIX", "async_session_factory"]


class ExecutionEngine:
    """
    Translates Agent decisions into broker orders.

    Trading is routed through the Data Gateway which proxies to Alpaca.
    Options-only: candidates without an option_symbol are rejected.

    Paper mode is controlled by the Data Gateway's Alpaca configuration.
    """

    def __init__(self) -> None:
        from orion.execution.risk_manager import RiskManager

        self.risk_manager = RiskManager()

        # Data Gateway trading client
        self._gateway_client = None
        self._gateway_available = False

        self.order_history: deque[bool] = deque(maxlen=20)
        self.last_positions_snapshot_ts: datetime | None = None
        self._last_fill_poll_ts: datetime | None = None

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

        try:

            async def fetch_recent_decisions(session: Any) -> list[Any]:
                stmt = (
                    select(StrategyDecision)
                    .where(StrategyDecision.decision == "EXECUTE")
                    .order_by(StrategyDecision.timestamp_utc.desc())
                    .limit(20)
                )
                result = await session.execute(stmt)
                return result.scalars().all()

            recent_decisions = await db_query(fetch_recent_decisions)

            for d in reversed(recent_decisions):
                if d.executed_successfully == DecisionStatus.TRUE:
                    self.order_history.append(True)
                elif d.executed_successfully == DecisionStatus.FALSE:
                    self.order_history.append(False)

            logger.info(
                "ExecutionEngine initialized",
                extra={"event_type": "EXECUTION_INIT", "loaded_history_count": len(self.order_history)},
            )
        except Exception as e:
            logger.error(
                "Failed to initialize ExecutionEngine history",
                extra={
                    "event_type": "EXECUTION_INIT_ERROR",
                    "error_code": ErrorCode.UNKNOWN_ERROR.value,
                    "error_details": str(e),
                },
            )

    async def _fetch_orion_tickers(self) -> set[str] | None:
        """Return the set of tickers that Orion has active orders for."""
        from orion.storage.models_execution import OrderRecord

        async def query_tickers(session: Any) -> set[str]:
            stmt = select(OrderRecord.ticker).where(OrderRecord.client_order_id.like(f"{ORDER_ID_PREFIX}%")).distinct()
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
                    self.risk_manager.current_daily_loss = max(0.0, last_equity - equity)

                    # If peak_equity is still the hardcoded default (no persisted
                    # state loaded), seed it from the actual account balance so the
                    # drawdown kill-switch is measured from the real starting point
                    # instead of an arbitrary $100K.
                    if self.risk_manager.peak_equity == 100000.0:
                        self.risk_manager.peak_equity = max(equity, last_equity)

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
                    # Only load positions that Orion has traded
                    if orion_tickers and symbol not in orion_tickers:
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

        client = self._get_gateway_client()
        chain_result = await client.get_option_chain(candidate.ticker)
        option_price = candidate.premium

        if "error" not in chain_result and candidate.option_symbol:
            contracts = chain_result.get("contracts", [])
            for contract in contracts:
                if contract.get("symbol") == candidate.option_symbol:
                    mid = contract.get("mid") or contract.get("ask")
                    if mid and float(mid) > 0:
                        option_price = float(mid)
                    break

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

        side_value = "buy" if candidate.direction == "LONG" else "sell"
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

        exposure = self.risk_manager.ticker_exposures.get(candidate.ticker, 0.0)
        side = "buy" if candidate.direction == "LONG" else "sell"
        is_short_opening = side == "sell" and exposure <= 0

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
        if not self.order_history:
            return False
        failures = self.order_history.count(False)
        rate = failures / len(self.order_history)
        if rate > 0.03:
            logger.critical("execution_blocked_error_rate", error_rate=rate, limit=0.03)
            return True
        return False

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

        client_order_id = f"{ORDER_ID_PREFIX}{uuid.uuid4()}"
        decision.execution_params = decision.execution_params or {}
        decision.execution_params["client_order_id"] = client_order_id
        decision.execution_params["order_type"] = "OPTIONS"
        decision.execution_params["contracts"] = num_contracts

        side = "buy" if candidate.direction == "LONG" else "sell"

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
                side=candidate.direction,
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

        Non-fatal: logs errors but does not roll back the entry order.
        """
        exit_side = "sell" if side == "buy" else "buy"
        sl_price = round(entry_price * (1 - stop_loss_pct), 2)
        tp_price = round(entry_price * (1 + take_profit_pct), 2)
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
            logger.error("bracket_stop_loss_failed", error=str(e), option_symbol=option_symbol)

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
            logger.error("bracket_take_profit_failed", error=str(e), option_symbol=option_symbol)

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
            client_order_id = f"{ORDER_ID_PREFIX}{uuid.uuid4()}"

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

                close_side = "buy" if str(direction).upper() == "SHORT" else "sell"

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
        """Queries SystemStatus table to ensure Global Health is OK and circuit breaker is not open."""
        from orion.core.circuit_breaker import CircuitBreaker
        from orion.storage.models import SystemStatus

        try:

            async def fetch_health_and_cb(session: Any) -> tuple[Any, Any]:
                cb_stmt = select(SystemStatus).where(SystemStatus.key == CircuitBreaker.KEY)
                health_stmt = select(SystemStatus).where(SystemStatus.key == "global_health")
                cb_result = await session.execute(cb_stmt)
                health_result = await session.execute(health_stmt)
                return cb_result.scalars().first(), health_result.scalars().first()

            cb_record, status_record = await db_query(fetch_health_and_cb)

            if cb_record and cb_record.status == "OPEN":
                logger.critical(
                    "EXECUTION BLOCKED: Circuit breaker is OPEN",
                    extra={
                        "event_type": "HEALTH_CHECK_FAILED",
                        "reason": "Circuit Breaker Open",
                        "details": cb_record.details,
                    },
                )
                return False

            if not status_record:
                logger.error(
                    "System Health Record missing. Execution BLOCKED until health record is created.",
                    extra={"event_type": "HEALTH_CHECK_FAILED", "details": "Record Missing"},
                )
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
                    return False

            return True
        except Exception as e:
            logger.error(
                f"Failed to check System Health: {e}",
                extra={"event_type": "HEALTH_CHECK_ERROR", "error_details": str(e)},
            )
            return False

    # ── Fill polling (delegates to FillProcessor) ────────────────────────

    async def poll_fills(self) -> None:
        """Polls Data Gateway for account equity and updates RiskManager.

        Only account-level equity is synced (shared across all systems).
        Position-level data is filtered to Orion-only in _sync_risk_from_gateway.
        """
        if not self._gateway_available:
            return

        try:
            client = self._get_gateway_client()

            account = await client.get_account()
            if "error" not in account:
                equity = float(account.get("equity", 0) or 0)
                if equity > 0:
                    self.risk_manager.current_equity = equity
                    last_equity = float(account.get("last_equity", 0) or account.get("equity", 0) or 0)
                    self.risk_manager.current_daily_loss = max(0.0, last_equity - equity)

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
        self.order_history.append(success)
