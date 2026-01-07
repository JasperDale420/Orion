"""
Alpaca Options Trading Connector.

Provides options order submission and quote retrieval via Alpaca Trading API.
"""

import logging
from datetime import datetime
from typing import Any, Optional

import urllib3.exceptions
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
from requests.exceptions import ConnectionError, Timeout
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from orion.config import SystemSettings

logger = logging.getLogger(__name__)

# Network retry strategy
network_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=0.1, min=0.1, max=10),
    retry=retry_if_exception_type(
        (ConnectionError, Timeout, urllib3.exceptions.HTTPError, urllib3.exceptions.MaxRetryError)
    ),
    reraise=True,
)


def generate_occ_symbol(
    ticker: str,
    expiration_date: datetime,
    option_type: str,
    strike_price: float
) -> str:
    """
    Generate OCC-format option symbol.

    Format: SYMBOL + YYMMDD + C/P + STRIKE*1000 (8 digits, zero-padded)
    Example: AAPL240419C00190000 = AAPL April 19, 2024 $190 Call

    Args:
        ticker: Underlying ticker symbol (e.g., "AAPL")
        expiration_date: Option expiration date
        option_type: "CALL" or "PUT" (or "C"/"P")
        strike_price: Strike price in dollars

    Returns:
        OCC format symbol string
    """
    # Format date as YYMMDD
    date_str = expiration_date.strftime("%y%m%d")

    # Option type: C for Call, P for Put
    opt_type = "C" if option_type.upper() in ("CALL", "C") else "P"

    # Strike price: multiply by 1000 and zero-pad to 8 digits
    # Example: $190 -> 00190000
    strike_int = int(strike_price * 1000)
    strike_str = f"{strike_int:08d}"

    return f"{ticker.upper()}{date_str}{opt_type}{strike_str}"


class AlpacaOptionsConnector:
    """
    Connects to Alpaca Trading API for options orders.

    Uses the same TradingClient as equity orders but with option-specific
    symbol format and order constraints.
    """

    def __init__(self, settings: SystemSettings):
        self.settings = settings
        is_paper = settings.alpaca_paper or (settings.orion_stage.upper() != "LIVE")

        self.client = TradingClient(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
            paper=is_paper
        )
        self._log_account_info()

    def _log_account_info(self) -> None:
        try:
            acct = self.client.get_account()
            logger.info(
                f"Alpaca Options Trading Connected. "
                f"Buying Power: {acct.buying_power} (Paper: {self.settings.alpaca_paper})"
            )
        except Exception as e:
            logger.error(f"Failed to connect to Alpaca for options: {e}")
            raise

    @network_retry
    def submit_option_order(
        self,
        option_symbol: str,
        qty: int,
        side: OrderSide,
        order_type: str = "limit",
        limit_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> Any:
        """
        Submit an options order.

        Args:
            option_symbol: OCC format symbol (e.g., AAPL240419C00190000)
            qty: Number of contracts (must be whole number)
            side: OrderSide.BUY or OrderSide.SELL
            order_type: "market" or "limit" (default: limit)
            limit_price: Required for limit orders
            client_order_id: Optional client order ID for tracking

        Returns:
            Order object from Alpaca

        Raises:
            ValueError: If limit order without price, or invalid qty
        """
        # Validate qty is whole number
        if qty <= 0 or qty != int(qty):
            raise ValueError(f"Options qty must be a positive whole number, got: {qty}")
        qty = int(qty)

        # Ensure side is Enum
        if isinstance(side, str):
            side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        else:
            side_enum = side

        # Options must use TIF.DAY and no extended hours
        if order_type.lower() == "market":
            req = MarketOrderRequest(
                symbol=option_symbol,
                qty=qty,
                side=side_enum,
                time_in_force=TimeInForce.DAY,
            )
        else:
            if limit_price is None:
                raise ValueError("Limit price required for limit orders")

            req = LimitOrderRequest(
                symbol=option_symbol,
                qty=qty,
                side=side_enum,
                limit_price=round(limit_price, 2),
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
            )

        try:
            logger.info(
                f"Submitting OPTIONS Order: {side_enum.value} {qty} {option_symbol} "
                f"@ {limit_price if limit_price else 'MARKET'}"
            )

            order = self.client.submit_order(order_data=req)

            logger.info(
                f"OPTIONS Order Submitted: {side_enum.value} {qty} {option_symbol} "
                f"| ID: {order.id} | Status: {order.status}"
            )
            return order

        except Exception as e:
            logger.error(f"Failed to submit OPTIONS order for {option_symbol}: {e}")
            raise

    async def get_option_quote(self, option_symbol: str) -> dict[str, Optional[float]]:
        """
        Get current bid/ask quote for an option.

        Uses the Alpaca Market Data API for options snapshots.

        Args:
            option_symbol: OCC format symbol

        Returns:
            Dict with 'bid', 'ask', 'last', 'mid' prices
        """
        try:

            import aiohttp

            api_key = self.settings.alpaca_api_key
            api_secret = self.settings.alpaca_secret_key

            url = "https://data.alpaca.markets/v1beta1/options/snapshots"
            params = {"symbols": option_symbol}
            headers = {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        snapshot = data.get("snapshots", {}).get(option_symbol, {})

                        latest_quote = snapshot.get("latestQuote", {})
                        latest_trade = snapshot.get("latestTrade", {})

                        bid = latest_quote.get("bp")
                        ask = latest_quote.get("ap")
                        last = latest_trade.get("p")

                        mid = None
                        if bid and ask:
                            mid = (bid + ask) / 2

                        return {
                            "bid": bid,
                            "ask": ask,
                            "last": last,
                            "mid": mid,
                        }
                    else:
                        logger.warning(f"Failed to fetch option quote: HTTP {resp.status}")
                        return {"bid": None, "ask": None, "last": None, "mid": None}

        except Exception as e:
            logger.error(f"Error fetching option quote for {option_symbol}: {e}")
            return {"bid": None, "ask": None, "last": None, "mid": None}

    def calculate_option_contracts(
        self,
        max_premium_usd: float,
        option_price: float,
    ) -> int:
        """
        Calculate number of contracts based on max premium.

        Each contract = 100 shares, so:
        premium_per_contract = option_price * 100

        Args:
            max_premium_usd: Maximum USD to spend on premium
            option_price: Current option price per share

        Returns:
            Number of contracts (floored to whole number)
        """
        if option_price <= 0:
            return 0

        premium_per_contract = option_price * 100
        return int(max_premium_usd / premium_per_contract)
