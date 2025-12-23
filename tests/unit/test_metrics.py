import numpy as np
from orion.analysis.metrics import compute_deflated_sharpe_ratio, compute_sharpe_ratio


def test_sharpe_ratio():
    rets = np.array([0.01, 0.02, -0.01, 0.01])
    sr = compute_sharpe_ratio(rets, annualized=False)
    # Mean = 0.0075, Std approx 0.0125
    # SR approx 0.6
    assert sr > 0
    assert isinstance(sr, float)


def test_deflated_sharpe():
    # Case 1: High SR, Low Trials -> High Prob
    est_sr = 2.0
    prob = compute_deflated_sharpe_ratio(est_sr, sample_len=100, skew=0, kurtosis=3, n_trials=1)
    assert prob > 0.95

    # Case 2: High SR, High Trials -> Lower Prob (Penalty)
    prob_many = compute_deflated_sharpe_ratio(est_sr, sample_len=100, skew=0, kurtosis=3, n_trials=1000)
    assert prob_many < prob

    # Case 3: Fat tails (Kurtosis) -> Higher Variance of Estimator -> Lower Confidence?
    # High kurtosis increases SigmaSR, which decreases Z-score if (SR > Expected).
    # Wait, Z = (SR - E)/Sigma. If Sigma up, Z down. Yes.
    prob_fat = compute_deflated_sharpe_ratio(est_sr, sample_len=100, skew=0, kurtosis=10, n_trials=1)
    assert prob_fat < prob


def test_bootstrap_p_value():
    from orion.analysis.metrics import compute_bootstrap_p_value

    np.random.seed(42)
    # 1. Clear winner: Mean > 0 (e.g. 0.01 per period)
    returns = np.random.normal(loc=0.01, scale=0.02, size=100)
    p_val = compute_bootstrap_p_value(returns, n_samples=500)
    assert p_val < 0.05  # Should be significant

    # 2. Random noise: Mean ~ 0
    returns_noise = np.random.normal(loc=0.0, scale=0.02, size=100)
    p_val2 = compute_bootstrap_p_value(returns_noise, n_samples=500)
    assert p_val2 > 0.1  # Not significant
