from enum import Enum


class StockEarningsTime(str, Enum):
    AFTERHOURS = "afterhours"
    PREMARKET = "premarket"
    UNKOWN = "unkown"

    def __str__(self) -> str:
        return str(self.value)
