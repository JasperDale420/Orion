"""EOD reconciliation must target the just-closed US trading session.

Adversarial-review finding O5: the post-close EOD trigger fires ~01:05 UTC and
historically called run_review() with no date, defaulting to "UTC today" (D+1).
The just-closed session D was therefore never reconciled. These tests pin the
trading-date computation (NYSE/XNYS calendar, ET handling) and verify run_review
defaults to and threads the last-closed session.
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.agents.eod_review_agent import EODReviewAgent
from orion.core.timekeeping import last_closed_trading_date


@pytest.mark.unit
@pytest.mark.parametrize(
    "trigger_utc, expected",
    [
        # Tue 01:05 UTC (EDT) == Mon 21:05 ET, post-close -> Monday's session.
        (datetime(2026, 6, 9, 1, 5, tzinfo=UTC), date(2026, 6, 8)),
        # Wed 01:05 UTC (EST) == Tue 20:05 ET, post-close -> Tuesday's session.
        (datetime(2026, 1, 14, 1, 5, tzinfo=UTC), date(2026, 1, 13)),
        # Saturday trigger -> Friday's session.
        (datetime(2026, 6, 6, 1, 5, tzinfo=UTC), date(2026, 6, 5)),
        # Sunday trigger -> Friday's session.
        (datetime(2026, 6, 7, 1, 5, tzinfo=UTC), date(2026, 6, 5)),
        # New Year's Day (holiday) -> prior open session (Dec 31).
        (datetime(2025, 1, 1, 1, 5, tzinfo=UTC), date(2024, 12, 31)),
    ],
)
def test_last_closed_trading_date(trigger_utc: datetime, expected: date):
    assert last_closed_trading_date(trigger_utc) == expected


@pytest.mark.unit
def test_last_closed_trading_date_requires_aware_datetime():
    with pytest.raises(ValueError):
        last_closed_trading_date(datetime(2026, 6, 9, 1, 5))


@pytest.mark.unit
def test_last_closed_trading_date_excludes_in_progress_session():
    """Mid-session (market open) the last *closed* session is the prior one."""
    # 2026-06-08 (Mon) 14:00 UTC == 10:00 ET, market open -> Friday is last closed.
    assert last_closed_trading_date(datetime(2026, 6, 8, 14, 0, tzinfo=UTC)) == date(2026, 6, 5)


@pytest.mark.asyncio
async def test_run_review_defaults_to_last_closed_session():
    """With no target_date, run_review uses the last-closed NYSE session and
    threads it into the PnL reconciliation, NOT UTC-today."""
    agent = EODReviewAgent(llm_client=AsyncMock())

    captured: dict[str, object] = {}

    async def fake_gather(target_date, *, run_id, reports_dir):
        captured["gather_date"] = target_date
        return ({"mock": "data"}, "snap.json")

    async def fake_reconcile(target_date):
        captured["reconcile_date"] = target_date
        return None

    with (
        patch(
            "orion.agents.eod_review_agent.last_closed_trading_date",
            return_value=date(2026, 6, 8),
        ) as mock_helper,
        patch.object(agent, "_gather_data", side_effect=fake_gather),
        patch.object(agent, "_run_pnl_reconciliation", side_effect=fake_reconcile),
        patch.object(agent, "_fetch_rag_context", return_value=""),
        patch.object(agent, "_fetch_valid_solver_ids", new_callable=AsyncMock, return_value=[]),
        patch.object(
            agent,
            "_generate_analysis",
            new_callable=AsyncMock,
            return_value={"analysis": "x", "proposals": []},
        ),
        patch.object(agent, "_persist_solver_edits", new_callable=AsyncMock),
        patch("builtins.open", new_callable=MagicMock),
        patch("os.makedirs"),
    ):
        result = await agent.run_review()

    mock_helper.assert_called_once()
    assert captured["gather_date"] == date(2026, 6, 8)
    assert captured["reconcile_date"] == date(2026, 6, 8)
    assert result["date"] == "2026-06-08"


@pytest.mark.asyncio
async def test_run_review_honours_explicit_target_date():
    """An explicit target_date overrides the computed default and is threaded."""
    agent = EODReviewAgent(llm_client=AsyncMock())

    captured: dict[str, object] = {}

    async def fake_gather(target_date, *, run_id, reports_dir):
        captured["gather_date"] = target_date
        return ({"mock": "data"}, "snap.json")

    async def fake_reconcile(target_date):
        captured["reconcile_date"] = target_date
        return None

    with (
        patch("orion.agents.eod_review_agent.last_closed_trading_date") as mock_helper,
        patch.object(agent, "_gather_data", side_effect=fake_gather),
        patch.object(agent, "_run_pnl_reconciliation", side_effect=fake_reconcile),
        patch.object(agent, "_fetch_rag_context", return_value=""),
        patch.object(agent, "_fetch_valid_solver_ids", new_callable=AsyncMock, return_value=[]),
        patch.object(
            agent,
            "_generate_analysis",
            new_callable=AsyncMock,
            return_value={"analysis": "x", "proposals": []},
        ),
        patch.object(agent, "_persist_solver_edits", new_callable=AsyncMock),
        patch("builtins.open", new_callable=MagicMock),
        patch("os.makedirs"),
    ):
        result = await agent.run_review(target_date=date(2026, 6, 5))

    mock_helper.assert_not_called()
    assert captured["gather_date"] == date(2026, 6, 5)
    assert captured["reconcile_date"] == date(2026, 6, 5)
    assert result["date"] == "2026-06-05"
