"""
Tests for FeatureDriftMonitor and calculate_psi.

Covers PSI calculation edge cases, baseline management,
drift detection with threshold classification, and summary reporting.
"""

import numpy as np
import pytest

from orion.ml.drift_monitor import (
    PSI_THRESHOLD_CRITICAL,
    PSI_THRESHOLD_WARNING,
    FeatureDriftMonitor,
    calculate_psi,
)


# ---------------------------------------------------------------------------
# calculate_psi
# ---------------------------------------------------------------------------


class TestCalculatePsi:
    """Tests for the standalone PSI calculation function."""

    @pytest.mark.unit
    def test_identical_distributions_near_zero(self) -> None:
        """PSI of a distribution against itself should be ~0."""
        rng = np.random.default_rng(42)
        data = rng.normal(0, 1, 1000)
        psi = calculate_psi(data, data)
        assert psi < 0.01

    @pytest.mark.unit
    def test_shifted_distribution_high_psi(self) -> None:
        """PSI between a baseline and a significantly shifted distribution should be high."""
        rng = np.random.default_rng(42)
        expected = rng.normal(0, 1, 1000)
        actual = rng.normal(3, 1, 1000)  # shifted by 3 std
        psi = calculate_psi(expected, actual)
        assert psi > PSI_THRESHOLD_CRITICAL

    @pytest.mark.unit
    def test_empty_expected_returns_zero(self) -> None:
        """Empty expected array should return 0."""
        psi = calculate_psi(np.array([]), np.array([1, 2, 3]))
        assert psi == 0.0

    @pytest.mark.unit
    def test_empty_actual_returns_zero(self) -> None:
        """Empty actual array should return 0."""
        psi = calculate_psi(np.array([1, 2, 3]), np.array([]))
        assert psi == 0.0

    @pytest.mark.unit
    def test_both_empty_returns_zero(self) -> None:
        """Both empty arrays should return 0."""
        psi = calculate_psi(np.array([]), np.array([]))
        assert psi == 0.0

    @pytest.mark.unit
    def test_constant_values_returns_zero(self) -> None:
        """All-same-value arrays produce < 2 unique breakpoints, returning 0."""
        data = np.ones(100)
        psi = calculate_psi(data, data)
        assert psi == 0.0

    @pytest.mark.unit
    def test_psi_is_non_negative(self) -> None:
        """PSI should always be >= 0."""
        rng = np.random.default_rng(42)
        expected = rng.normal(0, 1, 500)
        actual = rng.normal(0.5, 1.2, 500)
        psi = calculate_psi(expected, actual)
        assert psi >= 0.0

    @pytest.mark.unit
    def test_psi_symmetric_approximately(self) -> None:
        """PSI(A, B) should be approximately equal to PSI(B, A)."""
        rng = np.random.default_rng(42)
        a = rng.normal(0, 1, 1000)
        b = rng.normal(0.5, 1, 1000)
        psi_ab = calculate_psi(a, b)
        psi_ba = calculate_psi(b, a)
        # PSI is not perfectly symmetric due to binning from expected,
        # but both should be in the same ballpark.
        assert abs(psi_ab - psi_ba) < 0.5

    @pytest.mark.unit
    def test_custom_buckets(self) -> None:
        """Varying bucket count should still produce valid PSI."""
        rng = np.random.default_rng(42)
        expected = rng.normal(0, 1, 1000)
        actual = rng.normal(0, 1, 1000)

        psi_5 = calculate_psi(expected, actual, buckets=5)
        psi_20 = calculate_psi(expected, actual, buckets=20)

        # Both should be small for similar distributions
        assert psi_5 < 0.1
        assert psi_20 < 0.1


# ---------------------------------------------------------------------------
# FeatureDriftMonitor
# ---------------------------------------------------------------------------


class TestFeatureDriftMonitor:
    """Tests for FeatureDriftMonitor class."""

    @pytest.fixture
    def monitor(self) -> FeatureDriftMonitor:
        """Create a fresh monitor."""
        return FeatureDriftMonitor()

    # -- set_baseline --

    @pytest.mark.unit
    def test_set_baseline_stores_stats(self, monitor: FeatureDriftMonitor) -> None:
        """set_baseline should store distribution statistics."""
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        stats = monitor.set_baseline("feature_a", values)

        assert "mean" in stats
        assert "std" in stats
        assert "min" in stats
        assert "max" in stats
        assert "p25" in stats
        assert "p50" in stats
        assert "p75" in stats
        assert "n_samples" in stats
        assert stats["mean"] == pytest.approx(3.0)
        assert stats["n_samples"] == 5
        assert monitor.has_baseline("feature_a") is True

    @pytest.mark.unit
    def test_set_baseline_removes_nans(self, monitor: FeatureDriftMonitor) -> None:
        """NaN values should be stripped before computing baseline."""
        values = np.array([1.0, np.nan, 3.0, np.nan, 5.0])
        stats = monitor.set_baseline("feature_a", values)

        assert stats["n_samples"] == 3
        assert stats["mean"] == pytest.approx(3.0)

    @pytest.mark.unit
    def test_set_baseline_all_nan_returns_empty(self, monitor: FeatureDriftMonitor) -> None:
        """All-NaN input should return empty dict and not store a baseline."""
        values = np.array([np.nan, np.nan])
        stats = monitor.set_baseline("feature_a", values)

        assert stats == {}
        assert monitor.has_baseline("feature_a") is False

    @pytest.mark.unit
    def test_set_baseline_empty_array_returns_empty(self, monitor: FeatureDriftMonitor) -> None:
        """Empty array should return empty dict and not store a baseline."""
        stats = monitor.set_baseline("feature_a", np.array([]))
        assert stats == {}
        assert monitor.has_baseline("feature_a") is False

    @pytest.mark.unit
    def test_set_baseline_returned_dict_excludes_raw_values(self, monitor: FeatureDriftMonitor) -> None:
        """The returned stats dict should not contain the raw 'values' array."""
        values = np.array([1.0, 2.0, 3.0])
        stats = monitor.set_baseline("feature_a", values)
        assert "values" not in stats

    @pytest.mark.unit
    def test_set_baseline_2d_array_flattened(self, monitor: FeatureDriftMonitor) -> None:
        """2D arrays should be flattened before processing."""
        values = np.array([[1.0, 2.0], [3.0, 4.0]])
        stats = monitor.set_baseline("feature_a", values)
        assert stats["n_samples"] == 4

    # -- has_baseline --

    @pytest.mark.unit
    def test_has_baseline_false_when_unset(self, monitor: FeatureDriftMonitor) -> None:
        """has_baseline should return False for unknown features."""
        assert monitor.has_baseline("nonexistent") is False

    # -- check_drift --

    @pytest.mark.unit
    def test_check_drift_no_baseline(self, monitor: FeatureDriftMonitor) -> None:
        """check_drift without a baseline should return NO_BASELINE."""
        result = monitor.check_drift("feature_a", np.array([1, 2, 3]))
        assert result["status"] == "NO_BASELINE"
        assert result["psi"] is None

    @pytest.mark.unit
    def test_check_drift_insufficient_data(self, monitor: FeatureDriftMonitor) -> None:
        """check_drift with < 10 samples should return INSUFFICIENT_DATA."""
        rng = np.random.default_rng(42)
        monitor.set_baseline("feature_a", rng.normal(0, 1, 100))

        result = monitor.check_drift("feature_a", np.array([1.0, 2.0, 3.0]))
        assert result["status"] == "INSUFFICIENT_DATA"
        assert result["psi"] is None
        assert result["n_samples"] == 3

    @pytest.mark.unit
    def test_check_drift_ok_for_same_distribution(self, monitor: FeatureDriftMonitor) -> None:
        """Same distribution should produce OK status."""
        rng = np.random.default_rng(42)
        baseline = rng.normal(0, 1, 1000)
        monitor.set_baseline("feature_a", baseline)

        current = rng.normal(0, 1, 200)
        result = monitor.check_drift("feature_a", current)

        assert result["status"] == "OK"
        assert result["psi"] is not None
        assert result["psi"] < PSI_THRESHOLD_WARNING

    @pytest.mark.unit
    def test_check_drift_warning_for_moderate_shift(self, monitor: FeatureDriftMonitor) -> None:
        """Moderately shifted distribution should produce WARNING."""
        rng = np.random.default_rng(42)
        baseline = rng.normal(0, 1, 1000)
        monitor.set_baseline("feature_a", baseline)

        # Shift mean by ~1 std, should produce moderate PSI
        shifted = rng.normal(1.0, 1, 200)
        result = monitor.check_drift("feature_a", shifted)

        assert result["psi"] is not None
        assert result["psi"] >= PSI_THRESHOLD_WARNING

    @pytest.mark.unit
    def test_check_drift_critical_for_large_shift(self, monitor: FeatureDriftMonitor) -> None:
        """Significantly shifted distribution should produce CRITICAL."""
        rng = np.random.default_rng(42)
        baseline = rng.normal(0, 1, 1000)
        monitor.set_baseline("feature_a", baseline)

        # Shift mean by 3+ std
        shifted = rng.normal(3.0, 1, 500)
        result = monitor.check_drift("feature_a", shifted)

        assert result["status"] == "CRITICAL"
        assert result["psi"] >= PSI_THRESHOLD_CRITICAL

    @pytest.mark.unit
    def test_check_drift_result_fields(self, monitor: FeatureDriftMonitor) -> None:
        """check_drift result should contain all expected fields."""
        rng = np.random.default_rng(42)
        baseline = rng.normal(0, 1, 1000)
        monitor.set_baseline("feature_a", baseline)

        current = rng.normal(0, 1, 100)
        result = monitor.check_drift("feature_a", current)

        assert "feature" in result
        assert "psi" in result
        assert "status" in result
        assert "baseline_mean" in result
        assert "actual_mean" in result
        assert "mean_shift" in result
        assert "n_samples" in result
        assert "checked_at" in result

    @pytest.mark.unit
    def test_check_drift_strips_nans(self, monitor: FeatureDriftMonitor) -> None:
        """NaN values in current data should be stripped."""
        rng = np.random.default_rng(42)
        baseline = rng.normal(0, 1, 1000)
        monitor.set_baseline("feature_a", baseline)

        # 50 real values + 5 NaNs = 50 after stripping
        current = np.concatenate([rng.normal(0, 1, 50), np.full(5, np.nan)])
        result = monitor.check_drift("feature_a", current)

        assert result["n_samples"] == 50
        assert result["status"] in ("OK", "WARNING", "CRITICAL")

    @pytest.mark.unit
    def test_check_drift_adds_to_history(self, monitor: FeatureDriftMonitor) -> None:
        """Each check_drift call should add an entry to the history."""
        rng = np.random.default_rng(42)
        baseline = rng.normal(0, 1, 1000)
        monitor.set_baseline("feature_a", baseline)

        monitor.check_drift("feature_a", rng.normal(0, 1, 50))
        monitor.check_drift("feature_a", rng.normal(0, 1, 50))

        assert len(monitor._drift_history) == 2

    # -- get_drift_summary --

    @pytest.mark.unit
    def test_get_drift_summary_empty(self, monitor: FeatureDriftMonitor) -> None:
        """Summary with no checks should return total_checks=0."""
        summary = monitor.get_drift_summary()
        assert summary["total_checks"] == 0

    @pytest.mark.unit
    def test_get_drift_summary_counts_statuses(self, monitor: FeatureDriftMonitor) -> None:
        """Summary should count each status category."""
        rng = np.random.default_rng(42)

        # Set up a baseline and run multiple checks
        baseline = rng.normal(0, 1, 1000)
        monitor.set_baseline("ok_feature", baseline)
        monitor.set_baseline("shifted_feature", baseline)

        # OK check
        monitor.check_drift("ok_feature", rng.normal(0, 1, 200))
        # Shifted check (likely WARNING or CRITICAL)
        monitor.check_drift("shifted_feature", rng.normal(3, 1, 200))

        summary = monitor.get_drift_summary()
        assert summary["total_checks"] == 2
        assert "status_counts" in summary
        assert "worst_drifters" in summary
        assert "avg_psi" in summary
        total_counted = sum(summary["status_counts"].values())
        assert total_counted == 2

    @pytest.mark.unit
    def test_get_drift_summary_worst_drifters_sorted(self, monitor: FeatureDriftMonitor) -> None:
        """Worst drifters should be sorted by PSI descending."""
        rng = np.random.default_rng(42)

        baseline = rng.normal(0, 1, 1000)
        monitor.set_baseline("feat_low", baseline)
        monitor.set_baseline("feat_high", baseline)

        monitor.check_drift("feat_low", rng.normal(0, 1, 200))
        monitor.check_drift("feat_high", rng.normal(3, 1, 200))

        summary = monitor.get_drift_summary()
        worst = summary["worst_drifters"]
        assert len(worst) >= 2
        # First entry should have higher PSI
        assert worst[0][1] >= worst[1][1]

    # -- clear_history --

    @pytest.mark.unit
    def test_clear_history(self, monitor: FeatureDriftMonitor) -> None:
        """clear_history should empty the drift history deque."""
        rng = np.random.default_rng(42)
        baseline = rng.normal(0, 1, 1000)
        monitor.set_baseline("feature_a", baseline)
        monitor.check_drift("feature_a", rng.normal(0, 1, 50))

        assert len(monitor._drift_history) == 1

        monitor.clear_history()
        assert len(monitor._drift_history) == 0

    # -- history maxlen --

    @pytest.mark.unit
    def test_history_maxlen_enforced(self, monitor: FeatureDriftMonitor) -> None:
        """Drift history should not exceed the maxlen (500)."""
        rng = np.random.default_rng(42)
        baseline = rng.normal(0, 1, 1000)
        monitor.set_baseline("feature_a", baseline)

        for _ in range(510):
            monitor.check_drift("feature_a", rng.normal(0, 1, 50))

        assert len(monitor._drift_history) == 500
