import numpy as np
import pytest
import scipy.stats as stats

from orion.analysis.metrics import (
    compute_bootstrap_p_value,
    compute_deflated_sharpe_ratio,
    compute_sharpe_ratio,
)


class TestComputeSharpeRatio:
    def test_too_few_returns_is_zero(self) -> None:
        assert compute_sharpe_ratio(np.array([0.01])) == 0.0
        assert compute_sharpe_ratio(np.array([])) == 0.0

    def test_zero_std_is_zero(self) -> None:
        # Constant returns => std=0 => undefined Sharpe, function defines it as 0.0
        returns = np.array([0.02, 0.02, 0.02, 0.02])
        assert compute_sharpe_ratio(returns) == 0.0

    def test_matches_manual_annualized_calculation(self) -> None:
        returns = np.array([0.01, -0.005, 0.02, 0.0, 0.015])
        mean_ret = np.mean(returns)
        std_ret = np.std(returns, ddof=1)
        expected = float(mean_ret / std_ret * np.sqrt(252))

        result = compute_sharpe_ratio(returns, annualized=True, periods_per_year=252)

        assert result == pytest.approx(expected)

    def test_not_annualized_is_raw_mean_over_std(self) -> None:
        returns = np.array([0.01, -0.005, 0.02, 0.0, 0.015])
        mean_ret = np.mean(returns)
        std_ret = np.std(returns, ddof=1)
        expected = float(mean_ret / std_ret)

        result = compute_sharpe_ratio(returns, annualized=False)

        assert result == pytest.approx(expected)

    def test_annualized_scales_by_sqrt_periods(self) -> None:
        returns = np.array([0.01, -0.005, 0.02, 0.0, 0.015])
        raw = compute_sharpe_ratio(returns, annualized=False)
        annualized = compute_sharpe_ratio(returns, annualized=True, periods_per_year=12)

        assert annualized == pytest.approx(raw * np.sqrt(12))


class TestComputeDeflatedSharpeRatio:
    def test_sample_len_too_short_is_zero(self) -> None:
        assert compute_deflated_sharpe_ratio(estimated_sr=1.0, sample_len=1, skew=0.0, kurtosis=3.0, n_trials=10) == 0.0
        assert compute_deflated_sharpe_ratio(estimated_sr=1.0, sample_len=0, skew=0.0, kurtosis=3.0, n_trials=10) == 0.0

    def test_single_trial_has_no_selection_bias_penalty(self) -> None:
        # With n_trials <= 1, expected_max_sr is defined as 0.0, so the DSR
        # collapses to a plain z-test of estimated_sr against zero.
        estimated_sr = 1.2
        sample_len = 252
        skew = 0.0
        kurtosis = 3.0

        numerator = 1 - skew * estimated_sr + ((kurtosis - 1) / 4.0) * (estimated_sr**2)
        sigma_sr = np.sqrt(numerator / (sample_len - 1))
        expected = float(stats.norm.cdf(estimated_sr / sigma_sr))

        result = compute_deflated_sharpe_ratio(
            estimated_sr=estimated_sr, sample_len=sample_len, skew=skew, kurtosis=kurtosis, n_trials=1
        )

        assert result == pytest.approx(expected)

    def test_more_trials_lowers_deflated_probability(self) -> None:
        # More independent trials raises the selection-bias benchmark, which
        # should only ever reduce (never increase) the resulting probability.
        estimated_sr, sample_len, skew, kurtosis = 1.5, 252, 0.0, 3.0

        few_trials = compute_deflated_sharpe_ratio(estimated_sr, sample_len, skew, kurtosis, n_trials=2)
        many_trials = compute_deflated_sharpe_ratio(estimated_sr, sample_len, skew, kurtosis, n_trials=200)

        assert many_trials < few_trials

    def test_matches_manual_calculation_for_multiple_trials(self) -> None:
        # Pins the exact expected-max-SR benchmark (std_sr_trials * sqrt(2*ln(n_trials)))
        # used to penalize the estimate for the number of trials searched.
        estimated_sr = 1.5
        sample_len = 252
        skew = -0.2
        kurtosis = 4.0
        n_trials = 10
        var_sr = 0.3

        numerator = 1 - skew * estimated_sr + ((kurtosis - 1) / 4.0) * (estimated_sr**2)
        sigma_sr = np.sqrt(numerator / (sample_len - 1))
        std_sr_trials = np.sqrt(var_sr)
        expected_max_sr = std_sr_trials * np.sqrt(2 * np.log(n_trials))
        expected = float(stats.norm.cdf((estimated_sr - expected_max_sr) / sigma_sr))

        result = compute_deflated_sharpe_ratio(
            estimated_sr=estimated_sr,
            sample_len=sample_len,
            skew=skew,
            kurtosis=kurtosis,
            n_trials=n_trials,
            var_sr=var_sr,
        )

        assert result == pytest.approx(expected)

    def test_result_is_a_valid_probability(self) -> None:
        result = compute_deflated_sharpe_ratio(
            estimated_sr=0.8, sample_len=100, skew=-0.3, kurtosis=4.5, n_trials=20, var_sr=0.2
        )
        assert 0.0 <= result <= 1.0


class TestComputeBootstrapPValue:
    def test_too_few_returns_is_one(self) -> None:
        assert compute_bootstrap_p_value(np.array([0.01, 0.02, 0.03])) == 1.0

    def test_strongly_positive_returns_give_low_p_value(self) -> None:
        rng_state = np.random.get_state()
        try:
            np.random.seed(0)
            returns = np.full(200, 0.05)  # constant positive return, no variance
            p_value = compute_bootstrap_p_value(returns, n_samples=500)
        finally:
            np.random.set_state(rng_state)

        # Every bootstrap resample mean is >0.05, so no failures; only the
        # +1 smoothing keeps this off exactly zero.
        assert p_value == pytest.approx(1 / 501)

    def test_strongly_negative_returns_give_high_p_value(self) -> None:
        rng_state = np.random.get_state()
        try:
            np.random.seed(0)
            returns = np.full(200, -0.05)
            p_value = compute_bootstrap_p_value(returns, n_samples=500)
        finally:
            np.random.set_state(rng_state)

        assert p_value == pytest.approx(1.0)

    def test_p_value_is_bounded(self) -> None:
        returns = np.array([0.01, -0.02, 0.015, -0.005, 0.03, -0.01, 0.02])
        p_value = compute_bootstrap_p_value(returns, n_samples=200)
        assert 0.0 <= p_value <= 1.0
