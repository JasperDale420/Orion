from enum import Enum


class CandleSize(str, Enum):
    VALUE_0 = "1m"
    VALUE_1 = "5m"
    VALUE_2 = "10m"
    VALUE_3 = "15m"
    VALUE_4 = "30m"
    VALUE_5 = "1h"
    VALUE_6 = "4h"

    def __str__(self) -> str:
        return str(self.value)
