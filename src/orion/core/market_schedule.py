from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.core.schedule")


class MarketSchedule:
    _instance = None

    def __new__(cls) -> "MarketSchedule":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        # Initialize attributes with types
        self.open_col: str | None = None
        self.close_col: str | None = None

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
            # In newer exchange_calendars, schedule is a DataFrame attribute, not a method
            sample_sched = self.calendar.schedule

            # Try market_open/market_close first (some versions)
            if "market_open" in sample_sched.columns and "market_close" in sample_sched.columns:
                self.open_col = "market_open"
                self.close_col = "market_close"
            # Fallback to open/close (current versions)
            elif "open" in sample_sched.columns and "close" in sample_sched.columns:
                self.open_col = "open"
                self.close_col = "close"
            else:
                raise ValueError(f"Unrecognized schedule column names: {list(sample_sched.columns)}")
        except Exception as e:
            logger.error(f"Failed to detect calendar column names: {e}", exc_info=True)
            self.open_col = None
            self.close_col = None

    def is_market_open(self, timestamp: datetime | None = None) -> bool:
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

        ts = timestamp or datetime.now(UTC)
        return bool(self.calendar.is_open_on_minute(ts))

    def is_market_open_for_options(self, timestamp: datetime | None = None) -> bool:
        """Alpaca options trading: 9:30am–4:00pm ET Mon–Fri only.

        Unlike equity, Alpaca does NOT allow options orders during
        pre-market or after-hours sessions — paper OR live. Submitting
        any options order (market OR limit) outside this window gets
        rejected with error code ``42210000``.

        Use this gate before attempting any options close to avoid
        spurious EXIT_ORDER_FAILED events; outside the window the exit
        should be queued for replay at the next open.

        Equivalent to ``is_market_open`` for the standard XNYS
        configuration (which only marks the regular session as open),
        but explicitly enforces the [9:30, 16:00) ET window so a
        future calendar config that adds pre/post sessions won't
        accidentally route options orders into a rejection window.
        """
        ts = timestamp or datetime.now(UTC)
        if not self.is_market_open(ts):
            return False
        et = ts.astimezone(ZoneInfo("America/New_York"))
        # Half-open interval matches Alpaca's behavior: a 16:00:00 ET
        # request is rejected.
        open_t = et.replace(hour=9, minute=30, second=0, microsecond=0)
        close_t = et.replace(hour=16, minute=0, second=0, microsecond=0)
        return open_t <= et < close_t

    def get_open_close(self, timestamp: datetime | None = None) -> tuple[datetime, datetime] | tuple[None, None]:
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

        ts = timestamp or datetime.now(UTC)
        date_val = ts.date()

        try:
            # Use schedule as DataFrame attribute, not method
            import pandas as pd

            date_ts = pd.Timestamp(date_val)
            sched = self.calendar.schedule

            if date_ts not in sched.index:
                logger.warning(f"No market session for date: {date_val}")
                return None, None

            # Use validated column names from initialization
            open_dt = sched.loc[date_ts, self.open_col].to_pydatetime()
            close_dt = sched.loc[date_ts, self.close_col].to_pydatetime()

            return open_dt, close_dt

        except Exception as e:
            logger.error(
                f"Error getting open/close for {date_val}: {e}",
                extra={"date": str(date_val)},
                exc_info=True,
            )
            raise RuntimeError(f"Failed to get market hours for {date_val}") from e

    def get_next_market_open(self, timestamp: datetime | None = None) -> datetime:
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

        ts = timestamp or datetime.now(UTC)
        # exchange_calendars typing is loose, cast result
        return self.calendar.next_open(ts).to_pydatetime()  # type: ignore

    def seconds_until_open(self, timestamp: datetime | None = None) -> float:
        """
        Returns seconds until next market open. Returns 0 if currently open.
        """
        ts = timestamp or datetime.now(UTC)
        if self.is_market_open(ts):
            return 0.0

        next_open = self.get_next_market_open(ts)
        diff = (next_open - ts).total_seconds()
        return max(0.0, diff)

    def get_todays_close(self, timestamp: datetime | None = None) -> datetime | None:
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

        ts = timestamp or datetime.now(UTC)
        if self.is_market_open(ts):
            return self.calendar.next_close(ts).to_pydatetime()  # type: ignore
        return None
