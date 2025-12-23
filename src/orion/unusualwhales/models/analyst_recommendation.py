from enum import Enum


class AnalystRecommendation(str, Enum):
    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"

    def __str__(self) -> str:
        return str(self.value)
