from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from orion.config import system_settings
from orion.core.solver_router import SolverRouter
from orion.core.solver_schema import LiveContext


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_select_solvers_returns_synthetic_baseline_when_tables_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_settings, "baseline_solver_id", "baseline_v1")

    router = SolverRouter()
    context = LiveContext(
        ticker="SPY",
        regime="neutral",
        time_of_day_utc=datetime.now(UTC),
        current_stage="live",
    )

    mock_session = AsyncMock()
    mock_session.execute.side_effect = OperationalError(
        "SELECT * FROM solvers",
        {},
        Exception("no such table: solvers"),
    )

    session_cm = MagicMock()
    session_cm.__aenter__.return_value = mock_session
    session_cm.__aexit__.return_value = False

    with patch("orion.core.solver_router.async_session_factory", return_value=session_cm):
        selected = await router.select_solvers(context)

    assert len(selected) == 1
    assert selected[0].solver_id == "baseline_v1"
    assert selected[0].is_baseline is True
    assert selected[0].config.version_id == "baseline_v1"
