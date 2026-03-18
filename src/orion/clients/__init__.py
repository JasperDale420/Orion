"""
Orion External Clients.

Clients for external services:
- GatewayTradingClient: Alpaca trading via Data Gateway REST API
- TradingRAG: Strategy research Q&A from indexed trading books
- Heber: Data lakehouse for market data with anti-leakage semantics
"""

from orion.clients.gateway_trading_client import GatewayTradingClient, get_gateway_trading_client
from orion.clients.heber_reader import HeberReader, get_heber_reader
from orion.clients.trading_rag import TradingRAGClient, get_rag_client

__all__ = [
    "GatewayTradingClient",
    "HeberReader",
    "TradingRAGClient",
    "get_gateway_trading_client",
    "get_heber_reader",
    "get_rag_client",
]
