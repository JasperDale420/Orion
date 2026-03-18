import asyncio
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from orion.config import risk_settings, system_settings
from orion.core.enums import DecisionStatus
from orion.core.errors import ErrorCode
from orion.execution.rate_limiter import get_order_rate_limiter
from orion.labeler.constants import SECTOR_MAPPING
from orion.shared.db_utils import db_query, db_write
from orion.shared.decorators import db_retry
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import ensure_utc
from orion.storage.db import async_session_factory  # legacy patch target for tests
from orion.storage.models_gold import CandidateTrade, StrategyDecision

logger = setup_struct_logger(__name__)


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
        # Initialize Risk Manager State (DB Persistence)
        if hasattr(self.risk_manager, "initialize"):
            await self.risk_manager.initialize()

        # Sync risk state from Data Gateway (account equity, positions)
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

            # We want to append them in chronological order (oldest first)
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

    async def _sync_risk_from_gateway(self) -> None:
        """Sync risk manager state from Data Gateway (account + positions)."""
        if not await self._check_gateway_available():
            logger.info(
                "Skipping Gateway risk sync; server unavailable. Using persisted risk state.",
                extra={"event_type": "GATEWAY_RISK_SYNC_SKIPPED"},
            )
            return

        try:
            client = self._get_gateway_client()

            # Sync account equity
            account = await client.get_account()
            if "error" not in account:
                equity = float(account.get("equity", 0) or 0)
                last_equity = float(account.get("last_equity", 0) or account.get("equity", 0) or 0)

                if equity > 0:
                    self.risk_manager.current_equity = equity
                    self.risk_manager.starting_equity = last_equity
                    self.risk_manager.current_daily_loss = max(0.0, last_equity - equity)
                    logger.info(
                        "Risk state synced from Gateway account",
                        extra={
                            "event_type": "GATEWAY_RISK_SYNC",
                            "equity": equity,
                            "daily_loss": self.risk_manager.current_daily_loss,
                        },
                    )

            # Sync positions
            positions = await client.get_positions()
            if positions:
                self.risk_manager.positions = {}
                self.risk_manager.ticker_exposures = {}
                for p in positions:
                    symbol = p.get("symbol", "")
                    qty = float(p.get("qty", 0) or 0)
                    avg_entry = float(p.get("avg_entry_price", 0) or 0)
                    market_value = float(p.get("market_value", 0) or 0)

                    self.risk_manager.positions[symbol] = {"qty": qty, "avg_entry": avg_entry}
                    self.risk_manager.ticker_exposures[symbol] = abs(market_value)

                self.risk_manager.open_positions = len(
                    [p for p in self.risk_manager.positions.values() if abs(p["qty"]) > 1e-9]
                )
                logger.info(
                    "Positions synced from Gateway",
                    extra={
                        "event_type": "GATEWAY_POSITIONS_SYNC",
                        "open_positions": self.risk_manager.open_positions,
                    },
                )

            # Re-evaluate kill switch after sync
            if hasattr(self.risk_manager, "evaluate_drawdown_kill_switch"):
                await self.risk_manager.evaluate_drawdown_kill_switch()

        except Exception as e:
            logger.error(
                "Failed to sync risk state from Gateway",
                extra={"event_type": "GATEWAY_RISK_SYNC_ERROR", "error": str(e)},
            )

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

        # Normalize direction once before dispatching to avoid repeated guards
        if isinstance(candidate.direction, str):
            candidate.direction = candidate.direction.upper()

        # Options-only: reject candidates without an option_symbol
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

    # NOTE: _execute_equity_order removed — Orion is options-only.

    async def _execute_options_order(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        """Execute an options order via Data Gateway."""

        # 1. Pre-Flight Checks
        if not await self._pre_flight_checks(decision, candidate):
            return

        # 2. DTE Check
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

        # 3. Get current option price via Data Gateway
        client = self._get_gateway_client()
        chain_result = await client.get_option_chain(candidate.ticker)
        option_price = candidate.premium  # Fallback to candidate premium

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

        # 4. Calculate contracts based on max premium
        max_premium = self.risk_manager.current_equity * risk_settings.max_option_premium_pct
        num_contracts = max(0, int(max_premium / (option_price * 100)))

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

        # 4b. 0DTE Wind-Down: reduce size and/or block near close
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

        # 5. Risk Manager Check
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

        # 6. Circuit Breaker
        if self._check_circuit_breaker():
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "High Error Rate"
            return

        # 7. Submit options order via Data Gateway
        await self._submit_options_order(decision, candidate, num_contracts, option_price)

    async def _pre_flight_checks(self, decision: StrategyDecision, candidate: CandidateTrade) -> bool:
        """System Health, Data Lag, Shorting Checks"""
        # System Health
        if not await self._check_system_health():
            msg = "EXECUTION BLOCKED: System Status is UNHEALTHY."
            logger.critical(msg, extra={"event_type": "EXECUTION_BLOCKED", "ticker": candidate.ticker})
            decision.executed_successfully = DecisionStatus.FALSE
            decision.execution_log = msg
            return False

        # Shorting Guard
        exposure = self.risk_manager.ticker_exposures.get(candidate.ticker, 0.0)
        side = "buy" if candidate.direction == "LONG" else "sell"
        is_short_opening = side == "sell" and exposure <= 0

        if is_short_opening and not self.risk_manager.config.enable_shorting:
            logger.warning("Execution BLOCKED: Shorting is disabled")
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Shorting Disabled"
            return False

        # Data Lag
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

    # NOTE: _get_execution_price_mcp, _calculate_order_params, _submit_order_mcp removed — equity path deleted.

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

        client_order_id = str(uuid.uuid4())
        decision.execution_params = decision.execution_params or {}
        decision.execution_params["client_order_id"] = client_order_id
        decision.execution_params["order_type"] = "OPTIONS"
        decision.execution_params["contracts"] = num_contracts

        side = "buy" if candidate.direction == "LONG" else "sell"

        # Rate limit check before order submission
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

        # Optimistic Risk Update
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

            await self._persist_order_record(
                decision=decision,
                candidate=candidate,
                client_order_id=client_order_id,
                side=side,
                qty=num_contracts,
                limit_price=option_price,
                broker_order=result,
                error_message=None,
            )

            premium_paid = num_contracts * option_price * 100
            logger.info(
                "options_execution_successful",
                client_order_id=client_order_id,
                premium_paid=premium_paid,
                option_symbol=candidate.option_symbol,
            )
            decision.executed_successfully = DecisionStatus.TRUE
            self._record_result(True)

        except Exception as e:
            await self._remove_pending_order_compat(client_order_id)

            await self._persist_order_record(
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

    async def close_position(
        self,
        ticker: str,
        qty: float,
        exit_signal: Any,
        direction: str = "LONG",
        use_market_order: bool = False,
    ) -> bool:
        """
        Close a position based on exit signal via Data Gateway.

        Args:
            ticker: Symbol to close
            qty: Quantity to close
            exit_signal: ExitSignal from exit rule
            direction: Position direction (LONG/SHORT)
            use_market_order: If True, use market order; else limit with buffer

        Returns:
            True if order submitted successfully
        """
        if not await self._check_gateway_available():
            logger.warning(
                "Data Gateway unavailable. Cannot close position.",
                extra={"event_type": "CLOSE_POSITION_NOOP", "ticker": ticker},
            )
            return False

        try:
            client = self._get_gateway_client()
            client_order_id = str(uuid.uuid4())

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
                # Get current price for limit order
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

                # Determine side: closing LONG = sell, closing SHORT = buy
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

            # Persist exit decision
            await self._persist_exit_decision(ticker, exit_signal, client_order_id, result)
            self._record_result(True)
            return True

        except Exception as e:
            logger.error(
                f"Failed to close position {ticker}: {e}",
                extra={"event_type": "EXIT_ORDER_FAILED", "ticker": ticker, "error": str(e)},
            )
            self._record_result(False)
            return False

    async def _persist_exit_decision(self, ticker: str, exit_signal: Any, client_order_id: str, order: Any) -> None:
        """Persist exit decision to database."""
        try:

            async def save_exit(session: Any) -> None:
                from orion.storage.models_gold import ExitDecision

                broker_order_id = None
                if isinstance(order, dict):
                    broker_order_id = str(order.get("id", "")) or None
                elif order is not None:
                    broker_order_id = str(getattr(order, "id", "")) if order else None

                session.add(
                    ExitDecision(
                        exit_id=client_order_id,
                        ticker=ticker,
                        rule_id=exit_signal.rule_id,
                        exit_reason=exit_signal.reason,
                        urgency=exit_signal.urgency,
                        confidence=exit_signal.confidence,
                        details=exit_signal.details or {},
                        broker_order_id=broker_order_id,
                        exit_ts_utc=datetime.now(UTC),
                    )
                )

            await db_write(save_exit)
        except Exception as e:
            logger.error(f"Failed to persist exit decision: {e}")

    async def _check_system_health(self) -> bool:
        """
        Queries SystemStatus table to ensure Global Health is OK
        AND checks the circuit breaker is not open.
        """
        from orion.core.circuit_breaker import CircuitBreaker
        from orion.storage.models import SystemStatus

        try:
            # Fetch circuit breaker state and global health in a single DB round-trip
            async def fetch_health_and_cb(session: Any) -> tuple[Any, Any]:
                cb_stmt = select(SystemStatus).where(SystemStatus.key == CircuitBreaker.KEY)
                health_stmt = select(SystemStatus).where(SystemStatus.key == "global_health")
                cb_result = await session.execute(cb_stmt)
                health_result = await session.execute(health_stmt)
                return cb_result.scalars().first(), health_result.scalars().first()

            cb_record, status_record = await db_query(fetch_health_and_cb)

            # Check circuit breaker
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
            return False  # Fail Closed

    async def poll_fills(self) -> None:
        """
        Polls Data Gateway for positions/account and updates RiskManager.
        Called periodically by the main execution loop.

        Gracefully no-ops if Gateway is unavailable.
        """
        if not self._gateway_available:
            return

        try:
            client = self._get_gateway_client()

            # Get all positions to update risk state
            positions = await client.get_positions()
            if not positions:
                return

            # Refresh account equity
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
        """
        Processes a single fill event, updating risk state and persisting.

        Handles partial fills by tracking cumulative filled quantity and only
        processing the incremental amount since last update.
        """
        try:
            order_id = str(fill.get("id", "")) if isinstance(fill, dict) else str(fill.id)
            client_oid = (
                fill.get("client_order_id") if isinstance(fill, dict) else getattr(fill, "client_order_id", None)
            ) or order_id
            if await self._is_fill_processed(order_id):
                return

            if isinstance(fill, dict):
                filled_qty = float(fill.get("filled_qty", 0) or 0)
                total_qty = float(fill.get("qty", 0) or filled_qty)
                filled_avg_price = float(fill.get("filled_avg_price", 0) or 0)
                ticker = fill.get("symbol", "")
                side = fill.get("side", "")
            else:
                filled_qty = float(fill.filled_qty) if fill.filled_qty else 0.0
                total_qty = float(fill.qty) if fill.qty else filled_qty
                filled_avg_price = float(fill.filled_avg_price) if fill.filled_avg_price else 0.0
                ticker = fill.symbol
                side = str(fill.side)

            # Track partial fills
            if not hasattr(self, "_partial_fill_tracker"):
                self._partial_fill_tracker: dict = {}

            last_filled = self._partial_fill_tracker.get(order_id, 0.0)
            incremental_qty = filled_qty - last_filled

            if incremental_qty <= 0:
                return

            self._partial_fill_tracker[order_id] = filled_qty
            is_partial = filled_qty < total_qty
            fill_type = "PARTIAL" if is_partial else "COMPLETE"

            logger.info(
                f"Processing {fill_type} fill: {ticker} {side} {incremental_qty:.2f} @ {filled_avg_price:.2f} "
                f"(total: {filled_qty:.2f}/{total_qty:.2f})",
                extra={
                    "event_type": f"FILL_{fill_type}",
                    "order_id": order_id,
                    "ticker": ticker,
                    "incremental_qty": incremental_qty,
                    "filled_qty": filled_qty,
                    "total_qty": total_qty,
                },
            )

            fill_id = order_id if last_filled == 0 else f"{order_id}_{filled_qty}"
            await self.risk_manager.process_fill(ticker, incremental_qty, filled_avg_price, side, fill_id=fill_id)

            # Update sector exposure tracking
            sector = SECTOR_MAPPING.get(ticker)
            if sector:
                fill_cost = incremental_qty * filled_avg_price
                exposure_change = fill_cost if side.lower() == "buy" else -fill_cost
                self.risk_manager.update_sector_exposure(sector, exposure_change)

            if not is_partial:
                await self._remove_pending_order_compat(client_oid)
                self._partial_fill_tracker.pop(order_id, None)

            await self._persist_fill_record(fill)
            await self._mark_fill_processed(order_id, client_oid=client_oid, ticker=ticker, qty=incremental_qty)

        except Exception as e:
            fill_id_str = (
                str(fill.get("id", "unknown")) if isinstance(fill, dict) else str(getattr(fill, "id", "unknown"))
            )
            logger.error(f"Failed to process fill {fill_id_str}: {e}")

    @db_retry
    async def _maybe_snapshot_positions(self, min_interval_seconds: int = 60) -> None:
        """
        Persist positions snapshots via Data Gateway.
        """
        if not self._gateway_available:
            return

        now = datetime.now(UTC)
        if self.last_positions_snapshot_ts and (now - self.last_positions_snapshot_ts) < timedelta(
            seconds=min_interval_seconds
        ):
            return

        try:
            client = self._get_gateway_client()
            positions = await client.get_positions()
        except Exception as e:
            logger.error(
                "Failed to fetch positions for snapshot",
                extra={"event_type": "POSITIONS_SNAPSHOT_FETCH_ERROR", "error": str(e)},
            )
            return

        if not positions:
            self.last_positions_snapshot_ts = now
            return

        try:
            from orion.storage.models_execution import PositionSnapshot

            rows = []
            for p in positions:
                snapshot = self._create_position_snapshot_from_dict(p, now, PositionSnapshot)
                if snapshot:
                    rows.append(snapshot)

            async def save_snapshots(session: Any) -> None:
                session.add_all(rows)

            await db_write(save_snapshots)

            self.last_positions_snapshot_ts = now
            logger.info("Positions snapshot persisted", extra={"event_type": "POSITIONS_SNAPSHOT", "count": len(rows)})
        except Exception as e:
            logger.error(
                "Failed to persist positions snapshot",
                extra={"event_type": "POSITIONS_SNAPSHOT_PERSIST_ERROR", "error": str(e)},
            )

    def _create_position_snapshot_from_dict(self, p: dict, now: datetime, model_class: Any) -> Any | None:
        """Helper to create a PositionSnapshot model from a Gateway position dict."""
        symbol = p.get("symbol")
        if not symbol:
            return None

        def _maybe_float(v: Any) -> float | None:
            try:
                return float(v) if v is not None else None
            except Exception:
                return None

        qty = _maybe_float(p.get("qty")) or 0.0
        avg_entry = _maybe_float(p.get("avg_entry_price"))
        market_value = _maybe_float(p.get("market_value"))
        unrealized_pl = _maybe_float(p.get("unrealized_pl"))

        return model_class(
            id=str(uuid.uuid4()),
            snapshot_ts_utc=now,
            ticker=str(symbol),
            qty=float(qty),
            avg_entry_price=avg_entry,
            market_value=market_value,
            unrealized_pl=unrealized_pl,
            raw_json=p,
        )

    async def _is_fill_processed(self, order_id: str) -> bool:
        from orion.storage.models_risk import ProcessedFill

        try:

            async def check_fill(session: Any) -> bool:
                stmt = select(ProcessedFill).where(ProcessedFill.fill_id == order_id)
                result = await session.execute(stmt)
                row = result.scalars().first()
                if row is None:
                    return False
                return str(getattr(row, "fill_id", "")) == str(order_id)

            return await db_query(check_fill)
        except Exception as e:
            logger.error(f"Failed to check processed fills for {order_id}: {e}")
            return False  # Fail Safe - assume not processed to avoid skipping fills

    @db_retry
    async def _mark_fill_processed(
        self, order_id: str, client_oid: str | None = None, ticker: str | None = None, qty: float | None = None
    ) -> None:
        async def mark_fill(session: Any) -> None:
            from orion.storage.models_risk import ProcessedFill

            pf = ProcessedFill(
                fill_id=order_id,
                client_order_id=client_oid,
                ticker=ticker,
                qty=qty,
                processed_at_utc=datetime.now(UTC),
            )
            session.add(pf)

        try:
            await db_write(mark_fill)
        except Exception as e:
            logger.error(f"Failed to mark fill {order_id} as processed: {e}")

    @db_retry
    async def _persist_order_record(
        self,
        *,
        decision: StrategyDecision,
        candidate: CandidateTrade,
        client_order_id: str,
        side: str,
        qty: float,
        limit_price: float | None,
        broker_order: Any | None,
        error_message: str | None,
    ) -> None:
        async def save_order_and_journal(session: Any) -> None:
            from orion.storage.models_execution import OrderRecord

            broker_order_id = None
            status = None
            raw = {}
            if broker_order is not None:
                if isinstance(broker_order, dict):
                    broker_order_id = str(broker_order.get("id", "")) or None
                    status = str(broker_order.get("status", "")) or None
                    raw = broker_order
                else:
                    broker_order_id = str(getattr(broker_order, "id", None) or "")
                    status = str(getattr(broker_order, "status", None) or "")
                    raw = broker_order.model_dump(mode="json") if hasattr(broker_order, "model_dump") else {}

            session.add(
                OrderRecord(
                    id=str(uuid.uuid4()),
                    decision_id=decision.decision_id,
                    candidate_id=candidate.candidate_id,
                    ticker=candidate.ticker,
                    side=side,
                    qty=float(qty),
                    limit_price=float(limit_price) if limit_price is not None else None,
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id or None,
                    status=status or None,
                    error_message=error_message,
                    raw_json=raw,
                )
            )

            # PRD SS12.4: Ensure a trade journal entry exists and links to order ids.
            try:
                from orion.storage.models_trade_journal import TradeJournalEntry

                await session.merge(
                    TradeJournalEntry(
                        decision_id=decision.decision_id,
                        signal_id=f"sig_{candidate.candidate_id}",
                        candidate_id=candidate.candidate_id,
                        solver_id=decision.strategy_version_id,
                        ticker=candidate.ticker,
                        direction=candidate.direction,
                        evidence=candidate.evidence or {},
                        decision_trace_json=decision.decision_trace_json or {},
                        client_order_id=client_order_id,
                        broker_order_id=broker_order_id or None,
                        raw_json={"order_status": status or None, "order_error": error_message},
                    )
                )
            except Exception as e:
                logger.error(
                    "Failed to upsert trade journal on order persist",
                    extra={"event_type": "TRADE_JOURNAL_UPSERT_ERROR", "error": str(e)},
                )

        try:
            async with async_session_factory() as session:
                await save_order_and_journal(session)
                await session.commit()
        except Exception as e:
            logger.error("Failed to persist order record", extra={"event_type": "ORDER_PERSIST_ERROR", "error": str(e)})

    @db_retry
    async def _persist_fill_record(self, fill: Any) -> None:
        async def save_fill_and_update_journal(session: Any) -> None:
            from sqlalchemy.dialects.postgresql import insert

            from orion.storage.models_execution import FillRecord

            if isinstance(fill, dict):
                broker_order_id = str(fill.get("id", ""))
                ticker = fill.get("symbol", "")
                client_oid = fill.get("client_order_id")
                qty = float(fill.get("filled_qty", 0) or 0)
                price = float(fill.get("filled_avg_price", 0) or 0)
                side = fill.get("side") or None
                filled_at = fill.get("filled_at") or fill.get("filled_at_utc")
                raw = fill
            else:
                broker_order_id = str(getattr(fill, "id", ""))
                ticker = getattr(fill, "symbol", None) or ""
                client_oid = getattr(fill, "client_order_id", None)
                qty = float(getattr(fill, "filled_qty", 0) or 0)
                price = float(getattr(fill, "filled_avg_price", 0) or 0)
                side = str(getattr(fill, "side", "")) or None
                filled_at = getattr(fill, "filled_at", None) or getattr(fill, "filled_at_utc", None)
                raw = fill.model_dump(mode="json") if hasattr(fill, "model_dump") else {}

            values = {
                "id": str(uuid.uuid4()),
                "ticker": ticker,
                "broker_order_id": broker_order_id,
                "client_order_id": str(client_oid) if client_oid else None,
                "filled_qty": qty,
                "filled_avg_price": price or None,
                "side": side,
                "filled_at_utc": filled_at,
                "raw_json": raw,
            }

            stmt = insert(FillRecord).values(values)
            stmt = stmt.on_conflict_do_nothing(index_elements=["broker_order_id"])
            await session.execute(stmt)

            # PRD SS12.4: Update trade journal fill pointers by broker_order_id.
            try:
                from orion.storage.models_trade_journal import TradeJournalEntry

                tj_stmt = select(TradeJournalEntry).where(TradeJournalEntry.broker_order_id == broker_order_id).limit(1)
                existing = (await session.execute(tj_stmt)).scalars().first()
                if existing:
                    existing.filled_qty = qty
                    existing.filled_avg_price = price or None
                    existing.filled_at_utc = filled_at
            except Exception as e:
                logger.error(
                    "Failed to update trade journal on fill persist",
                    extra={"event_type": "TRADE_JOURNAL_FILL_UPDATE_ERROR", "error": str(e)},
                )

        try:
            await db_write(save_fill_and_update_journal)
        except Exception as e:
            logger.error("Failed to persist fill record", extra={"event_type": "FILL_PERSIST_ERROR", "error": str(e)})

    def _record_result(self, success: bool) -> None:
        self.order_history.append(success)
