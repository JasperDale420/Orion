"""
Black-Scholes Greeks calculations for the price target labeler.

Extracted from main_price_target_labeler.py for reuse across the codebase.
"""

import math

from scipy.stats import norm


def calculate_black_scholes_delta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,  # noqa: N803
) -> float | None:
    """Calculate option delta using Black-Scholes model.

    Args:
        S: Current underlying price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility (as decimal, e.g., 0.30 for 30%)
        option_type: 'C' for call, 'P' for put

    Returns:
        Delta value or None if calculation fails
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        if option_type == "C":
            return float(norm.cdf(d1))
        else:
            return float(norm.cdf(d1) - 1)
    except (ValueError, ZeroDivisionError) as e:
        raise ValueError(
            f"Failed to calculate Black-Scholes delta (S={S}, K={K}, T={T}, r={r}, sigma={sigma}, type={option_type}): {e}"
        ) from e


def calculate_black_scholes_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float | None:  # noqa: N803
    """Calculate option gamma using Black-Scholes model.

    Args:
        S: Current underlying price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility (as decimal)

    Returns:
        Gamma value or None if calculation fails
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        return float(norm.pdf(d1) / (S * sigma * math.sqrt(T)))
    except (ValueError, ZeroDivisionError) as e:
        raise ValueError(
            f"Failed to calculate Black-Scholes gamma (S={S}, K={K}, T={T}, r={r}, sigma={sigma}): {e}"
        ) from e


def calculate_black_scholes_theta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,  # noqa: N803
) -> float | None:
    """Calculate option theta using Black-Scholes model.

    Args:
        S: Current underlying price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility (as decimal)
        option_type: 'C' for call, 'P' for put

    Returns:
        Theta value (daily decay) or None if calculation fails
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        # Per-day theta (divide by 365)
        first_term = -(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
        if option_type == "C":
            second_term = -r * K * math.exp(-r * T) * norm.cdf(d2)
        else:
            second_term = r * K * math.exp(-r * T) * norm.cdf(-d2)
        return float((first_term + second_term) / 365)
    except (ValueError, ZeroDivisionError) as e:
        raise ValueError(
            f"Failed to calculate Black-Scholes theta (S={S}, K={K}, T={T}, r={r}, sigma={sigma}, type={option_type}): {e}"
        ) from e


def calculate_black_scholes_vega(S: float, K: float, T: float, r: float, sigma: float) -> float | None:  # noqa: N803
    """Calculate option vega using Black-Scholes model.

    Args:
        S: Current underlying price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility (as decimal)

    Returns:
        Vega value (per 1% IV change) or None if calculation fails
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        # Per 1% change (divide by 100)
        return float(S * norm.pdf(d1) * math.sqrt(T) / 100)
    except (ValueError, ZeroDivisionError) as e:
        raise ValueError(
            f"Failed to calculate Black-Scholes vega (S={S}, K={K}, T={T}, r={r}, sigma={sigma}): {e}"
        ) from e


def calculate_iv_rank_from_history(current_iv: float, iv_history: list[float]) -> float | None:
    """Calculate IV rank as percentile within historical IV range.

    Args:
        current_iv: Current IV value
        iv_history: List of historical IV values

    Returns:
        IV rank (0-100) or None if insufficient data
    """
    if not iv_history or len(iv_history) < 2:
        return None
    min_iv = min(iv_history)
    max_iv = max(iv_history)
    if max_iv == min_iv:
        return 50.0  # No range, default to middle
    return min(100.0, max(0.0, (current_iv - min_iv) / (max_iv - min_iv) * 100))
