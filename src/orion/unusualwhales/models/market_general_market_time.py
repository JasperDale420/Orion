from enum import Enum


class MarketGeneralMarketTime(str, Enum):
    PO = "po"
    PR = "pr"
    R = "r"

    def __str__(self) -> str:
        return str(self.value)
