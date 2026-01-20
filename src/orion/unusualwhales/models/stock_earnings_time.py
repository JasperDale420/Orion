from enum import Enum


class StockEarningsTime(str, Enum):
    AFTERHOURS = "afterhours"
    PREMARKET = "premarket"
    UNKOWN = "unkown"  # Legacy typo - keep for backwards compatibility
    UNKNOWN = "unknown"  # Correctly spelled version from API

    def __str__(self) -> str:
        return str(self.value)
