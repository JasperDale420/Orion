from enum import Enum


class InsiderTradesTransactionType(str, Enum):
    BUY = "Buy"
    EXCHANGE = "Exchange"
    PURCHASE = "Purchase"
    RECEIVE = "Receive"
    SALE_FULL = "Sale (Full)"
    SALE_PARTIAL = "Sale (Partial)"
    SELL = "Sell"
    SELL_PARTIAL = "Sell (PARTIAL)"

    def __str__(self) -> str:
        return str(self.value)
