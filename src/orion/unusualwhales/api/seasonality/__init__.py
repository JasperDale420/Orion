"""Contains methods for accessing the API Endpoints"""

import types

from . import (
    get_market_average_returns_by_month,
    get_monthly_average_returns,
    get_monthly_top_performers,
    get_price_changes_by_month_and_year,
)


class SeasonalityEndpoints:
    @classmethod
    def get_market_average_returns_by_month(cls) -> types.ModuleType:
        """
        Market Seasonality for ETFs
        =======================================================================

        Returns the average return by month for the tickers SPY, QQQ, IWM, XLE, XLC, XLK, XLV, XLP, XLY, XLRE, XLF, XLI, XLB .

        """
        return get_market_average_returns_by_month

    @classmethod
    def get_monthly_top_performers(cls) -> types.ModuleType:
        """
                Get Top Performers for a Month
                =======================================================================

                Returns the tickers with the highest performance in terms of price change in the month over the years.
        Per default the result is ordered by 'positive_months_perc' descending, then 'median_change' descending, then 'marketcap' descending.

        """
        return get_monthly_top_performers

    @classmethod
    def get_monthly_average_returns(cls) -> types.ModuleType:
        """
        Average return per month
        =======================================================================

        Returns the average return by month for the given ticker.

        """
        return get_monthly_average_returns

    @classmethod
    def get_price_changes_by_month_and_year(cls) -> types.ModuleType:
        """
        Price change per month per year
        =======================================================================

        Returns the relative price change for all past months over multiple years.

        """
        return get_price_changes_by_month_and_year
