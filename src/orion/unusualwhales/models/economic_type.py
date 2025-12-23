from enum import Enum


class EconomicType(str, Enum):
    FED_SPEAKER = "fed-speaker"
    FOMC = "fomc"
    REPORT = "report"

    def __str__(self) -> str:
        return str(self.value)
