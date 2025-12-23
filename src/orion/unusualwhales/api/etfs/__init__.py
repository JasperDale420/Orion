"""Contains methods for accessing the API Endpoints"""

import types

from . import get_holdings, get_info, get_sector_country_weights, get_ticker_exposure


class EtfsEndpoints:
    @classmethod
    def get_ticker_exposure(cls) -> types.ModuleType:
        """
        Ticker Exposure by ETF
        =======================================================================

        Returns all ETFs in which the given ticker is a holding

        """
        return get_ticker_exposure

    @classmethod
    def get_holdings(cls) -> types.ModuleType:
        """
        ETF Holdings
        =======================================================================

        Returns the holdings of the ETF

        """
        return get_holdings

    @classmethod
    def get_info(cls) -> types.ModuleType:
        """
        Information
        =======================================================================

        Returns the information about the given ETF ticker.

        """
        return get_info

    @classmethod
    def get_sector_country_weights(cls) -> types.ModuleType:
        """
        Sector & Country weights
        =======================================================================

        Returns the sector & country weights for the given ETF ticker.

        """
        return get_sector_country_weights
