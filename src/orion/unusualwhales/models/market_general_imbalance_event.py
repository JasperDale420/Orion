from enum import Enum


class MarketGeneralImbalanceEvent(str, Enum):
    MOC = "moc"
    MOO = "moo"

    def __str__(self) -> str:
        return str(self.value)
