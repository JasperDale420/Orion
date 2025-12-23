from enum import Enum


class MarketGeneralImbalanceType(str, Enum):
    FINAL = "final"
    FIRST = "first"
    SECOND = "second"
    THIRD = "third"

    def __str__(self) -> str:
        return str(self.value)
