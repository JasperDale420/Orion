"""
Real-time P&L Tracker for Orion Trading System.

Provides live position P&L calculations, daily summary metrics,
and alerts for risk thresholds.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from zoneinfo import ZoneInfo

from orion.config import risk_settings, RiskSettings
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.core.pnl_tracker")

ET = ZoneInfo("America/New_York")


@dataclass
class PositionPnL:
    """P&L data for a single position."""

    ticker: str
    quantity: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    realized_pnl: float
    side: str  # "long" or "short"
    sector: Optional[str] = None
    last_updated: datetime = field(default_factory=lambda: datetime.now(ET))


@dataclass
class DailyPnLSummary:
    """Daily P&L summary for the portfolio."""

    date: date
    starting_equity: float
    current_equity: float
    unrealized_pnl: float
    realized_pnl: float
    total_pnl: float
    total_pnl_pct: float
    high_water_mark: float
    drawdown: float
    drawdown_pct: float
    trades_today: int
    winners: int
    losers: int
    win_rate: float


@dataclass
class RiskAlert:
    """Risk threshold alert."""

    alert_type: str  # "DRAWDOWN", "DAILY_LOSS", "POSITION_SIZE", "SECTOR_CONCENTRATION"
    severity: str  # "WARNING", "CRITICAL"
    message: str
    current_value: float
    threshold: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(ET))


class PnLTracker:
    """
    Real-time P&L tracking and risk monitoring.

    Tracks position-level and portfolio-level P&L, generates alerts
    when risk thresholds are approached or breached.
    """

    def __init__(self, config: RiskSettings | None = None):
        self.config = config if config else risk_settings
        self.positions: Dict[str, PositionPnL] = {}
        self.daily_realized_pnl: float = 0.0
        self.trades_today: int = 0
        self.winners: int = 0
        self.losers: int = 0
        self.starting_equity: float = 0.0
        self.current_equity: float = 0.0
        self.high_water_mark: float = 0.0
        self.alerts: List[RiskAlert] = []
        self._last_alert_check: Optional[datetime] = None

    def update_position(
        self,
        ticker: str,
        quantity: float,
        avg_entry_price: float,
        current_price: float,
        realized_pnl: float = 0.0,
        side: str = "long",
        sector: Optional[str] = None,
    ) -> PositionPnL:
        """Update or create a position with current market data."""
        market_value = abs(quantity) * current_price
        cost_basis = abs(quantity) * avg_entry_price

        if side == "long":
            unrealized_pnl = market_value - cost_basis
        else:
            unrealized_pnl = cost_basis - market_value

        unrealized_pnl_pct = (unrealized_pnl / cost_basis) if cost_basis > 0 else 0.0

        position = PositionPnL(
            ticker=ticker,
            quantity=quantity,
            avg_entry_price=avg_entry_price,
            current_price=current_price,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            realized_pnl=realized_pnl,
            side=side,
            sector=sector,
            last_updated=datetime.now(ET),
        )

        self.positions[ticker] = position
        return position

    def close_position(self, ticker: str, exit_price: float) -> Optional[float]:
        """Close a position and record realized P&L."""
        if ticker not in self.positions:
            return None

        position = self.positions[ticker]
        cost_basis = abs(position.quantity) * position.avg_entry_price
        exit_value = abs(position.quantity) * exit_price

        if position.side == "long":
            realized_pnl = exit_value - cost_basis
        else:
            realized_pnl = cost_basis - exit_value

        self.daily_realized_pnl += realized_pnl
        self.trades_today += 1

        if realized_pnl > 0:
            self.winners += 1
        else:
            self.losers += 1

        del self.positions[ticker]

        logger.info(
            f"Position closed: {ticker} P&L=${realized_pnl:.2f}",
            extra={"event": "position_closed", "ticker": ticker, "pnl": realized_pnl},
        )

        return realized_pnl

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get current portfolio P&L summary."""
        total_unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        total_market_value = sum(p.market_value for p in self.positions.values())
        total_pnl = total_unrealized + self.daily_realized_pnl

        # Update equity tracking
        if self.starting_equity > 0:
            total_pnl_pct = total_pnl / self.starting_equity
            self.current_equity = self.starting_equity + total_pnl
            if self.current_equity > self.high_water_mark:
                self.high_water_mark = self.current_equity
            drawdown = self.high_water_mark - self.current_equity
            drawdown_pct = (drawdown / self.high_water_mark) if self.high_water_mark > 0 else 0.0
        else:
            total_pnl_pct = 0.0
            drawdown = 0.0
            drawdown_pct = 0.0

        win_rate = (self.winners / self.trades_today) if self.trades_today > 0 else 0.0

        return {
            "positions": len(self.positions),
            "total_market_value": total_market_value,
            "unrealized_pnl": total_unrealized,
            "realized_pnl": self.daily_realized_pnl,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "starting_equity": self.starting_equity,
            "current_equity": self.current_equity,
            "high_water_mark": self.high_water_mark,
            "drawdown": drawdown,
            "drawdown_pct": drawdown_pct,
            "trades_today": self.trades_today,
            "winners": self.winners,
            "losers": self.losers,
            "win_rate": win_rate,
            "timestamp": datetime.now(ET).isoformat(),
        }

    def get_position_details(self) -> List[Dict[str, Any]]:
        """Get detailed breakdown of all positions."""
        return [
            {
                "ticker": p.ticker,
                "quantity": p.quantity,
                "side": p.side,
                "avg_entry_price": p.avg_entry_price,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
                "unrealized_pnl_pct": p.unrealized_pnl_pct,
                "realized_pnl": p.realized_pnl,
                "sector": p.sector,
                "last_updated": p.last_updated.isoformat(),
            }
            for p in sorted(self.positions.values(), key=lambda x: abs(x.unrealized_pnl), reverse=True)
        ]

    def get_sector_breakdown(self) -> Dict[str, Dict[str, float]]:
        """Get P&L breakdown by sector."""
        sectors: Dict[str, Dict[str, float]] = {}

        for p in self.positions.values():
            sector = p.sector or "Unknown"
            if sector not in sectors:
                sectors[sector] = {"market_value": 0.0, "unrealized_pnl": 0.0, "position_count": 0}

            sectors[sector]["market_value"] += p.market_value
            sectors[sector]["unrealized_pnl"] += p.unrealized_pnl
            sectors[sector]["position_count"] += 1

        return sectors

    def check_risk_alerts(self) -> List[RiskAlert]:
        """Check for risk threshold breaches and generate alerts."""
        alerts: List[RiskAlert] = []
        summary = self.get_portfolio_summary()

        # Check daily loss limit
        if abs(summary["total_pnl"]) > 0 and summary["total_pnl"] < 0:
            loss = abs(summary["total_pnl"])
            if loss >= self.config.max_daily_loss:
                alerts.append(
                    RiskAlert(
                        alert_type="DAILY_LOSS",
                        severity="CRITICAL",
                        message=f"Daily loss ${loss:.2f} exceeds limit ${self.config.max_daily_loss:.2f}",
                        current_value=loss,
                        threshold=self.config.max_daily_loss,
                    )
                )
            elif loss >= self.config.max_daily_loss * 0.8:
                alerts.append(
                    RiskAlert(
                        alert_type="DAILY_LOSS",
                        severity="WARNING",
                        message=f"Daily loss ${loss:.2f} approaching limit ${self.config.max_daily_loss:.2f}",
                        current_value=loss,
                        threshold=self.config.max_daily_loss,
                    )
                )

        # Check drawdown
        if summary["drawdown_pct"] >= self.config.max_drawdown_pct:
            alerts.append(
                RiskAlert(
                    alert_type="DRAWDOWN",
                    severity="CRITICAL",
                    message=f"Drawdown {summary['drawdown_pct']:.1%} exceeds limit {self.config.max_drawdown_pct:.1%}",
                    current_value=summary["drawdown_pct"],
                    threshold=self.config.max_drawdown_pct,
                )
            )
        elif summary["drawdown_pct"] >= self.config.max_drawdown_pct * 0.8:
            alerts.append(
                RiskAlert(
                    alert_type="DRAWDOWN",
                    severity="WARNING",
                    message=f"Drawdown {summary['drawdown_pct']:.1%} approaching limit {self.config.max_drawdown_pct:.1%}",
                    current_value=summary["drawdown_pct"],
                    threshold=self.config.max_drawdown_pct,
                )
            )

        # Check sector concentration
        sectors = self.get_sector_breakdown()
        for sector, data in sectors.items():
            if self.current_equity > 0:
                sector_pct = data["market_value"] / self.current_equity
                if sector_pct >= self.config.max_sector_exposure_pct:
                    alerts.append(
                        RiskAlert(
                            alert_type="SECTOR_CONCENTRATION",
                            severity="WARNING",
                            message=f"Sector {sector} exposure {sector_pct:.1%} exceeds limit {self.config.max_sector_exposure_pct:.1%}",
                            current_value=sector_pct,
                            threshold=self.config.max_sector_exposure_pct,
                        )
                    )

        self.alerts = alerts
        self._last_alert_check = datetime.now(ET)

        return alerts

    def set_starting_equity(self, equity: float) -> None:
        """Set starting equity for the day."""
        self.starting_equity = equity
        self.current_equity = equity
        self.high_water_mark = equity

    def reset_daily(self) -> None:
        """Reset daily counters (call at start of trading day)."""
        self.daily_realized_pnl = 0.0
        self.trades_today = 0
        self.winners = 0
        self.losers = 0
        self.alerts = []


# Global instance
_pnl_tracker: Optional[PnLTracker] = None


def get_pnl_tracker() -> PnLTracker:
    """Get or create the global P&L tracker instance."""
    global _pnl_tracker
    if _pnl_tracker is None:
        _pnl_tracker = PnLTracker()
    return _pnl_tracker


def reset_pnl_tracker() -> None:
    """Reset the global P&L tracker (for testing)."""
    global _pnl_tracker
    _pnl_tracker = None
