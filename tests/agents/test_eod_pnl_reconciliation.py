"""EOD-agent wiring for the RB.3 PnL reconciliation (report sections + alert).

Verifies the deterministic report rendering (reconciliation verdict, per-solver
and per-rule tables with sample sizes, demotion-candidates recommendation), the
2+-consecutive-day Discord escalation, and that run_review invokes the
reconciliation and appends the section — all WITHOUT mutating any solver stage.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.agents.eod_review_agent import DEMOTION_MIN_SAMPLE, EODReviewAgent
from orion.jobs.reconcile_pnl import (
    AttributionRow,
    BrokerReconstruction,
    JournalTotals,
    ReconcileResult,
)

TODAY = date(2026, 6, 11)


def _result(
    *,
    status="MATCH",
    journal_total=100.0,
    broker_total=100.0,
    drift_abs=0.0,
    solver_rows=None,
    rule_rows=None,
    consecutive=1,
) -> ReconcileResult:
    return ReconcileResult(
        trade_date=TODAY,
        journal=JournalTotals(total=journal_total, trade_count=len(solver_rows or []), win_count=0),
        broker=BrokerReconstruction(total=broker_total, order_count=2, per_symbol={}),
        status=status,
        drift_abs=drift_abs,
        drift_pct=None,
        tolerance_usd=0.01,
        solver_rows=solver_rows or [],
        rule_rows=rule_rows or [],
        consecutive_mismatch_days=consecutive,
    )


def test_render_includes_verdict_and_sample_sizes():
    result = _result(
        solver_rows=[AttributionRow("solver_a", 21, -5.0, 8)],
        rule_rows=[AttributionRow("BullishSweep", 21, -5.0, 8)],
    )
    md = EODReviewAgent._render_reconciliation_report(result)
    assert "PnL Reconciliation" in md
    assert "Result: OK" in md
    # Sample sizes always shown.
    assert "| solver_a | 21 |" in md
    assert "| BullishSweep | 21 |" in md


def test_render_lists_demotion_candidate_when_match_and_negative_expectancy():
    result = _result(
        solver_rows=[AttributionRow("loser", DEMOTION_MIN_SAMPLE, -50.0, 5)],
    )
    md = EODReviewAgent._render_reconciliation_report(result)
    assert "Demotion candidates" in md
    assert "RECOMMENDATION ONLY" in md
    assert "| loser |" in md


def test_render_suppresses_demotion_when_not_match():
    """Untrusted reconciliation → attribution is not actionable, no candidates."""
    result = _result(
        status="MISMATCH",
        drift_abs=50.0,
        solver_rows=[AttributionRow("loser", DEMOTION_MIN_SAMPLE, -50.0, 5)],
        consecutive=2,
    )
    md = EODReviewAgent._render_reconciliation_report(result)
    assert "not MATCH today" in md
    # The solver appears in the per-solver table (sample sizes always shown) but
    # NOT in the demotion-candidates section (attribution untrusted when not
    # MATCH). Assert the demotion section carries the suppression note instead.
    demotion_section = md.split("### Demotion candidates", 1)[1]
    assert "not MATCH today" in demotion_section
    assert "| loser |" not in demotion_section


def test_render_excludes_small_sample_solvers_from_demotion():
    result = _result(solver_rows=[AttributionRow("small", 5, -50.0, 1)])
    md = EODReviewAgent._render_reconciliation_report(result)
    # n=5 < DEMOTION_MIN_SAMPLE → not a candidate.
    assert "None — no solver has non-positive expectancy" in md


async def test_alert_escalates_on_two_consecutive_mismatch():
    result = _result(status="MISMATCH", drift_abs=50.0, consecutive=2)
    with patch("orion.shared.alerts.send_discord_alert", new_callable=AsyncMock) as mock_alert:
        await EODReviewAgent._maybe_alert_reconciliation_mismatch(result)
        mock_alert.assert_awaited_once()
        _args, kwargs = mock_alert.call_args
        assert kwargs.get("dedupe_key") == "pnl_reconcile_mismatch"


async def test_alert_silent_on_first_mismatch_day():
    result = _result(status="MISMATCH", drift_abs=50.0, consecutive=1)
    with patch("orion.shared.alerts.send_discord_alert", new_callable=AsyncMock) as mock_alert:
        await EODReviewAgent._maybe_alert_reconciliation_mismatch(result)
        mock_alert.assert_not_awaited()


async def test_alert_silent_on_match():
    result = _result(status="MATCH")
    with patch("orion.shared.alerts.send_discord_alert", new_callable=AsyncMock) as mock_alert:
        await EODReviewAgent._maybe_alert_reconciliation_mismatch(result)
        mock_alert.assert_not_awaited()


async def test_run_review_invokes_reconciliation_and_does_not_touch_solvers():
    mock_llm = AsyncMock()
    mock_llm.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content='{"analysis": "Mock", "proposals": []}'))
    ]
    agent = EODReviewAgent(llm_client=mock_llm)
    recon = _result(solver_rows=[AttributionRow("solver_a", 3, 9.0, 2)])

    with (
        patch.object(agent, "_gather_data", return_value=({"mock": "data"}, "snap.json")),
        patch.object(agent, "_fetch_rag_context", return_value="ctx"),
        patch.object(agent, "_persist_solver_edits", new_callable=AsyncMock) as persist_edits,
        patch.object(agent, "_run_pnl_reconciliation", new_callable=AsyncMock, return_value=recon) as run_recon,
        patch.object(agent, "_maybe_alert_reconciliation_mismatch", new_callable=AsyncMock),
        patch("builtins.open", new_callable=MagicMock),
        patch("os.makedirs"),
    ):
        result = await agent.run_review()

    run_recon.assert_awaited_once()
    assert "run_id" in result
    # The reconciliation path persists ONLY measurement rows; it must not feed
    # recon data into the solver-mutation (edit-persistence) path. That path is
    # called exactly once, with the LLM proposals only (empty here).
    persist_edits.assert_awaited_once()
    (called_proposals, _run_id) = persist_edits.call_args.args
    assert called_proposals == []
