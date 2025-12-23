from enum import Enum


class SingleTradeSettlement(str, Enum):
    CASH_SETTLEMENT = "cash_settlement"
    NEXT_DAY_SETTLEMENT = "next_day_settlement"
    REGULAR_SETTLEMENT = "regular_settlement"
    SELLER_SETTLEMENT = "seller_settlement"
    REGULAR = "regular"

    def __str__(self) -> str:
        return str(self.value)
