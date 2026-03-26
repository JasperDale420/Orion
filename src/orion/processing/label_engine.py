import math

import pandas as pd

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.processing.label_engine")


class TripleBarrierLabeling:
    """
    Implements the Triple Barrier Method for labeling financial data.
    """

    def __init__(self, upper_barrier: float = 0.015, lower_barrier: float = 0.010, time_barrier_bars: int = 60):
        """
        Args:
            upper_barrier: Profit taking threshold (e.g. 0.015 for +1.5%)
            lower_barrier: Stop loss threshold (e.g. 0.010 for -1.0%)
            time_barrier_bars: Max holding period in bars
        """
        self.upper_barrier = upper_barrier
        self.lower_barrier = lower_barrier
        self.time_barrier_bars = time_barrier_bars

    def compute_labels(self, close_prices: pd.Series, events: pd.DatetimeIndex) -> pd.DataFrame:
        """
        Computes labels for a set of event timestamps.

        Args:
            close_prices: Series of close prices indexed by timestamp.
            events: DatetimeIndex of entry points (candidates).

        Returns:
            DataFrame with columns: ['ret', 'label', 'barrier_hit_ts']
            label: 1 (pt), -1 (sl), 0 (time)
        """
        out = []

        # Ensure prices are sorted
        prices = close_prices.sort_index()

        for start_ts in events:
            if start_ts not in prices.index:
                # Try to map to nearest future timestamp or skip
                try:
                    start_ts = prices.index[prices.index.get_indexer([start_ts], method="bfill")[0]]
                except Exception:
                    logger.warning(f"Failed to map event timestamp {start_ts} to price index", exc_info=True)
                    continue

            # Entry Price
            p0 = prices.loc[start_ts]

            # Define window
            # We simply slice the next N bars
            # Ideally we use time-based window if index is time, but for bars count is easier for now
            # or finding the timestamp at t0 + T

            # Slice from start_ts onwards
            future_prices = prices.loc[start_ts:]

            if len(future_prices) < 2:
                continue

            # Limit to max holding period
            window = future_prices.iloc[: self.time_barrier_bars + 1]

            # Calculate returns relative to p0
            # (pt - p0) / p0
            rets = (window - p0) / p0

            # Find first barrier touch
            # Upper touch
            # We want the FIRST touch.

            # Create boolean masks
            touch_upper = rets >= self.upper_barrier
            touch_lower = (
                rets <= -self.lower_barrier
            )  # lower_barrier passed as positive number usually, but let's check init

            # Timestamps where barriers are hit
            ts_upper = rets[touch_upper].index.min()
            ts_lower = rets[touch_lower].index.min()

            label = 0
            hit_ts = window.index[-1]  # Default to time barrier
            ret_at_hit = rets.iloc[-1]

            # Determine which happened first
            if pd.notna(ts_upper) and pd.notna(ts_lower):
                if ts_upper <= ts_lower:
                    label = 1
                    hit_ts = ts_upper
                    ret_at_hit = rets.loc[ts_upper]
                else:
                    label = -1
                    hit_ts = ts_lower
                    ret_at_hit = rets.loc[ts_lower]
            elif pd.notna(ts_upper):
                label = 1
                hit_ts = ts_upper
                ret_at_hit = rets.loc[ts_upper]
            elif pd.notna(ts_lower):
                label = -1
                hit_ts = ts_lower
                ret_at_hit = rets.loc[ts_lower]
            else:
                # Time barrier
                label = 0
                hit_ts = window.index[-1]
                ret_at_hit = rets.iloc[-1]

            # PRD-required diagnostics (MFE/MAE, time-to-hit)
            try:
                rets_to_hit = rets.loc[:hit_ts]
            except Exception:
                rets_to_hit = rets

            mfe = float(rets_to_hit.max()) if len(rets_to_hit) else float("nan")
            mae = float(rets_to_hit.min()) if len(rets_to_hit) else float("nan")
            delta = hit_ts - start_ts
            time_to_hit_seconds = float(delta.total_seconds()) if hasattr(delta, "total_seconds") else None

            # Temporal excursion tracking
            if len(rets_to_hit) > 0:
                ts_mfe = rets_to_hit.idxmax()
                ts_mae = rets_to_hit.idxmin()
                time_to_mfe_seconds = (ts_mfe - start_ts).total_seconds() if pd.notna(ts_mfe) else 0.0
                time_to_mae_seconds = (ts_mae - start_ts).total_seconds() if pd.notna(ts_mae) else 0.0
                mfe_mae_ratio = (
                    mfe / abs(mae) if mae != 0 and not math.isnan(mae) else (float("inf") if mfe > 0 else 0.0)
                )
                excursion_velocity = mfe / time_to_mfe_seconds if time_to_mfe_seconds > 0 else 0.0
                capture_efficiency = ret_at_hit / mfe if mfe > 0 and not math.isnan(mfe) else 0.0
            else:
                ts_mfe = ts_mae = None
                time_to_mfe_seconds = time_to_mae_seconds = 0.0
                mfe_mae_ratio = excursion_velocity = capture_efficiency = 0.0

            out.append(
                {
                    "event_ts": start_ts,
                    "label": label,
                    "ret": ret_at_hit,
                    "barrier_hit_ts": hit_ts,
                    "time_to_hit_seconds": time_to_hit_seconds,
                    "mfe": mfe,
                    "mae": mae,
                    "ts_mfe": ts_mfe,
                    "ts_mae": ts_mae,
                    "time_to_mfe_seconds": time_to_mfe_seconds,
                    "time_to_mae_seconds": time_to_mae_seconds,
                    "mfe_mae_ratio": mfe_mae_ratio,
                    "excursion_velocity": excursion_velocity,
                    "capture_efficiency": capture_efficiency,
                }
            )

        if not out:
            return pd.DataFrame(columns=["event_ts", "label", "ret", "barrier_hit_ts"])

        return pd.DataFrame(out).set_index("event_ts")
