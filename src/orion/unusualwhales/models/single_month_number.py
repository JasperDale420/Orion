from enum import Enum


class SingleMonthNumber(str, Enum):
    VALUE_0 = "1"
    VALUE_1 = "2"
    VALUE_10 = "11"
    VALUE_11 = "12"
    VALUE_2 = "3"
    VALUE_3 = "4"
    VALUE_4 = "5"
    VALUE_5 = "6"
    VALUE_6 = "7"
    VALUE_7 = "8"
    VALUE_8 = "9"
    VALUE_9 = "10"

    def __str__(self) -> str:
        return str(self.value)
