from enum import StrEnum


class DecisionStatus(StrEnum):
    """Status of a strategy decision through the execution pipeline.

    Uses StrEnum so that DecisionStatus.TRUE == "TRUE" is True,
    ensuring backward compatibility with existing DB values and
    string comparisons.
    """

    TRUE = "TRUE"
    FALSE = "FALSE"
    SKIPPED = "SKIPPED"
    PENDING = "PENDING"
