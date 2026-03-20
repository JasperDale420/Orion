"""Sector concentration tracking and limit checking."""

from orion.config import RiskSettings
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger(__name__)


class SectorTracker:
    """Tracks sector-level USD exposure and enforces concentration limits."""

    def __init__(self) -> None:
        self.sector_exposures: dict[str, float] = {}

    def check_sector_exposure(
        self,
        cfg: RiskSettings,
        sector: str,
        additional_exposure: float = 0.0,
        current_equity: float = 0.0,
    ) -> bool:
        """Check if a new position would breach sector concentration limits.

        Args:
            cfg: Risk settings with sector limits
            sector: Sector name (e.g., 'Technology', 'Healthcare')
            additional_exposure: USD exposure of the proposed new position
            current_equity: Current portfolio equity

        Returns:
            True if sector exposure is within limits, False if it would breach
        """
        if not cfg.enable_sector_checks:
            return True

        if current_equity <= 0:
            return True

        current_sector_exposure = self.sector_exposures.get(sector, 0.0)
        projected_exposure = current_sector_exposure + additional_exposure
        exposure_pct = projected_exposure / current_equity

        if exposure_pct > cfg.max_sector_exposure_pct:
            logger.warning(
                f"RISK REJECT: Sector {sector} exposure {exposure_pct:.1%} > limit {cfg.max_sector_exposure_pct:.1%}"
            )
            return False

        return True

    def update_sector_exposure(self, sector: str, exposure_change: float) -> None:
        """Update sector exposure after a trade.

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

    def get_sector_exposure_pct(self, sector: str, current_equity: float) -> float:
        """Get sector exposure as percentage of portfolio."""
        if current_equity <= 0:
            return 0.0
        return self.sector_exposures.get(sector, 0.0) / current_equity
