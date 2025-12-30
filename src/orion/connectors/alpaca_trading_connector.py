import logging
from datetime import datetime
from typing import Any, List, Optional

import urllib3.exceptions
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, MarketOrderRequest
from requests.exceptions import ConnectionError, Timeout
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from orion.config import SystemSettings

logger = logging.getLogger(__name__)

# Define retry strategy for network operations
network_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=0.1, min=0.1, max=10),
    retry=retry_if_exception_type(
        (ConnectionError, Timeout, urllib3.exceptions.HTTPError, urllib3.exceptions.MaxRetryError)
    ),
    reraise=True,
)


class AlpacaTradingConnector:
    """
    Connects to Alpaca Trading API to submit orders.
    """

    def __init__(self, settings: SystemSettings):
        self.settings = settings
        # Derive paper mode from configuration
        # SystemSettings has 'alpaca_paper' directly, or 'orion_stage'
        is_paper = settings.alpaca_paper or (settings.orion_stage.upper() != "LIVE")

        self.client = TradingClient(settings.alpaca_api_key, settings.alpaca_secret_key, paper=is_paper)
        self._log_account_info()

    def _log_account_info(self) -> None:
        try:
            acct = self.client.get_account()
            logger.info(f"Alpaca Trading Connected. Buying Power: {acct.buying_power} (Currency: {acct.currency})")
        except Exception as e:
            logger.error(f"Failed to connect to Alpaca: {e}")
            raise

    def submit_market_order(
        self, symbol: str, qty: float, side: OrderSide, time_in_force: TimeInForce = TimeInForce.DAY
    ) -> Any:
        """
        Submits a market order.
        """
        req = MarketOrderRequest(symbol=symbol, qty=qty, side=side, time_in_force=time_in_force)

        try:
            order = self.client.submit_order(order_data=req)
            logger.info(f"Order Submitted: {side} {qty} {symbol} | ID: {order.id} | Status: {order.status}")
            return order
        except Exception as e:
            logger.error(f"Failed to submit order for {symbol}: {e}")
            raise e

    @network_retry
    def submit_limit_order(
        self,
        symbol: str,
        qty: float,
        side: OrderSide,
        limit_price: float,
        time_in_force: TimeInForce = TimeInForce.DAY,
        client_order_id: Optional[str] = None,
    ) -> Any:
        """
        Submits a limit order with retry logic.
        """
        # Ensure side is Enum
        if isinstance(side, str):
            side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        else:
            side_enum = side

        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side_enum,
            limit_price=limit_price,
            time_in_force=time_in_force,
            client_order_id=client_order_id,
        )

        try:
            # Note: logging before retry might be noisy, but good for tracing
            logger.info(f"Submitting Limit Order: {side} {qty} {symbol} @ {limit_price}")

            order = self.client.submit_order(order_data=req)
            logger.info(
                f"LIMIT Order Submitted: {side} {qty} {symbol} @ {limit_price} | ID: {order.id} | Status: {order.status}"
            )
            return order
        except Exception as e:
            logger.error(f"Failed to submit LIMIT order for {symbol}: {e}")
            # Raise so tenacity can retry
            raise

    @network_retry
    def get_recent_fills(self, since: Optional[datetime] = None, limit: int = 50) -> List[Any]:
        """
        Fetches recently closed orders (potential fills) to reconcile state.
        """
        try:
            # We want closed orders (filled/cancelled/expired). We filter for filled later.
            req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=limit, after=since)
            orders = self.client.get_orders(filter=req)

            # Return only filled orders
            # Note: order.status is typically an instance of OrderStatus enum
            filled_orders = [o for o in orders if str(o.status) == "filled"]

            return filled_orders

        except Exception as e:
            logger.error(f"Failed to fetch recent fills: {e}")
            raise
