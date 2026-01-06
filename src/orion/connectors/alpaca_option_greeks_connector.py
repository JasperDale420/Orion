"""
Alpaca Option Greeks Connector.

Fetches option Greeks (delta, gamma, theta, vega, rho) and implied volatility
from Alpaca's Market Data API.
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Cache for Greeks data to avoid redundant API calls
_greeks_cache: Dict[str, Dict[str, Any]] = {}
_cache_ttl_seconds: int = 60  # Cache for 60 seconds


class AlpacaOptionGreeksConnector:
    """Connector for fetching option Greeks from Alpaca Market Data API."""

    BASE_URL = "https://data.alpaca.markets"

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.api_secret = api_secret or os.getenv("ALPACA_SECRET_KEY")

        if not self.api_key or not self.api_secret:
            logger.warning("Alpaca API credentials not configured")

    def _get_headers(self) -> Dict[str, str]:
        """Get authentication headers for Alpaca API."""
        return {
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.api_secret or "",
            "Accept": "application/json",
        }

    async def get_greeks(self, option_symbol: str) -> Dict[str, Optional[float]]:
        """Fetch Greeks for a specific option symbol.

        Args:
            option_symbol: OCC-format option symbol (e.g., AAPL240419C00190000)

        Returns:
            Dict with delta, gamma, theta, vega, rho, implied_volatility
            Returns None values if data unavailable
        """
        result = {
            "delta": None,
            "gamma": None,
            "theta": None,
            "vega": None,
            "rho": None,
            "implied_volatility": None,
        }

        if not option_symbol:
            return result

        if not self.api_key or not self.api_secret:
            logger.debug("Alpaca credentials not configured, skipping Greeks fetch")
            return result

        # Check cache first
        cache_key = option_symbol
        cached = _greeks_cache.get(cache_key)
        if cached:
            cache_time = cached.get("_cached_at")
            if cache_time and (datetime.utcnow() - cache_time).seconds < _cache_ttl_seconds:
                return {k: v for k, v in cached.items() if not k.startswith("_")}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{self.BASE_URL}/v1beta1/options/snapshots"
                params = {"symbols": option_symbol}

                response = await client.get(
                    url,
                    params=params,
                    headers=self._get_headers(),
                )

                if response.status_code == 200:
                    data = response.json()
                    snapshots = data.get("snapshots", {})
                    snapshot = snapshots.get(option_symbol, {})

                    greeks = snapshot.get("greeks", {})
                    if greeks:
                        result["delta"] = greeks.get("delta")
                        result["gamma"] = greeks.get("gamma")
                        result["theta"] = greeks.get("theta")
                        result["vega"] = greeks.get("vega")
                        result["rho"] = greeks.get("rho")

                    result["implied_volatility"] = snapshot.get("impliedVolatility")

                    # Cache the result
                    _greeks_cache[cache_key] = {**result, "_cached_at": datetime.utcnow()}

                    logger.debug(
                        f"Fetched Greeks for {option_symbol}: delta={result['delta']}, "
                        f"gamma={result['gamma']}, iv={result['implied_volatility']}"
                    )
                elif response.status_code == 404:
                    # Option not found (expired or invalid)
                    logger.debug(f"Option not found: {option_symbol}")
                elif response.status_code == 429:
                    logger.warning("Alpaca rate limit hit for options snapshots")
                else:
                    logger.warning(f"Alpaca Greeks API returned {response.status_code}: {response.text[:200]}")

        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching Greeks for {option_symbol}")
        except Exception as e:
            logger.error(f"Error fetching Greeks for {option_symbol}: {e}")

        return result

    async def get_greeks_batch(self, option_symbols: list[str]) -> Dict[str, Dict[str, Optional[float]]]:
        """Fetch Greeks for multiple option symbols in one request.

        Args:
            option_symbols: List of OCC-format option symbols (max 100)

        Returns:
            Dict mapping symbol -> Greeks dict
        """
        results = {}
        if not option_symbols:
            return results

        if not self.api_key or not self.api_secret:
            return {s: self._empty_greeks() for s in option_symbols}

        # Limit to 100 symbols per request (Alpaca limit)
        symbols_to_fetch = option_symbols[:100]

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                url = f"{self.BASE_URL}/v1beta1/options/snapshots"
                params = {"symbols": ",".join(symbols_to_fetch)}

                response = await client.get(
                    url,
                    params=params,
                    headers=self._get_headers(),
                )

                if response.status_code == 200:
                    data = response.json()
                    snapshots = data.get("snapshots", {})

                    for symbol in symbols_to_fetch:
                        snapshot = snapshots.get(symbol, {})
                        greeks = snapshot.get("greeks", {})

                        results[symbol] = {
                            "delta": greeks.get("delta"),
                            "gamma": greeks.get("gamma"),
                            "theta": greeks.get("theta"),
                            "vega": greeks.get("vega"),
                            "rho": greeks.get("rho"),
                            "implied_volatility": snapshot.get("impliedVolatility"),
                        }
                else:
                    logger.warning(f"Alpaca batch Greeks API returned {response.status_code}")
                    for symbol in symbols_to_fetch:
                        results[symbol] = self._empty_greeks()

        except Exception as e:
            logger.error(f"Error fetching batch Greeks: {e}")
            for symbol in symbols_to_fetch:
                results[symbol] = self._empty_greeks()

        return results

    def _empty_greeks(self) -> Dict[str, Optional[float]]:
        """Return empty Greeks dict."""
        return {
            "delta": None,
            "gamma": None,
            "theta": None,
            "vega": None,
            "rho": None,
            "implied_volatility": None,
        }


# Singleton instance
_connector: Optional[AlpacaOptionGreeksConnector] = None


def get_connector() -> AlpacaOptionGreeksConnector:
    """Get or create the singleton connector instance."""
    global _connector
    if _connector is None:
        _connector = AlpacaOptionGreeksConnector()
    return _connector


async def get_option_greeks(option_symbol: str) -> Dict[str, Optional[float]]:
    """Convenience function to fetch Greeks for a single option.

    Args:
        option_symbol: OCC-format option symbol

    Returns:
        Dict with delta, gamma, theta, vega, rho, implied_volatility
    """
    connector = get_connector()
    return await connector.get_greeks(option_symbol)
