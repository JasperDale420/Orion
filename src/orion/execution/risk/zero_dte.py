"""Zero-DTE wind-down rules for options trading."""

from datetime import datetime

from orion.config import RiskSettings
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger(__name__)


class ZeroDteGuard:
    """Enforces 0DTE time-of-day wind-down rules."""

    @staticmethod
    def _minutes_to_market_close(timestamp: datetime | None) -> float:
        """Minutes until the session actually closes — 13:00 ET on half days.

        A fixed 16:00 assumption reports 240 minutes remaining at noon on an
        early-close day, so the cutoff and size-reduction windows never engage
        and a full-size 0DTE gets opened an hour before expiry.
        """
        from zoneinfo import ZoneInfo

        from orion.core.market_schedule import resolve_session_close

        if timestamp is None:
            timestamp = datetime.now(ZoneInfo("America/New_York"))
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo("America/New_York"))
        # Outside a session there is no close to count down to; the fixed
        # 16:00 keeps the pre-open behaviour these windows were tuned on.
        session_close = resolve_session_close(timestamp)
        market_close = (
            session_close.astimezone(timestamp.tzinfo)
            if session_close is not None
            else timestamp.replace(hour=16, minute=0, second=0, microsecond=0)
        )
        return (market_close - timestamp).total_seconds() / 60

    def check_zero_dte_winddown(
        self, cfg: RiskSettings, dte: int, timestamp: datetime | None = None
    ) -> tuple[bool, str]:
        """Check if a 0DTE trade is allowed based on time-of-day wind-down rules.

        Args:
            cfg: Risk settings with 0DTE parameters
            dte: Days to expiration (0 for same-day expiry)
            timestamp: Trade timestamp (defaults to now ET)

        Returns:
            Tuple of (allowed, reason)
        """
        if dte != 0:
            return (True, "Not 0DTE")

        if not cfg.enable_zero_dte_winddown:
            return (True, "Wind-down disabled")

        minutes_to_close = self._minutes_to_market_close(timestamp)

        if minutes_to_close <= cfg.zero_dte_cutoff_minutes:
            logger.warning(
                f"RISK REJECT: 0DTE blocked - only {minutes_to_close:.0f} min to close "
                f"(cutoff: {cfg.zero_dte_cutoff_minutes} min)"
            )
            return (False, f"0DTE cutoff: {minutes_to_close:.0f} min to close")

        if minutes_to_close <= cfg.zero_dte_reduce_size_after_minutes:
            logger.info(
                f"0DTE size reduction active: {minutes_to_close:.0f} min to close "
                f"(reduce after: {cfg.zero_dte_reduce_size_after_minutes} min)"
            )
            return (True, f"Reduce size: {cfg.zero_dte_reduced_size_pct:.0%}")

        return (True, "Normal trading")

    def get_zero_dte_size_multiplier(self, cfg: RiskSettings, dte: int, timestamp: datetime | None = None) -> float:
        """Get size multiplier for 0DTE trades based on time-of-day.

        Args:
            cfg: Risk settings with 0DTE parameters
            dte: Days to expiration
            timestamp: Trade timestamp (defaults to now ET)

        Returns:
            Multiplier (1.0 for full size, <1.0 for reduced)
        """
        if dte != 0 or not cfg.enable_zero_dte_winddown:
            return 1.0

        minutes_to_close = self._minutes_to_market_close(timestamp)

        if minutes_to_close <= cfg.zero_dte_cutoff_minutes:
            return 0.0

        if minutes_to_close <= cfg.zero_dte_reduce_size_after_minutes:
            return cfg.zero_dte_reduced_size_pct

        return 1.0
