from __future__ import annotations

from orion import main_execution


def test_legacy_candidate_status_helpers_removed() -> None:
    assert hasattr(main_execution, "fetch_pending_candidates")
    assert hasattr(main_execution, "update_decision_status")
    assert not hasattr(main_execution, "get_pending_candidates")
    assert not hasattr(main_execution, "update_candidate_status")
