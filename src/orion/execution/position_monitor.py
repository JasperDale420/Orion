"""
Position Monitor.

Monitors open positions and triggers exits based on ML exit classifier
and rule-based exit signals.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from orion.ml.exit_classifier import (
    BucketExitClassifier,
    ExitFeatures,
    ExitPrediction,
    get_exit_classifier,
)
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.execution.position_monitor")


class GatewayPositionAdapter:
    """Adapts GatewayTradingClient.get_positions() dicts to the attribute-based
    interface expected by PositionMonitor.sync_positions().

    sync_positions expects a connector with ``get_all_positions()`` returning
    objects that have ``.symbol``, ``.current_price``, ``.avg_entry_price``,
    ``.qty``, and ``.unrealized_plpc`` attributes.
    """

    def __init__(self, gateway_client: Any) -> None:
        self._client = gateway_client

    def get_all_positions(self) -> list[SimpleNamespace]:
        """Synchronous wrapper — the actual fetch happens in _fetch_async, which
        the monitor loop awaits before calling sync_positions."""
        return self._cached_positions

    async def refresh(self) -> None:
        """Fetch positions from Gateway and cache as SimpleNamespace objects."""
        raw = await self._client.get_positions()
        self._cached_positions: list[SimpleNamespace] = []
        for p in raw:
            self._cached_positions.append(
                SimpleNamespace(
                    symbol=p.get("symbol", ""),
                    current_price=p.get("current_price", 0),
                    avg_entry_price=p.get("avg_entry_price", 0),
                    qty=p.get("qty", 0),
                    unrealized_plpc=p.get("unrealized_plpc", 0),
                )
            )


@dataclass
class TrackedPosition:
    """Position with tracking metadata for exit decisions."""

    symbol: str
    qty: float
    entry_price: float
    current_price: float
    unrealized_pnl_pct: float
    entry_time: datetime
    bucket: str  # 0DTE, SHORT_SWING, SWING, POSITION
    direction: str = "LONG"  # "LONG" or "SHORT"

    # Tracking state
    max_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    # Entry context (from original trade)
    premium_usd: float | None = None
    dte_at_entry: int | None = None
    is_sweep: bool = False
    iv_rank_at_entry: float | None = None
    vix_at_entry: float | None = None
    gex_at_entry: float | None = None
    market_tide_30m: float | None = None

    # Additional
    decision_id: str | None = None
    option_symbol: str | None = None  # For options positions

    # Optional — populated by sync_positions when the parent decision/order
    # carries it. TimeToExpiryRule no-ops when None (graceful), so it's
    # safe to leave unset on positions where we don't have the data.
    expiry_date: datetime | None = None


class PositionMonitor:
    """
    Monitors open positions and evaluates exit signals.

    Uses:
    - ML exit classifier (bucket-specific)
    - Heuristic fallbacks
    - Position tracking (max return, max drawdown)
    """

    def __init__(
        self,
        execution_engine: Any | None = None,
        position_manager: Any | None = None,
    ) -> None:
        self.exit_classifier: BucketExitClassifier = get_exit_classifier()
        self.tracked_positions: dict[str, TrackedPosition] = {}
        self._last_check_time: datetime | None = None
        self._execution_engine = execution_engine
        self._position_manager = position_manager

    async def sync_positions(self, connector: Any) -> list[TrackedPosition]:
        """
        Sync tracked positions with broker positions.

        Fetches current positions from Alpaca and updates tracking state.
        """
        try:
            broker_positions = connector.get_all_positions()
        except Exception as e:
            logger.error(f"Failed to fetch broker positions: {e}")
            return list(self.tracked_positions.values())

        # Get current symbols
        broker_symbols = {p.symbol for p in broker_positions}
        tracked_symbols = set(self.tracked_positions.keys())

        # Remove closed positions
        for symbol in tracked_symbols - broker_symbols:
            logger.info(
                f"Position closed externally: {symbol}",
                extra={"event": "position_closed", "symbol": symbol},
            )
            del self.tracked_positions[symbol]

        # Update existing or add new positions
        for bp in broker_positions:
            symbol = bp.symbol
            current_price = float(bp.current_price)
            entry_price = float(bp.avg_entry_price)
            qty = float(bp.qty)
            unrealized_pnl_pct = float(bp.unrealized_plpc) * 100  # Convert to percentage

            if symbol in self.tracked_positions:
                # Update existing
                pos = self.tracked_positions[symbol]
                pos.current_price = current_price
                pos.unrealized_pnl_pct = unrealized_pnl_pct

                # Update tracking metrics
                if unrealized_pnl_pct > pos.max_return_pct:
                    pos.max_return_pct = unrealized_pnl_pct
                if unrealized_pnl_pct < pos.max_drawdown_pct:
                    pos.max_drawdown_pct = unrealized_pnl_pct
            else:
                # New position - need to fetch entry context from DB
                entry_context = await self._fetch_entry_context(symbol)

                # Use the real entry timestamp from the decision row when
                # available — falling back to now() approximated entry_time
                # for every legacy position after restart, biasing the ML
                # `time_held_hours` feature toward "hold longer".
                entry_time = entry_context.get("entry_time") or datetime.now(UTC)

                pos = TrackedPosition(
                    symbol=symbol,
                    qty=qty,
                    entry_price=entry_price,
                    current_price=current_price,
                    unrealized_pnl_pct=unrealized_pnl_pct,
                    entry_time=entry_time,
                    bucket=entry_context.get("bucket", "SWING"),
                    direction=entry_context.get("direction", "LONG"),
                    max_return_pct=max(0, unrealized_pnl_pct),
                    max_drawdown_pct=min(0, unrealized_pnl_pct),
                    premium_usd=entry_context.get("premium_usd"),
                    dte_at_entry=entry_context.get("dte"),
                    is_sweep=entry_context.get("is_sweep", False),
                    iv_rank_at_entry=entry_context.get("iv_rank_at_entry"),
                    vix_at_entry=entry_context.get("vix_at_entry"),
                    gex_at_entry=entry_context.get("gex_at_entry"),
                    market_tide_30m=entry_context.get("market_tide_30m"),
                    decision_id=entry_context.get("decision_id"),
                    option_symbol=entry_context.get("option_symbol"),
                )
                self.tracked_positions[symbol] = pos

                logger.info(
                    f"New position tracked: {symbol} @ {entry_price}",
                    extra={
                        "event": "position_tracked",
                        "symbol": symbol,
                        "bucket": pos.bucket,
                    },
                )

        return list(self.tracked_positions.values())

    async def _fetch_entry_context(self, symbol: str) -> dict[str, Any]:
        """
        Fetch entry context from recent strategy decisions.
        """
        query = """
            SELECT
                sd.decision_id,
                sd.timestamp_utc as decision_ts,
                ct.option_symbol,
                ct.premium,
                ct.option_type,
                ct.direction,
                CASE
                    WHEN ct.expiration_date IS NOT NULL THEN
                        EXTRACT(DAY FROM ct.expiration_date - NOW())::int
                    ELSE NULL
                END as dte
            FROM strategy_decisions sd
            JOIN candidate_trades ct ON sd.candidate_id = ct.candidate_id
            WHERE ct.ticker = :symbol
            AND sd.decision = 'EXECUTE'
            AND sd.executed_successfully = 'TRUE'
            ORDER BY sd.timestamp_utc DESC
            LIMIT 1
        """

        try:

            async def run_query(session: Any) -> dict | None:
                from sqlalchemy import text

                result = await session.execute(text(query), {"symbol": symbol})
                row = result.mappings().first()
                return dict(row) if row else None

            row = await db_query(run_query)

            if row:
                # Classify bucket based on DTE
                dte = row.get("dte") or 7
                if dte == 0:
                    bucket = "0DTE"
                elif dte <= 3:
                    bucket = "SHORT_SWING"
                elif dte <= 14:
                    bucket = "SWING"
                else:
                    bucket = "POSITION"

                # The decision row has the real entry timestamp; use it instead of
                # now() so ML exit features (time_held_hours) are correct after a
                # restart. Fall back to None so the caller can decide.
                decision_ts = row.get("decision_ts")
                from orion.shared.utils import ensure_utc as _ensure_utc

                entry_time = _ensure_utc(decision_ts) if decision_ts is not None else None

                return {
                    "decision_id": row.get("decision_id"),
                    "option_symbol": row.get("option_symbol"),
                    "premium_usd": row.get("premium"),
                    "dte": dte,
                    "bucket": bucket,
                    "direction": row.get("direction", "LONG"),
                    "entry_time": entry_time,
                }
        except Exception as e:
            logger.warning(f"Failed to fetch entry context for {symbol}: {e}")

        # Default to SWING bucket if we can't determine
        return {"bucket": "SWING"}

    def evaluate_exits(self) -> list[tuple[TrackedPosition, ExitPrediction]]:
        """Evaluate exit signals for all tracked positions.

        Returns list of (position, prediction) tuples for positions that
        should be exited.

        Fallback rules (profit / time-to-expiry / drawdown) are evaluated
        FIRST and short-circuit the ML classifier when they fire. This
        keeps the deterministic safety net in place even when the
        classifier is degraded or returns low-confidence predictions
        (see FOLLOWUPS.md #0).
        """
        from orion.config import system_settings
        from orion.execution.exit_fallback_rules import evaluate_fallback_rules

        exit_signals = []

        for symbol, pos in self.tracked_positions.items():
            # Fallback rules first — they're cheap and deterministic.
            fallback = evaluate_fallback_rules(
                pos,
                profit_target_pct=system_settings.exit_fallback_profit_target_pct,
                min_dte=system_settings.exit_fallback_min_dte,
                max_drawdown_pct=system_settings.exit_fallback_max_drawdown_from_peak_pct,
            )
            if fallback is not None:
                logger.info(
                    f"Exit fallback fired for {symbol}: {fallback.reason}",
                    extra={
                        "event": "exit_signal_fallback",
                        "symbol": symbol,
                        "rule_id": fallback.rule_id,
                        "urgency": fallback.urgency,
                        "pnl_pct": pos.unrealized_pnl_pct,
                        "bucket": pos.bucket,
                    },
                )
                # Wrap the ExitSignal as ExitPrediction-compatible so the
                # downstream execute_exits path doesn't need to branch.
                # ExitPrediction's consumer reads .should_exit, .confidence,
                # .reasoning, and (optionally) .rule_id.
                prediction = SimpleNamespace(
                    should_exit=True,
                    confidence=fallback.confidence,
                    reasoning=fallback.reason,
                    rule_id=fallback.rule_id,
                )
                exit_signals.append((pos, prediction))
                continue

            # ML classifier path — unchanged from before.
            time_held = datetime.now(UTC) - pos.entry_time
            time_held_hours = time_held.total_seconds() / 3600

            features = ExitFeatures(
                current_return_pct=pos.unrealized_pnl_pct,
                time_held_hours=time_held_hours,
                max_return_so_far=pos.max_return_pct,
                max_drawdown_so_far=pos.max_drawdown_pct,
                premium_usd=pos.premium_usd or 0,
                dte_at_entry=pos.dte_at_entry or 7,
                is_sweep=pos.is_sweep,
                bucket=pos.bucket,
                iv_rank_at_entry=pos.iv_rank_at_entry,
                vix_at_entry=pos.vix_at_entry,
                gex_at_entry=pos.gex_at_entry,
                market_tide_30m=pos.market_tide_30m,
            )

            prediction = self.exit_classifier.predict(features)

            if prediction.should_exit:
                logger.info(
                    f"Exit signal for {symbol}: {prediction.reasoning}",
                    extra={
                        "event": "exit_signal",
                        "symbol": symbol,
                        "confidence": prediction.confidence,
                        "pnl_pct": pos.unrealized_pnl_pct,
                        "bucket": pos.bucket,
                    },
                )
                exit_signals.append((pos, prediction))

        return exit_signals

    async def execute_exits(
        self,
        connector: Any,
        exit_signals: list[tuple[TrackedPosition, ExitPrediction]],
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Execute exit orders for positions with exit signals.

        Routes through ExecutionEngine.close_position() when available to ensure
        circuit breaker, rate limiter, risk manager, and order persistence are applied.
        Falls back to direct connector call only when no engine is configured.

        Uses PositionManager._closing_symbols guard to prevent duplicate close orders
        from both the ML exit classifier and rule-based exit logic.

        Args:
            connector: AlpacaTradingConnector
            exit_signals: List of (position, prediction) tuples
            dry_run: If True, log but don't execute

        Returns:
            List of execution results
        """
        from orion.ml.performance_tracker import log_exit_prediction, log_outcome

        results = []

        for pos, prediction in exit_signals:
            result = {
                "symbol": pos.symbol,
                "position_type": "option" if pos.option_symbol else "equity",
                "pnl_pct": pos.unrealized_pnl_pct,
                "confidence": prediction.confidence,
                "reasoning": prediction.reasoning,
                "executed": False,
                "order_id": None,
                "error": None,
            }

            # Log exit prediction for performance tracking
            try:
                await log_exit_prediction(
                    symbol=pos.symbol,
                    option_chain=pos.option_symbol or "",
                    bucket=pos.bucket,
                    prediction_score=prediction.confidence,
                    position_id=pos.decision_id,
                )
            except Exception as e:
                logger.debug(f"Failed to log exit prediction: {e}")

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would close: {pos.symbol} @ {pos.unrealized_pnl_pct:.1f}%",
                    extra={"event": "dry_run_exit", "symbol": pos.symbol},
                )
                result["executed"] = False
                result["error"] = "dry_run"
            else:
                # Check closing guard — prevent duplicate close orders
                if self._position_manager and self._position_manager.is_closing(pos.symbol):
                    logger.warning(
                        f"ML exit skipped: {pos.symbol} already has a close in progress",
                        extra={"event": "ml_exit_duplicate_blocked", "symbol": pos.symbol},
                    )
                    result["error"] = "close_already_in_progress"
                    results.append(result)
                    continue

                # Mark symbol as closing before submitting order
                if self._position_manager:
                    if not self._position_manager.mark_closing(pos.symbol):
                        result["error"] = "close_already_in_progress"
                        results.append(result)
                        continue

                try:
                    closed = False

                    if self._execution_engine is not None:
                        # Route through ExecutionEngine for circuit breaker, rate limiter,
                        # risk manager, and order persistence
                        from types import SimpleNamespace

                        exit_signal = SimpleNamespace(
                            rule_id=f"ml_exit_{pos.bucket}",
                            reason=prediction.reasoning,
                            urgency="IMMEDIATE",
                            confidence=prediction.confidence,
                            details={"bucket": pos.bucket, "pnl_pct": pos.unrealized_pnl_pct},
                        )

                        closed = await self._execution_engine.close_position(
                            ticker=pos.symbol,
                            qty=pos.qty,
                            exit_signal=exit_signal,
                            direction=pos.direction,
                            use_market_order=True,
                        )
                    else:
                        # Fallback: direct connector call (legacy path, no safety guards)
                        logger.warning(
                            f"No ExecutionEngine configured — closing {pos.symbol} directly via connector",
                            extra={"event": "direct_close_fallback", "symbol": pos.symbol},
                        )
                        close_symbol = pos.option_symbol or pos.symbol
                        order = connector.close_position(close_symbol)
                        closed = order is not None

                    if closed:
                        result["executed"] = True
                        logger.info(
                            f"Exit executed via engine: {pos.symbol}",
                            extra={"event": "exit_executed", "symbol": pos.symbol},
                        )

                        # Log outcome for performance tracking
                        hit_target = pos.unrealized_pnl_pct >= 50
                        hit_stop = pos.unrealized_pnl_pct <= -20
                        try:
                            await log_outcome(
                                position_id=pos.decision_id or pos.symbol,
                                actual_return_pct=pos.unrealized_pnl_pct,
                                hit_target=hit_target,
                                hit_stop=hit_stop,
                            )
                        except Exception as e:
                            logger.debug(f"Failed to log outcome: {e}")

                except Exception as e:
                    logger.error(f"Exit execution failed for {pos.symbol}: {e}")
                    result["error"] = str(e)
                finally:
                    # Always release the closing guard
                    if self._position_manager:
                        self._position_manager.unmark_closing(pos.symbol)

            results.append(result)

        return results

    async def run_check(
        self,
        connector: Any,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Run a full position check cycle.

        1. Sync positions with broker
        2. Evaluate exit signals
        3. Execute exits

        Returns summary of the check.
        """
        self._last_check_time = datetime.now(UTC)

        # Sync positions
        positions = await self.sync_positions(connector)

        if not positions:
            return {
                "timestamp": self._last_check_time.isoformat(),
                "positions_checked": 0,
                "exit_signals": 0,
                "exits_executed": 0,
            }

        # Evaluate exits
        exit_signals = self.evaluate_exits()

        # Execute exits
        results = []
        if exit_signals:
            results = await self.execute_exits(connector, exit_signals, dry_run=dry_run)

        summary = {
            "timestamp": self._last_check_time.isoformat(),
            "positions_checked": len(positions),
            "exit_signals": len(exit_signals),
            "exits_executed": sum(1 for r in results if r.get("executed")),
            "exits": results,
        }

        logger.info(
            f"Position check complete: {summary['positions_checked']} positions, "
            f"{summary['exit_signals']} signals, {summary['exits_executed']} executed",
            extra={"event": "position_check_complete", **summary},
        )

        return summary


# Background monitoring loop
async def run_position_monitor_loop(
    check_interval_seconds: int = 60,
    dry_run: bool = False,
    execution_engine: Any | None = None,
    position_manager: Any | None = None,
    gateway_client: Any | None = None,
) -> None:
    """
    Run continuous position monitoring loop.

    Syncs positions via GatewayTradingClient and routes exits through
    ExecutionEngine.close_position() for full safety-guard coverage
    (circuit breaker, rate limiter, risk manager, order persistence).

    Args:
        check_interval_seconds: Seconds between position checks
        dry_run: If True, log but don't execute exits
        execution_engine: ExecutionEngine instance for safe order routing
        position_manager: PositionManager instance for closing-guard coordination
        gateway_client: GatewayTradingClient for position sync
    """
    if gateway_client is None:
        from orion.clients.gateway_trading_client import get_gateway_trading_client

        gateway_client = get_gateway_trading_client()

    adapter = GatewayPositionAdapter(gateway_client)
    monitor = get_position_monitor(
        execution_engine=execution_engine,
        position_manager=position_manager,
    )

    logger.info(
        "Position monitor loop started",
        extra={
            "event": "monitor_started",
            "interval_seconds": check_interval_seconds,
            "dry_run": dry_run,
            "has_execution_engine": execution_engine is not None,
        },
    )

    while True:
        try:
            # Refresh positions from Gateway before each check
            await adapter.refresh()
            summary = await monitor.run_check(adapter, dry_run=dry_run)
            logger.debug(
                "Position monitor cycle complete",
                extra={"event": "monitor_cycle", **summary},
            )
        except Exception as e:
            logger.error(f"Position monitor error: {e}", exc_info=True)

        await asyncio.sleep(check_interval_seconds)


# Singleton
_position_monitor: PositionMonitor | None = None


def get_position_monitor(
    execution_engine: Any | None = None,
    position_manager: Any | None = None,
) -> PositionMonitor:
    """Get or create position monitor singleton."""
    global _position_monitor
    if _position_monitor is None:
        _position_monitor = PositionMonitor(
            execution_engine=execution_engine,
            position_manager=position_manager,
        )
    return _position_monitor
