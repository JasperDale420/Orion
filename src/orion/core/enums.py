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


class DecisionAction(StrEnum):
    """Action determined by the signal engine for a candidate trade."""

    EXECUTE = "EXECUTE"
    SKIP = "SKIP"


class TradeDirection(StrEnum):
    """Direction of a trade (long or short)."""

    LONG = "LONG"
    SHORT = "SHORT"


class OrderSide(StrEnum):
    """Side of an order sent to the broker."""

    BUY = "buy"
    SELL = "sell"
