from enum import Enum


class Sector(str, Enum):
    BASIC_MATERIALS = "Basic Materials"
    COMMUNICATION_SERVICES = "Communication Services"
    CONSUMER_CYCLICAL = "Consumer Cyclical"
    CONSUMER_DEFENSIVE = "Consumer Defensive"
    ENERGY = "Energy"
    FINANCIAL_SERVICES = "Financial Services"
    HEALTHCARE = "Healthcare"
    INDUSTRIALS = "Industrials"
    REAL_ESTATE = "Real Estate"
    TECHNOLOGY = "Technology"
    UTILITIES = "Utilities"

    def __str__(self) -> str:
        return str(self.value)
