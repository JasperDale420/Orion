import logging
from datetime import datetime
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, MarketOrderRequest

logger = logging.getLogger(__name__)


class AlpacaTradingConnector:
    """
    Connects to Alpaca Trading API to submit orders.
    """

    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.client = TradingClient(api_key, secret_key, paper=paper)
        account = self.client.get_account()
        logger.info(f"Alpaca Trading Connected. Buying Power: {account.buying_power} (Currency: {account.currency})")

    def submit_market_order(
        self, symbol: str, qty: float, side: OrderSide, time_in_force: TimeInForce = TimeInForce.DAY
    ):
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

    def submit_limit_order(
        self,
        symbol: str,
        qty: float,
        side: OrderSide,
        limit_price: float,
        time_in_force: TimeInForce = TimeInForce.DAY,
        client_order_id: Optional[str] = None,
    ):
        """
        Submits a limit order.
        """
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            limit_price=limit_price,
            time_in_force=time_in_force,
            client_order_id=client_order_id,
        )

        try:
            order = self.client.submit_order(order_data=req)
            logger.info(
                f"LIMIT Order Submitted: {side} {qty} {symbol} @ {limit_price} | ID: {order.id} | Status: {order.status}"
            )
            return order
        except Exception as e:
            logger.error(f"Failed to submit LIMIT order for {symbol}: {e}")
            raise e

    def get_recent_fills(self, since: Optional[datetime] = None, limit: int = 50):
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
            return []
