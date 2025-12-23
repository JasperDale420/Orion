from enum import Enum


class SingleTradeTradeCode(str, Enum):
    DERIVATIVE_PRICED = "derivative_priced"
    INTERMARKET_SWEEP = "intermarket_sweep"
    QUALIFIED_CONTINGENT_TRADE = "qualified_contingent_trade"

    def __str__(self) -> str:
        return str(self.value)
