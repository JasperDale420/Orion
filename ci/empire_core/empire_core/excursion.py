"""Temporal MFE/MAE excursion analytics for trade analysis.

Provides streaming (ExcursionTracker) and batch (compute_excursion) interfaces
for computing Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE)
with full temporal metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass(frozen=True)
class ExcursionProfile:
    """Immutable result of an excursion analysis for a single trade."""

    mfe: float
    mae: float
    ts_mfe: datetime | None
    ts_mae: datetime | None
    time_to_mfe_seconds: float
    time_to_mae_seconds: float
    mfe_mae_ratio: float
    capture_efficiency: float
    excursion_velocity: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    side: str

    def as_pct(self) -> dict:
        """Return excursion values as percentages of entry price."""
        return {
            "mfe_pct": (self.mfe / self.entry_price) * 100.0 if self.entry_price else 0.0,
            "mae_pct": (self.mae / self.entry_price) * 100.0 if self.entry_price else 0.0,
            "capture_efficiency_pct": self.capture_efficiency * 100.0,
        }

    def as_r(self, initial_risk: float) -> dict:
        """Return excursion values as R-multiples of initial risk."""
        pnl = self.exit_price - self.entry_price if self.side == "long" else self.entry_price - self.exit_price
        return {
            "mfe_r": self.mfe / initial_risk if initial_risk else 0.0,
            "mae_r": self.mae / initial_risk if initial_risk else 0.0,
            "pnl_r": pnl / initial_risk if initial_risk else 0.0,
        }

    def to_dict(self) -> dict:
        """Serialize to a plain dictionary."""
        return {
            "mfe": self.mfe,
            "mae": self.mae,
            "ts_mfe": self.ts_mfe.isoformat() if self.ts_mfe else None,
            "ts_mae": self.ts_mae.isoformat() if self.ts_mae else None,
            "time_to_mfe_seconds": self.time_to_mfe_seconds,
            "time_to_mae_seconds": self.time_to_mae_seconds,
            "mfe_mae_ratio": self.mfe_mae_ratio,
            "capture_efficiency": self.capture_efficiency,
            "excursion_velocity": self.excursion_velocity,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "side": self.side,
        }


class ExcursionTracker:
    """Streaming tracker that updates MFE/MAE on each price tick or bar.

    For long trades: MFE = highest price - entry, MAE = entry - lowest price.
    For short trades: MFE = entry - lowest price, MAE = highest price - entry.
    """

    def __init__(self, entry_price: float, entry_time: datetime, side: str) -> None:
        if side not in ("long", "short"):
            raise ValueError(f"side must be 'long' or 'short', got {side!r}")
        self._entry_price = entry_price
        self._entry_time = entry_time
        self._side = side
        self._best_price = entry_price
        self._worst_price = entry_price
        self._ts_best: datetime = entry_time
        self._ts_worst: datetime = entry_time

    def update(self, price: float, timestamp: datetime, *, low: float | None = None, high: float | None = None) -> None:
        """Update with a new price observation.

        Args:
            price: The current price (or bar high for long / bar low for short).
            timestamp: Timestamp of this observation.
            low: Optional bar low for intra-bar worst-case tracking.
            high: Optional bar high for intra-bar best-case tracking.
        """
        # Determine the candidate best and worst prices from this observation
        candidate_high = high if high is not None else price
        candidate_low = low if low is not None else price

        # Also consider the main price
        effective_high = max(price, candidate_high)
        effective_low = min(price, candidate_low)

        if self._side == "long":
            # Best = highest price, Worst = lowest price
            if effective_high > self._best_price:
                self._best_price = effective_high
                self._ts_best = timestamp
            if effective_low < self._worst_price:
                self._worst_price = effective_low
                self._ts_worst = timestamp
        else:
            # Short: Best = lowest price, Worst = highest price
            if effective_low < self._best_price:
                self._best_price = effective_low
                self._ts_best = timestamp
            if effective_high > self._worst_price:
                self._worst_price = effective_high
                self._ts_worst = timestamp

    def finalize(self, exit_price: float, exit_time: datetime) -> ExcursionProfile:
        """Finalize the tracker and return an ExcursionProfile."""
        if self._side == "long":
            mfe = self._best_price - self._entry_price
            mae = self._entry_price - self._worst_price
        else:
            mfe = self._entry_price - self._best_price
            mae = self._worst_price - self._entry_price

        # Clamp to zero (should not go negative, but guard against floating point)
        mfe = max(mfe, 0.0)
        mae = max(mae, 0.0)

        ts_mfe = self._ts_best if mfe > 0 else None
        ts_mae = self._ts_worst if mae > 0 else None

        time_to_mfe = (self._ts_best - self._entry_time).total_seconds() if mfe > 0 else 0.0
        time_to_mae = (self._ts_worst - self._entry_time).total_seconds() if mae > 0 else 0.0

        pnl = exit_price - self._entry_price if self._side == "long" else self._entry_price - exit_price
        capture_efficiency = pnl / mfe if mfe > 0 else 0.0
        mfe_mae_ratio = mfe / mae if mae > 0 else float("inf") if mfe > 0 else 0.0
        excursion_velocity = mfe / time_to_mfe if time_to_mfe > 0 else 0.0

        return ExcursionProfile(
            mfe=mfe,
            mae=mae,
            ts_mfe=ts_mfe,
            ts_mae=ts_mae,
            time_to_mfe_seconds=time_to_mfe,
            time_to_mae_seconds=time_to_mae,
            mfe_mae_ratio=mfe_mae_ratio,
            capture_efficiency=capture_efficiency,
            excursion_velocity=excursion_velocity,
            entry_price=self._entry_price,
            exit_price=exit_price,
            entry_time=self._entry_time,
            exit_time=exit_time,
            side=self._side,
        )


def compute_excursion(
    prices: pd.Series | None = None,
    entry_price: float | None = None,
    side: str = "long",
    *,
    highs: pd.Series | None = None,
    lows: pd.Series | None = None,
) -> ExcursionProfile:
    """Batch-compute excursion profile from a price series.

    Args:
        prices: Series of prices with DatetimeIndex. Used as both high and low when
            highs/lows are not provided.
        entry_price: Entry price for the trade.
        side: Trade direction, "long" or "short".
        highs: Optional series of bar highs with DatetimeIndex.
        lows: Optional series of bar lows with DatetimeIndex.

    Returns:
        ExcursionProfile with full temporal metadata.
    """
    if entry_price is None:
        raise ValueError("entry_price is required")

    # Determine the source series for iteration
    if highs is not None and lows is not None:
        ref_series = highs
    elif prices is not None:
        ref_series = prices
        highs = prices
        lows = prices
    else:
        raise ValueError("Either prices or both highs and lows must be provided")

    entry_time = ref_series.index[0]
    tracker = ExcursionTracker(entry_price=entry_price, entry_time=entry_time, side=side)

    for ts in ref_series.index:
        h = highs.loc[ts]
        lo = lows.loc[ts]
        tracker.update(h, ts, low=lo)

    exit_time = ref_series.index[-1]
    exit_price_val = prices.iloc[-1] if prices is not None else (highs.iloc[-1] + lows.iloc[-1]) / 2.0

    return tracker.finalize(exit_price=exit_price_val, exit_time=exit_time)
