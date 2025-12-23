from enum import Enum


class OptionContractType(str, Enum):
    CALL = "call"
    PUT = "put"

    def __str__(self) -> str:
        return str(self.value)
