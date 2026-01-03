from enum import Enum


class SingleTradeSaleCondCode(str, Enum):
    AVERAGE_PRICE_TRADE = "average_price_trade"
    CONTINGENT_TRADE = "contingent_trade"
    ODD_LOT_EXECUTION = "odd_lot_execution"
    PRIO_REFERENCE_PRICE = "prio_reference_price"
    PRIOR_REFERENCE_PRICE = "prior_reference_price"

    def __str__(self) -> str:
        return str(self.value)
