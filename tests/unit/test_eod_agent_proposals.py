import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion.agents.eod_review_agent import EODReviewAgent
from orion.agents.proposal_builder import ProposalBuilder


@pytest.mark.asyncio
async def test_eod_review_writes_report_input_and_proposal(tmp_path, monkeypatch):
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock()

    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(
        {
            "analysis": "Test Analysis",
            "proposals": [
                {
                    "type": "solver_edit",
                    "priority": 1,
                    "category": "risk",
                    "target_solver_id": "solver_base_1",
                    "ops": [{"op": "modify_param", "name": "risk.max_daily_loss", "value": 123}],
                    "rationale": "Testing",
                    "expected_effect": "Reduce daily loss blowups",
                    "evidence_pointers": {
                        "queries": ["signals_live where risk_score high"],
                        "ids": {
                            "signal_ids": [],
                            "candidate_ids": [],
                            "event_ids": [],
                            "rollup_ids": [],
                            "order_ids": [],
                            "fill_ids": [],
                            "dlq_ids": [],
                        },
                    },
                    "test_plan": ["unit: tests/unit/test_drawdown_kill_switch.py"],
                    "rollback_plan": "Revert the config change and restart services",
                    "changes": {"params": {"ORION_RISK_MAX_DAILY_LOSS": 123}},
                }
            ],
        }
    )
    mock_client.chat.completions.create.return_value = mock_response

    agent = EODReviewAgent(
        llm_client=mock_client,
        vector_store=MagicMock(search=AsyncMock(return_value=[])),
        proposal_builder=ProposalBuilder(output_dir=str(tmp_path / "proposals")),
    )

    result = await agent.run_review(datetime.now(UTC).date())
    assert result["report_path"].endswith(".md")
    assert result["input_snapshot_path"].endswith(".json")
    assert len(result["proposal_paths"]) == 1

    with open(result["input_snapshot_path"]) as f:
        payload = json.load(f)
    assert "fills" in payload
    assert "orders" in payload
    assert "signals_live" in payload
    assert "dlq" in payload
    # PRD.md §13.2: inputs must include machine-readable performance + drift + incidents.
    assert "performance_metrics" in payload
    assert "drift_metrics" in payload
    assert "errors_incidents" in payload

    # Proposal artifact must be auditable and reference the input snapshot.
    import yaml

    with open(result["proposal_paths"][0]) as f:
        artifact = yaml.safe_load(f)
    assert artifact["meta"]["run_id"] == result["run_id"]
    assert artifact["meta"]["input_snapshot_path"] == result["input_snapshot_path"]
    assert artifact["meta"]["report_path"] == result["report_path"]
    assert artifact["proposal"]["type"] == "solver_edit"
    assert "evidence_pointers" in artifact["proposal"]
    assert isinstance(artifact["proposal"]["test_plan"], list)
