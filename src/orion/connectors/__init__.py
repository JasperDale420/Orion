"""Orion Connectors.

Connectors for external data sources:
- Gateway: Centralized data via Data-Gateway (market data + trading)

All Alpaca API interactions are routed through Data Gateway.
Direct Alpaca connectors archived to archive/connectors/.
"""

from .gateway_stream_client import GatewayStreamClient, create_gateway_stream_client

__all__ = [
    "GatewayStreamClient",
    "create_gateway_stream_client",
]
