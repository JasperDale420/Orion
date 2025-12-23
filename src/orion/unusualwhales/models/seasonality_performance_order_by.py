from enum import Enum


class SeasonalityPerformanceOrderBy(str, Enum):
    AVG_CHANGE = "avg_change"
    MAX_CHANGE = "max_change"
    MEDIAN_CHANGE = "median_change"
    MIN_CHANGE = "min_change"
    MONTH = "month"
    POSITIVE_CLOSES = "positive_closes"
    POSITIVE_MONTHS_PERC = "positive_months_perc"
    TICKER = "ticker"
    YEARS = "years"

    def __str__(self) -> str:
        return str(self.value)
