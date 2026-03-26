"""
Tests that heuristic scorer weights are configurable via HeuristicWeights config.
"""

from unittest.mock import patch

import pytest


def test_heuristic_weights_change_score():
    """Changing weight values should change the heuristic score."""
    from orion.ml.scorer import MLScorer

    scorer = MLScorer()

    flow = {
        "premium_usd": 600000,
        "is_sweep": "true",
        "put_call": "C",
        "aggressor": "ASK",
        "volume_contract": 500,
        "open_interest": 100,
    }

    # Score with default weights
    default_score = scorer._heuristic_score(flow)

    # Score with boosted premium weight
    with patch("orion.config.heuristic_weights.premium_500k_plus", 0.50):
        boosted_score = scorer._heuristic_score(flow)

    assert boosted_score > default_score


def test_heuristic_base_score_configurable():
    """Base score should be configurable."""
    from orion.ml.scorer import MLScorer

    scorer = MLScorer()

    flow = {"premium_usd": 5000}  # Low premium triggers penalty

    with patch("orion.config.heuristic_weights.base_score", 0.50):
        higher_base = scorer._heuristic_score(flow)

    with patch("orion.config.heuristic_weights.base_score", 0.10):
        lower_base = scorer._heuristic_score(flow)

    assert higher_base > lower_base


def test_sweep_bonus_configurable():
    """Sweep bonus should be configurable."""
    from orion.ml.scorer import MLScorer

    scorer = MLScorer()

    flow = {"premium_usd": 100000, "is_sweep": "true"}

    with patch("orion.config.heuristic_weights.sweep_bonus", 0.30):
        high_sweep = scorer._heuristic_score(flow)

    with patch("orion.config.heuristic_weights.sweep_bonus", 0.00):
        no_sweep = scorer._heuristic_score(flow)

    assert high_sweep > no_sweep


def test_low_premium_penalty_configurable():
    """Low premium penalty should be configurable."""
    from orion.ml.scorer import MLScorer

    scorer = MLScorer()

    flow = {"premium_usd": 5000}  # Below $10k threshold

    with patch("orion.config.heuristic_weights.low_premium_penalty", -0.05):
        mild_penalty = scorer._heuristic_score(flow)

    with patch("orion.config.heuristic_weights.low_premium_penalty", -0.40):
        harsh_penalty = scorer._heuristic_score(flow)

    assert mild_penalty > harsh_penalty
