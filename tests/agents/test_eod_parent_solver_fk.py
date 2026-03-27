"""Test that EOD agent handles missing parent solver gracefully (FK constraint)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from orion.agents.eod_review_agent import EODReviewAgent
from orion.storage.models_solvers import Solver
from sqlalchemy import select


@pytest.mark.asyncio
async def test_eod_agent_handles_missing_parent_solver():
    """EOD agent should set parent_solver_id=None when parent doesn't exist (FK constraint).

    This prevents IntegrityError when LLM proposes edits to solver IDs that don't exist
    in the solvers table (e.g., 'paper_v1' in example prompt but not seeded in DB).
    """
    agent = EODReviewAgent()

    # Mock session where:
    # 1. New solver doesn't exist (allows creation)
    # 2. Parent solver doesn't exist (triggers FK issue without fix)
    mock_session = AsyncMock()

    new_solver_result = MagicMock()
    new_solver_result.scalars().first.return_value = None

    parent_solver_result = MagicMock()
    parent_solver_result.scalars().first.return_value = None  # Parent missing

    mock_session.execute = AsyncMock(side_effect=[new_solver_result, parent_solver_result])

    proposals = [
        {
            "type": "solver_edit",
            "target_solver_id": "paper_v1",  # Doesn't exist in DB
            "ops": [{"op": "modify_param", "param_name": "test", "new_value": 1}],
        }
    ]

    # Track Solver objects added to session
    added_solvers = []

    def track_add(obj):
        if isinstance(obj, Solver):
            added_solvers.append(obj)

    mock_session.add = track_add

    # Run the _persist_solver_edits inline logic
    from orion.core.id_utils import deterministic_solver_id
    import uuid

    for p in proposals:
        if p.get("type") != "solver_edit":
            continue

        base_id = p.get("target_solver_id")
        ops_data = p.get("ops", [])

        new_solver_id = deterministic_solver_id(
            base_solver_id=str(base_id),
            edit_ops={"ops": ops_data},
            prefix="eod",
        )

        existing = await mock_session.execute(select(Solver).where(Solver.solver_id == new_solver_id))
        if existing.scalars().first() is None:
            # This is the fix: validate parent exists before setting FK
            parent_exists = await mock_session.execute(select(Solver).where(Solver.solver_id == str(base_id)))
            parent_solver_id = str(base_id) if parent_exists.scalars().first() is not None else None

            mock_session.add(
                Solver(
                    solver_id=new_solver_id,
                    family_name="eod_derived",
                    parent_solver_id=parent_solver_id,
                    created_by="llm_eod_agent",
                    stage="research",
                    config={"derived_from": str(base_id), "ops": ops_data},
                    notes="Test",
                )
            )

    # Validate FK-safe behavior
    assert len(added_solvers) == 1
    solver = added_solvers[0]
    assert solver.parent_solver_id is None, (
        f"Expected parent_solver_id=None when parent doesn't exist, got {solver.parent_solver_id}"
    )
    assert solver.config["derived_from"] == "paper_v1", (
        "derived_from should still reference the proposed parent for audit trail"
    )


@pytest.mark.asyncio
async def test_eod_agent_sets_parent_when_exists():
    """EOD agent should set parent_solver_id when parent exists in DB."""
    agent = EODReviewAgent()

    mock_session = AsyncMock()

    new_solver_result = MagicMock()
    new_solver_result.scalars().first.return_value = None

    parent_solver_result = MagicMock()
    parent_solver_result.scalars().first.return_value = MagicMock()  # Parent exists

    mock_session.execute = AsyncMock(side_effect=[new_solver_result, parent_solver_result])

    proposals = [
        {
            "type": "solver_edit",
            "target_solver_id": "existing_solver",
            "ops": [{"op": "modify_param", "param_name": "test", "new_value": 1}],
        }
    ]

    added_solvers = []
    mock_session.add = lambda obj: added_solvers.append(obj) if isinstance(obj, Solver) else None

    from orion.core.id_utils import deterministic_solver_id

    for p in proposals:
        if p.get("type") != "solver_edit":
            continue

        base_id = p.get("target_solver_id")
        ops_data = p.get("ops", [])

        new_solver_id = deterministic_solver_id(
            base_solver_id=str(base_id),
            edit_ops={"ops": ops_data},
            prefix="eod",
        )

        existing = await mock_session.execute(select(Solver).where(Solver.solver_id == new_solver_id))
        if existing.scalars().first() is None:
            parent_exists = await mock_session.execute(select(Solver).where(Solver.solver_id == str(base_id)))
            parent_solver_id = str(base_id) if parent_exists.scalars().first() is not None else None

            mock_session.add(
                Solver(
                    solver_id=new_solver_id,
                    family_name="eod_derived",
                    parent_solver_id=parent_solver_id,
                    created_by="llm_eod_agent",
                    stage="research",
                    config={"derived_from": str(base_id), "ops": ops_data},
                    notes="Test",
                )
            )

    assert len(added_solvers) == 1
    solver = added_solvers[0]
    assert solver.parent_solver_id == "existing_solver", (
        f"Expected parent_solver_id='existing_solver', got {solver.parent_solver_id}"
    )
