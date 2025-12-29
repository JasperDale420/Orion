from datetime import datetime, timedelta, timezone
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
        try:
            self.calendar = xcals.get_calendar("XNYS")
            logger.info("Market Schedule initialized (XNYS)")
        except Exception as e:
            logger.error(f"Failed to load exchange calendar: {e}")
            self.calendar = None

    def is_market_open(self, timestamp: Optional[datetime] = None) -> bool:
        """
        Checks if the market is currently open.
        """
        if not self.calendar:
            # Fail open if calendar missing to avoid blocking critical ops, but log it.
            return True

        ts = timestamp or datetime.now(timezone.utc)
        return bool(self.calendar.is_open_on_minute(ts))

    def get_open_close(
        self, timestamp: Optional[datetime] = None
    ) -> Union[Tuple[datetime, datetime], Tuple[None, None]]:
        """
        Returns (market_open, market_close) for the given day.
        """
        if not self.calendar:
            return None, None

        ts = timestamp or datetime.now(timezone.utc)
        # xcals.schedule returns a DataFrame, we need to extract values safely
        try:
            # get_schedule returns DataFrame with open, close columns
            # we want the session corresponding to ts
            # is_open = self.calendar.is_open_on_minute(ts) # check if open now
            # Actually we want the full session boundaries for the day of 'ts'

            # session_open(ts) returns the open time for the session *containing* ts (or next/prev depending on impl)
            # Let's use `schedule` method for the day
            date_val = ts.date()

            sched_attr = self.calendar.schedule
            if callable(sched_attr):
                sched = sched_attr(start_date=date_val, end_date=date_val)
            else:
                # DataFrame property fallback
                try:
                    sched = sched_attr.loc[str(date_val) : str(date_val)]
                except Exception:
                    return None, None

            if sched.empty:
                return None, None

            # Extract open/close as datetime
            try:
                open_dt = sched.iloc[0]["market_open"].to_pydatetime()
                close_dt = sched.iloc[0]["market_close"].to_pydatetime()
            except KeyError:
                try:
                    open_dt = sched.iloc[0]["open"].to_pydatetime()
                    close_dt = sched.iloc[0]["close"].to_pydatetime()
                except KeyError:
                    # One last try for partial match or index? Best to just return None
                    return None, None

            return open_dt, close_dt

        except Exception as e:
            logger.error(f"Error getting open/close: {e}")
            return None, None

    def get_next_market_open(self, timestamp: Optional[datetime] = None) -> datetime:
        """
        Returns the next market open time.
        """
        if not self.calendar:
            # Fallback: next weekday 9:30 AM ET roughly converted?
            # Safer to return strict next day for now or raise.
            # Let's return a safe fallback of 24h later to avoid tight loops.
            ts = timestamp or datetime.now(timezone.utc)
            return ts + timedelta(days=1)

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
        """
        if not self.calendar:
            return None

        ts = timestamp or datetime.now(timezone.utc)
        if self.is_market_open(ts):
            return self.calendar.next_close(ts).to_pydatetime()  # type: ignore
        return None
