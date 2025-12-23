from enum import Enum


class AnalystFieldRecommendation(str, Enum):
    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"

    def __str__(self) -> str:
        return str(self.value)
