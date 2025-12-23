"""Contains methods for accessing the API Endpoints"""

import types

from . import get_analyst_ratings, get_option_contracts, get_stocks


class ScreenerEndpoints:
    @classmethod
    def get_analyst_ratings(cls) -> types.ModuleType:
        """
        Analyst Ratings for a Ticker
        =======================================================================

        Returns the latest analyst rating for the given ticker.

        """
        return get_analyst_ratings

    @classmethod
    def get_option_contracts(cls) -> types.ModuleType:
        """
                Screener for Option Contracts
                =======================================================================

                A contract screener endpoint to screen the market for contracts by a variety of filter options.

        For an example of what can be build with this endpoint check out the [Hottest Contracts](https://unusualwhales.com/hottest-contracts?limit=100&hide_index_etf=true)
        on unusualwhales.

        """
        return get_option_contracts

    @classmethod
    def get_stocks(cls) -> types.ModuleType:
        """
                Stock Screener
                =======================================================================

                A stock screener endpoint to screen the market for stocks by a variety of filter options.

        For an example of what can be build with this endpoint check out the [Stock Screener](https://unusualwhales.com/flow/ticker_flows)
        on unusualwhales.com

        """
        return get_stocks
