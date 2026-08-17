"""Core RiskManager — enforces pre-trade risk controls using composition.

Delegates to focused sub-modules:
- GreeksTracker: options Greeks limits
- SectorTracker: sector concentration limits
- ZeroDteGuard: 0DTE time-of-day wind-down
- PositionSizer: risk-per-trade position sizing with correlation
"""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from orion.config import RiskSettings, risk_settings
from orion.core.enums import OrderSide
from orion.execution.attribution import is_occ_option_symbol
from orion.execution.risk.greeks import GreeksTracker
from orion.execution.risk.sector import SectorTracker
from orion.execution.risk.sizing import PositionSizer
from orion.execution.risk.zero_dte import ZeroDteGuard
from orion.shared.db_utils import db_write
from orion.shared.decorators import db_retry
from orion.shared.logger import setup_struct_logger

if TYPE_CHECKING:
    from orion.execution.correlation_adjuster import CorrelationAdjuster
    from orion.shared.metrics import Metrics

logger = setup_struct_logger(__name__)

# Dollars represented by 1.00 of an option's quoted price. Options are quoted
# per share but trade in 100-share contracts.
_OPTION_CONTRACT_MULTIPLIER = 100.0

# Accounting convention for the persisted `risk_state` money columns.
#   1 (or NULL) — option realized P&L recorded WITHOUT the contract multiplier,
#                 i.e. at 1/100th scale. Untrusted; migration required.
#   2           — option realized P&L recorded in true dollars.
# The version is stored ON the row (`RiskState.accounting_version`) so provenance
# travels with the data: an external flag could not prove which convention wrote
# a given row. Bump this whenever the meaning of those columns changes.
RISK_ACCOUNTING_VERSION = 2

_ET = ZoneInfo("America/New_York")


def _trading_date(now_utc: datetime) -> date:
    """Calendar date in America/New_York that ``now_utc`` falls on.

    The daily-loss limit is a per-session control, so its day boundary is the
    exchange's midnight rather than UTC's: 2026-08-17T03:30Z is still Sunday
    23:30 in New York and belongs to 2026-08-16.
    """
    return now_utc.astimezone(_ET).date()


def _contract_multiplier(symbol: str | None) -> float:
    """Dollars per 1.00 of quoted price: 100 for an OCC option, 1 for equity.

    Applies ONLY on the fill path (``process_fill``), which receives the OCC
    contract symbol and a raw per-share premium straight from the broker fill.
    Omitting it there understated realized PnL — and therefore
    ``current_daily_loss``, the kill-switch input — by 100x: a real $3,879 loss
    booked as $38.79.

    The order-admission path is denominated differently and must NOT use this:
    ``check_order`` / ``check_options_order`` / ``update_post_trade`` are called
    with the UNDERLYING ticker, and ``execution_engine`` pre-multiplies via
    ``notional = option_price * 100``. Applying the factor there would either
    double-count or silently no-op on the underlying symbol.

    ``jobs/reconcile_pnl.py`` applies the same factor for OCC symbols.
    """
    return _OPTION_CONTRACT_MULTIPLIER if is_occ_option_symbol(symbol) else 1.0


@dataclass(frozen=True)
class FillOutcome:
    """Result of ``RiskManager.process_fill``.

    ``is_closing`` is True when the fill reduced/flattened an existing
    position (so the caller can attribute ``realized_pnl`` to the trade
    journal). ``realized_pnl`` is 0.0 for opening fills.
    """

    realized_pnl: float = 0.0
    is_closing: bool = False


# Lazily resolved Prometheus metrics. The previous module-level init called
# the async Metrics.get_instance() without awaiting, so the gauges were
# permanently inert (2026-06-10 audit, confirmed by adversarial review).
# get_metrics_sync materialises the singleton synchronously on first use;
# failures return None so metrics can never break the risk path.
_metrics: "Metrics | None" = None


def _get_metrics() -> "Metrics | None":
    global _metrics
    if _metrics is None:
        try:
            from orion.shared.metrics import get_metrics_sync

            _metrics = get_metrics_sync()
        except ImportError:
            return None
    return _metrics


class RiskManager:
    """Enforces pre-trade risk controls to prevent catastrophic loss or specific rule violations."""

    def __init__(self, config: RiskSettings | None = None):
        self.config = config if config is not None else risk_settings

        self.current_daily_loss = 0.0
        # Trading date (America/New_York calendar day) that `current_daily_loss`
        # belongs to. A freshly-constructed manager owns today's zero figure;
        # `initialize()` replaces it with the persisted date and discards any
        # loss carried from an earlier session. Whenever it lags the current
        # trading date, the next admission check or fill rolls the figure to 0.
        self._daily_loss_date: date | None = _trading_date(self._now())
        self.open_positions = 0
        self.ticker_exposures: dict[str, float] = {}
        self.pending_orders: dict[str, tuple[str, float]] = {}
        self.current_equity = 100000.0
        self.starting_equity = self.current_equity
        self.peak_equity = self.current_equity
        # Tracks whether peak_equity has been seeded from a real source (DB
        # state on init, or the first Gateway-sync seed). Replaces the older
        # `peak_equity == 100000.0` magic-default check, which broke when a
        # legitimately-loaded peak just happened to equal the hardcoded
        # default and got silently overwritten on next sync.
        self._peak_equity_seeded = False
        # Same one-shot seed pattern for current_equity: the paper Alpaca
        # account is shared across 3Roses/Cerberus/Kairos/Orbit/WhaleHunter,
        # so Gateway-account equity reflects ALL systems' P&L. Overwriting
        # `current_equity` from that pool falsely trips Orion's drawdown
        # kill switch when other systems lose. After the first seed we
        # only mutate `current_equity` from Orion-attributed fills via
        # `update_post_fill` (line 590).
        self._equity_seeded = False

        # Fail-closed admission gate. Set by `initialize()` when a persisted
        # risk_state row exists but no operator baseline has been recorded at
        # the current RISK_ACCOUNTING_VERSION — i.e. the stored loss/equity may
        # be pre-multiplier (1/100th scale) and cannot arm the kill switch.
        # Defaults False so a directly-constructed manager (unit tests, fresh
        # process that never loads state) behaves exactly as before.
        self.baseline_unverified = False

        # Track full position details (qty, avg_entry)
        self.positions: dict[str, dict[str, float]] = {}

        # Projected position greeks stashed at order-submit time (share-
        # equivalent units), applied to portfolio greeks when the fill lands.
        self._intended_position_greeks: dict[str, dict[str, float]] = {}

        # Idempotency Tracking
        self.processed_fill_ids: set[str] = set()

        # In-memory registry of positions whose protective bracket order(s)
        # (stop-loss / take-profit) failed to place at entry. Keyed by
        # option_symbol. This is a *secondary* signal for operators and the
        # PositionMonitor's re-protection retry — NOT a durable record and
        # NOT a gate on order flow. A process restart loses this registry;
        # the durable record is the `position_unprotected` /
        # `position_partial_protection` flags hoisted onto each
        # StrategyDecision.execution_params at entry time.
        self.unprotected_positions: dict[str, dict[str, Any]] = {}

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

    # ── Daily-loss rollover ──────────────────────────────────────────────

    def _now(self) -> datetime:
        """Current UTC time. Single seam for everything that derives the trading date."""
        return datetime.now(UTC)

    @staticmethod
    def _fill_trading_date(filled_at: datetime | None) -> date | None:
        """Trading date a broker fill executed on, or None when no time is known.

        A naive timestamp is taken as UTC, matching how the fill ledger coerces
        Gateway timestamps.
        """
        if filled_at is None:
            return None
        if filled_at.tzinfo is None:
            filled_at = filled_at.replace(tzinfo=UTC)
        return _trading_date(filled_at)

    def _roll_daily_loss_if_new_day(self) -> bool:
        """Zero ``current_daily_loss`` when the trading date has advanced past
        the day the figure belongs to.

        The in-memory reset is immediate; the new date reaches the ``risk_state``
        row on the next ``_save_state``. Returns True when a rollover happened.
        A figure whose date is still None (a pre-column row whose last write was
        not today, see ``_infer_legacy_daily_loss_date``) has no day identity
        and rolls the same way — the accounting gate, not this figure, guards
        the legacy-scale hazard.
        """
        today = _trading_date(self._now())
        if self._daily_loss_date == today:
            return False

        discarded_loss = self.current_daily_loss
        discarded_date = self._daily_loss_date
        if discarded_date is None:
            message = (
                f"Daily loss rollover: persisted figure {discarded_loss} carries no trading date (legacy row); "
                f"starting {today.isoformat()} at 0.0"
            )
        else:
            message = (
                f"Daily loss rollover: discarding {discarded_loss} from {discarded_date.isoformat()}; "
                f"starting {today.isoformat()} at 0.0"
            )
        logger.info(
            message,
            extra={
                "event_type": "RISK_DAILY_LOSS_ROLLOVER",
                "discarded_loss": discarded_loss,
                "discarded_date": None if discarded_date is None else discarded_date.isoformat(),
                "trading_date": today.isoformat(),
                "legacy_row": discarded_date is None,
            },
        )
        self.current_daily_loss = 0.0
        self._daily_loss_date = today
        return True

    def _infer_legacy_daily_loss_date(self, state: Any) -> date | None:
        """Day identity for a ``risk_state`` row that predates ``daily_loss_date``.

        The column is added to the live database mid-life, so the row in place
        at that moment is NULL-dated even though its figure may have been
        written — and may already sit at the limit — this very session. Its
        last write time is the only evidence: a row last written today keeps
        its figure under today's date (the limit stays in force; at worst it
        also carries earlier sessions' losses until the next rollover, which
        only blocks more). A row last written on an earlier day, or never
        stamped, has no claim on today and returns None so the caller rolls it.
        """
        last_written = state.updated_at_utc
        if last_written is None:
            return None
        if last_written.tzinfo is None:
            last_written = last_written.replace(tzinfo=UTC)
        today = _trading_date(self._now())
        if _trading_date(last_written) != today:
            return None
        logger.info(
            f"Daily loss {self.current_daily_loss} carries no trading date but was last written today "
            f"({last_written.isoformat()}); retaining it for {today.isoformat()}",
            extra={
                "event_type": "RISK_DAILY_LOSS_DATE_INFERRED",
                "current_daily_loss": self.current_daily_loss,
                "updated_at_utc": last_written.isoformat(),
                "trading_date": today.isoformat(),
            },
        )
        return today

    # ── Equity baseline ──────────────────────────────────────────────────

    def seed_equity_baseline(self, gateway_equity: float) -> None:
        """Seed current/starting/peak equity ONCE from the Gateway account
        equity, capped to Orion's allocated slice (`config.allocated_equity`).

        The Alpaca paper account is shared across many systems, so Gateway
        reports the full pooled equity. Sizing (max premium/order %) must
        compute off Orion's slice, not the pool — uncapped seeding was the
        root of the 5/26 over-exposure. After the one-shot seed, equity moves
        only via Orion-attributed fills. `allocated_equity=None` disables the
        cap.
        """
        allocated = getattr(self.config, "allocated_equity", None)
        capped = min(gateway_equity, allocated) if allocated and allocated > 0 else gateway_equity
        if not self._equity_seeded:
            self.current_equity = capped
            self.starting_equity = capped
            self._equity_seeded = True
        if not self._peak_equity_seeded:
            self.peak_equity = capped
            self._peak_equity_seeded = True

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
        if self.baseline_unverified:
            logger.error(
                "RISK REJECT: risk baseline unverified — persisted loss/equity may be pre-multiplier. "
                "Record a verified baseline to re-enable order admission.",
                extra={"event_type": "RISK_REJECT_BASELINE_UNVERIFIED", "ticker": ticker},
            )
            return False

        cfg = self._resolve_config(risk_override)
        # `price` is caller-denominated: the options path passes
        # `notional = option_price * 100` (execution_engine) so this is already
        # USD. Do NOT apply _contract_multiplier here — it would double-count.
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
        self._roll_daily_loss_if_new_day()
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
            # `price` arrives already USD-denominated from check_order (see there).
            signed_current_exposure = pos["qty"] * price

        effective_signed = signed_current_exposure + pending_exposure
        cost_impact = estimated_cost if side.lower() == OrderSide.BUY else -estimated_cost
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
        max_exposure_usd = getattr(cfg, "max_ticker_exposure_usd", None)
        limit = (
            float(max_exposure_usd)
            if max_exposure_usd is not None
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

    def set_intended_position_greeks(
        self, ticker: str, delta: float, gamma: float, theta: float = 0.0, vega: float = 0.0
    ) -> None:
        """Record the projected position greeks for `ticker` at order-submit
        time (share-equivalent units) so the matching fill can update portfolio
        greeks. Overwrites any prior stash for the ticker."""
        self._intended_position_greeks[ticker] = {
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
        }

    def clear_intended_position_greeks(self, ticker: str) -> None:
        """Drop the stashed intended greeks for `ticker` (e.g. on submit failure)."""
        self._intended_position_greeks.pop(ticker, None)

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

    # ── Unprotected-position registry ────────────────────────────────────
    #
    # First-class (in-memory) knowledge that a position is missing one or
    # both protective bracket legs. The ExecutionEngine marks here when
    # bracket placement fully/partially fails; the PositionMonitor reads it
    # to prioritise re-protection and clears it on success. Purely
    # advisory: it never blocks order flow. Lost on restart (see note in
    # __init__ — the durable record lives in execution_params).

    def mark_unprotected(
        self,
        ticker: str,
        option_symbol: str,
        reason: str,
        missing_legs: list[str] | None = None,
    ) -> None:
        """Record that ``option_symbol`` is missing protective coverage.

        ``missing_legs`` records exactly which bracket legs failed so
        re-protection places only those (None = treat both as missing).
        """
        self.unprotected_positions[option_symbol] = {
            "ticker": ticker,
            "option_symbol": option_symbol,
            "reason": reason,
            "missing_legs": list(missing_legs) if missing_legs is not None else ["stop_loss", "take_profit"],
            "marked_at_utc": datetime.now(UTC),
        }
        logger.warning(
            "risk_position_marked_unprotected",
            ticker=ticker,
            option_symbol=option_symbol,
            reason=reason,
        )

    def clear_unprotected(self, option_symbol: str) -> None:
        """Drop a position from the unprotected registry (re-protection succeeded)."""
        if self.unprotected_positions.pop(option_symbol, None) is not None:
            logger.info("risk_position_unprotected_cleared", option_symbol=option_symbol)

    def get_unprotected(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot copy of the unprotected-position registry."""
        return dict(self.unprotected_positions)

    # ── Persistence ──────────────────────────────────────────────────────

    @db_retry
    async def initialize(self) -> None:
        """Loads risk state from DB (if exists) to survive restarts.

        Fails CLOSED on the admission gate: the gate is raised before the read
        and lowered only once the read proves the persisted state is safe. A
        transient DB failure here must not admit orders against a legacy ledger,
        so every exception path leaves the gate raised.
        """
        self.baseline_unverified = True
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
                    # The persisted figure belongs to the trading date stamped
                    # on the row; a row from an earlier session starts today at
                    # zero, while a mid-day restart keeps the running total.
                    self._daily_loss_date = state.daily_loss_date
                    if self._daily_loss_date is None:
                        self._daily_loss_date = self._infer_legacy_daily_loss_date(state)
                    self._roll_daily_loss_if_new_day()
                    self.current_equity = state.current_equity
                    self.starting_equity = state.starting_equity
                    self.peak_equity = getattr(state, "peak_equity", 0.0) or max(
                        self.current_equity, self.starting_equity
                    )
                    self.open_positions = state.open_positions_count
                    # Loaded peak from a real source — even if it happens to
                    # numerically equal the $100K default, downstream syncs
                    # must not overwrite it.
                    self._peak_equity_seeded = True
                    # Same: persisted current_equity is the Orion-only
                    # running total; treat it as already seeded so the
                    # Gateway sync doesn't clobber it with account-wide
                    # equity on the next poll.
                    self._equity_seeded = True
                    logger.info(f"Risk State Loaded: DailyLoss={self.current_daily_loss}")
                    # Provenance travels on the row: only a row stamped with the
                    # current convention may arm the kill switch.
                    row_version = getattr(state, "accounting_version", None)
                    self.baseline_unverified = row_version != RISK_ACCOUNTING_VERSION
                    if self.baseline_unverified:
                        logger.critical(
                            "Risk baseline UNVERIFIED — order admission blocked. Persisted risk_state carries "
                            f"accounting_version={row_version!r}, required {RISK_ACCOUNTING_VERSION}: its loss and "
                            "equity figures may be at 1/100th scale (option P&L recorded without the contract "
                            "multiplier), so the daily-loss and drawdown gates cannot be trusted. Record a "
                            "verified baseline to re-enable entries.",
                            extra={
                                "event_type": "RISK_BASELINE_UNVERIFIED",
                                "current_daily_loss": self.current_daily_loss,
                                "current_equity": self.current_equity,
                                "row_version": row_version,
                                "required_version": RISK_ACCOUNTING_VERSION,
                            },
                        )
                else:
                    # A fresh DB has no legacy figures to mis-scale; the first
                    # `_save_state` stamps the current version.
                    self.baseline_unverified = False
                    logger.info("No persisted Risk State found.")

            # Restore in-flight orders so the first cycle after restart
            # doesn't under-count exposure for orders submitted just before
            # the crash. Stale rows (>TTL) are dropped on load.
            await self._load_pending_orders()

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

            if self.baseline_unverified and state.accounting_version == RISK_ACCOUNTING_VERSION:
                # Another process recorded a verified baseline while this one
                # was still holding the legacy figures it loaded at startup.
                # Writing them now would leave the row carrying stale
                # 1/100th-scale money under the new version stamp, and the next
                # restart would trust it. Discard this snapshot entirely; the
                # operator must restart the service to pick up the baseline.
                logger.critical(
                    "Discarding risk-state save: a verified baseline was recorded by another process while "
                    "this one held unverified legacy figures. RESTART THIS SERVICE to load the new baseline.",
                    extra={
                        "event_type": "RISK_STATE_SAVE_DISCARDED_STALE",
                        "in_memory_daily_loss": self.current_daily_loss,
                        "in_memory_equity": self.current_equity,
                        "row_version": state.accounting_version,
                    },
                )
                return

            state.updated_at_utc = datetime.now(UTC)
            state.current_daily_loss = self.current_daily_loss
            state.current_equity = self.current_equity
            state.starting_equity = self.starting_equity
            state.peak_equity = self.peak_equity
            state.open_positions_count = self.open_positions
            # Stamp provenance ONLY when these figures are genuinely on the
            # current convention. While the gate is raised the in-memory totals
            # still carry the legacy 1/100th-scale values loaded from this row;
            # stamping them would forge provenance and silently lower the gate
            # on the next restart. Leave the existing version (or NULL) alone.
            # The trading date follows the same rule: it gives the loss figure
            # a day identity, and legacy figures have none worth asserting.
            if not self.baseline_unverified:
                state.accounting_version = RISK_ACCOUNTING_VERSION
                state.daily_loss_date = self._daily_loss_date

        await db_write(save_risk_state)
        logger.info("Risk state persisted to DB")
        metrics = _get_metrics()
        if metrics is not None:
            metrics.risk_equity.set(self.current_equity)
            metrics.risk_daily_loss.set(self.current_daily_loss)
            metrics.risk_open_positions.set(self.open_positions)

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
        filled_at: datetime | None = None,
    ) -> FillOutcome:
        """Updates authoritative risk state based on actual broker fills.
        Calculates Realized PnL using robust signed arithmetic.
        Idempotent: Checks fill_id against in-memory history.

        ``filled_at`` is the broker's execution time. Realized P&L is booked
        into ``current_daily_loss`` only when the fill belongs to the current
        trading session: a fill from an earlier session that is only being
        processed now (recovered after a restart or a poll miss) still moves
        equity and positions, but its session's budget is closed, so it neither
        buys headroom under today's limit nor consumes it. Without a timestamp
        the fill is booked into the current session.

        Returns a ``FillOutcome`` so the caller can persist realized PnL on a
        closing fill. A duplicate (already-processed) fill returns a neutral
        outcome.
        """
        # A fill on a new trading day must accumulate into a fresh figure, not
        # on top of yesterday's; the `_save_state` below persists the new date.
        self._roll_daily_loss_if_new_day()

        if fill_id in self.processed_fill_ids:
            logger.warning(f"Fill {fill_id} already processed by RiskManager. Skipping.")
            return FillOutcome()

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
            metrics = _get_metrics()
            if metrics is not None and hasattr(metrics, "slippage_bps"):
                metrics.slippage_bps.labels(ticker=ticker, side=side).observe(slippage_bps)

        sign = 1 if side.lower() == OrderSide.BUY else -1
        signed_fill_qty = abs(qty) * sign

        current_pos = self.positions.get(ticker, {"qty": 0.0, "avg_entry": 0.0})
        old_qty = current_pos["qty"]
        old_entry = current_pos["avg_entry"]

        new_qty = old_qty + signed_fill_qty

        realized_pnl = 0.0

        is_closing = (old_qty > 0 and signed_fill_qty < 0) or (old_qty < 0 and signed_fill_qty > 0)

        if is_closing:
            # Defensive guard against out-of-order partial-fill delivery. Under
            # in-order delivery a closing fill can never exceed the known
            # position. If it does (e.g. a later partial arrives before an
            # earlier one), computing PnL off the oversized fill would
            # mis-realize PnL into `current_daily_loss` — the kill-switch input.
            # Clamp to the known position size (which `min(...)` below already
            # does for the normal path) and log loudly so the reorder is
            # visible. The normal-path math is unchanged.
            if abs(signed_fill_qty) > abs(old_qty):
                logger.error(
                    f"Closing fill exceeds known position for {ticker}: "
                    f"fill_qty={signed_fill_qty} old_qty={old_qty} (clamping PnL to old_qty)",
                    extra={
                        "event_type": "closing_fill_exceeds_position",
                        "ticker": ticker,
                        "fill_qty": signed_fill_qty,
                        "old_qty": old_qty,
                        "client_order_id": fill_id,
                    },
                )

            qty_closing = min(abs(old_qty), abs(signed_fill_qty))

            multiplier = _contract_multiplier(ticker)
            if old_qty > 0:
                pnl = (price - old_entry) * qty_closing * multiplier
            else:
                pnl = (old_entry - price) * qty_closing * multiplier

            realized_pnl = pnl

            self.current_equity += realized_pnl
            fill_trading_date = self._fill_trading_date(filled_at)
            if (
                fill_trading_date is not None
                and self._daily_loss_date is not None
                and (fill_trading_date < self._daily_loss_date)
            ):
                logger.warning(
                    f"Late fill for {ticker}: executed {fill_trading_date.isoformat()}, processed "
                    f"{self._daily_loss_date.isoformat()}. Realized PnL=${realized_pnl:.2f} belongs to that closed "
                    f"session and is NOT booked into today's daily loss (still ${self.current_daily_loss:.2f}).",
                    extra={
                        "event_type": "RISK_LATE_FILL_PRIOR_SESSION",
                        "ticker": ticker,
                        "fill_id": fill_id,
                        "realized_pnl": realized_pnl,
                        "fill_trading_date": fill_trading_date.isoformat(),
                        "trading_date": self._daily_loss_date.isoformat(),
                        "daily_loss": self.current_daily_loss,
                    },
                )
            else:
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

        self.ticker_exposures[ticker] = abs(new_qty * price * _contract_multiplier(ticker))
        self.open_positions = sum(1 for p in self.positions.values() if not math.isclose(p["qty"], 0, abs_tol=1e-9))

        # Keep portfolio greeks tracking reality so the projected-greek gate on
        # the next order is accurate. Intended greeks are stashed at submit time
        # (set_intended_position_greeks); a flattened position clears them.
        if math.isclose(self.positions[ticker]["qty"], 0, abs_tol=1e-9):
            self.clear_position_greeks(ticker)
            self._intended_position_greeks.pop(ticker, None)
        else:
            intended = self._intended_position_greeks.get(ticker)
            if intended is not None:
                self.update_position_greeks(
                    ticker, intended["delta"], intended["gamma"], intended["theta"], intended["vega"]
                )

        await self._save_state()

        metrics = _get_metrics()
        if metrics is not None:
            metrics.risk_exposure.labels(ticker=ticker).set(abs(new_qty * price * _contract_multiplier(ticker)))

        return FillOutcome(realized_pnl=realized_pnl, is_closing=is_closing)

    # ── Post-trade & pending order tracking ──────────────────────────────

    # Pending-order rows older than this on `_load_pending_orders` are
    # discarded as stale (almost certainly leftovers from a process that
    # crashed between the DB write and the broker call). 1h is generous —
    # most orders fill or fail within minutes.
    PENDING_ORDER_LOAD_TTL_SECONDS = 3600

    async def update_post_trade(
        self, ticker: str, qty: float, price: float, side: str, order_id: str | None = None
    ) -> None:
        """Updates internal risk state immediately after an order is sent (Optimistic).

        Also persists the pending order to TimescaleDB so a restart between
        submission and fill doesn't lose the in-flight exposure tracking
        until the next Gateway sync.
        """
        if not order_id:
            order_id = f"pending_{datetime.now(UTC).timestamp()}"

        cost = qty * price
        signed_cost = cost if side.lower() == OrderSide.BUY else -cost
        self.pending_orders[order_id] = (ticker, signed_cost)
        await self._persist_pending_order(order_id, ticker, signed_cost)

    async def remove_pending_order(self, order_id: str) -> None:
        """Removes a pending order from risk tracking (memory + DB).

        `_remove_pending_order_compat` in execution_engine awaits coroutines
        returned from this method, so making it async is safe even though
        callers used to invoke it synchronously.
        """
        self.pending_orders.pop(order_id, None)
        await self._remove_persisted_pending_order(order_id)

    @db_retry
    async def _persist_pending_order(self, order_id: str, ticker: str, signed_cost: float) -> None:
        """Upsert a pending-order row keyed on order_id."""

        async def _upsert(session: Any) -> None:
            from sqlalchemy import select

            from orion.storage.models_risk import PendingOrder

            stmt = select(PendingOrder).where(PendingOrder.order_id == order_id)
            existing = (await session.execute(stmt)).scalars().first()
            if existing is not None:
                existing.ticker = ticker
                existing.signed_cost = signed_cost
            else:
                session.add(
                    PendingOrder(
                        order_id=order_id,
                        ticker=ticker,
                        signed_cost=signed_cost,
                        created_at_utc=datetime.now(UTC),
                    )
                )

        try:
            await db_write(_upsert)
        except Exception as exc:
            logger.warning(
                "pending_order_persist_failed",
                extra={"event_type": "PENDING_ORDER_PERSIST_FAILED", "order_id": order_id, "error": str(exc)},
            )

    @db_retry
    async def _remove_persisted_pending_order(self, order_id: str) -> None:
        """Delete the persisted pending-order row, if it exists."""

        async def _delete(session: Any) -> None:
            from sqlalchemy import select

            from orion.storage.models_risk import PendingOrder

            stmt = select(PendingOrder).where(PendingOrder.order_id == order_id)
            existing = (await session.execute(stmt)).scalars().first()
            if existing is not None:
                await session.delete(existing)

        try:
            await db_write(_delete)
        except Exception as exc:
            logger.warning(
                "pending_order_delete_failed",
                extra={"event_type": "PENDING_ORDER_DELETE_FAILED", "order_id": order_id, "error": str(exc)},
            )

    async def prune_stale_pending_orders(self) -> int:
        """Drop pending orders older than the load TTL from memory + DB at runtime.

        ``_load_pending_orders`` only prunes on startup. A day order that
        EXPIRES unfilled never fires a fill, so ``remove_pending_order`` is
        never called and the row lingers as phantom pending exposure until the
        next process restart (RCA 2026-06-05: three expired 6/04 entries —
        RBLX/ETSY/QURE — were still counted on 6/05 on a long-running native
        process). Running the same TTL sweep on the periodic risk re-sync
        bounds the staleness to the TTL. Returns the number pruned.
        """
        from sqlalchemy import select

        from orion.shared.utils import ensure_utc as _ensure_utc
        from orion.storage.db import async_session_factory
        from orion.storage.models_risk import PendingOrder

        cutoff = datetime.now(UTC) - timedelta(seconds=self.PENDING_ORDER_LOAD_TTL_SECONDS)
        pruned = 0
        try:
            async with async_session_factory() as session:
                rows = list((await session.execute(select(PendingOrder))).scalars().all())
                for row in rows:
                    created = row.created_at_utc
                    created_utc = _ensure_utc(created) if created is not None else None
                    if created_utc is None or created_utc < cutoff:
                        self.pending_orders.pop(row.order_id, None)
                        await session.delete(row)
                        pruned += 1
                if pruned:
                    await session.commit()
        except Exception as exc:
            logger.warning(
                "pending_orders_prune_failed",
                extra={"event_type": "PENDING_ORDERS_PRUNE_FAILED", "error": str(exc)},
            )
            return 0
        if pruned:
            logger.info(
                "pending_orders_pruned",
                extra={
                    "event_type": "PENDING_ORDERS_PRUNED",
                    "pruned": pruned,
                    "ttl_seconds": self.PENDING_ORDER_LOAD_TTL_SECONDS,
                },
            )
        return pruned

    async def _load_pending_orders(self) -> None:
        """Restore the in-memory `pending_orders` dict from the DB.

        Called from `initialize` so a restart picks up where the prior run
        left off. Rows older than `PENDING_ORDER_LOAD_TTL_SECONDS` are
        discarded — they are almost certainly stale (orphaned by an earlier
        crash) and counting them would over-state pending exposure
        indefinitely.
        """
        try:
            from sqlalchemy import select

            from orion.shared.utils import ensure_utc as _ensure_utc
            from orion.storage.db import async_session_factory
            from orion.storage.models_risk import PendingOrder

            cutoff = datetime.now(UTC) - timedelta(seconds=self.PENDING_ORDER_LOAD_TTL_SECONDS)

            async with async_session_factory() as session:
                stmt = select(PendingOrder)
                rows = list((await session.execute(stmt)).scalars().all())

                fresh = 0
                stale_ids: list[str] = []
                for row in rows:
                    created = row.created_at_utc
                    created_utc = _ensure_utc(created) if created is not None else None
                    if created_utc is None or created_utc < cutoff:
                        stale_ids.append(row.order_id)
                        continue
                    self.pending_orders[row.order_id] = (row.ticker, row.signed_cost)
                    fresh += 1

                # Clean up stale rows so they don't accumulate
                if stale_ids:
                    for sid in stale_ids:
                        stale = await session.get(PendingOrder, sid)
                        if stale is not None:
                            await session.delete(stale)
                    await session.commit()

            logger.info(
                "pending_orders_loaded",
                extra={
                    "event_type": "PENDING_ORDERS_LOADED",
                    "fresh": fresh,
                    "stale_dropped": len(stale_ids),
                    "ttl_seconds": self.PENDING_ORDER_LOAD_TTL_SECONDS,
                },
            )
        except Exception as exc:
            logger.warning(
                "pending_orders_load_failed",
                extra={"event_type": "PENDING_ORDERS_LOAD_FAILED", "error": str(exc)},
            )

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
                    signed_cost = cost if side_str.lower() == OrderSide.BUY else -cost
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
