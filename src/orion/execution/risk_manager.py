import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Dict

from orion.config import RiskSettings, risk_settings

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Enforces pre-trade risk controls to prevent catastrophic loss or specific rule violations.
    """

    def __init__(self, config: RiskSettings = None):
        if config is None:
            self.config = risk_settings
        else:
            self.config = config

        self.current_daily_loss = 0.0
        self.open_positions = 0
        self.ticker_exposures: Dict[str, float] = {}  # ticker -> usd value
        self.pending_orders: Dict[str, float] = {}  # order_id -> estimated cost (signed)
        self.current_equity = 100000.0  # Default fallback, should be synced
        self.starting_equity = self.current_equity
        self.peak_equity = self.current_equity

        # Track full position details (qty, avg_entry)
        self.positions: Dict[str, Dict[str, float]] = {}

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
        timestamp: datetime = None,
        risk_override: RiskSettings = None,
    ) -> bool:
        """
        Returns True if the order is safe to execute, False otherwise.
        """
        # Use override if provided, else global config
        cfg = risk_override if risk_override else self.config

        estimated_cost = quantity * price

        # 0. Global Time Checks
        if cfg.time_of_day_bans:
            if timestamp is None:
                timestamp = datetime.now(timezone.utc)

            # Use exchange_calendars for robust DST/Holiday handling
            if not hasattr(self, "calendar"):
                try:
                    import exchange_calendars as xcals

                    self.calendar = xcals.get_calendar("XNYS")
                except ImportError:
                    logger.warning("exchange_calendars not installed, skipping advanced time checks")
                    self.calendar = None

            if self.calendar:
                try:
                    # Find the session that this timestamp belongs to.
                    date = timestamp.date()
                    schedule = self.calendar.schedule(start_date=date, end_date=date)

                    if schedule.empty:
                        # Fail Closed if market schedule is undefined
                        logger.warning(f"RISK REJECT: No market schedule found for {date} (Holiday/Closed).")
                        return False
                    else:
                        row = schedule.iloc[0]
                        market_open = row.market_open  # UTC
                        market_close = row.market_close  # UTC

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

        # 1. Daily Loss Limit
        if self.current_daily_loss >= cfg.max_daily_loss:
            logger.error(
                f"RISK REJECT: Daily Loss Limit {cfg.max_daily_loss} Hit (Current Loss: {self.current_daily_loss})"
            )
            return False

        # 1b. Drawdown Kill Switch
        if self._drawdown_breached(cfg):
            dd = self._current_drawdown_pct() * 100.0
            logger.critical(f"RISK REJECT: Max Drawdown {cfg.max_drawdown_pct:.2%} Hit (Current Drawdown: {dd:.2f}%)")
            return False

        # 2. Max Order Size
        if estimated_cost > cfg.max_order_size_usd:
            logger.warning(f"RISK REJECT: Order Size {estimated_cost} > Limit {cfg.max_order_size_usd}")
            return False

        # 3. Projected Exposure Check (Signed)
        pending_exposure = 0.0
        for oid, (p_ticker, p_cost) in self.pending_orders.items():
            if p_ticker == ticker:
                pending_exposure += p_cost

        current_exposure = self.ticker_exposures.get(ticker, 0.0)

        # For signed logic, we rely on 'positions' dict if available, but ticker_exposures is USD value (unsigned usually?)
        # Let's standardize ticker_exposures to be Market Value (unsigned).
        # But we need Signed checks for Shorting.
        # We need to construct Signed Current Exposure.
        # Use self.positions if available
        signed_current_exposure = 0.0
        if ticker in self.positions:
            pos = self.positions[ticker]
            # approximate with current price
            signed_current_exposure = pos["qty"] * price

        # If positions not tracking yet (legacy), fallback?
        # Let's assume initialized.

        # Effective including pending
        effective_signed = signed_current_exposure + pending_exposure

        # Project outcome
        cost_impact = estimated_cost if side.lower() == "buy" else -estimated_cost
        projected_signed = effective_signed + cost_impact

        # 4. Shorting Permission Check
        is_moving_short = projected_signed < effective_signed
        if projected_signed < 0 and is_moving_short:
            if not cfg.enable_shorting:
                # Exception: Closing a long is moving short, but checks against projected < 0
                # If we projected < 0, we flip to short.
                logger.error(f"RISK REJECT: Shorting Disabled. Cannot move to {projected_signed}")
                return False

        # 5. Max Positions Check
        # Count based on projected non-zero
        # Approximate by checking if we are opening a new ticker

        # If we have 0 qty and 0 pending, and we add check -> +1
        # This implementation requires iterating all positions + pending is heavy.
        # Simplified:
        if ticker not in self.positions or math.isclose(self.positions[ticker]["qty"], 0):
            # Opening new?
            # Check if we have pending for this ticker already
            has_pending = any(p_ticker == ticker for (p_ticker, _) in self.pending_orders.values())
            if not has_pending:
                # Truly new?
                if self.open_positions >= cfg.max_positions:
                    logger.warning(f"RISK REJECT: Max Positions {cfg.max_positions} Reached")
                    return False

        # 6. Max Ticker Exposure Check (Risk Reduction Exception)
        abs_proj = abs(projected_signed)
        abs_curr = abs(effective_signed)
        limit = cfg.max_ticker_exposure_usd

        if abs_proj > limit:
            if abs_proj < abs_curr:
                # Allowing Risk Reduction
                pass
            else:
                logger.warning(
                    f"RISK REJECT: Max Ticker Exposure {limit} Exceeded for {ticker} (Projected: {abs_proj})"
                )
                return False

        return True

    def calculate_size(self, entry_price: float, stop_loss_pct: float = None, account_equity: float = None) -> float:
        """
        Calculates position size based on risk per trade.
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

        # Cap by Max Ticker Exposure
        exposure_cap_qty = math.floor(self.config.max_ticker_exposure_usd / entry_price)
        qty = min(qty, exposure_cap_qty)

        return float(qty)

    async def initialize(self):
        """
        Loads risk state from DB (if exists) to survive restarts.
        """
        try:
            from orion.storage.db import async_session_factory
            from orion.storage.models_risk import RiskState
            from sqlalchemy import select

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

    async def _save_state(self):
        """
        Persists current risk state to DB.
        """
        try:
            from orion.storage.db import async_session_factory
            from orion.storage.models_risk import RiskState
            from sqlalchemy import select

            async with async_session_factory() as session:
                stmt = select(RiskState).where(RiskState.id == "global_risk_v1")
                result = await session.execute(stmt)
                state = result.scalars().first()

                if not state:
                    state = RiskState(id="global_risk_v1")
                    session.add(state)

                state.updated_at_utc = datetime.now(timezone.utc)
                state.current_daily_loss = self.current_daily_loss
                state.current_equity = self.current_equity
                state.starting_equity = getattr(self, "starting_equity", self.current_equity)
                state.peak_equity = getattr(self, "peak_equity", self.current_equity)
                state.open_positions_count = self.open_positions

                await session.commit()
        except Exception as e:
            logger.error(f"Failed to save Risk State: {e}")

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

    async def process_fill(self, ticker: str, qty: float, price: float, side: str):
        """
        Updates authoritative risk state based on actual broker fills.
        Calculates Realized PnL using robust signed arithmetic.
        """
        fill_cost = abs(qty * price)
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
        for t, p in self.positions.items():
            if not math.isclose(p["qty"], 0, abs_tol=1e-9):
                count += 1
        self.open_positions = count

        await self._save_state()

    async def update_post_trade(self, ticker: str, qty: float, price: float, side: str, order_id: str = None):
        """
        Updates internal risk state immediately after an order is sent (Optimistic).
        """
        if not order_id:
            order_id = f"pending_{datetime.now(timezone.utc).timestamp()}"

        cost = qty * price

        # Determine signed cost for pending tracking
        signed_cost = cost if side.lower() == "buy" else -cost

        # Track pending exposure
        self.pending_orders[order_id] = (ticker, signed_cost)

    def remove_pending_order(self, order_id: str):
        """
        Removes a pending order from risk tracking (e.g. after Fill or Cancel).
        """
        if order_id in self.pending_orders:
            del self.pending_orders[order_id]

    def update_metrics(self, realized_pnl: float = 0.0, open_positions_count: int = None, open_pnl: float = 0.0):
        """
        Legacy sync method.
        """
        pass

    def sync_with_broker(self, connector):
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

            self.ticker_exposures = {}
            total_open_pnl = 0.0

            # Rebuild self.positions from broker data
            self.positions = {}

            for p in positions:
                symbol = p.symbol
                market_value = float(p.market_value)
                qty = float(p.qty)
                avg_entry = float(p.avg_entry_price)
                unrealized_pl = float(p.unrealized_pl)

                total_open_pnl += unrealized_pl

                self.positions[symbol] = {"qty": qty, "avg_entry": avg_entry}

                # Check signed exposure logic compatibility
                # market_value is abs by default in some APIs, let's just use our loop logic if needed
                # But here we just set ticker_exposures (Market Value)
                self.ticker_exposures[symbol] = abs(market_value)

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
                f"Risk Synced: OpenPositions={self.open_positions}, DailyLoss={self.current_daily_loss} (PnL={pnl}, Equity={self.current_equity})"
            )

        except Exception as e:
            logger.error(f"Failed to sync RiskManager with broker: {e}")
