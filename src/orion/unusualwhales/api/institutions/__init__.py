import types

from . import (
    get_activity,
    get_holdings,
    get_institutions_list,
    get_latest_filings,
    get_ownership,
    get_sector_exposure,
)


class InstitutionsEndpoints:
    @classmethod
    def get_activity(cls) -> types.ModuleType:
        """
        Institutional Activity
        """
        return get_activity

    @classmethod
    def get_holdings(cls) -> types.ModuleType:
        """
        Institutional Holdings
        """
        return get_holdings

    @classmethod
    def get_institutions_list(cls) -> types.ModuleType:
        """
        List of Institutions
        """
        return get_institutions_list

    @classmethod
    def get_latest_filings(cls) -> types.ModuleType:
        """
        Latest Filings
        """
        return get_latest_filings

    @classmethod
    def get_ownership(cls) -> types.ModuleType:
        """
        Institutional Ownership
        """
        return get_ownership

    @classmethod
    def get_sector_exposure(cls) -> types.ModuleType:
        """
        Sector Exposure
        """
        return get_sector_exposure
