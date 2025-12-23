from enum import Enum


class Side(str, Enum):
    ALL = "ALL"
    ASK = "ASK"
    BID = "BID"
    MID = "MID"

    def __str__(self) -> str:
        return str(self.value)
