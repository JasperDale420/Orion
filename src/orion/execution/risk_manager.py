import logging
import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from orion.config import RiskSettings, risk_settings
from orion.shared.db_utils import db_write
from orion.shared.decorators import db_retry

if TYPE_CHECKING:
    from orion.execution.correlation_adjuster import CorrelationAdjuster
    from orion.storage.models_execution import Position

logger = logging.getLogger(__name__)

# Initialize metrics
_metrics: "Metrics | None" = None
try:
    from orion.shared.metrics import Metrics

    _maybe_metrics = Metrics.get_instance()
    _metrics = _maybe_metrics if hasattr(_maybe_metrics, "risk_equity") else None
except ImportError:
    pass


class RiskManager:
    """
    Enforces pre-trade risk controls to prevent catastrophic loss or specific rule violations.
    """

    def __init__(self, config: RiskSettings | None = None):
        if config is None:
            self.config = risk_settings
        else:
            self.config = config

        self.current_daily_loss = 0.0
        self.open_positions = 0
        self.ticker_exposures: dict[str, float] = {}  # ticker -> usd value
        self.pending_orders: dict[str, tuple[str, float]] = {}  # order_id -> (ticker, estimated cost signed)
        self.current_equity = 100000.0  # Default fallback, should be synced
        self.starting_equity = self.current_equity
        self.peak_equity = self.current_equity

        # Track full position details (qty, avg_entry)
        self.positions: dict[str, dict[str, float]] = {}

        # Idempotency Tracking
        self.processed_fill_ids: set[str] = set()

        # Greeks tracking for options risk (portfolio-level)
        self.portfolio_delta: float = 0.0
        self.portfolio_gamma: float = 0.0
        self.portfolio_vega: float = 0.0
        self.position_greeks: dict[str, dict[str, float]] = {}  # ticker -> {delta, gamma, theta, vega}

        # Sector exposure tracking
        self.sector_exposures: dict[str, float] = {}  # sector -> total USD exposure

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

    def check_order(
        self,
        ticker: str,
        quantity: float,
        price: float,
        side: str,
        timestamp: datetime | None = None,
        risk_override: RiskSettings | None = None,
    ) -> bool:
        """
        Returns True if the order is safe to execute, False otherwise.
        """
        cfg = risk_override if risk_override else self.config
        estimated_cost = quantity * price

        # 0. Global Time Checks
        if not self._check_time_bans(cfg, timestamp):
            return False

        # 1. Daily Loss & Drawdown Checks
        if not self._check_loss_limits(cfg):
            return False

        # 2. Max Order Size (legacy USD override takes precedence when set)
        max_order_size = (
            float(cfg.max_order_size_usd)
            if cfg.max_order_size_usd is not None
            else self.current_equity * cfg.max_order_size_pct
        )
        if estimated_cost > max_order_size:
            logger.warning(f"RISK REJECT: Order Size ${estimated_cost:.2f} > Limit ${max_order_size:.2f}")
            return False

        # Calculate projected signed exposure
        projected_signed, effective_signed = self._calculate_projected_exposure(ticker, estimated_cost, side, price)

        # 4. Shorting Permission Check
        if not self._check_shorting(cfg, projected_signed, effective_signed):
            return False

        # 5. Max Positions Check
        if not self._check_max_positions(cfg, ticker):
            return False

        # 6. Max Ticker Exposure Check
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
        """
        Returns True if the options order is safe to execute.
        Validates both standard order checks AND Greeks limits.

        Args:
            ticker: Underlying or option symbol
            quantity: Number of contracts
            price: Option premium per contract
            side: 'buy' or 'sell'
            delta: Position delta (contracts * delta * 100)
            gamma: Position gamma (contracts * gamma * 100)
            vega: Position vega (contracts * vega * 100)
            timestamp: Order timestamp
            risk_override: Optional override settings
        """
        cfg = risk_override if risk_override else self.config

        # Run standard order checks first
        if not self.check_order(ticker, quantity, price, side, timestamp, risk_override):
            return False

        # Then check Greeks limits (pass all Greeks for comprehensive check)
        return self._check_greeks_limits(cfg, ticker, delta, gamma, vega)

    def _check_time_bans(self, cfg: RiskSettings, timestamp: datetime | None = None) -> bool:
        if not cfg.time_of_day_bans:
            return True

        if timestamp is None:
            timestamp = datetime.now(UTC)

        # Use exchange_calendars for robust DST/Holiday handling
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
    ) -> tuple[float, float, float]:
        pending_exposure = 0.0
        for _, p_val in self.pending_orders.items():
            # Check p_val tuple (ticker, cost)
            # wait, pending_orders values are (ticker, signed_cost)
            # self.pending_orders: Dict[str, float] defined in init, but usage implies (ticker, cost) tuple?
            # Init says: self.pending_orders: Dict[str, float] = {}  # order_id -> estimated cost (signed)
            # But line 126 loop was: for oid, (p_ticker, p_cost) in self.pending_orders.items():
            # So the type hint in init might be wrong or the usage.
            # Looking at update_post_trade (line 416): self.pending_orders[order_id] = (ticker, signed_cost)
            # So it IS a tuple.
            p_ticker, p_cost = p_val
            if p_ticker == ticker:
                pending_exposure += p_cost

        signed_current_exposure = 0.0
        if ticker in self.positions:
            pos = self.positions[ticker]
            signed_current_exposure = pos["qty"] * price

        effective_signed = signed_current_exposure + pending_exposure
        cost_impact = estimated_cost if side.lower() == "buy" else -estimated_cost
        projected_signed = effective_signed + cost_impact

        return projected_signed, effective_signed

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
                return True  # Allowing Risk Reduction
            else:
                logger.warning(
                    f"RISK REJECT: Max Ticker Exposure ${limit:.2f} ({cfg.max_ticker_exposure_pct:.0%} of equity) Exceeded for {ticker} (Projected: ${abs_proj:.2f})"
                )
                return False
        return True

    def _check_greeks_limits(
        self,
        cfg: RiskSettings,
        ticker: str,
        position_delta: float = 0.0,
        position_gamma: float = 0.0,
        position_vega: float = 0.0,
    ) -> bool:
        """Check portfolio-level Greeks limits for options trades.

        All checks use PROJECTED values (current + new position) to prevent
        trades that would breach limits upon execution.
        """
        if not cfg.enable_greeks_checks:
            return True

        # Check per-position delta limit
        if abs(position_delta) > cfg.max_position_delta:
            logger.warning(
                f"RISK REJECT: Position Delta {position_delta:.1f} > Limit {cfg.max_position_delta:.1f} for {ticker}"
            )
            return False

        # Check per-position vega limit
        if abs(position_vega) > cfg.max_position_vega:
            logger.warning(
                f"RISK REJECT: Position Vega {position_vega:.1f} > Limit {cfg.max_position_vega:.1f} for {ticker}"
            )
            return False

        # Check portfolio-level delta limit (projected)
        projected_portfolio_delta = self.portfolio_delta + position_delta
        if abs(projected_portfolio_delta) > cfg.max_portfolio_delta:
            logger.warning(
                f"RISK REJECT: Portfolio Delta {projected_portfolio_delta:.1f} > Limit {cfg.max_portfolio_delta:.1f}"
            )
            return False

        # Check portfolio-level gamma limit (projected - FIX for gamma gap)
        projected_portfolio_gamma = self.portfolio_gamma + position_gamma
        if abs(projected_portfolio_gamma) > cfg.max_portfolio_gamma:
            logger.warning(
                f"RISK REJECT: Portfolio Gamma {projected_portfolio_gamma:.1f} > Limit {cfg.max_portfolio_gamma:.1f}"
            )
            return False

        # Check portfolio-level vega limit (projected)
        projected_portfolio_vega = self.portfolio_vega + position_vega
        if abs(projected_portfolio_vega) > cfg.max_portfolio_vega:
            logger.warning(
                f"RISK REJECT: Portfolio Vega {projected_portfolio_vega:.1f} > Limit {cfg.max_portfolio_vega:.1f}"
            )
            return False

        return True

    def update_position_greeks(
        self, ticker: str, delta: float, gamma: float, theta: float = 0.0, vega: float = 0.0
    ) -> None:
        """Update Greeks for a position and recalculate portfolio totals."""
        # Update position Greeks
        self.position_greeks[ticker] = {
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
        }

        # Recalculate portfolio totals
        self.portfolio_delta = sum(g["delta"] for g in self.position_greeks.values())
        self.portfolio_gamma = sum(g["gamma"] for g in self.position_greeks.values())
        self.portfolio_vega = sum(g["vega"] for g in self.position_greeks.values())

        logger.info(
            f"Greeks Updated for {ticker}: delta={delta:.2f}, gamma={gamma:.4f}, vega={vega:.4f} | "
            f"Portfolio: delta={self.portfolio_delta:.2f}, gamma={self.portfolio_gamma:.4f}, vega={self.portfolio_vega:.4f}"
        )

    def clear_position_greeks(self, ticker: str) -> None:
        """Clear Greeks for a closed position."""
        if ticker in self.position_greeks:
            del self.position_greeks[ticker]
            # Recalculate portfolio totals
            self.portfolio_delta = sum(g["delta"] for g in self.position_greeks.values())
            self.portfolio_gamma = sum(g["gamma"] for g in self.position_greeks.values())
            self.portfolio_vega = sum(g["vega"] for g in self.position_greeks.values())

    def check_sector_exposure(
        self, sector: str, additional_exposure: float = 0.0, risk_override: RiskSettings | None = None
    ) -> bool:
        """
        Check if a new position would breach sector concentration limits.

        Args:
            sector: Sector name (e.g., 'Technology', 'Healthcare')
            additional_exposure: USD exposure of the proposed new position
            risk_override: Optional custom risk settings

        Returns:
            True if sector exposure is within limits, False if it would breach
        """
        cfg = risk_override if risk_override else self.config

        if not cfg.enable_sector_checks:
            return True

        if self.current_equity <= 0:
            return True

        current_sector_exposure = self.sector_exposures.get(sector, 0.0)
        projected_exposure = current_sector_exposure + additional_exposure
        exposure_pct = projected_exposure / self.current_equity

        if exposure_pct > cfg.max_sector_exposure_pct:
            logger.warning(
                f"RISK REJECT: Sector {sector} exposure {exposure_pct:.1%} > limit {cfg.max_sector_exposure_pct:.1%}"
            )
            return False

        return True

    def update_sector_exposure(self, sector: str, exposure_change: float) -> None:
        """
        Update sector exposure after a trade.

        Args:
            sector: Sector name
            exposure_change: USD exposure change (+/- for buy/sell)
        """
        if not sector:
            return

        current = self.sector_exposures.get(sector, 0.0)
        new_exposure = max(0.0, current + exposure_change)

        if new_exposure > 0:
            self.sector_exposures[sector] = new_exposure
        elif sector in self.sector_exposures:
            del self.sector_exposures[sector]

        logger.info(
            f"Sector exposure updated: {sector} {current:.2f} -> {new_exposure:.2f}",
            extra={"event": "sector_exposure_update", "sector": sector, "exposure": new_exposure},
        )

    def get_sector_exposure_pct(self, sector: str) -> float:
        """Get sector exposure as percentage of portfolio."""
        if self.current_equity <= 0:
            return 0.0
        return self.sector_exposures.get(sector, 0.0) / self.current_equity

    def check_zero_dte_winddown(
        self, dte: int, timestamp: datetime | None = None, risk_override: RiskSettings | None = None
    ) -> tuple[bool, str]:
        """
        Check if a 0DTE trade is allowed based on time-of-day wind-down rules.

        Args:
            dte: Days to expiration (0 for same-day expiry)
            timestamp: Trade timestamp (defaults to now ET)
            risk_override: Optional custom risk settings

        Returns:
            Tuple of (allowed, reason)
        """
        cfg = risk_override if risk_override else self.config

        # Only applies to 0DTE trades
        if dte != 0:
            return (True, "Not 0DTE")

        if not cfg.enable_zero_dte_winddown:
            return (True, "Wind-down disabled")

        # Get current time in ET
        if timestamp is None:
            from zoneinfo import ZoneInfo

            timestamp = datetime.now(ZoneInfo("America/New_York"))
        elif timestamp.tzinfo is None:
            from zoneinfo import ZoneInfo

            timestamp = timestamp.replace(tzinfo=ZoneInfo("America/New_York"))

        # Market close is 4:00 PM ET
        market_close = timestamp.replace(hour=16, minute=0, second=0, microsecond=0)
        minutes_to_close = (market_close - timestamp).total_seconds() / 60

        # Check hard cutoff
        if minutes_to_close <= cfg.zero_dte_cutoff_minutes:
            logger.warning(
                f"RISK REJECT: 0DTE blocked - only {minutes_to_close:.0f} min to close "
                f"(cutoff: {cfg.zero_dte_cutoff_minutes} min)"
            )
            return (False, f"0DTE cutoff: {minutes_to_close:.0f} min to close")

        # Check if in reduced size window
        if minutes_to_close <= cfg.zero_dte_reduce_size_after_minutes:
            logger.info(
                f"0DTE size reduction active: {minutes_to_close:.0f} min to close "
                f"(reduce after: {cfg.zero_dte_reduce_size_after_minutes} min)"
            )
            return (True, f"Reduce size: {cfg.zero_dte_reduced_size_pct:.0%}")

        return (True, "Normal trading")

    def get_zero_dte_size_multiplier(
        self, dte: int, timestamp: datetime | None = None, risk_override: RiskSettings | None = None
    ) -> float:
        """
        Get size multiplier for 0DTE trades based on time-of-day.

        Args:
            dte: Days to expiration
            timestamp: Trade timestamp (defaults to now ET)
            risk_override: Optional custom risk settings

        Returns:
            Multiplier (1.0 for full size, <1.0 for reduced)
        """
        cfg = risk_override if risk_override else self.config

        if dte != 0 or not cfg.enable_zero_dte_winddown:
            return 1.0

        # Get current time in ET
        if timestamp is None:
            from zoneinfo import ZoneInfo

            timestamp = datetime.now(ZoneInfo("America/New_York"))
        elif timestamp.tzinfo is None:
            from zoneinfo import ZoneInfo

            timestamp = timestamp.replace(tzinfo=ZoneInfo("America/New_York"))

        market_close = timestamp.replace(hour=16, minute=0, second=0, microsecond=0)
        minutes_to_close = (market_close - timestamp).total_seconds() / 60

        if minutes_to_close <= cfg.zero_dte_cutoff_minutes:
            return 0.0  # Blocked entirely

        if minutes_to_close <= cfg.zero_dte_reduce_size_after_minutes:
            return cfg.zero_dte_reduced_size_pct

        return 1.0

    def calculate_size(
        self, entry_price: float, stop_loss_pct: float | None = None, account_equity: float | None = None
    ) -> float:
        """
        Calculates position size based on risk per trade.
        Caps at max_order_size_pct of account equity.

        Note: For correlation-aware sizing, use calculate_size_with_correlation().
        """
        if entry_price <= 0:
            return 0.0

        equity = account_equity if account_equity is not None else self.current_equity
        sl_pct = stop_loss_pct if stop_loss_pct is not None else self.config.default_stop_loss_pct

        risk_amt = equity * self.config.risk_per_trade_pct
        stop_distance = entry_price * sl_pct

        if stop_distance <= 0:
            return 0.0

        qty = math.floor(risk_amt / stop_distance)

        # Cap by Max Order Size (% of equity)
        max_order_value = equity * self.config.max_order_size_pct
        max_order_qty = math.floor(max_order_value / entry_price)
        qty = min(qty, max_order_qty)

        # Cap by Max Ticker Exposure (% of equity)
        max_exposure = equity * self.config.max_ticker_exposure_pct
        exposure_cap_qty = math.floor(max_exposure / entry_price)
        qty = min(qty, exposure_cap_qty)

        return float(qty)

    async def calculate_size_with_correlation(
        self,
        ticker: str,
        entry_price: float,
        stop_loss_pct: float | None = None,
        account_equity: float | None = None,
    ) -> float:
        """
        Calculates position size with correlation adjustment.

        First calculates base size using standard risk-per-trade,
        then applies correlation penalty if new ticker is highly
        correlated with existing holdings.

        Args:
            ticker: Symbol to size
            entry_price: Entry price per share
            stop_loss_pct: Optional stop loss percentage
            account_equity: Optional account equity override

        Returns:
            Position size in shares (may be reduced from base size)
        """
        # Get base size
        base_qty = self.calculate_size(entry_price, stop_loss_pct, account_equity)

        if base_qty <= 0:
            return 0.0

        # Apply correlation adjustment if enabled and adjuster available
        if not self.config.correlation_size_scaling:
            return base_qty

        if not hasattr(self, "_correlation_adjuster") or self._correlation_adjuster is None:
            return base_qty

        # Get existing position tickers
        existing_tickers = [t for t, p in self.positions.items() if p.get("qty", 0) != 0]

        if not existing_tickers:
            return base_qty

        # Get correlation-adjusted multiplier
        multiplier = await self._correlation_adjuster.get_size_multiplier(ticker, existing_tickers, self.config)

        adjusted_qty = max(1.0, math.floor(base_qty * multiplier))

        if adjusted_qty < base_qty:
            logger.info(
                f"Correlation sizing for {ticker}: {base_qty:.0f} -> {adjusted_qty:.0f} shares (x{multiplier:.2f})",
                extra={
                    "event": "correlation_size_applied",
                    "ticker": ticker,
                    "base": base_qty,
                    "adjusted": adjusted_qty,
                },
            )

        return float(adjusted_qty)

    def set_correlation_adjuster(self, adjuster: "CorrelationAdjuster") -> None:
        """
        Inject correlation adjuster for size scaling.

        Args:
            adjuster: CorrelationAdjuster instance with market connector
        """
        self._correlation_adjuster = adjuster
        logger.info("Correlation adjuster configured for RiskManager")

    @db_retry
    async def upsert_position(self, position: "Position") -> None:
        """
        Persist position to DB.
        """

        async def save_position(session: Any) -> None:
            from datetime import datetime

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
        """
        Loads risk state from DB (if exists) to survive restarts.
        """
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

            # If we restart in a breached drawdown state, trip the global circuit breaker immediately.
            await self._evaluate_drawdown_kill_switch()

        except Exception as e:
            logger.error(f"Failed to load Risk State: {e}")

    async def evaluate_drawdown_kill_switch(self) -> None:
        """
        Public wrapper for the drawdown kill switch evaluation.
        """
        await self._evaluate_drawdown_kill_switch()

    @db_retry
    async def _save_state(self) -> None:
        """
        Persist current risk state to DB.
        """

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
            state.starting_equity = getattr(self, "starting_equity", self.current_equity)
            state.peak_equity = getattr(self, "peak_equity", self.current_equity)
            state.open_positions_count = self.open_positions

        await db_write(save_risk_state)
        logger.info("Risk state persisted to DB")
        # Metrics: track risk state
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

    async def process_fill(
        self,
        ticker: str,
        qty: float,
        price: float,
        side: str,
        fill_id: str,
        expected_price: float | None = None,
    ) -> None:
        """
        Updates authoritative risk state based on actual broker fills.
        Calculates Realized PnL using robust signed arithmetic.
        Idempotent: Checks fill_id against in-memory history.

        Args:
            ticker: Symbol filled
            qty: Quantity filled
            price: Fill price
            side: 'buy' or 'sell'
            fill_id: Unique fill identifier (for idempotency)
            expected_price: Expected price for slippage calculation
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

        # Standardize: side='buy' -> positive qty effect, side='sell'/'short' -> negative qty effect
        sign = 1 if side.lower() == "buy" else -1
        signed_fill_qty = abs(qty) * sign

        # Initialize trackers if missing
        if not hasattr(self, "positions"):
            self.positions = {}  # ticker -> {'qty': float, 'avg_entry': float}

        current_pos = self.positions.get(ticker, {"qty": 0.0, "avg_entry": 0.0})
        old_qty = current_pos["qty"]
        old_entry = current_pos["avg_entry"]

        # New Position Qty
        new_qty = old_qty + signed_fill_qty

        realized_pnl = 0.0

        # Check if we are reducing or closing
        # Reducing happens if signs are different
        is_closing = (old_qty > 0 and signed_fill_qty < 0) or (old_qty < 0 and signed_fill_qty > 0)

        if is_closing:
            # MAGNITUDE of closure
            qty_closing = min(abs(old_qty), abs(signed_fill_qty))

            # PnL Calculation
            # If Long (old_qty > 0), PnL = (price - old_entry) * qty_closing
            # If Short (old_qty < 0), PnL = (old_entry - price) * qty_closing

            if old_qty > 0:
                pnl = (price - old_entry) * qty_closing
            else:
                pnl = (old_entry - price) * qty_closing

            realized_pnl = pnl

            # Update Equity
            self.current_equity += realized_pnl

            # Update Daily Loss (Profit reduces daily loss, Loss increases it)
            self.current_daily_loss -= realized_pnl
            if self.current_daily_loss < 0:
                self.current_daily_loss = 0.0

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
            # Building position (or fresh entry)
            # Weighted Average Price = (OldVal + NewVal) / NewQty
            total_val = (old_qty * old_entry) + (signed_fill_qty * price)
            new_avg = total_val / new_qty if new_qty != 0 else 0.0
            self.positions[ticker] = {"qty": new_qty, "avg_entry": new_avg}

        elif abs(signed_fill_qty) > abs(old_qty):
            # FLIP detected (Closed full old position, opened new opposite position)
            # Remaining qty is the new position
            # New entry is the fill price
            self.positions[ticker] = {"qty": new_qty, "avg_entry": price}

        else:
            # Just reduced existing position. Avg Entry stays same.
            if math.isclose(new_qty, 0, abs_tol=1e-9):
                self.positions[ticker] = {"qty": 0.0, "avg_entry": 0.0}
            else:
                self.positions[ticker] = {"qty": new_qty, "avg_entry": old_entry}

        # Cleanup zero positions
        if math.isclose(new_qty, 0, abs_tol=1e-9):
            self.positions[ticker] = {"qty": 0.0, "avg_entry": 0.0}

        # Update Ticker Exposure (Authoritative USD Value)
        # We track exposure as Market Value (Qty * Price) - Absolute for sizing consistency
        self.ticker_exposures[ticker] = abs(new_qty * price)

        # Update Open Positions Count
        count = 0
        for _t, p in self.positions.items():
            if not math.isclose(p["qty"], 0, abs_tol=1e-9):
                count += 1
        self.open_positions = count

        await self._save_state()

        # Metrics: track ticker exposure
        if _metrics and hasattr(_metrics, "risk_exposure"):
            _metrics.risk_exposure.labels(ticker=ticker).set(abs(new_qty * price))

    async def update_post_trade(
        self, ticker: str, qty: float, price: float, side: str, order_id: str | None = None
    ) -> None:
        """
        Updates internal risk state immediately after an order is sent (Optimistic).
        """
        # Kept async to match interface if needed, but if unused, we can make it sync.
        # However, call sites use await?
        # Line 272 in ExecutionEngine: await self.risk_manager.update_post_trade(...)
        # So we MUST keep it async OR change call site.
        # To satisfy linter, we can add a dummy await or just accept the warning?
        # Or better -> simple await asyncio.sleep(0) to yield?
        # Or change call site.
        # Changing call site is better.
        if not order_id:
            order_id = f"pending_{datetime.now(UTC).timestamp()}"

        cost = qty * price

        # Determine signed cost for pending tracking
        signed_cost = cost if side.lower() == "buy" else -cost

        # Track pending exposure
        self.pending_orders[order_id] = (ticker, signed_cost)

    def remove_pending_order(self, order_id: str) -> None:
        """
        Removes a pending order from risk tracking (e.g. after Fill or Cancel).
        """
        if order_id in self.pending_orders:
            del self.pending_orders[order_id]

    def update_metrics(
        self, realized_pnl: float = 0.0, open_positions_count: int | None = None, open_pnl: float = 0.0
    ) -> None:
        """
        Legacy sync method.
        """
        pass

    def sync_with_broker(self, connector: Any) -> None:
        """
        Syncs risk state with the broker to handle restarts.
        """
        if not connector:
            return

        try:
            logger.info("Syncing RiskManager with Broker...")

            # 1. Sync Open Positions & Exposures
            positions = connector.client.get_all_positions()
            self.open_positions = len(positions)

            # Reset Idempotency Cache (State is fresh from broker)
            self.processed_fill_ids.clear()

            self.ticker_exposures = {}

            # Rebuild self.positions from broker data
            self.positions = {}

            for p in positions:
                symbol = p.symbol
                market_value = float(p.market_value)
                qty = float(p.qty)
                avg_entry = float(p.avg_entry_price)

                self.positions[symbol] = {"qty": qty, "avg_entry": avg_entry}

                # Check signed exposure logic compatibility
                # market_value is abs by default in some APIs, let's just use our loop logic if needed
                # But here we just set ticker_exposures (Market Value)
                self.ticker_exposures[symbol] = abs(market_value)

            # 2. Sync Pending Orders (CRITICAL RESTART FIX)
            # We must know about open orders to avoid double-execution
            try:
                from alpaca.trading.enums import QueryOrderStatus
                from alpaca.trading.requests import GetOrdersRequest

                req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
                open_orders = connector.client.get_orders(filter=req)

                logger.info(f"Found {len(open_orders)} open orders on restart.")

                self.pending_orders = {}
                for o in open_orders:
                    # We need to estimate cost for risk tracking
                    # If limit order, use limit price. If market, use current price?
                    # For safety, let's assume limit price or 0 if missing (market orders risky anyway on restart)

                    # Alpaca Order model: symbol, qty, side, type, limit_price, client_order_id
                    symbol = o.symbol
                    qty = float(o.qty or 0.0)
                    side_str = str(o.side.value) if hasattr(o.side, "value") else str(o.side)
                    limit_price = float(o.limit_price) if o.limit_price else 0.0
                    order_id = str(o.client_order_id or o.id)  # Use client_order_id preferred

                    # If limit price is missing (Market Order?), we might underestimate.
                    # Ideally we fetch current price. For now, log warning if 0.
                    if limit_price <= 0 and o.order_type == "limit":
                        logger.warning(f"Open Limit Order {o.id} has no price, skipping risk track.")
                        continue

                    cost = qty * limit_price

                    # Robust side check
                    is_buy = side_str.lower() == "buy"
                    signed_cost = cost if is_buy else -cost
                    self.pending_orders[order_id] = (symbol, signed_cost)

            except Exception as e:
                logger.error(f"Failed to sync pending orders: {e}")

            # 2. Sync Daily PnL & Equity
            account = connector.client.get_account()
            equity = float(account.equity)
            last_equity = float(account.last_equity)

            self.current_equity = equity
            self.starting_equity = last_equity

            pnl = equity - last_equity

            if pnl < 0:
                self.current_daily_loss = abs(pnl)
            else:
                self.current_daily_loss = 0.0

            logger.info(
                f"Risk Synced: OpenPositions={self.open_positions}, PendingOrders={len(self.pending_orders)}, DailyLoss={self.current_daily_loss}"
            )

        except Exception as e:
            logger.error(f"Failed to sync RiskManager with broker: {e}")
