"""Unit tests for the Discord-notification wiring in the meta-search and
meta-weekly *scheduled* loops (main_meta.run_scheduled /
main_meta_weekly.run_scheduled).

These loops are the first-ever production runs of meta-search/meta-weekly,
so each fire must emit a Discord alert (success summary or failure with the
exception class). The loops are infinite `while True` pollers, so each test
drives exactly one fire by:
  - patching ``datetime`` so ``now`` lands on the loop's target fire time,
  - patching ``MetaSearchAgent`` with a fake that succeeds or raises, and
  - patching ``asyncio.sleep`` to raise ``_StopLoop`` (so the post-fire
    1-hour sleep terminates the loop after a single iteration).

``send_discord_alert`` is mocked everywhere — no network, no webhook.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

import orion.main_meta as main_meta
import orion.main_meta_weekly as main_meta_weekly

_ET = ZoneInfo("America/New_York")


class _StopLoop(Exception):
    """Sentinel raised from the patched asyncio.sleep to end the while-loop."""


def _fake_now_factory(fire_dt: datetime):
    """Return a stand-in for ``datetime`` whose ``.now(tz)`` is ``fire_dt``."""

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):  # noqa: ANN001 - mirrors datetime.now signature
            return fire_dt

    return _FakeDateTime


# --- meta-search daily loop -------------------------------------------------

# Monday 18:00 ET — the daily meta-search target fire time.
_META_FIRE = datetime(2026, 6, 8, 18, 0, tzinfo=_ET)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_meta_search_scheduled_success_sends_discord_alert() -> None:
    fake_agent = AsyncMock()
    fake_agent.run_evolution_cycle = AsyncMock(return_value=None)

    alert = AsyncMock(return_value=True)

    with (
        patch.object(main_meta, "datetime", _fake_now_factory(_META_FIRE)),
        patch.object(main_meta, "MetaSearchAgent", return_value=fake_agent),
        patch.object(main_meta, "send_discord_alert", alert),
        patch.object(main_meta.asyncio, "sleep", AsyncMock(side_effect=_StopLoop)),
    ):
        with pytest.raises(_StopLoop):
            await main_meta.run_scheduled("diversified_baseline_v1")

    fake_agent.run_evolution_cycle.assert_awaited_once()
    alert.assert_awaited_once()
    msg = alert.await_args.args[0]
    assert "completed" in msg
    assert "diversified_baseline_v1" in msg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_meta_search_scheduled_failure_sends_discord_alert() -> None:
    fake_agent = AsyncMock()
    fake_agent.run_evolution_cycle = AsyncMock(side_effect=ValueError("boom"))

    alert = AsyncMock(return_value=True)

    with (
        patch.object(main_meta, "datetime", _fake_now_factory(_META_FIRE)),
        patch.object(main_meta, "MetaSearchAgent", return_value=fake_agent),
        patch.object(main_meta, "send_discord_alert", alert),
        patch.object(main_meta.asyncio, "sleep", AsyncMock(side_effect=_StopLoop)),
    ):
        with pytest.raises(_StopLoop):
            await main_meta.run_scheduled("diversified_baseline_v1")

    alert.assert_awaited_once()
    msg = alert.await_args.args[0]
    assert "FAILED" in msg
    assert "ValueError" in msg
    assert "boom" in msg


# --- meta-weekly loop -------------------------------------------------------

# Friday 17:30 ET — the weekly evolution target fire time.
_WEEKLY_FIRE = datetime(2026, 6, 12, 17, 30, tzinfo=_ET)

_SUMMARY = {
    "eod_summary": {"reports_analyzed": 5},
    "mutations_applied": [{"id": "a"}, {"id": "b"}],
    "period": {"start": "2026-06-08T00:00:00", "end": "2026-06-12T00:00:00"},
}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_meta_weekly_scheduled_success_sends_discord_alert() -> None:
    fake_agent = AsyncMock()
    fake_agent.run_weekly_evolution = AsyncMock(return_value=_SUMMARY)

    alert = AsyncMock(return_value=True)
    promo = {"promoted": 1, "rejected": 0, "manual_required": 0}

    with (
        patch.object(main_meta_weekly, "datetime", _fake_now_factory(_WEEKLY_FIRE)),
        patch.object(main_meta_weekly, "MetaSearchAgent", return_value=fake_agent),
        patch.object(main_meta_weekly, "send_discord_alert", alert),
        patch.object(main_meta_weekly, "_save_summary", AsyncMock()),
        patch.object(main_meta_weekly, "run_solver_promotions", AsyncMock(return_value=promo)),
        patch.object(main_meta_weekly.asyncio, "sleep", AsyncMock(side_effect=_StopLoop)),
    ):
        with pytest.raises(_StopLoop):
            await main_meta_weekly.run_scheduled()

    fake_agent.run_weekly_evolution.assert_awaited_once()
    alert.assert_awaited_once()
    msg = alert.await_args.args[0]
    assert "completed" in msg
    assert "5 EOD reports" in msg
    assert "2 mutations" in msg
    assert "1 solvers promoted" in msg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_meta_weekly_scheduled_failure_sends_discord_alert() -> None:
    fake_agent = AsyncMock()
    fake_agent.run_weekly_evolution = AsyncMock(side_effect=RuntimeError("kaboom"))

    alert = AsyncMock(return_value=True)

    with (
        patch.object(main_meta_weekly, "datetime", _fake_now_factory(_WEEKLY_FIRE)),
        patch.object(main_meta_weekly, "MetaSearchAgent", return_value=fake_agent),
        patch.object(main_meta_weekly, "send_discord_alert", alert),
        patch.object(main_meta_weekly.asyncio, "sleep", AsyncMock(side_effect=_StopLoop)),
    ):
        with pytest.raises(_StopLoop):
            await main_meta_weekly.run_scheduled()

    alert.assert_awaited_once()
    msg = alert.await_args.args[0]
    assert "FAILED" in msg
    assert "RuntimeError" in msg
    assert "kaboom" in msg
