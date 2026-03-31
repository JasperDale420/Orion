"""Position sizing with optional correlation adjustment."""

import math
from typing import TYPE_CHECKING

from orion.config import RiskSettings
from orion.shared.logger import setup_struct_logger

if TYPE_CHECKING:
    from orion.execution.correlation_adjuster import CorrelationAdjuster

logger = setup_struct_logger(__name__)


class PositionSizer:
    """Calculates position sizes based on risk-per-trade and portfolio constraints."""

    def __init__(self) -> None:
        self._correlation_adjuster: "CorrelationAdjuster | None" = None

    def calculate_size(
        self,
        cfg: RiskSettings,
        entry_price: float,
        current_equity: float,
        stop_loss_pct: float | None = None,
    ) -> float:
        """Calculates position size based on risk per trade.

        Caps at max_order_size_pct of account equity.
        For correlation-aware sizing, use calculate_size_with_correlation().
        """
        if entry_price <= 0:
            return 0.0

        sl_pct = stop_loss_pct if stop_loss_pct is not None else cfg.default_stop_loss_pct

        risk_amt = current_equity * cfg.risk_per_trade_pct
        stop_distance = entry_price * sl_pct

        if stop_distance <= 0:
            return 0.0

        qty = math.floor(risk_amt / stop_distance)

        # Cap by Max Order Size (% of equity)
        max_order_value = (
            float(cfg.max_order_size_usd)
            if cfg.max_order_size_usd is not None
            else current_equity * cfg.max_order_size_pct
        )
        max_order_qty = math.floor(max_order_value / entry_price)
        qty = min(qty, max_order_qty)

        # Cap by Max Ticker Exposure (% of equity)
        max_exposure = (
            float(cfg.max_ticker_exposure_usd)
            if cfg.max_ticker_exposure_usd is not None
            else current_equity * cfg.max_ticker_exposure_pct
        )
        exposure_cap_qty = math.floor(max_exposure / entry_price)
        qty = min(qty, exposure_cap_qty)

        return float(qty)

    async def calculate_size_with_correlation(
        self,
        cfg: RiskSettings,
        ticker: str,
        entry_price: float,
        current_equity: float,
        positions: dict[str, dict[str, float]],
        stop_loss_pct: float | None = None,
    ) -> float:
        """Calculates position size with correlation adjustment.

        First calculates base size using standard risk-per-trade,
        then applies correlation penalty if new ticker is highly
        correlated with existing holdings.
        """
        base_qty = self.calculate_size(cfg, entry_price, current_equity, stop_loss_pct)

        if base_qty <= 0:
            return 0.0

        if not cfg.correlation_size_scaling:
            return base_qty

        if self._correlation_adjuster is None:
            return base_qty

        existing_tickers = [t for t, p in positions.items() if p.get("qty", 0) != 0]

        if not existing_tickers:
            return base_qty

        multiplier = await self._correlation_adjuster.get_size_multiplier(ticker, existing_tickers, cfg)

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
        """Inject correlation adjuster for size scaling."""
        self._correlation_adjuster = adjuster
        logger.info("Correlation adjuster configured for PositionSizer")
