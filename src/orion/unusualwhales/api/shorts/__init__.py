import types

from . import (
    get_ftds,
    get_interest_float,
    get_short_data,
    get_volume_and_ratio,
    get_volumes_by_exchange,
)


class ShortsEndpoints:
    @classmethod
    def get_short_data(cls) -> types.ModuleType:
        """
        Short Data
        """
        return get_short_data

    @classmethod
    def get_ftds(cls) -> types.ModuleType:
        """
        Failures to Deliver
        """
        return get_ftds

    @classmethod
    def get_volume_and_ratio(cls) -> types.ModuleType:
        """
        Short Volume and Ratio
        """
        return get_volume_and_ratio

    @classmethod
    def get_interest_float(cls) -> types.ModuleType:
        """
        Short Interest and Float
        """
        return get_interest_float

    @classmethod
    def get_volumes_by_exchange(cls) -> types.ModuleType:
        """
        Short Volume By Exchange
        """
        return get_volumes_by_exchange
