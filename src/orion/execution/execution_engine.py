import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from alpaca.trading.enums import OrderSide, TimeInForce
from orion.config import risk_settings, system_settings
from orion.connectors.alpaca_market_connector import AlpacaMarketConnector
from orion.connectors.alpaca_options_connector import AlpacaOptionsConnector
from orion.connectors.alpaca_trading_connector import AlpacaTradingConnector
from orion.core.errors import ErrorCode
from orion.execution.rate_limiter import get_order_rate_limiter
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
    """

    def __init__(self) -> None:
        # Load keys from SystemSettings
        api_key = system_settings.alpaca_api_key
        secret_key = system_settings.alpaca_secret_key

        # Initialize Risk Manager (autoloads RiskConfig from env)
        from orion.execution.risk_manager import RiskManager

        self.risk_manager = RiskManager()

        if not api_key or not secret_key:
            logger.warning(
                "Alpaca Credentials missing. ExecutionEngine disabled.",
                extra={"event_type": "EXECUTION_INIT_WARNING", "error_code": ErrorCode.PROVIDER_AUTH_FAILED.value},
            )
            self.connector = None
            self.options_connector = None
            self.market_connector = None
        else:
            self.connector = AlpacaTradingConnector(settings=system_settings)
            self.options_connector = AlpacaOptionsConnector(settings=system_settings)
            # Use same keys for market data
            self.market_connector = AlpacaMarketConnector(api_key=api_key, secret_key=secret_key)

            # Wire up correlation-aware sizing if enabled
            if risk_settings.correlation_size_scaling:
                from orion.execution.correlation_adjuster import CorrelationAdjuster

                adjuster = CorrelationAdjuster(market_connector=self.market_connector)
                self.risk_manager.set_correlation_adjuster(adjuster)
                logger.info(
                    "Correlation-aware sizing enabled",
                    extra={"event": "correlation_sizing_enabled", "threshold": risk_settings.correlation_threshold},
                )

            # Sync Risk State
            # self.risk_manager.sync_with_broker(self.connector)

        from collections import deque

        self.order_history = deque(maxlen=20)
        self.last_positions_snapshot_ts: datetime | None = None

    async def initialize(self) -> None:
        """
        Loads the last 20 execution attempts and initializes RiskManager state.
        """
        # Initialize Risk Manager State (DB Persistence)
        if hasattr(self.risk_manager, "initialize"):
            await self.risk_manager.initialize()

        # Sync Risk Manager with Broker (Moved from __init__)
        if self.connector and self.risk_manager:
            # Run in thread to avoid blocking loop if sync is slow
            await asyncio.to_thread(self.risk_manager.sync_with_broker, self.connector)
            # Re-evaluate kill switch after broker sync refreshes equity/positions.
            if hasattr(self.risk_manager, "evaluate_drawdown_kill_switch"):
                await self.risk_manager.evaluate_drawdown_kill_switch()

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
                if d.executed_successfully == "TRUE":
                    self.order_history.append(True)
                elif d.executed_successfully == "FALSE":
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

    async def execute_order(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        if not self.connector:
            logger.warning("No connector available. Skipping execution.")
            return

        action = decision.decision.upper()
        if action != "EXECUTE":
            logger.info("decision_skipped", ticker=candidate.ticker, action=action)
            return

        # Check if this is an options trade
        is_options_trade = bool(candidate.option_symbol)

        if is_options_trade:
            await self._execute_options_order(decision, candidate)
        else:
            await self._execute_equity_order(decision, candidate)

    async def _remove_pending_order_compat(self, order_id: str) -> None:
        """Support both sync and async remove_pending_order implementations."""
        if not order_id or not hasattr(self.risk_manager, "remove_pending_order"):
            return
        maybe_result = self.risk_manager.remove_pending_order(order_id)
        if asyncio.iscoroutine(maybe_result):
            await maybe_result

    async def _execute_equity_order(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        """Execute a standard equity order."""
        # 1. Pre-Flight Checks (Health, Lag, Shorting)
        if not await self._pre_flight_checks(decision, candidate):
            return

        # 2. Price Discovery
        current_price = self._get_execution_price(decision, candidate)
        if current_price <= 0:
            decision.executed_successfully = "FALSE"
            decision.reason = "Price Fetch Failed"
            return

        # 3. Sizing & Risk Check
        side, qty, limit_price = self._calculate_order_params(decision, candidate, current_price)
        if qty <= 0:
            return  # Decision updated in helper

        # 4. Check Risk Manager
        if not self.risk_manager.check_order(candidate.ticker, qty, current_price, side.value):
            logger.error(
                "execution_blocked_by_risk", ticker=candidate.ticker, qty=qty, price=current_price, side=side.value
            )
            decision.executed_successfully = "FALSE"
            decision.reason = "Risk Rejection"
            return

        # 5. Circuit Breaker
        if self._check_circuit_breaker():
            decision.executed_successfully = "FALSE"
            decision.reason = "High Error Rate"
            return

        # 6. Execution
        await self._submit_order(decision, candidate, side, qty, limit_price)

    async def _execute_options_order(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        """Execute an options order."""
        if not self.options_connector:
            logger.warning("No options connector available. Skipping options execution.")
            decision.executed_successfully = "FALSE"
            decision.reason = "Options Connector Missing"
            return

        # 1. Pre-Flight Checks
        if not await self._pre_flight_checks(decision, candidate):
            return

        # 2. DTE Check
        if candidate.expiration_date:
            now = datetime.now(UTC)
            dte = (candidate.expiration_date - now).days
            if dte < risk_settings.min_dte:
                logger.warning(
                    "options_blocked_dte_low", dte=dte, min_dte=risk_settings.min_dte, ticker=candidate.ticker
                )
                decision.executed_successfully = "FALSE"
                decision.reason = f"DTE Too Low ({dte} days)"
                return

        # 3. Get current option price
        quote = await self.options_connector.get_option_quote(candidate.option_symbol)
        option_price = quote.get("mid") or quote.get("ask") or candidate.premium

        if not option_price or option_price <= 0:
            logger.error("options_price_fetch_failed", option_symbol=candidate.option_symbol, ticker=candidate.ticker)
            decision.executed_successfully = "FALSE"
            decision.reason = "Option Price Fetch Failed"
            return

        # 4. Calculate contracts based on max premium
        max_premium = self.risk_manager.current_equity * risk_settings.max_option_premium_pct
        num_contracts = self.options_connector.calculate_option_contracts(max_premium, option_price)

        if num_contracts <= 0:
            logger.warning(
                "options_calculated_0_contracts",
                option_symbol=candidate.option_symbol,
                ticker=candidate.ticker,
                option_price=option_price,
            )
            decision.executed_successfully = "SKIPPED"
            decision.reason = "Size 0 Contracts"
            return

        # 5. Circuit Breaker
        if self._check_circuit_breaker():
            decision.executed_successfully = "FALSE"
            decision.reason = "High Error Rate"
            return

        # 6. Submit options order
        await self._submit_options_order(decision, candidate, num_contracts, option_price)

    async def _pre_flight_checks(self, decision: StrategyDecision, candidate: CandidateTrade) -> bool:
        """System Health, Data Lag, Shorting Checks"""
        # System Health
        if not await self._check_system_health():
            msg = "EXECUTION BLOCKED: System Status is UNHEALTHY."
            logger.critical(msg, extra={"event_type": "EXECUTION_BLOCKED", "ticker": candidate.ticker})
            decision.executed_successfully = "FALSE"
            decision.execution_log = msg
            return False

        # Shorting Guard
        exposure = self.risk_manager.ticker_exposures.get(candidate.ticker, 0.0)
        side = OrderSide.BUY if candidate.direction == "LONG" else OrderSide.SELL
        is_short_opening = side == OrderSide.SELL and exposure <= 0

        if is_short_opening and not self.risk_manager.config.enable_shorting:
            logger.warning("Execution BLOCKED: Shorting is disabled")
            decision.executed_successfully = "FALSE"
            decision.reason = "Shorting Disabled"
            return False

        # Data Lag
        now_utc = datetime.now(UTC)
        cand_ts = (
            candidate.timestamp_utc.replace(tzinfo=UTC)
            if candidate.timestamp_utc.tzinfo is None
            else candidate.timestamp_utc
        )
        lag = (now_utc - cand_ts).total_seconds()

        if lag > system_settings.max_data_lag_seconds:
            logger.critical(
                "execution_blocked_data_lag",
                lag_seconds=lag,
                max_lag=system_settings.max_data_lag_seconds,
                ticker=candidate.ticker,
            )
            decision.executed_successfully = "FALSE"
            decision.reason = "Data Lag"
            return False

        return True

    def _get_execution_price(self, decision: StrategyDecision, candidate: CandidateTrade) -> float:
        try:
            price = self.market_connector.get_latest_price(candidate.ticker)
            if price > 0:
                return price
        except Exception:
            pass

        # Fallback
        ep = decision.execution_params or {}
        return float(ep.get("limit_price", 0.0))

    def _calculate_order_params(
        self, decision: StrategyDecision, candidate: CandidateTrade, current_price: float
    ) -> tuple[Any, float, float]:
        side = OrderSide.BUY if candidate.direction == "LONG" else OrderSide.SELL
        qty = self.risk_manager.calculate_size(entry_price=current_price)

        if qty <= 0:
            logger.warning("calculated_qty_0", ticker=candidate.ticker, current_price=current_price)
            decision.executed_successfully = "SKIPPED"
            decision.reason = "Size 0"
            return side, 0.0, 0.0

        entry_buffer_bps = 10
        if side == OrderSide.BUY:
            limit_price = current_price * (1 + entry_buffer_bps / 10000.0)
        else:
            limit_price = current_price * (1 - entry_buffer_bps / 10000.0)

        return side, float(qty), round(limit_price, 2)

    def _check_circuit_breaker(self) -> bool:
        if len(self.order_history) > 0:
            failures = self.order_history.count(False)
            rate = failures / len(self.order_history)
            if rate > 0.03:
                logger.critical("execution_blocked_error_rate", error_rate=rate, limit=0.03)
                return True
        return False

    async def _submit_order(self, decision: Any, candidate: Any, side: Any, qty: float, limit_price: float) -> None:
        logger.info("execution_triggered", side=str(side), qty=qty, ticker=candidate.ticker, limit_price=limit_price)

        # Rate limit check before order submission
        rate_limiter = get_order_rate_limiter()
        if not await rate_limiter.acquire(timeout=10.0):
            logger.warning(
                "rate_limit_reached",
                ticker=candidate.ticker,
                capacity=rate_limiter.available_capacity,
                max_capacity=rate_limiter.max_per_minute,
            )
            decision.executed_successfully = "FALSE"
            decision.reason = "Rate limit exceeded"
            return

        client_order_id = str(uuid.uuid4())
        decision.execution_params = decision.execution_params or {}
        decision.execution_params["client_order_id"] = client_order_id

        # Optimistic Risk Update
        if hasattr(self.risk_manager, "update_post_trade"):
            await self.risk_manager.update_post_trade(
                ticker=candidate.ticker, qty=qty, price=limit_price, side=candidate.direction, order_id=client_order_id
            )

        try:
            order = self.connector.submit_limit_order(
                symbol=candidate.ticker,
                qty=qty,
                side=side,
                limit_price=limit_price,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
            )
            await self._persist_order_record(
                decision=decision,
                candidate=candidate,
                client_order_id=client_order_id,
                side=str(side),
                qty=qty,
                limit_price=limit_price,
                broker_order=order,
                error_message=None,
            )
            logger.info("execution_successful", client_order_id=client_order_id, ticker=candidate.ticker)
            decision.executed_successfully = "TRUE"
            self._record_result(True)
        except Exception as e:
            await self._remove_pending_order_compat(client_order_id)

            await self._persist_order_record(
                decision=decision,
                candidate=candidate,
                client_order_id=client_order_id,
                side=str(side),
                qty=qty,
                limit_price=limit_price,
                broker_order=None,
                error_message=str(e),
            )
            logger.error("execution_failed", error=str(e), client_order_id=client_order_id, ticker=candidate.ticker)
            decision.executed_successfully = "FALSE"
            decision.reason = f"Broker Error: {e}"
            self._record_result(False)

    async def _submit_options_order(
        self, decision: Any, candidate: Any, num_contracts: int, option_price: float
    ) -> None:
        """Submit an options order."""
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

        # Determine side from direction
        side = OrderSide.BUY if candidate.direction == "LONG" else OrderSide.SELL

        try:
            order = self.options_connector.submit_option_order(
                option_symbol=candidate.option_symbol,
                qty=num_contracts,
                side=side,
                order_type="limit",
                limit_price=option_price,
                client_order_id=client_order_id,
            )

            await self._persist_order_record(
                decision=decision,
                candidate=candidate,
                client_order_id=client_order_id,
                side=str(side.value),
                qty=num_contracts,
                limit_price=option_price,
                broker_order=order,
                error_message=None,
            )

            premium_paid = num_contracts * option_price * 100
            logger.info(
                "options_execution_successful",
                client_order_id=client_order_id,
                premium_paid=premium_paid,
                option_symbol=candidate.option_symbol,
            )
            decision.executed_successfully = "TRUE"
            self._record_result(True)

        except Exception as e:
            await self._persist_order_record(
                decision=decision,
                candidate=candidate,
                client_order_id=client_order_id,
                side=str(side.value),
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
            decision.executed_successfully = "FALSE"
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
        Close a position based on exit signal.

        Args:
            ticker: Symbol to close
            qty: Quantity to close
            exit_signal: ExitSignal from exit rule
            use_market_order: If True, use market order; else limit with buffer

        Returns:
            True if order submitted successfully
        """
        if not self.connector:
            logger.warning("No connector available. Cannot close position.")
            return False

        try:
            # Get current price
            current_price = self.market_connector.get_latest_price(ticker) if self.market_connector else 0.0

            if current_price <= 0:
                logger.error(f"Cannot close {ticker}: Failed to get current price")
                return False

            # Determine order params
            side = OrderSide.BUY if str(direction).upper() == "SHORT" else OrderSide.SELL
            client_order_id = str(uuid.uuid4())

            if use_market_order or exit_signal.urgency == "IMMEDIATE":
                # Market order for urgent exits
                order = self.connector.submit_market_order(
                    symbol=ticker,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id,
                )
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
                # Limit order with small buffer below current price
                exit_buffer_bps = 5  # 5 basis points buffer
                limit_price = round(current_price * (1 - exit_buffer_bps / 10000.0), 2)

                order = self.connector.submit_limit_order(
                    symbol=ticker,
                    qty=qty,
                    side=side,
                    limit_price=limit_price,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id,
                )
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
            await self._persist_exit_decision(ticker, exit_signal, client_order_id, order)
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
        Queries SystemStatus table to ensure Global Health is OK.
        """
        from orion.storage.models import SystemStatus

        try:

            async def fetch_health_status(session: Any) -> Any:
                stmt = select(SystemStatus).where(SystemStatus.key == "global_health")
                result = await session.execute(stmt)
                return result.scalars().first()

            status_record = await db_query(fetch_health_status)

            if not status_record:
                # Compatibility default for local/test startup: allow execution when
                # global health row has not been seeded yet.
                logger.warning(
                    "System Health Record missing. Defaulting to HEALTHY in local/test mode.",
                    extra={"event_type": "HEALTH_CHECK_WARNING", "details": "Record Missing"},
                )
                return True

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

            # Optional: Check 'last_updated_utc' staleness?
            # If ingestion died hard, it might check 'Healthy' but be 1 hour old.
            # PRD 9.1 says "UW ingestion heartbeat missing > 60s".
            # If record is > 60s old, Ingestion is dead.
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
        Polls broker for recent fills and updates RiskManager.
        Should be called periodically by the main execution loop.
        """
        if not self.connector:
            return

        try:
            # Poll for fills in the last X minutes (e.g. 5 mins) to catch anything missed
            # Use last_poll_ts or default to 5 min ago
            now = datetime.now(UTC)
            lookback = getattr(self, "last_fill_poll_ts", now - timedelta(minutes=5))
            # Safety buffer: overlap by 10s
            fetch_start = lookback - timedelta(seconds=10)

            fills = await asyncio.to_thread(self.connector.get_recent_fills, since=fetch_start)

            if fills:
                logger.info(f"Found {len(fills)} new fills during polling.")
                for fill in fills:
                    await self._process_single_fill(fill)

            self.last_fill_poll_ts = now
            await self._maybe_snapshot_positions()

        except Exception as e:
            logger.error(f"Failed to poll fills: {e}")

    async def _process_single_fill(self, fill: Any) -> None:
        """
        Processes a single fill event, updating risk state and persisting.

        Handles partial fills by tracking cumulative filled quantity and only
        processing the incremental amount since last update.
        """
        try:
            order_id = str(fill.id)
            client_oid = getattr(fill, "client_order_id", None) or order_id
            if await self._is_fill_processed(order_id):
                return

            # Get current filled qty and total order qty
            filled_qty = float(fill.filled_qty) if fill.filled_qty else 0.0
            total_qty = float(fill.qty) if fill.qty else filled_qty
            filled_avg_price = float(fill.filled_avg_price) if fill.filled_avg_price else 0.0

            # Track partial fills: only process incremental fills
            if not hasattr(self, "_partial_fill_tracker"):
                self._partial_fill_tracker: dict = {}  # order_id -> last_filled_qty

            last_filled = self._partial_fill_tracker.get(order_id, 0.0)
            incremental_qty = filled_qty - last_filled

            if incremental_qty <= 0:
                # No new fills since last check
                return

            # Update tracker
            self._partial_fill_tracker[order_id] = filled_qty

            # Check if this is a partial or complete fill
            is_partial = filled_qty < total_qty
            fill_type = "PARTIAL" if is_partial else "COMPLETE"

            ticker = fill.symbol
            side = str(fill.side)

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

            # Process the incremental fill amount through risk manager
            fill_id = order_id if last_filled == 0 else f"{order_id}_{filled_qty}"
            await self.risk_manager.process_fill(ticker, incremental_qty, filled_avg_price, side, fill_id=fill_id)

            # Only remove from pending orders when fully filled
            if not is_partial:
                if client_oid:
                    await self._remove_pending_order_compat(client_oid)
                # Clean up tracker
                if order_id in self._partial_fill_tracker:
                    del self._partial_fill_tracker[order_id]

            await self._persist_fill_record(fill)
            await self._mark_fill_processed(order_id, client_oid=client_oid, ticker=ticker, qty=incremental_qty)

        except Exception as e:
            logger.error(f"Failed to process fill {getattr(fill, 'id', 'unknown')}: {e}")

    @db_retry
    async def _maybe_snapshot_positions(self, min_interval_seconds: int = 60) -> None:
        """
        PRDv2 12.4: Persist positions snapshots (Alpaca source-of-truth) once trading is enabled.
        """
        if not self.connector:
            return

        now = datetime.now(UTC)
        if self.last_positions_snapshot_ts and (now - self.last_positions_snapshot_ts) < timedelta(
            seconds=min_interval_seconds
        ):
            return

        try:
            positions = await asyncio.to_thread(self.connector.client.get_all_positions)
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
                snapshot = self._create_position_snapshot(p, now, PositionSnapshot)
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

    def _create_position_snapshot(self, p: Any, now: datetime, model_class: Any) -> Any | None:
        """Helper to create a PositionSnapshot model from an Alpaca position."""
        symbol = getattr(p, "symbol", None)
        if not symbol:
            return None

        def _maybe_float(v: Any) -> float | None:
            try:
                return float(v) if v is not None else None
            except Exception:
                return None

        qty = _maybe_float(getattr(p, "qty", None)) or 0.0
        avg_entry = _maybe_float(getattr(p, "avg_entry_price", None))
        market_value = _maybe_float(getattr(p, "market_value", None))
        unrealized_pl = _maybe_float(getattr(p, "unrealized_pl", None))

        if hasattr(p, "model_dump"):
            raw = p.model_dump(mode="json")
        else:
            raw = getattr(p, "__dict__", None) or {"repr": repr(p)}

        return model_class(
            id=str(uuid.uuid4()),
            snapshot_ts_utc=now,
            ticker=str(symbol),
            qty=float(qty),
            avg_entry_price=avg_entry,
            market_value=market_value,
            unrealized_pl=unrealized_pl,
            raw_json=raw,
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
    async def _persist_order(self, order_payload: dict) -> None:
        """
        Save order submission details to DB.
        """

        async def save_order(session: Any) -> None:
            from datetime import datetime

            from orion.storage.models_execution import OrderSubmission

            order_record = OrderSubmission(
                order_id=order_payload.get("order_id"),
                ticker=order_payload.get("ticker"),
                qty=order_payload.get("qty"),
                side=order_payload.get("side"),
                order_type=order_payload.get("order_type"),
                limit_price=order_payload.get("limit_price"),
                stop_price=order_payload.get("stop_price"),
                time_in_force=order_payload.get("time_in_force"),
                submitted_at_utc=datetime.now(UTC),
                broker_response=order_payload,
            )
            session.add(order_record)

        await db_write(save_order)

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

            # PRD §12.4: Ensure a trade journal entry exists and links to order ids.
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

            # PRD §12.4: Update trade journal fill pointers by broker_order_id.
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
