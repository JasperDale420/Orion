from datetime import datetime

from orion.agents.eod_review_agent import EODReviewAgent


def test_eod_agent_mock_poor_performance():
    agent = EODReviewAgent(llm_client=None)

    metrics = {"sharpe": -1.5, "pnl": -500.0}
    trades = [{"id": 1, "pnl": -100}]

    result = agent.run_review(datetime.now(), metrics, trades)

    assert "Performance was poor" in result["report_text"]
    assert len(result["proposals"]) == 1
    assert "Tighten stop loss" in result["proposals"][0]


def test_eod_agent_mock_good_performance():
    agent = EODReviewAgent(llm_client=None)

    metrics = {"sharpe": 2.0, "pnl": 500.0}
    trades = []

    result = agent.run_review(datetime.now(), metrics, trades)

    assert "Performance was adequate" in result["report_text"]
    assert len(result["proposals"]) == 0
