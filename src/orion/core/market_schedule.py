from datetime import datetime, timezone
from typing import Optional, Tuple, Union

import exchange_calendars as xcals

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.core.schedule")


class MarketSchedule:
    _instance = None

    def __new__(cls) -> "MarketSchedule":
        if cls._instance is None:
            cls._instance = super(MarketSchedule, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        # Initialize attributes with types
        self.open_col: Optional[str] = None
        self.close_col: Optional[str] = None

        try:
            self.calendar = xcals.get_calendar("XNYS")
            # Validate column names at startup to fail fast on version mismatch
            self._detect_column_names()
            logger.info(f"Market Schedule initialized (XNYS, columns: {self.open_col}, {self.close_col})")
        except Exception as e:
            logger.error(f"Failed to load exchange calendar: {e}", exc_info=True)
            self.calendar = None
            self.open_col = None
            self.close_col = None

    def _detect_column_names(self) -> None:
        """Detect and validate column names used by exchange_calendars version."""
        if not self.calendar:
            return

        try:
            # Get a sample schedule to detect column names
            today = datetime.now(timezone.utc).date()
            sample_sched = self.calendar.schedule(start_date=today, end_date=today)

            # Try market_open/market_close first (newer versions)
            if "market_open" in sample_sched.columns and "market_close" in sample_sched.columns:
                self.open_col = "market_open"
                self.close_col = "market_close"
            # Fallback to open/close (older versions)
            elif "open" in sample_sched.columns and "close" in sample_sched.columns:
                self.open_col = "open"
                self.close_col = "close"
            else:
                raise ValueError(f"Unrecognized schedule column names: {list(sample_sched.columns)}")
        except Exception as e:
            logger.error(f"Failed to detect calendar column names: {e}", exc_info=True)
            self.open_col = None
            self.close_col = None

    def is_market_open(self, timestamp: Optional[datetime] = None) -> bool:
        """
        Checks if the market is currently open.

        :raises RuntimeError: If calendar not properly initialized
        """
        if not self.calendar:
            logger.error(
                "Market calendar unavailable - CANNOT DETERMINE MARKET HOURS",
                extra={"error_code": "CALENDAR_UNAVAILABLE"},
            )
            raise RuntimeError("Cannot verify market hours without calendar")

        ts = timestamp or datetime.now(timezone.utc)
        return bool(self.calendar.is_open_on_minute(ts))

    def get_open_close(
        self, timestamp: Optional[datetime] = None
    ) -> Union[Tuple[datetime, datetime], Tuple[None, None]]:
        """
        Returns (market_open, market_close) for the given day.

        :raises RuntimeError: If calendar not properly initialized
        """
        if not self.calendar or not self.open_col or not self.close_col:
            logger.error(
                "Market calendar unavailable - CANNOT DETERMINE MARKET HOURS",
                extra={"error_code": "CALENDAR_UNAVAILABLE"},
            )
            raise RuntimeError("Cannot get market hours without calendar")

        ts = timestamp or datetime.now(timezone.utc)
        date_val = ts.date()

        try:
            sched = self.calendar.schedule(start_date=date_val, end_date=date_val)

            if sched.empty:
                logger.warning(f"No market session for date: {date_val}")
                return None, None

            # Use validated column names from initialization
            open_dt = sched.iloc[0][self.open_col].to_pydatetime()
            close_dt = sched.iloc[0][self.close_col].to_pydatetime()

            return open_dt, close_dt

        except Exception as e:
            logger.error(
                f"Error getting open/close for {date_val}: {e}",
                extra={"date": str(date_val)},
                exc_info=True,
            )
            raise RuntimeError(f"Failed to get market hours for {date_val}") from e

    def get_next_market_open(self, timestamp: Optional[datetime] = None) -> datetime:
        """
        Returns the next market open time.

        :raises RuntimeError: If calendar not properly initialized
        """
        if not self.calendar:
            logger.error(
                "Market calendar unavailable - CANNOT DETERMINE NEXT MARKET OPEN",
                extra={"error_code": "CALENDAR_UNAVAILABLE"},
            )
            raise RuntimeError("Cannot determine next market open without calendar")

        ts = timestamp or datetime.now(timezone.utc)
        # exchange_calendars typing is loose, cast result
        return self.calendar.next_open(ts).to_pydatetime()  # type: ignore

    def seconds_until_open(self, timestamp: Optional[datetime] = None) -> float:
        """
        Returns seconds until next market open. Returns 0 if currently open.
        """
        ts = timestamp or datetime.now(timezone.utc)
        if self.is_market_open(ts):
            return 0.0

        next_open = self.get_next_market_open(ts)
        diff = (next_open - ts).total_seconds()
        return max(0.0, diff)

    def get_todays_close(self, timestamp: Optional[datetime] = None) -> Optional[datetime]:
        """
        Returns market close time for the session containing timestamp.

        :raises RuntimeError: If calendar not properly initialized
        """
        if not self.calendar:
            logger.error(
                "Market calendar unavailable - CANNOT DETERMINE TODAYS CLOSE",
                extra={"error_code": "CALENDAR_UNAVAILABLE"},
            )
            raise RuntimeError("Cannot determine today's close without calendar")

        ts = timestamp or datetime.now(timezone.utc)
        if self.is_market_open(ts):
            return self.calendar.next_close(ts).to_pydatetime()  # type: ignore
        return None
