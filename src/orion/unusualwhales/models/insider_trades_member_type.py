from enum import Enum


class InsiderTradesMemberType(str, Enum):
    HOUSE = "house"
    OTHER = "other"
    SENATE = "senate"

    def __str__(self) -> str:
        return str(self.value)
