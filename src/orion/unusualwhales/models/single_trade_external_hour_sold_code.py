from enum import Enum


class SingleTradeExternalHourSoldCode(str, Enum):
    EXTENDED_HOURS_TRADE = "extended_hours_trade"
    EXTENDED_HOURS_TRADE_LATE_OR_OUT_OF_SEQUENCE = "extended_hours_trade_late_or_out_of_sequence"
    SOLD_OUT_OF_SEQUENCE = "sold_out_of_sequence"

    def __str__(self) -> str:
        return str(self.value)
