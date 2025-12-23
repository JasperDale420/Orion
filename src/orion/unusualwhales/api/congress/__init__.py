"""Contains methods for accessing the API Endpoints"""

import types

from . import get_late_reports, get_reports, get_trades, get_trades_by_member


class CongressEndpoints:
    @classmethod
    def get_trades_by_member(cls) -> types.ModuleType:
        """
        Recent Reports By Trader
        =======================================================================

        Returns the recent reports by the given congress member.

        """
        return get_trades_by_member

    @classmethod
    def get_late_reports(cls) -> types.ModuleType:
        """
                Recent Late Reports
                =======================================================================

                Returns the recent late reports by congress members.
        If a date is given, will only return recent late reports, which's report date is <= the given input date.

        """
        return get_late_reports

    @classmethod
    def get_reports(cls) -> types.ModuleType:
        """
                Recent Reported Congress Trades
                =======================================================================

                Returns the latest reported trades by congress members.
        If a date is given, will only return reports, which's transaction date is <= the given input date.

        """
        return get_reports

    @classmethod
    def get_trades(cls) -> types.ModuleType:
        """
                Recent Congressional Trades
                =======================================================================

                Returns the latest transacted trades by congress members.
        If a date is given, will only return reports, which's transaction date is <= the given input date.

        """
        return get_trades
