"""Contains methods for accessing the API Endpoints"""

import types

from . import get_trades_by_date, get_trades_by_ticker


class DarkpoolEndpoints:
    @classmethod
    def get_trades_by_date(cls) -> types.ModuleType:
        """
        Darkpool trades for a given date. All Symbols.
        =======================================================================

        Returns the latest darkpool trades.

        """
        return get_trades_by_date

    @classmethod
    def get_trades_by_ticker(cls) -> types.ModuleType:
        """
                Darkpool trades for a given ticker and date
                =======================================================================

                Returns the darkpool trades for the given ticker on a given day.
        Date must be the current or a past date. If no date is given, returns data for the current/last market day.

        """
        return get_trades_by_ticker
