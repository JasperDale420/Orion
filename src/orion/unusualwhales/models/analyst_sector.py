from enum import Enum


class AnalystSector(str, Enum):
    CONGLOMERATES = "Conglomerates"
    CONSUMER_GOODS = "Consumer Goods"
    FINANCIAL = "Financial"
    GENERAL = "General"
    HEALTHCARE = "Healthcare"
    INDUSTRIAL_GOODS = "Industrial Goods"
    MATERIALS = "Materials"
    SERVICES = "Services"
    TECHNOLOGY = "Technology"
    UTILITIES = "Utilities"

    def __str__(self) -> str:
        return str(self.value)
