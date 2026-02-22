"""
Black-Scholes Greeks Calculator.

Computes option Greeks (delta, gamma, theta, vega) using the Black-Scholes model.
Used when Greeks are not available from external APIs.
"""

import math
from typing import Dict, Optional

import structlog
from scipy.stats import norm

logger = structlog.get_logger()


def calculate_greeks(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    iv: float,
    risk_free_rate: float = 0.05,
    option_type: str = "CALL",
) -> Dict[str, Optional[float]]:
    """Calculate option Greeks using Black-Scholes model.

    Args:
        spot: Current underlying price
        strike: Option strike price
        time_to_expiry_years: Time to expiry in years (e.g., 30 days = 30/365)
        iv: Implied volatility as decimal (e.g., 0.30 for 30%)
        risk_free_rate: Risk-free interest rate (default 5%)
        option_type: "CALL" or "PUT"

    Returns:
        Dict with delta, gamma, theta, vega (None if calculation fails)
    """
    result = {
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
    }

    if spot <= 0 or strike <= 0 or time_to_expiry_years <= 0 or iv <= 0:
        return result

    try:
        # Black-Scholes d1 and d2
        sqrt_t = math.sqrt(time_to_expiry_years)
        d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * iv**2) * time_to_expiry_years) / (iv * sqrt_t)
        d2 = d1 - iv * sqrt_t

        # Standard normal PDF and CDF
        nd1 = norm.cdf(d1)
        nd2 = norm.cdf(d2)
        pdf_d1 = norm.pdf(d1)

        # Delta
        if option_type.upper() == "CALL":
            result["delta"] = nd1
        else:
            result["delta"] = nd1 - 1

        # Gamma (same for calls and puts)
        result["gamma"] = pdf_d1 / (spot * iv * sqrt_t)

        # Vega (per 1% IV change, scaled to match market convention)
        result["vega"] = spot * pdf_d1 * sqrt_t * 0.01

        # Theta (daily decay, negative means losing value)
        first_term = -(spot * pdf_d1 * iv) / (2 * sqrt_t)
        if option_type.upper() == "CALL":
            second_term = risk_free_rate * strike * math.exp(-risk_free_rate * time_to_expiry_years) * nd2
            result["theta"] = (first_term - second_term) / 365
        else:
            nd2_put = norm.cdf(-d2)
            second_term = risk_free_rate * strike * math.exp(-risk_free_rate * time_to_expiry_years) * nd2_put
            result["theta"] = (first_term + second_term) / 365

    except Exception as e:
        logger.warning(
            "Greeks calculation failed",
            option_type=option_type,
            spot=spot,
            strike=strike,
            error=str(e),
            exc_info=True,
        )

    return result


def calculate_delta_gamma(
    spot: float,
    strike: float,
    dte: int,
    iv: float,
    option_type: str = "CALL",
    risk_free_rate: float = 0.05,
) -> Dict[str, Optional[float]]:
    """Convenience function to get delta and gamma from DTE.

    Args:
        spot: Current underlying price
        strike: Option strike price
        dte: Days to expiry
        iv: Implied volatility as decimal
        option_type: "CALL" or "PUT"
        risk_free_rate: Risk-free rate (default 5%)

    Returns:
        Dict with delta and gamma
    """
    if dte <= 0:
        return {"delta": None, "gamma": None}

    time_to_expiry = dte / 365.0
    greeks = calculate_greeks(spot, strike, time_to_expiry, iv, risk_free_rate, option_type)

    return {
        "delta": greeks.get("delta"),
        "gamma": greeks.get("gamma"),
    }
