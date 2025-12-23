"""Contains methods for accessing the API Endpoints"""

import types

from . import (
    get_contract_flow,
    get_flow_by_expiry,
    get_flow_by_strike,
    get_full_tape,
    get_order_flow,
    get_ticker_order_flow,
    get_unusual_flow_alerts,
)


class FlowEndpoints:
    @classmethod
    def get_contract_flow(cls) -> types.ModuleType:
        """
        Get Option Order Flow for a Given Contract
        =======================================================================

        Option Contract Order Flow

        """
        return get_contract_flow

    @classmethod
    def get_ticker_order_flow(cls) -> types.ModuleType:
        """
        Search Option Trades With Filters
        =======================================================================

        Search option trades based on a variety of parameters

        """
        return get_ticker_order_flow

    @classmethod
    def get_full_tape(cls) -> types.ModuleType:
        """
                Full Tape all trades and statuses for all option orders by date
                =======================================================================

                Download the full tape of data for a given trading date.

        NOTICE:
        This endpoint is not included by default in your access.
        For information on how to access this data email support@unusualwhales.com

        """
        return get_full_tape

    @classmethod
    def get_unusual_flow_alerts(cls) -> types.ModuleType:
        """
        Unusual Whales Alerts for a Ticker
        =======================================================================

        Returns the latest unusual whales flow alerts for the given ticker.

        """
        return get_unusual_flow_alerts

    @classmethod
    def get_flow_by_expiry(cls) -> types.ModuleType:
        """
        Option Order Flow for a Ticker Grouped by Expiry
        =======================================================================

        Returns all option orders grouped by expiry for a given ticker for the last Trading Day

        """
        return get_flow_by_expiry

    @classmethod
    def get_flow_by_strike(cls) -> types.ModuleType:
        """
        Option Order Flow for a Ticker Grouped By Strike
        =======================================================================

        Returns the option flow per strike for the last Trading Day

        """
        return get_flow_by_strike

    @classmethod
    def get_order_flow(cls) -> types.ModuleType:
        """
        Option Order Flow By Date
        =======================================================================

        Returns the latest flows for the given ticker. Optionally a min premium and a side can be supplied in the query for further filtering.

        """
        return get_order_flow
