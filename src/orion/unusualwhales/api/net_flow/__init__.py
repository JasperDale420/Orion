import types

from . import (
    get_expiry,
)


class NetFlowEndpoints:
    @classmethod
    def get_expiry(cls) -> types.ModuleType:
        """
        Net Flow Expiry
        """
        return get_expiry
