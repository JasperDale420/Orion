import pytest

from orion.agents.eod_review_agent import EODReviewAgent


def test_eod_agent_init():
    """
    Basic smoke test for agent instantiation.
    """
    try:
        agent = EODReviewAgent()
        assert agent is not None
    except Exception as e:
        pytest.fail(f"Agent instantiation failed: {e}")
