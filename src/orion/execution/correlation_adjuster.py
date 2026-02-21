"""
Correlation-aware position sizing adjuster.

Scales down position size when new asset is highly correlated
with existing portfolio holdings to reduce concentration risk.
"""

from orion.shared.logger import setup_struct_logger
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from orion.config import RiskSettings, risk_settings

logger = setup_struct_logger("orion.execution.correlation_adjuster")

# In-memory cache for daily returns (ticker -> (timestamp, returns_array))
_returns_cache: Dict[str, tuple[datetime, np.ndarray]] = {}
CACHE_TTL_HOURS = 4


class CorrelationAdjuster:
    """
    Calculates correlation-based position size multiplier.

    When a new trade is highly correlated with existing holdings,
    the size is reduced to limit unbounded portfolio risk.
    """

    def __init__(self, market_connector: Optional[Any] = None):
        """
        Initialize with optional market connector for fetching price data.

        Args:
            market_connector: AlpacaMarketConnector or compatible object with fetch_bars()
        """
        self.connector = market_connector

    async def get_size_multiplier(
        self,
        new_ticker: str,
        existing_tickers: List[str],
        cfg: Optional[RiskSettings] = None,
    ) -> float:
        """
        Calculate size multiplier based on correlation with existing positions.

        Returns:
            Float in [correlation_penalty_factor, 1.0]
            1.0 means no penalty (low correlation or no positions)
        """
        cfg = cfg or risk_settings

        if not cfg.correlation_size_scaling:
            return 1.0

        if not existing_tickers:
            return 1.0

        # Get correlation with each existing position
        correlations = []
        for existing in existing_tickers:
            if existing == new_ticker:
                continue  # Skip self
            corr = await self._calculate_correlation(new_ticker, existing, cfg.correlation_lookback_days, cfg)
            if corr is not None:
                correlations.append(abs(corr))  # Use absolute correlation

        if not correlations:
            logger.debug(f"No correlation data for {new_ticker}, skipping adjustment")
            return 1.0

        # Average correlation with portfolio
        avg_corr = float(np.mean(correlations))

        # Apply penalty if above threshold
        if avg_corr < cfg.correlation_threshold:
            return 1.0

        # Linear interpolation: threshold -> 1.0, 1.0 -> penalty_factor
        slope = (1.0 - cfg.correlation_penalty_factor) / (1.0 - cfg.correlation_threshold)
        multiplier = 1.0 - slope * (avg_corr - cfg.correlation_threshold)
        multiplier = max(cfg.correlation_penalty_factor, multiplier)

        logger.info(
            f"Correlation adjustment for {new_ticker}: avg_corr={avg_corr:.2f}, multiplier={multiplier:.2f}",
            extra={
                "event": "correlation_size_adjustment",
                "ticker": new_ticker,
                "avg_corr": avg_corr,
                "multiplier": multiplier,
                "existing_count": len(existing_tickers),
            },
        )

        return multiplier

    async def _calculate_correlation(
        self, ticker_a: str, ticker_b: str, lookback_days: int, cfg: RiskSettings
    ) -> Optional[float]:
        """Calculate Pearson correlation between two tickers."""
        returns_a = await self._get_daily_returns(ticker_a, lookback_days, cfg)
        returns_b = await self._get_daily_returns(ticker_b, lookback_days, cfg)

        if returns_a is None or returns_b is None:
            return None

        # Align arrays (same length)
        min_len = min(len(returns_a), len(returns_b))
        if min_len < cfg.min_bars_for_correlation:
            return None

        returns_a = returns_a[-min_len:]
        returns_b = returns_b[-min_len:]

        # Pearson correlation
        try:
            corr = np.corrcoef(returns_a, returns_b)[0, 1]
            return float(corr) if not np.isnan(corr) else None
        except Exception as e:
            logger.warning(f"Correlation calculation failed for {ticker_a}/{ticker_b}: {e}")
            return None

    async def _get_daily_returns(self, ticker: str, lookback_days: int, cfg: RiskSettings) -> Optional[np.ndarray]:
        """Fetch daily returns with caching."""
        # Check cache
        cache_key = ticker
        if cache_key in _returns_cache:
            cached_ts, cached_returns = _returns_cache[cache_key]
            age_hours = (datetime.now(timezone.utc) - cached_ts).total_seconds() / 3600
            if age_hours < CACHE_TTL_HOURS:
                return cached_returns

        # Fetch from market connector
        if not self.connector:
            return None

        try:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(days=lookback_days + 10)  # Buffer for weekends

            bars = self.connector.fetch_bars([ticker], start_time, end_time)
            if not bars or len(bars) < cfg.min_bars_for_correlation:
                return None

            # Extract close prices from bronze events
            closes = []
            for b in bars:
                payload = b.payload if hasattr(b, "payload") else b
                close = payload.get("c") or payload.get("close", 0)
                if close and float(close) > 0:
                    closes.append(float(close))

            if len(closes) < cfg.min_bars_for_correlation:
                return None

            # Calculate daily returns
            closes = np.array(closes)
            returns = np.diff(closes) / closes[:-1]

            # Cache result
            _returns_cache[cache_key] = (datetime.now(timezone.utc), returns)

            return returns

        except Exception as e:
            logger.warning(f"Failed to get returns for {ticker}: {e}")
            return None


def clear_correlation_cache() -> None:
    """Clear the returns cache (for testing)."""
    global _returns_cache
    _returns_cache.clear()
