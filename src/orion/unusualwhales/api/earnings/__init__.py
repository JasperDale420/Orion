"""Contains methods for accessing the API Endpoints"""

import types

from . import get_afterhours, get_premarket, get_ticker_earnings


class EarningsEndpoints:
    @classmethod
    def get_afterhours(cls) -> types.ModuleType:
        """
                Afterhours
                =======================================================================

                Returns the next upcoming afterhours earnings for the given date.
        Date must be the current or a past date. If no date is given, returns data for the current/last market day.

        """
        return get_afterhours

    @classmethod
    def get_premarket(cls) -> types.ModuleType:
        """
                Next Premarket Earnings by Date
                =======================================================================

                Returns the next upcoming premarket earnings for the given date.
        Date must be the current or a past date. If no date is given, returns data for the current/last market day.

        """
        return get_premarket

    @classmethod
    def get_ticker_earnings(cls) -> types.ModuleType:
        """
        Past Ticker Earnings
        =======================================================================

        Returns the past earnings for the given ticker.

        """
        return get_ticker_earnings
