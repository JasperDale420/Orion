import types

from . import (
    get_greek_flow,
    get_greek_flow_by_expiry,
)


class GroupFlowEndpoints:
    @classmethod
    def get_greek_flow(cls) -> types.ModuleType:
        """
        Greek Flow
        """
        return get_greek_flow

    @classmethod
    def get_greek_flow_by_expiry(cls) -> types.ModuleType:
        """
        Greek Flow by Expiry
        """
        return get_greek_flow_by_expiry
