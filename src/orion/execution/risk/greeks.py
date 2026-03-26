"""Greeks tracking and limit checking for options risk management."""

from orion.config import RiskSettings
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger(__name__)


class GreeksTracker:
    """Tracks per-position and portfolio-level Greeks for options risk."""

    def __init__(self) -> None:
        self.portfolio_delta: float = 0.0
        self.portfolio_gamma: float = 0.0
        self.portfolio_vega: float = 0.0
        self.position_greeks: dict[str, dict[str, float]] = {}

    def check_greeks_limits(
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

        if abs(position_delta) > cfg.max_position_delta:
            logger.warning(
                f"RISK REJECT: Position Delta {position_delta:.1f} > Limit {cfg.max_position_delta:.1f} for {ticker}"
            )
            return False

        if abs(position_vega) > cfg.max_position_vega:
            logger.warning(
                f"RISK REJECT: Position Vega {position_vega:.1f} > Limit {cfg.max_position_vega:.1f} for {ticker}"
            )
            return False

        projected_portfolio_delta = self.portfolio_delta + position_delta
        if abs(projected_portfolio_delta) > cfg.max_portfolio_delta:
            logger.warning(
                f"RISK REJECT: Portfolio Delta {projected_portfolio_delta:.1f} > Limit {cfg.max_portfolio_delta:.1f}"
            )
            return False

        projected_portfolio_gamma = self.portfolio_gamma + position_gamma
        if abs(projected_portfolio_gamma) > cfg.max_portfolio_gamma:
            logger.warning(
                f"RISK REJECT: Portfolio Gamma {projected_portfolio_gamma:.1f} > Limit {cfg.max_portfolio_gamma:.1f}"
            )
            return False

        projected_portfolio_vega = self.portfolio_vega + position_vega
        if abs(projected_portfolio_vega) > cfg.max_portfolio_vega:
            logger.warning(
                f"RISK REJECT: Portfolio Vega {projected_portfolio_vega:.1f} > Limit {cfg.max_portfolio_vega:.1f}"
            )
            return False

        return True

    def recalculate_portfolio_greeks(self) -> None:
        """Recalculate portfolio-level Greeks from all position Greeks."""
        self.portfolio_delta = sum(g["delta"] for g in self.position_greeks.values())
        self.portfolio_gamma = sum(g["gamma"] for g in self.position_greeks.values())
        self.portfolio_vega = sum(g["vega"] for g in self.position_greeks.values())

    def update_position_greeks(
        self, ticker: str, delta: float, gamma: float, theta: float = 0.0, vega: float = 0.0
    ) -> None:
        """Update Greeks for a position and recalculate portfolio totals."""
        self.position_greeks[ticker] = {
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
        }
        self.recalculate_portfolio_greeks()

        logger.info(
            f"Greeks Updated for {ticker}: delta={delta:.2f}, gamma={gamma:.4f}, vega={vega:.4f} | "
            f"Portfolio: delta={self.portfolio_delta:.2f}, gamma={self.portfolio_gamma:.4f}, vega={self.portfolio_vega:.4f}"
        )

    def clear_position_greeks(self, ticker: str) -> None:
        """Clear Greeks for a closed position."""
        if ticker in self.position_greeks:
            del self.position_greeks[ticker]
            self.recalculate_portfolio_greeks()
