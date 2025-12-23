"""Contains methods for accessing the API Endpoints"""

import types

from . import (
    get_economic_calendar,
    get_fda_calendar,
    get_holidays,
    get_imbalances,
    get_insider_trades,
    get_market_options_volume,
    get_market_tide,
    get_market_tide_by_etf,
    get_open_interest_change,
    get_sector_stats,
    get_spike,
)


class MarketEndpoints:
    @classmethod
    def get_economic_calendar(cls) -> types.ModuleType:
        """
        Economic calendar
        =======================================================================

        Returns the economic calendar.

        """
        return get_economic_calendar

    @classmethod
    def get_fda_calendar(cls) -> types.ModuleType:
        """
        Fda calendar
        =======================================================================

        Returns the fda calendar for the current week.

        """
        return get_fda_calendar

    @classmethod
    def get_holidays(cls) -> types.ModuleType:
        """
        Market Holidays
        =======================================================================

        Returns the market holidays.

        """
        return get_holidays

    @classmethod
    def get_imbalances(cls) -> types.ModuleType:
        """
        MOC/MOO Imbalances Data
        =======================================================================

        Returns the MOC/MOO imbalances.

        """
        return get_imbalances

    @classmethod
    def get_insider_trades(cls) -> types.ModuleType:
        """
                Total Insider Buy & Sells
                =======================================================================

                Returns the total amount of purchases & sells as well as notional values for insider transactions
        across the market

        """
        return get_insider_trades

    @classmethod
    def get_market_tide(cls) -> types.ModuleType:
        """
                Returns The Unusual Whales Market Tide Data
                =======================================================================

                Market Tide is a proprietary tool that can be viewed from the Market Overview page. The Market Tide chart provides real time data based on a proprietary formula that examines market wide options activity and filters out 'noise'.

        Date must be the current or a past date. If no date is given, returns data for the current/last market day.

        Per default data are returned in 1 minute intervals. Use `interval_5m=true` to have this return data in 5 minute intervals instead.


        for example
        - $15,000 in calls transacted at the ask has the effect of increasing the daily net call premium by $15,000.
        - $10,000 in calls transacted at the bid has the effect of decreasing the daily net call premium by $10,000.

        The resulting net premium from both of these trades would be $5000 (+ $15,000 - $10,000).

        Transactions taking place at the mid are not accounted for.

        In theory:

        The sentiment in the options market becomes increasingly bullish if:
        1. The aggregated CALL PREMIUM is increasing at a faster rate.
        2. The aggregated PUT PREMIUM is decreasing at a faster rate.

        The sentiment in the options market becomes increasingly bearish if:
        1. The aggregated CALL PREMIUM is decreasing at a faster rate.
        2. The aggregated PUT PREMIUM is increasing at a faster rate.

        ----
        This can be used to build a market overview such as:

        ![market tide](https://i.imgur.com/tuwTCDc.png)

        """
        return get_market_tide

    @classmethod
    def get_open_interest_change(cls) -> types.ModuleType:
        """
                Returns the Option Contracts With The Highest Open Interest Change by Date
                =======================================================================

                Returns the non-Index/non-ETF contracts and OI change data with the highest OI change (default: descending).
        Date must be the current or a past date. If no date is given, returns data for the current/last market day.

        """
        return get_open_interest_change

    @classmethod
    def get_sector_stats(cls) -> types.ModuleType:
        """
                Returns the stats for sector etfs for the most recent trading day
                =======================================================================

                Returns the current Trading Days statistics for the SPDR sector etfs

        ----
        This can be used to build a market overview such as:

        ![sectors etf](https://i.imgur.com/yQ5o6rR.png)

        """
        return get_sector_stats

    @classmethod
    def get_spike(cls) -> types.ModuleType:
        """
                SPIKE
                =======================================================================

                Returns the SPIKE values for the given date.
        Date must be the current or a past date. If no date is given, returns data for the current/last market day.

        """
        return get_spike

    @classmethod
    def get_market_options_volume(cls) -> types.ModuleType:
        """
                Get Options Activity for the Entire Market (Market Tide)
                =======================================================================

                Returns the total options volume and premium for all trade executions
        that happened on a given trading date.

        ----
        This can be used to build a market options overview such as:

        ![Market State](https://i.imgur.com/IioJyq9.png)

        """
        return get_market_options_volume

    @classmethod
    def get_market_tide_by_etf(cls) -> types.ModuleType:
        """
                Get Options Activity for the Specified ETF
                =======================================================================

                The ETF tide is similar to the Market Tide. While the market tide is based on options activity of the whole market
        the ETF tide is only based on the options activity of the holdings of the specified ETF.

        """
        return get_market_tide_by_etf
