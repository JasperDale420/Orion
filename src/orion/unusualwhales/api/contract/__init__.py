"""Contains methods for accessing the API Endpoints"""

import types

from . import get_price_history


class ContractEndpoints:
    @classmethod
    def get_price_history(cls) -> types.ModuleType:
        """
        Price history for the given option contract
        =======================================================================

        Historic EOD Stats for the given option contract

        """
        return get_price_history
