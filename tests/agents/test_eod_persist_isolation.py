"""Regression tests for EOD solver-edit persistence robustness.

Reproduces the production failure where the LLM follows the prompt and emits
target_solver_id='paper_v1' — a solver that is never seeded — causing the
derived-solver insert to violate the parent_solver_id FK and abort the ENTIRE
EOD run (report + all YAML artifacts).

The existing tests/agents suite always seeds valid parents, which is why this
class of failure was never caught here.
"""

import json
import logging

import pytest
from sqlalchemy import select
from unittest.mock import MagicMock, patch

from orion.agents.eod_review_agent import EODReviewAgent
from orion.shared.db_utils import db_query, db_write
from orion.storage.models_solvers import Solver, SolverEdits

EOD_LOGGER = "orion.agents.eod_review_agent"


def _events(caplog) -> list[dict]:
    """Decode the JSON-rendered structlog records emitted by the EOD agent.

    empire_core routes structlog through stdlib logging with a JSON renderer,
    so each record's message is a JSON string. Parsing it is order-independent
    (unlike structlog.testing.capture_logs, which misses cached loggers).
    """
    out = []
    for rec in caplog.records:
        if rec.name != EOD_LOGGER:
            continue
        try:
            out.append(json.loads(rec.getMessage()))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def _named(events: list[dict], name: str) -> list[dict]:
    # empire_core renames structlog's 'event' key to 'message'.
    return [e for e in events if e.get("message") == name or e.get("event") == name]


async def _seed_solver(solver_id: str) -> None:
    async def _insert(session):
        session.add(
            Solver(
                solver_id=solver_id,
                family_name="seeded",
                stage="paper",
                config={"k": "v"},
            )
        )

    await db_write(_insert)


@pytest.mark.asyncio
async def test_invalid_parent_solver_skipped_run_not_aborted(caplog):
    """A proposal targeting a non-existent solver ('paper_v1') is skipped with a
    warning while the remaining valid proposal still persists."""
    await _seed_solver("swing_entry_paper_v1")

    agent = EODReviewAgent(vector_store=MagicMock())

    bad_proposal = {
        "type": "solver_edit",
        "target_solver_id": "paper_v1",  # not seeded -> would FK-violate
        "ops": [{"op": "modify_param", "param_name": "x", "new_value": 1}],
    }
    good_proposal = {
        "type": "solver_edit",
        "target_solver_id": "swing_entry_paper_v1",  # real seeded id
        "ops": [{"op": "modify_param", "param_name": "y", "new_value": 2}],
    }

    with caplog.at_level(logging.WARNING, logger=EOD_LOGGER):
        # Must NOT raise — the bad proposal cannot abort the run.
        await agent._persist_solver_edits([bad_proposal, good_proposal], run_id="run_abc")

    # The invalid id is reported and identifiable.
    invalid_events = _named(_events(caplog), "eod_proposal_invalid_solver")
    assert invalid_events, "expected an eod_proposal_invalid_solver warning"
    assert invalid_events[0]["target_solver_id"] == "paper_v1"

    # The valid proposal persisted; the bad one did not.
    async def _fetch(session):
        edits = (await session.execute(select(SolverEdits))).scalars().all()
        return [e.base_solver_id for e in edits]

    base_ids = await db_query(_fetch)
    assert base_ids == ["swing_entry_paper_v1"]
    assert "paper_v1" not in base_ids


@pytest.mark.asyncio
async def test_persist_failure_isolated_per_proposal(caplog):
    """A genuine DB error on one proposal is logged and does not stop the others."""
    await _seed_solver("real_solver_v1")

    agent = EODReviewAgent(vector_store=MagicMock())

    proposals = [
        {
            "type": "solver_edit",
            "target_solver_id": "real_solver_v1",
            "ops": [{"op": "modify_param", "param_name": "a", "new_value": 1}],
        },
        {
            "type": "solver_edit",
            "target_solver_id": "real_solver_v1",
            "ops": [{"op": "modify_param", "param_name": "b", "new_value": 2}],
        },
    ]

    original = agent._persist_one_solver_edit
    calls = {"n": 0}

    async def flaky(base_id, ops_data, run_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated DB failure")
        return await original(base_id, ops_data, run_id)

    with caplog.at_level(logging.ERROR, logger=EOD_LOGGER):
        with patch.object(agent, "_persist_one_solver_edit", side_effect=flaky):
            await agent._persist_solver_edits(proposals, run_id="run_xyz")

    failures = _named(_events(caplog), "eod_proposal_persist_failed")
    assert failures, "expected an eod_proposal_persist_failed error"

    # Second proposal still persisted despite the first failing.
    async def _fetch(session):
        return (await session.execute(select(SolverEdits))).scalars().all()

    edits = await db_query(_fetch)
    assert len(edits) == 1


@pytest.mark.asyncio
async def test_prompt_lists_seeded_ids_and_drops_paper_v1():
    """The generated prompt injects the real seeded solver ids and no longer
    instructs the model to use the hardcoded 'paper_v1'."""
    await _seed_solver("bullish_sweep_paper_v1")
    await _seed_solver("swing_entry_paper_v1")

    agent = EODReviewAgent(vector_store=MagicMock())

    valid_ids = await agent._fetch_valid_solver_ids()
    assert "bullish_sweep_paper_v1" in valid_ids
    assert "swing_entry_paper_v1" in valid_ids

    captured = {}

    async def fake_completion(messages, model):
        captured["system"] = messages[0]["content"]
        return '{"analysis": "x", "proposals": []}'

    with patch("orion.agents.eod_review_agent.run_codex_completion", side_effect=fake_completion):
        await agent._generate_analysis({"some": "data"}, "", valid_ids)

    system_prompt = captured["system"]
    # Seeded ids surfaced to the model.
    assert "bullish_sweep_paper_v1" in system_prompt
    assert "swing_entry_paper_v1" in system_prompt
    # The hardcoded instruction to mutate 'paper_v1' is gone.
    assert "target_solver_id='paper_v1'" not in system_prompt
    assert "Use target_solver_id='paper_v1' to mutate" not in system_prompt
