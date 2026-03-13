"""
Feature Drift Monitoring using Population Stability Index (PSI).

Detects when production feature distributions shift from training baseline.
"""

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# PSI thresholds
PSI_THRESHOLD_WARNING = 0.1  # Moderate drift
PSI_THRESHOLD_CRITICAL = 0.25  # Significant drift requiring investigation


def calculate_psi(expected: np.ndarray, actual: np.ndarray, buckets: int = 10) -> float:
    """
    Calculate Population Stability Index between expected and actual distributions.

    PSI measures how much a distribution has shifted from a baseline.
    - PSI < 0.1: No significant change
    - 0.1 <= PSI < 0.25: Moderate change, monitor
    - PSI >= 0.25: Significant change, investigate

    Args:
        expected: Baseline distribution values (training data)
        actual: Current distribution values (production data)
        buckets: Number of buckets for binning

    Returns:
        PSI value (0 = identical distributions)
    """
    if len(expected) == 0 or len(actual) == 0:
        return 0.0

    # Create bucket boundaries from expected distribution
    breakpoints = np.percentile(expected, np.linspace(0, 100, buckets + 1))
    breakpoints = np.unique(breakpoints)  # Handle tied percentiles

    if len(breakpoints) < 2:
        return 0.0

    # Count observations in each bucket
    expected_counts = np.histogram(expected, bins=breakpoints)[0]
    actual_counts = np.histogram(actual, bins=breakpoints)[0]

    # Convert to percentages
    expected_pct = expected_counts / len(expected)
    actual_pct = actual_counts / len(actual)

    # Avoid log(0) by adding small epsilon
    epsilon = 1e-10
    expected_pct = np.clip(expected_pct, epsilon, 1)
    actual_pct = np.clip(actual_pct, epsilon, 1)

    # Calculate PSI
    psi = np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct))

    return float(psi)


class FeatureDriftMonitor:
    """
    Monitors feature distributions for drift from training baseline.

    Stores baseline statistics and compares production batches.
    """

    def __init__(self):
        # Feature baselines: feature_name -> {'values': array, 'mean': float, 'std': float}
        self._baselines: dict[str, dict[str, Any]] = {}
        self._drift_history: deque = deque(maxlen=500)

    def set_baseline(self, feature_name: str, values: np.ndarray) -> dict[str, float]:
        """
        Set baseline distribution for a feature.

        Args:
            feature_name: Name of the feature
            values: Training values for this feature

        Returns:
            Baseline statistics
        """
        values = np.array(values).flatten()
        values = values[~np.isnan(values)]  # Remove NaNs

        if len(values) == 0:
            logger.warning(f"Empty baseline for feature {feature_name}")
            return {}

        stats = {
            "values": values,
            "mean": float(np.mean(values)),
            "std": float(np.std(values)),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
            "p25": float(np.percentile(values, 25)),
            "p50": float(np.percentile(values, 50)),
            "p75": float(np.percentile(values, 75)),
            "n_samples": len(values),
            "created_at": datetime.now(UTC),
        }

        self._baselines[feature_name] = stats

        logger.info(
            f"Set baseline for {feature_name}: mean={stats['mean']:.4f}, std={stats['std']:.4f}",
            extra={"event": "baseline_set", "feature": feature_name, "n_samples": len(values)},
        )

        return {k: v for k, v in stats.items() if k != "values"}

    def set_baselines_from_df(self, df: Any, feature_names: list[str]) -> None:
        """Set baselines from a DataFrame for multiple features."""
        for name in feature_names:
            if name in df.columns:
                self.set_baseline(name, df[name].values)

    def check_drift(self, feature_name: str, values: np.ndarray) -> dict[str, Any]:
        """
        Check a feature for drift against its baseline.

        Args:
            feature_name: Name of the feature
            values: Current production values

        Returns:
            Dict with PSI, status, and statistics
        """
        if feature_name not in self._baselines:
            return {"status": "NO_BASELINE", "psi": None}

        values = np.array(values).flatten()
        values = values[~np.isnan(values)]

        if len(values) < 10:
            return {"status": "INSUFFICIENT_DATA", "psi": None, "n_samples": len(values)}

        baseline = self._baselines[feature_name]
        psi = calculate_psi(baseline["values"], values)

        # Determine status
        if psi >= PSI_THRESHOLD_CRITICAL:
            status = "CRITICAL"
        elif psi >= PSI_THRESHOLD_WARNING:
            status = "WARNING"
        else:
            status = "OK"

        result = {
            "feature": feature_name,
            "psi": psi,
            "status": status,
            "baseline_mean": baseline["mean"],
            "actual_mean": float(np.mean(values)),
            "mean_shift": float(np.mean(values) - baseline["mean"]),
            "n_samples": len(values),
            "checked_at": datetime.now(UTC).isoformat(),
        }

        # Log if drift detected
        if status != "OK":
            logger.warning(
                f"Feature drift detected: {feature_name} PSI={psi:.3f} ({status})",
                extra={"event": "feature_drift", **result},
            )

        # Track history
        self._drift_history.append(result)

        return result

    def check_all_features(self, df: Any) -> dict[str, dict[str, Any]]:
        """
        Check drift for all baselined features in a DataFrame.

        Returns:
            Dict mapping feature names to drift results
        """
        results = {}
        for feature_name in self._baselines:
            if feature_name in df.columns:
                results[feature_name] = self.check_drift(feature_name, df[feature_name].values)
        return results

    def get_drift_summary(self) -> dict[str, Any]:
        """
        Get summary of all drift checks.

        Returns:
            Summary with counts by status and worst drifters
        """
        if not self._drift_history:
            return {"total_checks": 0}

        # Count by status
        status_counts = {"OK": 0, "WARNING": 0, "CRITICAL": 0}
        psi_values = []

        for check in self._drift_history:
            if check.get("status") in status_counts:
                status_counts[check["status"]] += 1
            if check.get("psi") is not None:
                psi_values.append((check["feature"], check["psi"]))

        # Get worst drifters
        psi_values.sort(key=lambda x: x[1], reverse=True)
        worst = psi_values[:5]

        return {
            "total_checks": len(self._drift_history),
            "status_counts": status_counts,
            "worst_drifters": worst,
            "avg_psi": float(np.mean([p[1] for p in psi_values])) if psi_values else 0.0,
        }

    def clear_history(self) -> None:
        """Clear drift check history."""
        self._drift_history.clear()

    def has_baseline(self, feature_name: str) -> bool:
        """Check if a feature has a baseline set."""
        return feature_name in self._baselines


# Global monitor instance
_drift_monitor: FeatureDriftMonitor | None = None


def get_drift_monitor() -> FeatureDriftMonitor:
    """Get or create the global drift monitor."""
    global _drift_monitor
    if _drift_monitor is None:
        _drift_monitor = FeatureDriftMonitor()
    return _drift_monitor
