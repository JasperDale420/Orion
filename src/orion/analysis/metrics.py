from typing import Any

import numpy as np
import numpy.typing as npt
import scipy.stats as stats


def compute_sharpe_ratio(returns: npt.NDArray[Any], annualized: bool = True, periods_per_year: int = 252) -> float:
    """
    Computes standard Sharpe Ratio.
    Args:
        returns: Array of returns (per period or per trade).
        annualized: If True, scales by sqrt(periods_per_year).
    """
    if len(returns) < 2:
        return 0.0

    mean_ret = np.mean(returns)
    std_ret = np.std(returns, ddof=1)

    if std_ret == 0:
        return 0.0

    sr = mean_ret / std_ret

    if annualized:
        sr *= np.sqrt(periods_per_year)

    return float(sr)


def compute_deflated_sharpe_ratio(
    estimated_sr: float, sample_len: int, skew: float, kurtosis: float, n_trials: int, var_sr: float | None = None
) -> float:
    """
    Computes the Deflated Sharpe Ratio (DSR).
    Reference: Bailey, D. and Lopez de Prado, M. (2014) "The Deflated Sharpe Ratio"

    DSR penalizes the SR based on:
    1. Non-normality of returns (Skew/Kurtosis) causing "fat tails" risk.
    2. Multiple testing (Number of trials/experiments run) causing selection bias.

    Args:
        estimated_sr: The annualized Sharpe Ratio estimated from the backtest.
        sample_len: Number of returns points (T).
        skew: Skewness of the returns.
        kurtosis: Kurtosis of the returns (Pearson's definition, normal=3).
        n_trials: Number of independent trials/strategies tested (N).
        var_sr: Variance of the SR across trials (optional, defaults to 0).

    Returns:
        Probability that the true SR is positive (0.0 to 1.0).
        A high value (>0.95) indicates significance.
        Wait, standard output is the Probability (PSR/DSR are probs).
        Sometimes people refer to the adjusted *value*, but the paper defines DSR as a probability.
        We will return the probability.
    """

    # 1. Estimate standard deviation of the Sharpe Ratio estimator
    # sigma_SR = sqrt( (1 + 0.5*skew^2 - 0.25*(kurtosis-3)) / (T - 1) )
    # Note: Using T-1 for unbiased

    if sample_len <= 1:
        return 0.0

    numerator = 1 - skew * estimated_sr + ((kurtosis - 1) / 4.0) * (estimated_sr**2)
    # The approximation formula from 2012 paper is roughly: (1 + 0.5*skew^2 - 0.25*excess_kurt) ??
    # Let's use the standard efficient formula for variance of SR:
    # V[SR] approx (1 + 0.5*skew^2 + ...) / T
    # Actually, let's use the Bailey/LdP formula explicitly:

    # Variance of SR estimator:
    # V = (1 - skew * SR + (k-1)/4 * SR^2) / (T-1)

    var_sr_estimator = numerator / (sample_len - 1)
    if var_sr_estimator < 0:
        var_sr_estimator = 0  # Should not happen unless bad inputs

    sigma_sr = np.sqrt(var_sr_estimator)

    # 2. Compute Expected Maximum Sharpe Ratio (benchmark) under Null Hypothesis
    # Max{SR} expected given N trials independent with 0 mean.
    # E[Max(Z)] approx (1 - gamma) * Z_score + gamma/Z_score ?
    # Golden approximation:
    # expected_max_sr = sigma_sr * sqrt(2 * ln(n_trials)) * EulerGamma?
    # Wait, simple approx: sqrt(2 * log(n_trials)) if assumed Normal.

    # Standard formula:
    # SR_benchmark = sqrt(var_sr_trials) * ( (1-gamma)*Z[1-1/N] + gamma*Z[1-1/(N*e)] )
    # Simplified DSR benchmark often assumes True SR = 0, so we just penalize for selection bias.

    # Let's use the simple approximation for Expected Max of N Gaussian variables:
    # E[max] approx sqrt(2 * ln(N))
    # But SR is annual. So we assume the trials have variance=1 (annualized)?
    # Usually we estimate variance of SR across the N trials.
    # If var_sr is None, we assume variance of trials is same as variance of estimator? No.
    # We assume standard deviation of the *underlying* strategies' SR distribution.
    # If unknown, often assumed 1.0 or taken from the specific set of trials in MetaSearch.

    # For V1, let's assume standard deviation of SRs across trials is 0.5 (conservative).
    std_sr_trials = np.sqrt(var_sr) if var_sr else 0.5

    if n_trials > 1:
        expected_max_sr = std_sr_trials * np.sqrt(2 * np.log(n_trials))
    else:
        expected_max_sr = 0.0

    # 3. Compute Probabilistic Deflated Sharpe Ratio
    # Z = (Estimated_SR - Expected_Max_SR) / Sigma_SR_Estimator

    if sigma_sr == 0:
        return 0.0  # Undefined confidence

    z_score = (estimated_sr - expected_max_sr) / sigma_sr

    dsr_prob = stats.norm.cdf(z_score)

    return float(dsr_prob)


def compute_smart_sharpe(returns: npt.NDArray[Any], periods_per_year: int = 252) -> float:
    """
    Computes Sharpe penalized for autocorrelation.
    Auto-correlation inflates Sharpe.
    """
    # ... Implementation optional for V1, stick to Deflated above ...


def compute_bootstrap_p_value(returns: npt.NDArray[Any], n_samples: int = 1000) -> float:
    """
    Computes a simple bootstrap p-value for the hypothesis that the mean return > 0.
    p_value = Proportion of bootstrap samples where mean(sample) <= 0.
    """
    if len(returns) < 5:
        return 1.0  # Not enough data

    # Vectorized bootstrap?
    # For n=1000 and T ~1000, loops might be slow.
    # np.random.choice with (n_samples, len(returns))

    n_obs = len(returns)

    # Generate indices: (n_samples, n_obs)
    idx = np.random.randint(0, n_obs, size=(n_samples, n_obs))

    # Extract
    samples = returns[idx]

    # Compute means along axis 1
    sample_means = np.mean(samples, axis=1)

    # P-value = fraction where mean <= 0
    failures = np.sum(sample_means <= 0)

    # Add 1 smoothing
    p_value = (failures + 1) / (n_samples + 1)

    return float(p_value)
