import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

from alpaca.trading.enums import OrderSide, TimeInForce
from orion.config import system_settings, risk_settings
from orion.connectors.alpaca_market_connector import AlpacaMarketConnector
from orion.connectors.alpaca_options_connector import AlpacaOptionsConnector
from orion.connectors.alpaca_trading_connector import AlpacaTradingConnector
from orion.core.errors import ErrorCode
from orion.shared.db_utils import db_query, db_write
from orion.shared.decorators import db_retry
from orion.shared.utils import ensure_utc
from orion.storage.models_gold import CandidateTrade, StrategyDecision
from sqlalchemy import select

logger = logging.getLogger(__name__)


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

            async def fetch_recent_decisions(session: Any) -> List[Any]:
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
            logger.info(f"Decision for {candidate.ticker} was {action}")
            return

        # Check if this is an options trade
        is_options_trade = bool(candidate.option_symbol)
        
        if is_options_trade:
            await self._execute_options_order(decision, candidate)
        else:
            await self._execute_equity_order(decision, candidate)

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
            logger.error(f"Execution BLOCKED by RiskManager for {candidate.ticker}")
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
            from datetime import timezone
            now = datetime.now(timezone.utc)
            dte = (candidate.expiration_date - now).days
            if dte < risk_settings.min_dte:
                logger.warning(f"OPTIONS BLOCKED: DTE {dte} < min {risk_settings.min_dte}")
                decision.executed_successfully = "FALSE"
                decision.reason = f"DTE Too Low ({dte} days)"
                return
        
        # 3. Get current option price
        quote = await self.options_connector.get_option_quote(candidate.option_symbol)
        option_price = quote.get("mid") or quote.get("ask") or candidate.premium
        
        if not option_price or option_price <= 0:
            logger.error(f"Cannot get option price for {candidate.option_symbol}")
            decision.executed_successfully = "FALSE"
            decision.reason = "Option Price Fetch Failed"
            return
        
        # 4. Calculate contracts based on max premium
        max_premium = self.risk_manager.current_equity * risk_settings.max_option_premium_pct
        num_contracts = self.options_connector.calculate_option_contracts(max_premium, option_price)
        
        if num_contracts <= 0:
            logger.warning(f"OPTIONS: Calculated 0 contracts for {candidate.option_symbol}")
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
        now_utc = datetime.now(timezone.utc)
        cand_ts = (
            candidate.timestamp_utc.replace(tzinfo=timezone.utc)
            if candidate.timestamp_utc.tzinfo is None
            else candidate.timestamp_utc
        )
        lag = (now_utc - cand_ts).total_seconds()

        if lag > system_settings.max_data_lag_seconds:
            logger.critical(f"EXECUTION BLOCKED: Data Lag {lag:.1f}s")
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
    ) -> Tuple[Any, float, float]:
        side = OrderSide.BUY if candidate.direction == "LONG" else OrderSide.SELL
        qty = self.risk_manager.calculate_size(entry_price=current_price)

        if qty <= 0:
            logger.warning(f"Calculated quantity is 0 for {candidate.ticker}")
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
                logger.critical(f"EXECUTION BLOCKED: Error Rate {rate:.1%} > 3%")
                return True
        return False

    async def _submit_order(self, decision: Any, candidate: Any, side: Any, qty: float, limit_price: float) -> None:
        logger.info(f"EXECUTION TRIGGERED: {side} {qty} {candidate.ticker} @ {limit_price}")

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
            logger.info(f"Execution Successful {client_order_id}")
            decision.executed_successfully = "TRUE"
            self._record_result(True)
        except Exception as e:
            if hasattr(self.risk_manager, "remove_pending_order"):
                self.risk_manager.remove_pending_order(client_order_id)

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
            logger.error(f"Execution Failed: {e}")
            decision.executed_successfully = "FALSE"
            decision.reason = f"Broker Error: {e}"
            self._record_result(False)

    async def _submit_options_order(
        self, decision: Any, candidate: Any, num_contracts: int, option_price: float
    ) -> None:
        """Submit an options order."""
        logger.info(
            f"OPTIONS EXECUTION TRIGGERED: BUY {num_contracts} {candidate.option_symbol} @ {option_price}"
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
                f"OPTIONS Execution Successful {client_order_id} | "
                f"Premium: ${premium_paid:.2f}"
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
            logger.error(f"OPTIONS Execution Failed: {e}")
            decision.executed_successfully = "FALSE"
            decision.reason = f"Options Broker Error: {e}"
            self._record_result(False)


    async def close_position(
        self,
        ticker: str,
        qty: float,
        exit_signal: Any,
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
            side = OrderSide.SELL  # Closing a long position
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
                        exit_ts_utc=datetime.now(timezone.utc),
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
                # If no record exists, assume Healthy (start up) or Unhealthy?
                # PRD says "Fail Closed". If we don't know, we don't trade.
                logger.warning(
                    "System Health Record missing. Defaulting to UNHEALTHY (Fail Closed).",
                    extra={"event_type": "HEALTH_CHECK_WARNING", "details": "Record Missing"},
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

            # Optional: Check 'last_updated_utc' staleness?
            # If ingestion died hard, it might check 'Healthy' but be 1 hour old.
            # PRD 9.1 says "UW ingestion heartbeat missing > 60s".
            # If record is > 60s old, Ingestion is dead.
            now = datetime.now(timezone.utc)
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
            now = datetime.now(timezone.utc)
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
        """Processes a single fill event, updating risk state and persisting."""
        try:
            order_id = str(fill.id)
            if await self._is_fill_processed(order_id):
                return

            ticker = fill.symbol
            qty = float(fill.filled_qty)
            price = float(fill.filled_avg_price) if fill.filled_avg_price else 0.0
            side = str(fill.side)

            await self.risk_manager.process_fill(ticker, qty, price, side, fill_id=order_id)

            # Remove Pending Order (avoid double count)
            client_oid = getattr(fill, "client_order_id", None)
            if client_oid and hasattr(self.risk_manager, "remove_pending_order"):
                self.risk_manager.remove_pending_order(client_oid)

            await self._mark_fill_processed(order_id, client_oid, ticker, qty)
            await self._persist_fill_record(fill)
        except Exception as e:
            logger.error(f"Failed to process fill {getattr(fill, 'id', 'unknown')}: {e}")

    @db_retry
    async def _maybe_snapshot_positions(self, min_interval_seconds: int = 60) -> None:
        """
        PRDv2 12.4: Persist positions snapshots (Alpaca source-of-truth) once trading is enabled.
        """
        if not self.connector:
            return

        now = datetime.now(timezone.utc)
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

        def _maybe_float(v: Any) -> Optional[float]:
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
                return result.scalars().first() is not None

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
            from datetime import datetime, timezone

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
                submitted_at_utc=datetime.now(timezone.utc),
                broker_response=order_payload,
            )
            session.add(order_record)

        await db_write(save_order)

    @db_retry
    async def _mark_fill_processed(
        self, order_id: str, client_oid: Optional[str] = None, ticker: Optional[str] = None, qty: Optional[float] = None
    ) -> None:
        async def mark_fill(session: Any) -> None:
            from orion.storage.models_risk import ProcessedFill

            pf = ProcessedFill(
                fill_id=order_id,
                client_order_id=client_oid,
                ticker=ticker,
                qty=qty,
                processed_at_utc=datetime.now(timezone.utc),
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
            await db_write(save_order_and_journal)
        except Exception as e:
            logger.error("Failed to persist order record", extra={"event_type": "ORDER_PERSIST_ERROR", "error": str(e)})

    @db_retry
    async def _persist_fill_record(self, fill: Any) -> None:
        async def save_fill_and_update_journal(session: Any) -> None:
            from orion.storage.models_execution import FillRecord
            from sqlalchemy.dialects.postgresql import insert

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
