"""Orion Connectors.

Connectors for external data sources:
- Alpaca: Market data and trading
- Gateway: Centralized data via Data-Gateway

DEPRECATED: legacy UW connectors archived under archive/2026-02-05_gateway-heber-migration/.
Data-Gateway now handles flow/darkpool ingestion -> Heber.
"""

from .alpaca_market_connector import AlpacaMarketConnector
from .alpaca_stream_connector import AlpacaStreamConnector
from .gateway_stream_client import GatewayStreamClient, create_gateway_stream_client

__all__ = [
    "AlpacaMarketConnector",
    "AlpacaStreamConnector",
    "GatewayStreamClient",
    "create_gateway_stream_client",
]
