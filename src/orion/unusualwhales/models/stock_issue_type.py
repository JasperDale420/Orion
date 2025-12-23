from enum import Enum


class StockIssueType(str, Enum):
    ADR = "ADR"
    COMMON_STOCK = "Common Stock"
    ETF = "ETF"
    INDEX = "Index"

    def __str__(self) -> str:
        return str(self.value)
