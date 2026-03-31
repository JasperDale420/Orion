"""Core RiskManager — enforces pre-trade risk controls using composition.

Delegates to focused sub-modules:
- GreeksTracker: options Greeks limits
- SectorTracker: sector concentration limits
- ZeroDteGuard: 0DTE time-of-day wind-down
- PositionSizer: risk-per-trade position sizing with correlation
"""

import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from orion.config import RiskSettings, risk_settings
from orion.execution.risk.greeks import GreeksTracker
from orion.execution.risk.sector import SectorTracker
from orion.execution.risk.sizing import PositionSizer
from orion.execution.risk.zero_dte import ZeroDteGuard
from orion.shared.db_utils import db_write
from orion.shared.decorators import db_retry
from orion.shared.logger import setup_struct_logger

if TYPE_CHECKING:
    from orion.execution.correlation_adjuster import CorrelationAdjuster
    from orion.storage.models_execution import Position

logger = setup_struct_logger(__name__)

# Initialize metrics
_metrics: "Metrics | None" = None
try:
    from orion.shared.metrics import Metrics

    _maybe_metrics = Metrics.get_instance()
    _metrics = _maybe_metrics if hasattr(_maybe_metrics, "risk_equity") else None
except ImportError:
    pass


class RiskManager:
    """Enforces pre-trade risk controls to prevent catastrophic loss or specific rule violations."""

    def __init__(self, config: RiskSettings | None = None):
        self.config = config if config is not None else risk_settings

        self.current_daily_loss = 0.0
        self.open_positions = 0
        self.ticker_exposures: dict[str, float] = {}
        self.pending_orders: dict[str, tuple[str, float]] = {}
        self.current_equity = 100000.0
        self.starting_equity = self.current_equity
        self.peak_equity = self.current_equity

        # Track full position details (qty, avg_entry)
        self.positions: dict[str, dict[str, float]] = {}

        # Idempotency Tracking
        self.processed_fill_ids: set[str] = set()

        # Composed sub-modules
        self._greeks = GreeksTracker()
        self._sector = SectorTracker()
        self._zero_dte = ZeroDteGuard()
        self._sizer = PositionSizer()

    # ── Proxy properties for backward compatibility ──────────────────────

    @property
    def portfolio_delta(self) -> float:
        return self._greeks.portfolio_delta

    @portfolio_delta.setter
    def portfolio_delta(self, value: float) -> None:
        self._greeks.portfolio_delta = value

    @property
    def portfolio_gamma(self) -> float:
        return self._greeks.portfolio_gamma

    @portfolio_gamma.setter
    def portfolio_gamma(self, value: float) -> None:
        self._greeks.portfolio_gamma = value

    @property
    def portfolio_vega(self) -> float:
        return self._greeks.portfolio_vega

    @portfolio_vega.setter
    def portfolio_vega(self, value: float) -> None:
        self._greeks.portfolio_vega = value

    @property
    def position_greeks(self) -> dict[str, dict[str, float]]:
        return self._greeks.position_greeks

    @position_greeks.setter
    def position_greeks(self, value: dict[str, dict[str, float]]) -> None:
        self._greeks.position_greeks = value

    @property
    def sector_exposures(self) -> dict[str, float]:
        return self._sector.sector_exposures

    @sector_exposures.setter
    def sector_exposures(self, value: dict[str, float]) -> None:
        self._sector.sector_exposures = value

    # ── Config resolution ────────────────────────────────────────────────

    def _resolve_config(self, risk_override: RiskSettings | None) -> RiskSettings:
        return risk_override if risk_override else self.config

    # ── Drawdown helpers ─────────────────────────────────────────────────

    def _current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.current_equity) / self.peak_equity)

    def _drawdown_breached(self, cfg: RiskSettings) -> bool:
        if cfg.max_drawdown_pct is None:
            return False
        if cfg.max_drawdown_pct <= 0:
            return False
        return self._current_drawdown_pct() >= cfg.max_drawdown_pct

    # ── Order checking ───────────────────────────────────────────────────

    def check_order(
        self,
        ticker: str,
        quantity: float,
        price: float,
        side: str,
        timestamp: datetime | None = None,
        risk_override: RiskSettings | None = None,
    ) -> bool:
        """Returns True if the order is safe to execute, False otherwise."""
        cfg = self._resolve_config(risk_override)
        estimated_cost = quantity * price

        if not self._check_time_bans(cfg, timestamp):
            return False

        if not self._check_loss_limits(cfg):
            return False

        max_order_size = (
            float(cfg.max_order_size_usd)
            if cfg.max_order_size_usd is not None
            else self.current_equity * cfg.max_order_size_pct
        )
        projected_signed, effective_signed = self._calculate_projected_exposure(ticker, estimated_cost, side, price)

        if estimated_cost > max_order_size and not self._is_risk_reducing_trade(projected_signed, effective_signed):
            logger.warning(f"RISK REJECT: Order Size ${estimated_cost:.2f} > Limit ${max_order_size:.2f}")
            return False

        if estimated_cost > max_order_size:
            logger.info(
                f"RISK BYPASS: Order Size ${estimated_cost:.2f} exceeds limit ${max_order_size:.2f} "
                f"but reduces {ticker} exposure"
            )

        if not self._check_shorting(cfg, projected_signed, effective_signed):
            return False

        if not self._check_max_positions(cfg, ticker):
            return False

        return self._check_ticker_exposure_limit(cfg, ticker, projected_signed, effective_signed)

    def check_options_order(
        self,
        ticker: str,
        quantity: float,
        price: float,
        side: str,
        delta: float,
        gamma: float = 0.0,
        vega: float = 0.0,
        timestamp: datetime | None = None,
        risk_override: RiskSettings | None = None,
    ) -> bool:
        """Returns True if the options order is safe to execute."""
        cfg = self._resolve_config(risk_override)

        if not self.check_order(ticker, quantity, price, side, timestamp, risk_override):
            return False

        return self._check_greeks_limits(cfg, ticker, delta, gamma, vega)

    # ── Internal check helpers ───────────────────────────────────────────

    def _check_time_bans(self, cfg: RiskSettings, timestamp: datetime | None = None) -> bool:
        if not cfg.time_of_day_bans:
            return True

        if timestamp is None:
            timestamp = datetime.now(UTC)

        if not hasattr(self, "calendar"):
            try:
                import exchange_calendars as xcals

                self.calendar = xcals.get_calendar("XNYS")
            except ImportError:
                logger.warning("exchange_calendars not installed, skipping advanced time checks")
                self.calendar = None

        if not self.calendar:
            return True

        try:
            date = timestamp.date()
            schedule = self.calendar.schedule(start_date=date, end_date=date)

            if schedule.empty:
                logger.warning(f"RISK REJECT: No market schedule found for {date} (Holiday/Closed).")
                return False

            row = schedule.iloc[0]
            market_open = row.market_open
            market_close = row.market_close

            for ban in cfg.time_of_day_bans:
                if ban == "FIRST_5_MIN":
                    if market_open <= timestamp < market_open + timedelta(minutes=5):
                        logger.warning(f"RISK REJECT: Time violation {ban} at {timestamp}")
                        return False
                elif ban == "LAST_5_MIN":
                    if market_close - timedelta(minutes=5) <= timestamp < market_close:
                        logger.warning(f"RISK REJECT: Time violation {ban} at {timestamp}")
                        return False
        except Exception as e:
            logger.error(f"Time check logic failed: {e}")
            return False

        return True

    def _check_loss_limits(self, cfg: RiskSettings) -> bool:
        if self.current_daily_loss >= cfg.max_daily_loss:
            logger.error(
                f"RISK REJECT: Daily Loss Limit {cfg.max_daily_loss} Hit (Current Loss: {self.current_daily_loss})"
            )
            return False

        if self._drawdown_breached(cfg):
            dd = self._current_drawdown_pct() * 100.0
            logger.critical(f"RISK REJECT: Max Drawdown {cfg.max_drawdown_pct:.2%} Hit (Current Drawdown: {dd:.2f}%)")
            return False

        return True

    def _calculate_projected_exposure(
        self, ticker: str, estimated_cost: float, side: str, price: float
    ) -> tuple[float, float]:
        pending_exposure = sum(p_cost for p_ticker, p_cost in self.pending_orders.values() if p_ticker == ticker)

        signed_current_exposure = 0.0
        if ticker in self.positions:
            pos = self.positions[ticker]
            signed_current_exposure = pos["qty"] * price

        effective_signed = signed_current_exposure + pending_exposure
        cost_impact = estimated_cost if side.lower() == "buy" else -estimated_cost
        projected_signed = effective_signed + cost_impact

        return projected_signed, effective_signed

    @staticmethod
    def _is_risk_reducing_trade(projected_signed: float, effective_signed: float) -> bool:
        return abs(projected_signed) < abs(effective_signed) - 1e-9

    def _check_shorting(self, cfg: RiskSettings, projected_signed: float, effective_signed: float) -> bool:
        is_moving_short = projected_signed < effective_signed
        if projected_signed < 0 and is_moving_short:
            if not cfg.enable_shorting:
                logger.error(f"RISK REJECT: Shorting Disabled. Cannot move to {projected_signed}")
                return False
        return True

    def _check_max_positions(self, cfg: RiskSettings, ticker: str) -> bool:
        if ticker not in self.positions or math.isclose(self.positions[ticker]["qty"], 0, abs_tol=1e-9):
            has_pending = any(p_ticker == ticker for (p_ticker, _) in self.pending_orders.values())
            if not has_pending:
                if self.open_positions >= cfg.max_positions:
                    logger.warning(f"RISK REJECT: Max Positions {cfg.max_positions} Reached")
                    return False
        return True

    def _check_ticker_exposure_limit(
        self, cfg: RiskSettings, ticker: str, projected_signed: float, effective_signed: float
    ) -> bool:
        abs_proj = abs(projected_signed)
        abs_curr = abs(effective_signed)
        limit = (
            float(cfg.max_ticker_exposure_usd)
            if getattr(cfg, "max_ticker_exposure_usd", None) is not None
            else self.current_equity * cfg.max_ticker_exposure_pct
        )

        if abs_proj > limit:
            if abs_proj < abs_curr:
                return True
            else:
                logger.warning(
                    f"RISK REJECT: Max Ticker Exposure ${limit:.2f} ({cfg.max_ticker_exposure_pct:.0%} of equity) Exceeded for {ticker} (Projected: ${abs_proj:.2f})"
                )
                return False
        return True

    # ── Delegated methods (Greeks) ───────────────────────────────────────

    def _check_greeks_limits(
        self,
        cfg: RiskSettings,
        ticker: str,
        position_delta: float = 0.0,
        position_gamma: float = 0.0,
        position_vega: float = 0.0,
    ) -> bool:
        return self._greeks.check_greeks_limits(cfg, ticker, position_delta, position_gamma, position_vega)

    def _recalculate_portfolio_greeks(self) -> None:
        self._greeks.recalculate_portfolio_greeks()

    def update_position_greeks(
        self, ticker: str, delta: float, gamma: float, theta: float = 0.0, vega: float = 0.0
    ) -> None:
        self._greeks.update_position_greeks(ticker, delta, gamma, theta, vega)

    def clear_position_greeks(self, ticker: str) -> None:
        self._greeks.clear_position_greeks(ticker)

    # ── Delegated methods (Sector) ───────────────────────────────────────

    def check_sector_exposure(
        self, sector: str, additional_exposure: float = 0.0, risk_override: RiskSettings | None = None
    ) -> bool:
        cfg = self._resolve_config(risk_override)
        return self._sector.check_sector_exposure(cfg, sector, additional_exposure, self.current_equity)

    def update_sector_exposure(self, sector: str, exposure_change: float) -> None:
        self._sector.update_sector_exposure(sector, exposure_change)

    def get_sector_exposure_pct(self, sector: str) -> float:
        return self._sector.get_sector_exposure_pct(sector, self.current_equity)

    # ── Delegated methods (Zero-DTE) ─────────────────────────────────────

    @staticmethod
    def _minutes_to_market_close(timestamp: datetime | None) -> float:
        return ZeroDteGuard._minutes_to_market_close(timestamp)

    def check_zero_dte_winddown(
        self, dte: int, timestamp: datetime | None = None, risk_override: RiskSettings | None = None
    ) -> tuple[bool, str]:
        cfg = self._resolve_config(risk_override)
        return self._zero_dte.check_zero_dte_winddown(cfg, dte, timestamp)

    def get_zero_dte_size_multiplier(
        self, dte: int, timestamp: datetime | None = None, risk_override: RiskSettings | None = None
    ) -> float:
        cfg = self._resolve_config(risk_override)
        return self._zero_dte.get_zero_dte_size_multiplier(cfg, dte, timestamp)

    # ── Delegated methods (Sizing) ───────────────────────────────────────

    def calculate_size(
        self, entry_price: float, stop_loss_pct: float | None = None, account_equity: float | None = None
    ) -> float:
        """Calculates position size based on risk per trade.
        Caps at max_order_size_pct of account equity.

        Note: For correlation-aware sizing, use calculate_size_with_correlation().
        """
        equity = account_equity if account_equity is not None else self.current_equity
        return self._sizer.calculate_size(self.config, entry_price, equity, stop_loss_pct)

    async def calculate_size_with_correlation(
        self,
        ticker: str,
        entry_price: float,
        stop_loss_pct: float | None = None,
        account_equity: float | None = None,
    ) -> float:
        """Calculates position size with correlation adjustment."""
        equity = account_equity if account_equity is not None else self.current_equity
        return await self._sizer.calculate_size_with_correlation(
            self.config, ticker, entry_price, equity, self.positions, stop_loss_pct
        )

    def set_correlation_adjuster(self, adjuster: "CorrelationAdjuster") -> None:
        """Inject correlation adjuster for size scaling."""
        self._sizer.set_correlation_adjuster(adjuster)
        logger.info("Correlation adjuster configured for RiskManager")

    # ── Persistence ──────────────────────────────────────────────────────

    @db_retry
    async def upsert_position(self, position: "Position") -> None:
        """Persist position to DB."""

        async def save_position(session: Any) -> None:
            from sqlalchemy import select

            from orion.storage.models_execution import Position as PositionModel

            stmt = select(PositionModel).where(PositionModel.ticker == position.ticker)
            result = await session.execute(stmt)
            existing = result.scalars().first()

            if existing:
                existing.qty = position.qty
                existing.avg_price = position.avg_price
                existing.updated_at_utc = datetime.now(UTC)
            else:
                session.add(
                    PositionModel(
                        ticker=position.ticker,
                        qty=position.qty,
                        avg_price=position.avg_price,
                    )
                )

        await db_write(save_position)

    @db_retry
    async def initialize(self) -> None:
        """Loads risk state from DB (if exists) to survive restarts."""
        try:
            from sqlalchemy import select

            from orion.storage.db import async_session_factory
            from orion.storage.models_risk import RiskState

            async with async_session_factory() as session:
                stmt = select(RiskState).where(RiskState.id == "global_risk_v1")
                result = await session.execute(stmt)
                state = result.scalars().first()

                if state:
                    self.current_daily_loss = state.current_daily_loss
                    self.current_equity = state.current_equity
                    self.starting_equity = state.starting_equity
                    self.peak_equity = getattr(state, "peak_equity", 0.0) or max(
                        self.current_equity, self.starting_equity
                    )
                    self.open_positions = state.open_positions_count
                    logger.info(f"Risk State Loaded: DailyLoss={self.current_daily_loss}")
                else:
                    logger.info("No persisted Risk State found.")

            await self._evaluate_drawdown_kill_switch()

        except Exception as e:
            logger.error(f"Failed to load Risk State: {e}", exc_info=True)

    async def evaluate_drawdown_kill_switch(self) -> None:
        """Public wrapper for the drawdown kill switch evaluation."""
        await self._evaluate_drawdown_kill_switch()

    @db_retry
    async def _save_state(self) -> None:
        """Persist current risk state to DB."""

        async def save_risk_state(session: Any) -> None:
            from sqlalchemy import select

            from orion.storage.models_risk import RiskState

            stmt = select(RiskState).where(RiskState.id == "global_risk_v1")
            result = await session.execute(stmt)
            state = result.scalars().first()

            if not state:
                state = RiskState(id="global_risk_v1")
                session.add(state)

            state.updated_at_utc = datetime.now(UTC)
            state.current_daily_loss = self.current_daily_loss
            state.current_equity = self.current_equity
            state.starting_equity = self.starting_equity
            state.peak_equity = self.peak_equity
            state.open_positions_count = self.open_positions

        await db_write(save_risk_state)
        logger.info("Risk state persisted to DB")
        if _metrics and hasattr(_metrics, "risk_equity"):
            _metrics.risk_equity.set(self.current_equity)
            _metrics.risk_daily_loss.set(self.current_daily_loss)
            _metrics.risk_open_positions.set(self.open_positions)

    async def _evaluate_drawdown_kill_switch(self) -> None:
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

        if not self._drawdown_breached(self.config):
            return

        from orion.core.circuit_breaker import CircuitBreaker

        dd_pct = self._current_drawdown_pct()
        reason = (
            f"Max drawdown breached: drawdown={dd_pct:.2%} "
            f">= limit={self.config.max_drawdown_pct:.2%}; "
            f"equity={self.current_equity:.2f} peak={self.peak_equity:.2f}"
        )
        await CircuitBreaker().open(reason)

    # ── Fill processing ──────────────────────────────────────────────────

    async def process_fill(
        self,
        ticker: str,
        qty: float,
        price: float,
        side: str,
        fill_id: str,
        expected_price: float | None = None,
    ) -> None:
        """Updates authoritative risk state based on actual broker fills.
        Calculates Realized PnL using robust signed arithmetic.
        Idempotent: Checks fill_id against in-memory history.
        """
        if fill_id in self.processed_fill_ids:
            logger.warning(f"Fill {fill_id} already processed by RiskManager. Skipping.")
            return

        self.processed_fill_ids.add(fill_id)

        # Calculate slippage if expected price provided
        if expected_price is not None and expected_price > 0:
            slippage_bps = (price - expected_price) / expected_price * 10000
            logger.info(
                f"Fill slippage for {ticker}: {slippage_bps:.1f} bps (expected={expected_price:.4f}, actual={price:.4f})",
                extra={
                    "event": "fill_slippage",
                    "ticker": ticker,
                    "fill_id": fill_id,
                    "expected_price": expected_price,
                    "fill_price": price,
                    "slippage_bps": slippage_bps,
                    "side": side,
                },
            )
            if _metrics and hasattr(_metrics, "slippage_bps"):
                _metrics.slippage_bps.labels(ticker=ticker, side=side).observe(slippage_bps)

        sign = 1 if side.lower() == "buy" else -1
        signed_fill_qty = abs(qty) * sign

        current_pos = self.positions.get(ticker, {"qty": 0.0, "avg_entry": 0.0})
        old_qty = current_pos["qty"]
        old_entry = current_pos["avg_entry"]

        new_qty = old_qty + signed_fill_qty

        realized_pnl = 0.0

        is_closing = (old_qty > 0 and signed_fill_qty < 0) or (old_qty < 0 and signed_fill_qty > 0)

        if is_closing:
            qty_closing = min(abs(old_qty), abs(signed_fill_qty))

            if old_qty > 0:
                pnl = (price - old_entry) * qty_closing
            else:
                pnl = (old_entry - price) * qty_closing

            realized_pnl = pnl

            self.current_equity += realized_pnl
            self.current_daily_loss -= realized_pnl

            logger.info(
                f"Fill Processed for {ticker}: Realized PnL=${realized_pnl:.2f}. New DailyLoss=${self.current_daily_loss:.2f}",
                extra={
                    "event_type": "FILL_PROCESSED",
                    "ticker": ticker,
                    "pnl": realized_pnl,
                    "daily_loss": self.current_daily_loss,
                },
            )
            await self._evaluate_drawdown_kill_switch()

        # Update Position State (Avg Entry Logic)
        if not is_closing:
            total_val = (old_qty * old_entry) + (signed_fill_qty * price)
            new_avg = total_val / new_qty if new_qty != 0 else 0.0
            self.positions[ticker] = {"qty": new_qty, "avg_entry": new_avg}
        elif abs(signed_fill_qty) > abs(old_qty):
            self.positions[ticker] = {"qty": new_qty, "avg_entry": price}
        else:
            if math.isclose(new_qty, 0, abs_tol=1e-9):
                self.positions[ticker] = {"qty": 0.0, "avg_entry": 0.0}
            else:
                self.positions[ticker] = {"qty": new_qty, "avg_entry": old_entry}

        self.ticker_exposures[ticker] = abs(new_qty * price)
        self.open_positions = sum(1 for p in self.positions.values() if not math.isclose(p["qty"], 0, abs_tol=1e-9))

        await self._save_state()

        if _metrics and hasattr(_metrics, "risk_exposure"):
            _metrics.risk_exposure.labels(ticker=ticker).set(abs(new_qty * price))

    # ── Post-trade & pending order tracking ──────────────────────────────

    async def update_post_trade(
        self, ticker: str, qty: float, price: float, side: str, order_id: str | None = None
    ) -> None:
        """Updates internal risk state immediately after an order is sent (Optimistic)."""
        if not order_id:
            order_id = f"pending_{datetime.now(UTC).timestamp()}"

        cost = qty * price
        signed_cost = cost if side.lower() == "buy" else -cost
        self.pending_orders[order_id] = (ticker, signed_cost)

    def remove_pending_order(self, order_id: str) -> None:
        """Removes a pending order from risk tracking."""
        if order_id in self.pending_orders:
            del self.pending_orders[order_id]

    def update_metrics(
        self, realized_pnl: float = 0.0, open_positions_count: int | None = None, open_pnl: float = 0.0
    ) -> None:
        """Legacy sync method."""
        pass

    # ── Broker sync ──────────────────────────────────────────────────────

    def sync_with_broker(self, connector: Any) -> None:
        """Syncs risk state with the broker to handle restarts."""
        if not connector:
            return

        try:
            logger.info("Syncing RiskManager with Broker...")

            positions = connector.client.get_all_positions()
            self.open_positions = len(positions)

            self.processed_fill_ids.clear()
            self.ticker_exposures = {}
            self.positions = {}

            for p in positions:
                symbol = p.symbol
                market_value = float(p.market_value)
                qty = float(p.qty)
                avg_entry = float(p.avg_entry_price)

                self.positions[symbol] = {"qty": qty, "avg_entry": avg_entry}
                self.ticker_exposures[symbol] = abs(market_value)

            try:
                from alpaca.trading.enums import QueryOrderStatus
                from alpaca.trading.requests import GetOrdersRequest

                req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
                open_orders = connector.client.get_orders(filter=req)

                logger.info(f"Found {len(open_orders)} open orders on restart.")

                self.pending_orders = {}
                for o in open_orders:
                    symbol = o.symbol
                    qty = float(o.qty or 0.0)
                    side_str = str(o.side.value) if hasattr(o.side, "value") else str(o.side)
                    limit_price = float(o.limit_price) if o.limit_price else 0.0
                    order_id = str(o.client_order_id or o.id)

                    if limit_price <= 0 and o.order_type == "limit":
                        logger.warning(f"Open Limit Order {o.id} has no price, skipping risk track.")
                        continue

                    cost = qty * limit_price
                    signed_cost = cost if side_str.lower() == "buy" else -cost
                    self.pending_orders[order_id] = (symbol, signed_cost)

            except Exception as e:
                logger.error(f"Failed to sync pending orders: {e}")

            account = connector.client.get_account()
            # Cap equity to $100K — each bot gets a virtual slice of the shared $1M account
            _ALLOCATED_EQUITY = 100_000.0
            equity = min(float(account.equity), _ALLOCATED_EQUITY)
            last_equity = min(float(account.last_equity), _ALLOCATED_EQUITY)

            self.current_equity = equity
            self.starting_equity = last_equity

            self.current_daily_loss = max(0.0, last_equity - equity)

            logger.info(
                f"Risk Synced: OpenPositions={self.open_positions}, PendingOrders={len(self.pending_orders)}, DailyLoss={self.current_daily_loss}"
            )

        except Exception as e:
            logger.error(f"Failed to sync RiskManager with broker: {e}")
