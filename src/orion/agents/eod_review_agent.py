import asyncio
import json
import math
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import func, select, text

from orion.agents.eod_metrics import (
    adverse_slippage_bps,
    aggregate_performance,
    classify_session,
    compute_baseline_slippage_rows,
    compute_volatility_regimes,
    index_orders_by_broker,
    percentile,
    psi,
    summarize_slippage,
)
from orion.clients.heber_reader import get_heber_reader
from orion.shared.logger import setup_logging

load_dotenv()

from orion.agents.codex_client import (
    extract_json_from_response,
    run_codex_completion,
)
from orion.agents.proposal_builder import ProposalBuilder
from orion.core.enums import DecisionAction
from orion.core.id_utils import deterministic_solver_id
from orion.core.timekeeping import last_closed_trading_date
from orion.rag.vector_store import VectorStore
from orion.shared.db_utils import db_query, db_write
from orion.shared.liveness import publish_liveness
from orion.shared.logger import setup_struct_logger
from orion.storage.models import BronzeEvent
from orion.storage.models_dlq import DeadLetterQueue
from orion.storage.models_execution import FillRecord, OrderRecord
from orion.storage.models_gold import StrategyDecision
from orion.storage.models_signals import SignalLive
from orion.storage.models_silver import SilverSignal
from orion.storage.models_solvers import Solver, SolverEdits
from orion.storage.models_trade_journal import TradeJournalEntry

logger = setup_struct_logger("orion.agents.eod_review_agent")

# Liveness cadence budget. The EOD review runs once per trading day; 1.5 days
# bridges a normal weekday gap before the dead-man watchdog flags a missed run.
# 3.5 days: survives weekends + a Monday holiday without false dead-man
# alerts (the run's own Discord success/failure alert is the primary signal;
# the dead-man is a backstop). Adversarial-review finding: 1.5d went stale
# every Sunday.
EOD_LIVENESS_CADENCE_BUDGET_SECONDS = int(86400 * 3.5)

# RB.3 demotion-candidate gate (RECOMMENDATION ONLY — no stage flag is moved by
# any code in this task; actual demotion is the separately-gated task RD.1).
# A solver is *listed* as a demotion candidate when its reconciled realized-PnL
# expectancy is non-positive AND it has at least this many closed trades.
DEMOTION_MIN_SAMPLE = 20


class EODReviewAgent:
    """
    PRD 17: Daily EOD Review Agent.
    - Inspects trades/outcomes
    - Uses RAG for historical context
    - Proposes SolverEdits for Meta-Search Layer
    """

    def __init__(
        self,
        *,
        llm_client: Any | None = None,
        vector_store: Any | None = None,
        proposal_builder: ProposalBuilder | None = None,
    ):
        self.llm_client = llm_client
        self.vector_store = vector_store or VectorStore()
        self.proposal_builder = proposal_builder or ProposalBuilder()

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Satisfies BaseAgent interface. Wraps run_review.
        """
        target_date = context.get("date")
        result = await self.run_review(target_date)
        return {"status": "completed", **(result or {})}

    async def run_review(self, target_date: date | None = None) -> dict[str, Any]:
        if not target_date:
            # Default to the most-recently-CLOSED NYSE session, NOT "UTC today".
            # A post-close trigger fires shortly after midnight UTC (the prior
            # evening in ET); UTC-today would be the next calendar day and the
            # just-closed session would never be reconciled.
            target_date = last_closed_trading_date()

        logger.info(f"Starting EOD Review for {target_date}...")
        run_id = str(uuid.uuid4())

        # Ensure artifacts directory exists
        from orion.config import system_settings

        reports_dir = os.path.join(system_settings.artifacts_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)

        # 1. Gather Data
        data, input_snapshot_path = await self._gather_data(target_date, run_id=run_id, reports_dir=reports_dir)

        # 1b. RB.3: journal-vs-broker PnL reconciliation + per-solver/per-rule
        # attribution. Runs INSIDE the EOD trigger (no standalone daemon), so the
        # EOD agent's own liveness publish covers it. Recommendations-only — no
        # solver stage flags are touched here. Self-isolating: any failure logs
        # and yields None so the rest of the review still completes.
        reconcile_result = await self._run_pnl_reconciliation(target_date)
        if reconcile_result is not None:
            data["pnl_reconciliation"] = self._reconcile_snapshot_for_llm(reconcile_result)

        # 2. RAG Context Lookup (VS5)
        # Search for recent similar performance issues or strategy docs
        rag_context = await self._fetch_rag_context("performance drift strategy issues")

        # 2b. Resolve the real, valid solver ids the LLM may target. The prompt
        # must never hardcode an id (e.g. 'paper_v1') that isn't seeded, or the
        # derived-solver insert FK-violates and aborts the run.
        valid_solver_ids = await self._fetch_valid_solver_ids()

        # 3. LLM Analysis & Proposal Generation
        analysis_json = await self._generate_analysis(data, rag_context, valid_solver_ids)

        # 4. Save Artifacts
        # Save Markdown Report
        report_text = analysis_json.get("analysis", "No analysis generated.")
        filename = f"eod_report_{target_date}.md"
        file_path = os.path.join(reports_dir, filename)

        def _write_report():
            with open(file_path, "w") as f:
                f.write(report_text)

        await asyncio.to_thread(_write_report)

        # 4b. RB.3: append the DETERMINISTIC reconciliation + attribution +
        # demotion-candidate sections to the report. These are computed in code
        # (not LLM-generated) so the reconciliation verdict, drift, and sample
        # sizes are always present and exact, regardless of LLM output.
        if reconcile_result is not None:
            recon_md = self._render_reconciliation_report(reconcile_result)

            def _append_report():
                with open(file_path, "a") as f:
                    f.write("\n\n" + recon_md)

            await asyncio.to_thread(_append_report)
            await self._maybe_alert_reconciliation_mismatch(reconcile_result)

        # Save Proposals
        proposals = analysis_json.get("proposals", [])
        saved_paths = []

        # PRDv2 §5.7.2: EOD agent writes proposals into solver_edits with generated_by='llm_eod_agent' and reward=NULL.
        await self._persist_solver_edits(proposals, run_id)

        for p in proposals:
            # We persist them as YAML artifacts for the Meta-Search or Human Review
            path = self.proposal_builder.save_proposal(
                p,
                str(target_date),
                run_id,
                input_snapshot_path=input_snapshot_path,
                report_path=file_path,
            )
            if path:
                saved_paths.append(path)

        logger.info(f"EOD Review Complete. Report: {file_path}. Proposals: {len(saved_paths)}")

        # Liveness: one publish per completed review (swallows its own errors).
        await publish_liveness("eod_agent", cadence_budget_seconds=EOD_LIVENESS_CADENCE_BUDGET_SECONDS)

        return {
            "run_id": run_id,
            "date": str(target_date),
            "report_path": file_path,
            "input_snapshot_path": input_snapshot_path,
            "proposal_paths": saved_paths,
            "proposals_count": len(proposals),
            "solver_edit_proposals": [p for p in proposals if p.get("type") == "solver_edit"],
        }

    # ── RB.3: PnL reconciliation + attribution ───────────────────────────

    async def _run_pnl_reconciliation(self, target_date: datetime.date) -> Any | None:
        """Compute + persist the journal-vs-broker reconciliation for the day.

        Returns the ``ReconcileResult`` (used to render the report section and
        drive the mismatch alert), or ``None`` on any failure — a reconciliation
        problem must never abort the EOD review.
        """
        try:
            from orion.jobs.reconcile_pnl import run_reconciliation

            # publish_liveness_row=False: the EOD agent's own liveness publish
            # (end of run_review) already covers this work; we do not register a
            # second service row in the dead-man watchdog.
            return await run_reconciliation(target_date, publish_liveness_row=False)
        except Exception:
            logger.error("eod_pnl_reconciliation_failed", exc_info=True)
            return None

    @staticmethod
    def _reconcile_snapshot_for_llm(result: Any) -> dict[str, Any]:
        """Compact, JSON-safe view of the reconciliation for the LLM snapshot."""
        return {
            "status": result.status,
            "journal_total": result.journal.total,
            "broker_total": result.broker.total,
            "drift_abs": result.drift_abs,
            "drift_pct": result.drift_pct,
            "journal_trade_count": result.journal.trade_count,
            "broker_order_count": result.broker.order_count,
            "tolerance_usd": result.tolerance_usd,
            "consecutive_mismatch_days": result.consecutive_mismatch_days,
            "note": "Journal realized-PnL has NOT been broker-trusted historically; "
            "this line is the verification gate. Recommendations only — no solver "
            "stage flags change.",
        }

    @staticmethod
    def _render_reconciliation_report(result: Any) -> str:
        """Render the deterministic markdown: reconciliation verdict, per-solver
        and per-rule PnL tables (sample sizes ALWAYS shown), and the
        demotion-candidates section (clearly labeled recommendation-only)."""
        status_marker = {"MATCH": "OK", "MISMATCH": "MISMATCH", "NO_DATA": "no data"}.get(result.status, result.status)
        drift_pct = f"{result.drift_pct:.2f}%" if result.drift_pct is not None else "n/a"
        lines: list[str] = []
        lines.append("## PnL Reconciliation (journal vs broker) — RB.3")
        lines.append("")
        lines.append(f"**Result: {status_marker}** — drift ${result.drift_abs:.2f} ({drift_pct})")
        lines.append("")
        lines.append(
            f"- Journal realized PnL (orion-attributed, closed): ${result.journal.total:.2f} "
            f"across {result.journal.trade_count} trades"
        )
        lines.append(
            f"- Broker realized PnL (orion fills, reconstructed): ${result.broker.total:.2f} "
            f"across {result.broker.order_count} filled orders"
        )
        lines.append(f"- Tolerance: ${result.tolerance_usd:.2f}/trade")
        if result.broker.non_flat_symbols:
            lines.append(
                f"- NOTE: symbols not flat at day end (cashflow includes open exposure): "
                f"{', '.join(result.broker.non_flat_symbols)}"
            )
        if result.status == "MISMATCH":
            lines.append(
                f"- ESCALATION: MISMATCH for {result.consecutive_mismatch_days} consecutive day(s). "
                "Journal PnL is NOT trustworthy until this reconciles — do NOT act on attribution below."
            )
        lines.append("")

        lines.append("### Per-solver realized PnL (sample sizes shown)")
        lines.append("")
        lines.append("| solver_id | n_trades | realized_pnl | expectancy | wins |")
        lines.append("|---|---:|---:|---:|---:|")
        if result.solver_rows:
            for row in result.solver_rows:
                exp = f"{row.expectancy:.2f}" if row.expectancy is not None else "n/a"
                lines.append(f"| {row.key} | {row.n_trades} | {row.realized_pnl_total:.2f} | {exp} | {row.win_count} |")
        else:
            lines.append("| _(no closed orion trades)_ | 0 | 0.00 | n/a | 0 |")
        lines.append("")

        lines.append("### Per-rule realized PnL (sample sizes shown)")
        lines.append("")
        lines.append("| rule_id | n_trades | realized_pnl | expectancy | wins |")
        lines.append("|---|---:|---:|---:|---:|")
        if result.rule_rows:
            for row in result.rule_rows:
                exp = f"{row.expectancy:.2f}" if row.expectancy is not None else "n/a"
                lines.append(f"| {row.key} | {row.n_trades} | {row.realized_pnl_total:.2f} | {exp} | {row.win_count} |")
        else:
            lines.append("| _(no closed orion trades)_ | 0 | 0.00 | n/a | 0 |")
        lines.append("")

        candidates = [
            row
            for row in result.solver_rows
            if row.n_trades >= DEMOTION_MIN_SAMPLE and (row.expectancy is None or row.expectancy <= 0.0)
        ]
        lines.append("### Demotion candidates — RECOMMENDATION ONLY (no stage flags changed)")
        lines.append("")
        lines.append(
            f"Solvers with non-positive reconciled expectancy AND n>={DEMOTION_MIN_SAMPLE} closed trades. "
            "This is a recommendation for human review (task RD.1); NO solver stage is modified by this report."
        )
        lines.append("")
        if result.status != "MATCH":
            lines.append(
                "_Reconciliation is not MATCH today — the attribution above is NOT broker-trusted, "
                "so no demotion candidates are listed._"
            )
        elif candidates:
            lines.append("| solver_id | n_trades | realized_pnl | expectancy |")
            lines.append("|---|---:|---:|---:|")
            for row in candidates:
                exp = f"{row.expectancy:.2f}" if row.expectancy is not None else "n/a"
                lines.append(f"| {row.key} | {row.n_trades} | {row.realized_pnl_total:.2f} | {exp} |")
        else:
            lines.append(f"_None — no solver has non-positive expectancy with n>={DEMOTION_MIN_SAMPLE}._")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    async def _maybe_alert_reconciliation_mismatch(result: Any) -> None:
        """Send a Discord alert; escalate when MISMATCH persists 2+ days.

        A single-day mismatch is logged but not paged (could be a one-off
        reconstruction edge). 2+ consecutive MISMATCH days is an escalation —
        the journal PnL write-back is systematically wrong and nothing may act
        on it. Uses dedupe_key='pnl_reconcile_mismatch' to avoid in-run spam.
        """
        if result.status != "MISMATCH":
            return
        from orion.shared.alerts import send_discord_alert

        if result.consecutive_mismatch_days >= 2:
            msg = (
                f"PnL reconciliation MISMATCH {result.consecutive_mismatch_days} days running "
                f"({result.trade_date}): journal ${result.journal.total:.2f} vs broker "
                f"${result.broker.total:.2f}, drift ${result.drift_abs:.2f}. "
                "Journal realized-PnL is NOT broker-trusted — block any action on attribution."
            )
            await send_discord_alert(msg, dedupe_key="pnl_reconcile_mismatch")
        else:
            logger.warning(
                "pnl_reconcile_mismatch_first_day",
                trade_date=str(result.trade_date),
                drift_abs=result.drift_abs,
            )

    async def _fetch_valid_solver_ids(self) -> list[str]:
        """Return the solver ids the LLM may legally target as parent/base.

        A proposal's target_solver_id becomes the parent_solver_id of an
        auto-derived solver, which is FK-constrained to ``solvers.solver_id``.
        Surfacing the real list to the prompt (and validating against it before
        insert) prevents the FK violation that aborts the run.
        """

        async def query(session: Any) -> list[str]:
            result = await session.execute(select(Solver.solver_id, Solver.stage))
            rows = result.all()
            # Prefer non-research solvers (paper/live/shadow) as edit targets, but
            # return all ids so validation never rejects a real solver.
            return [str(r[0]) for r in rows]

        try:
            return await db_query(query)
        except Exception:
            logger.warning("eod_valid_solver_fetch_failed", exc_info=True)
            return []

    async def _persist_solver_edits(self, proposals: list[dict[str, Any]], run_id: str) -> None:
        if not proposals:
            return

        for p in proposals:
            if p.get("type") != "solver_edit":
                continue

            base_id = p.get("target_solver_id")
            ops_data = p.get("ops", [])
            if not base_id or not isinstance(ops_data, list) or not ops_data:
                continue

            try:
                await self._persist_one_solver_edit(str(base_id), ops_data, run_id)
            except Exception as e:
                # Isolate failures so one bad proposal cannot abort the whole run
                # (report + remaining YAML artifacts must still be saved).
                logger.error(
                    "eod_proposal_persist_failed",
                    target_solver_id=str(base_id),
                    run_id=run_id,
                    error=str(e),
                    exc_info=True,
                )

    async def _persist_one_solver_edit(self, base_id: str, ops_data: list[Any], run_id: str) -> None:
        new_solver_id = deterministic_solver_id(
            base_solver_id=base_id,
            edit_ops={"ops": ops_data},
            prefix="eod",
        )

        async def save_edit(session: Any) -> None:
            # Validate the parent/target solver exists before inserting a
            # derived solver (whose parent_solver_id is FK-constrained). An
            # invalid id (e.g. a hallucinated 'paper_v1') is skipped, not raised.
            parent_exists = (
                await session.execute(select(Solver.solver_id).where(Solver.solver_id == base_id))
            ).scalars().first() is not None
            if not parent_exists:
                logger.warning(
                    "eod_proposal_invalid_solver",
                    target_solver_id=base_id,
                    run_id=run_id,
                )
                return

            existing = await session.execute(select(Solver).where(Solver.solver_id == new_solver_id))
            if existing.scalars().first() is None:
                # Create solver stub in research stage
                session.add(
                    Solver(
                        solver_id=new_solver_id,
                        family_name="eod_derived",
                        parent_solver_id=base_id,
                        created_by="llm_eod_agent",
                        stage="research",
                        config={"derived_from": base_id, "ops": ops_data},
                        notes=f"Auto-generated from EOD review run {run_id}",
                    )
                )

            session.add(
                SolverEdits(
                    id=str(uuid.uuid4()),
                    experiment_id=None,
                    base_solver_id=base_id,
                    new_solver_id=new_solver_id,
                    edit_json={"ops": ops_data, "run_id": run_id},
                    generated_by="llm_eod_agent",
                    reward=None,
                )
            )

        await db_write(save_edit)

    def _day_bounds_utc(self, date: datetime.date) -> tuple[datetime, datetime]:
        start_ts = datetime.combine(date, datetime.min.time()).replace(tzinfo=UTC)
        end_ts = start_ts + timedelta(days=1)
        return start_ts, end_ts

    def _classify_session(self, ts_utc: datetime | None) -> str:
        return classify_session(ts_utc)

    def _psi(self, baseline: list[float], current: list[float], *, bins: int = 10) -> float | None:
        return psi(baseline, current, bins=bins)

    def _adverse_slippage_bps(
        self, *, side: str | None, limit_price: float | None, fill_price: float | None
    ) -> float | None:
        return adverse_slippage_bps(side=side, limit_price=limit_price, fill_price=fill_price)

    @staticmethod
    def _index_orders_by_broker(orders: list[Any]) -> dict[str, Any]:
        return index_orders_by_broker(orders)

    def _compute_baseline_slippage_rows(
        self, fills: list[Any], orders_by_broker: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return compute_baseline_slippage_rows(fills, orders_by_broker)

    @staticmethod
    def _summarize_slippage(slippage_rows: list[dict[str, Any]], fills_count: int) -> dict[str, Any]:
        return summarize_slippage(slippage_rows, fills_count)

    async def _load_regime_bars_from_heber(self, tickers: list[str], start_ts: datetime, end_ts: datetime) -> list[Any]:
        if not tickers:
            return []

        reader = get_heber_reader()
        try:
            frame = await asyncio.to_thread(
                reader.read_bars,
                symbols=tickers,
                asof_time=end_ts,
                start_time=start_ts,
                end_time=end_ts,
            )
        except Exception:
            logger.debug("Heber bars read failed for EOD regime context", exc_info=True)
            return []

        if frame.empty:
            return []

        ticker_col = None
        for candidate in ("ticker", "symbol", "underlying", "instrument_key"):
            if candidate in frame.columns:
                ticker_col = candidate
                break
        close_col = "close" if "close" in frame.columns else ("c" if "c" in frame.columns else None)
        if ticker_col is None or close_col is None:
            return []

        tickers_upper = {str(t).upper() for t in tickers}
        rows: list[Any] = []
        for _, row in frame.iterrows():
            ticker_raw = row.get(ticker_col)
            if ticker_raw is None:
                continue
            ticker = str(ticker_raw).upper().split(":")[-1]
            if ticker not in tickers_upper:
                continue
            close = pd.to_numeric(row.get(close_col), errors="coerce")
            if pd.isna(close):
                continue
            rows.append(SimpleNamespace(ticker=ticker, close=float(close)))
        return rows

    async def _gather_data(self, date: datetime.date, *, run_id: str, reports_dir: str) -> tuple[dict[str, Any], str]:
        """
        Gather metrics, decisions, and outcomes for the day.
        """
        start_ts, end_ts = self._day_bounds_utc(date)

        async def fetch_all_data(session: Any) -> dict[str, Any]:
            # Decisions for the day
            stmt = select(StrategyDecision).where(
                StrategyDecision.timestamp_utc >= start_ts, StrategyDecision.timestamp_utc < end_ts
            )
            result = await session.execute(stmt)
            decisions = result.scalars().all()

            # Signals live
            sig_stmt = select(SignalLive).where(SignalLive.timestamp_utc >= start_ts, SignalLive.timestamp_utc < end_ts)
            sigs = (await session.execute(sig_stmt)).scalars().all()

            # Trade journal entries
            tj_stmt = select(TradeJournalEntry).where(
                TradeJournalEntry.created_at_utc >= start_ts,
                TradeJournalEntry.created_at_utc < end_ts,
            )
            trade_journal = (await session.execute(tj_stmt)).scalars().all()

            # Orders
            order_stmt = select(OrderRecord).where(
                OrderRecord.created_at_utc >= start_ts,
                OrderRecord.created_at_utc < end_ts,
            )
            orders = (await session.execute(order_stmt)).scalars().all()

            # Fills (use filled_at_utc when present, fallback to created_at_utc)
            fill_stmt = select(FillRecord).where(
                ((FillRecord.filled_at_utc >= start_ts) & (FillRecord.filled_at_utc < end_ts))
                | (
                    (FillRecord.filled_at_utc.is_(None))
                    & (FillRecord.created_at_utc >= start_ts)
                    & (FillRecord.created_at_utc < end_ts)
                )
            )
            fills = (await session.execute(fill_stmt)).scalars().all()

            # Rolling baseline for drift metrics (previous 20d window)
            baseline_start = start_ts - timedelta(days=20)

            baseline_order_stmt = select(OrderRecord).where(
                OrderRecord.created_at_utc >= baseline_start,
                OrderRecord.created_at_utc < start_ts,
            )
            baseline_orders = (await session.execute(baseline_order_stmt)).scalars().all()

            baseline_fill_stmt = select(FillRecord).where(
                ((FillRecord.filled_at_utc >= baseline_start) & (FillRecord.filled_at_utc < start_ts))
                | (
                    (FillRecord.filled_at_utc.is_(None))
                    & (FillRecord.created_at_utc >= baseline_start)
                    & (FillRecord.created_at_utc < start_ts)
                )
            )
            baseline_fills = (await session.execute(baseline_fill_stmt)).scalars().all()

            baseline_tj_stmt = select(TradeJournalEntry).where(
                TradeJournalEntry.created_at_utc >= baseline_start,
                TradeJournalEntry.created_at_utc < start_ts,
            )
            baseline_trade_journal = (await session.execute(baseline_tj_stmt)).scalars().all()

            # DLQ
            dlq_stmt = select(DeadLetterQueue).where(
                DeadLetterQueue.timestamp_utc >= start_ts,
                DeadLetterQueue.timestamp_utc < end_ts,
            )
            dlq_rows = (await session.execute(dlq_stmt)).scalars().all()

            # Bronze ingestion lag/gaps
            bronze_stmt = select(BronzeEvent).where(
                BronzeEvent.received_ts_utc >= start_ts,
                BronzeEvent.received_ts_utc < end_ts,
            )
            bronze_rows = (await session.execute(bronze_stmt)).scalars().all()

            # Feature drift data: pull sampled daily OHLCV signals and a rolling baseline window (previous 20d)
            # Limit to 5000 rows each to prevent OOM (full baseline can be 500K+ rows)
            silver_today_stmt = (
                select(SilverSignal)
                .where(
                    SilverSignal.signal_ts_utc >= start_ts,
                    SilverSignal.signal_ts_utc < end_ts,
                )
                .order_by(func.random())
                .limit(5000)
            )
            silver_base_stmt = (
                select(SilverSignal)
                .where(
                    SilverSignal.signal_ts_utc >= baseline_start,
                    SilverSignal.signal_ts_utc < start_ts,
                )
                .order_by(func.random())
                .limit(5000)
            )
            silver_today = (await session.execute(silver_today_stmt)).scalars().all()
            silver_baseline = (await session.execute(silver_base_stmt)).scalars().all()

            # Volatility regime from bars (intraday realized vol)
            tickers_for_regime = {s.ticker for s in sigs if s.ticker} | {t.ticker for t in trade_journal if t.ticker}

            if tickers_for_regime:
                bars = await self._load_regime_bars_from_heber(
                    tickers=sorted(tickers_for_regime),
                    start_ts=start_ts,
                    end_ts=end_ts,
                )
            else:
                bars = []

            return {
                "decisions": decisions,
                "sigs": sigs,
                "trade_journal": trade_journal,
                "orders": orders,
                "fills": fills,
                "baseline_orders": baseline_orders,
                "baseline_fills": baseline_fills,
                "baseline_trade_journal": baseline_trade_journal,
                "dlq_rows": dlq_rows,
                "bronze_rows": bronze_rows,
                "silver_today": silver_today,
                "silver_baseline": silver_baseline,
                "bars": bars,
                "baseline_start": baseline_start,
            }

        data = await db_query(fetch_all_data)

        decisions = data["decisions"]
        sigs = data["sigs"]
        trade_journal = data["trade_journal"]
        orders = data["orders"]
        fills = data["fills"]
        baseline_orders = data["baseline_orders"]
        baseline_fills = data["baseline_fills"]
        baseline_trade_journal = data["baseline_trade_journal"]
        dlq_rows = data["dlq_rows"]
        bronze_rows = data["bronze_rows"]
        silver_today = data["silver_today"]
        silver_baseline = data["silver_baseline"]
        bars = data["bars"]
        baseline_start = data["baseline_start"]

        total_decisions = len(decisions)
        executed_count = sum(1 for d in decisions if d.decision == DecisionAction.EXECUTE)
        skipped_count = sum(1 for d in decisions if d.decision == DecisionAction.SKIP)

        # --- slippage joins ---
        baseline_orders_by_broker = self._index_orders_by_broker(baseline_orders)
        orders_by_broker = self._index_orders_by_broker(orders)

        slippage_rows: list[dict[str, Any]] = []
        for f in fills:
            order = orders_by_broker.get(f.broker_order_id)
            limit_price = order.limit_price if order is not None else None
            adverse_bps = self._adverse_slippage_bps(
                side=f.side, limit_price=limit_price, fill_price=f.filled_avg_price
            )
            slippage_rows.append(
                {
                    "fill_id": f.id,
                    "ticker": f.ticker,
                    "broker_order_id": f.broker_order_id,
                    "side": f.side,
                    "filled_qty": f.filled_qty,
                    "fill_price": f.filled_avg_price,
                    "limit_price": limit_price,
                    "adverse_slippage_bps": adverse_bps,
                    "filled_at_utc": f.filled_at_utc.isoformat() if f.filled_at_utc else None,
                    "linked_order": order is not None,
                    "order_id": order.id if order is not None else None,
                }
            )

        slippage_summary = self._summarize_slippage(slippage_rows, len(fills))

        baseline_slippage_rows = self._compute_baseline_slippage_rows(baseline_fills, baseline_orders_by_broker)
        baseline_slippage_summary = self._summarize_slippage(baseline_slippage_rows, len(baseline_fills))
        baseline_slippage_summary["window_utc"] = {"start": baseline_start.isoformat(), "end": start_ts.isoformat()}

        _, regime_map, regime_stats = compute_volatility_regimes(bars)

        # --- ingestion lag/gap stats ---
        lag_by_key: dict[str, list[float]] = {}
        gaps_by_key: dict[str, float] = {}
        last_recv_by_key: dict[str, datetime] = {}
        for ev in sorted(bronze_rows, key=lambda x: x.received_ts_utc or start_ts):
            key = f"{ev.source}:{ev.event_type}"
            if ev.event_ts_utc and ev.received_ts_utc:
                lag = (ev.received_ts_utc - ev.event_ts_utc).total_seconds()
                if math.isfinite(lag):
                    lag_by_key.setdefault(key, []).append(float(lag))
            prev = last_recv_by_key.get(key)
            if prev is not None and ev.received_ts_utc:
                gap = (ev.received_ts_utc - prev).total_seconds()
                gaps_by_key[key] = max(gaps_by_key.get(key, 0.0), float(gap))
            if ev.received_ts_utc:
                last_recv_by_key[key] = ev.received_ts_utc

        ingestion_stats: dict[str, Any] = {"sources": {}}
        for key, vals in lag_by_key.items():
            ingestion_stats["sources"][key] = {
                "count": len(vals),
                "lag_p50_s": percentile(vals, 0.50),
                "lag_p95_s": percentile(vals, 0.95),
                "lag_max_s": max(vals) if vals else None,
                "max_gap_s": gaps_by_key.get(key),
            }

        # --- performance metrics (grouped) ---
        signals_by_id = {s.signal_id: s for s in sigs}

        trade_rows = []
        for t in trade_journal:
            sig = signals_by_id.get(t.signal_id)
            ts = sig.timestamp_utc if sig is not None else t.created_at_utc
            session_label = self._classify_session(ts)
            regime_label = regime_map.get(t.ticker, "UNKNOWN")
            trade_rows.append(
                {
                    "ticker": t.ticker,
                    "rule_id": sig.rule_id if sig is not None else None,
                    "model_version": sig.model_version if sig is not None else None,
                    "session": session_label,
                    "regime": regime_label,
                    "realized_pnl": t.realized_pnl,
                }
            )

        performance_metrics = {
            "by_rule": aggregate_performance(trade_rows, "rule_id"),
            "by_model": aggregate_performance(trade_rows, "model_version"),
            "by_ticker": aggregate_performance(trade_rows, "ticker"),
            "by_session": aggregate_performance(trade_rows, "session"),
            "by_regime": aggregate_performance(trade_rows, "regime"),
        }

        baseline_pnls = [t.realized_pnl for t in baseline_trade_journal if t.realized_pnl is not None]
        today_pnls = [r["realized_pnl"] for r in trade_rows if r.get("realized_pnl") is not None]

        # --- drift metrics (feature PSI + slippage drift) ---
        def _extract_feature(rows: list[SilverSignal], feature_key: str) -> list[float]:
            vals: list[float] = []
            for r in rows:
                if not r.features:
                    continue
                v = r.features.get(feature_key)
                if isinstance(v, (int, float)) and math.isfinite(float(v)):
                    vals.append(float(v))
            return vals

        drift_features = ["close", "volume", "vwap", "flow_net_premium_15m", "flow_count_15m"]
        feature_shift: dict[str, Any] = {}
        for fk in drift_features:
            bvals = _extract_feature(silver_baseline, fk)
            cvals = _extract_feature(silver_today, fk)
            feature_shift[fk] = {
                "baseline_n": len(bvals),
                "current_n": len(cvals),
                "psi": self._psi(bvals, cvals, bins=10),
            }

        # Trigger pattern mining if high drift detected
        try:
            from orion.core.drift_trigger import set_drift_flag

            psi_values = {k: v.get("psi") for k, v in feature_shift.items() if v.get("psi") is not None}
            if await set_drift_flag(psi_values, source="eod_agent"):
                logger.info(
                    "High drift detected, pattern mining will be triggered",
                    extra={"event": "drift_trigger_set", "psi_values": psi_values},
                )
        except Exception as e:
            logger.warning(f"Failed to check/set drift flag: {e}")

        drift_metrics = {
            "feature_distribution_shift": feature_shift,
            "execution_slippage": {
                "today": slippage_summary,
                "rolling_baseline": baseline_slippage_summary,
                "mean_adverse_slippage_bps_delta": (
                    (
                        slippage_summary["mean_adverse_slippage_bps"]
                        - baseline_slippage_summary["mean_adverse_slippage_bps"]
                    )
                    if (
                        slippage_summary["mean_adverse_slippage_bps"] is not None
                        and baseline_slippage_summary["mean_adverse_slippage_bps"] is not None
                    )
                    else None
                ),
            },
            "degradation_vs_rolling_baseline": {
                "window_utc": {"start": baseline_start.isoformat(), "end": start_ts.isoformat()},
                "today_realized_pnl_count": len(today_pnls),
                "baseline_realized_pnl_count": len(baseline_pnls),
                "today_realized_pnl_sum": float(sum(today_pnls)) if today_pnls else None,
                "baseline_realized_pnl_sum": float(sum(baseline_pnls)) if baseline_pnls else None,
            },
            "backtest_vs_live_deltas": {
                "available": False,
                "reason": "Backtest regression outputs are not persisted in a queryable table in this codebase yet.",
            },
        }

        errors_incidents = {
            "dlq_events": {"count": len(dlq_rows)},
            "ingestion": ingestion_stats,
        }

        payload: dict[str, Any] = {
            "run_id": run_id,
            "date": str(date),
            "window_utc": {"start": start_ts.isoformat(), "end": end_ts.isoformat()},
            "decisions": {
                "total": total_decisions,
                "executed_count": executed_count,
                "skipped_count": skipped_count,
                "sample": [
                    {
                        "decision_id": d.decision_id,
                        "timestamp_utc": d.timestamp_utc.isoformat() if d.timestamp_utc else None,
                        "ticker": d.ticker,
                        "action": d.decision,
                        "reason": d.reason,
                        "solver": d.strategy_version_id,
                        "p_take": d.p_take,
                        "execution_params": d.execution_params or {},
                    }
                    for d in sorted(decisions, key=lambda x: x.timestamp_utc or start_ts)[:50]
                ],
            },
            "signals_live": {
                "total": len(sigs),
                "sample": [
                    {
                        "signal_id": s.signal_id,
                        "timestamp_utc": s.timestamp_utc.isoformat() if s.timestamp_utc else None,
                        "ticker": s.ticker,
                        "direction": s.direction,
                        "rule_id": s.rule_id,
                        "model_version": s.model_version,
                        "expected_return": s.expected_return,
                        "p_take": s.p_take,
                        "risk_score": s.risk_score,
                        "entry_logic": s.entry_logic or {},
                        "exit_rules": s.exit_rules or {},
                        "evidence": s.evidence or {},
                    }
                    for s in sorted(sigs, key=lambda x: x.timestamp_utc)[:50]
                ],
            },
            "trade_journal": {
                "total": len(trade_journal),
                "sample": [
                    {
                        "decision_id": t.decision_id,
                        "created_at_utc": t.created_at_utc.isoformat() if t.created_at_utc else None,
                        "signal_id": t.signal_id,
                        "candidate_id": t.candidate_id,
                        "solver_id": t.solver_id,
                        "ticker": t.ticker,
                        "direction": t.direction,
                        "client_order_id": t.client_order_id,
                        "broker_order_id": t.broker_order_id,
                        "filled_qty": t.filled_qty,
                        "filled_avg_price": t.filled_avg_price,
                        "filled_at_utc": t.filled_at_utc.isoformat() if t.filled_at_utc else None,
                        "evidence": t.evidence or {},
                    }
                    for t in sorted(trade_journal, key=lambda x: x.created_at_utc or start_ts)[:50]
                ],
            },
            "orders": {
                "total": len(orders),
                "sample": [
                    {
                        "order_id": o.id,
                        "created_at_utc": o.created_at_utc.isoformat() if o.created_at_utc else None,
                        "decision_id": o.decision_id,
                        "candidate_id": o.candidate_id,
                        "ticker": o.ticker,
                        "side": o.side,
                        "qty": o.qty,
                        "limit_price": o.limit_price,
                        "client_order_id": o.client_order_id,
                        "broker_order_id": o.broker_order_id,
                        "status": o.status,
                        "error_message": o.error_message,
                    }
                    for o in sorted(orders, key=lambda x: x.created_at_utc)[:50]
                ],
            },
            "fills": {
                "total": len(fills),
                "sample": slippage_rows[:100],
            },
            "slippage_summary": slippage_summary,
            "performance_metrics": performance_metrics,
            "drift_metrics": drift_metrics,
            "errors_incidents": errors_incidents,
            "regime_summary": regime_stats,
            "dlq": {
                "total": len(dlq_rows),
                "sample": [
                    {
                        "id": r.id,
                        "timestamp_utc": r.timestamp_utc.isoformat() if r.timestamp_utc else None,
                        "event_type": r.event_type,
                        "source": r.source,
                        "error_message": r.error_message,
                        "status": r.status,
                        "retry_count": r.retry_count,
                    }
                    for r in sorted(dlq_rows, key=lambda x: x.timestamp_utc or start_ts, reverse=True)[:50]
                ],
            },
        }

        # Add ML pattern insights if available
        try:
            ml_insights = await self._fetch_ml_insights()
            if ml_insights:
                payload["ml_insights"] = ml_insights
        except Exception as e:
            logger.warning(f"Failed to fetch ML insights: {e}")

        # Add ML prediction accuracy metrics
        try:
            from orion.ml.performance_tracker import get_daily_accuracy

            ml_accuracy = await get_daily_accuracy()
            payload["ml_prediction_accuracy"] = {
                "total_predictions": ml_accuracy.get("total", 0),
                "correct": ml_accuracy.get("correct", 0),
                "incorrect": ml_accuracy.get("incorrect", 0),
                "accuracy_pct": ml_accuracy.get("accuracy_pct"),
                "avg_return_high_score": ml_accuracy.get("avg_return_high_score"),
                "avg_return_low_score": ml_accuracy.get("avg_return_low_score"),
                "edge_bps": (
                    (ml_accuracy.get("avg_return_high_score", 0) - ml_accuracy.get("avg_return_low_score", 0)) * 100
                    if ml_accuracy.get("avg_return_high_score") is not None
                    and ml_accuracy.get("avg_return_low_score") is not None
                    else None
                ),
            }
        except Exception as e:
            logger.warning(f"Failed to fetch ML accuracy: {e}")

        input_snapshot_path = os.path.join(reports_dir, f"eod_input_{date}_{run_id}.json")

        def _dump_json():
            with open(input_snapshot_path, "w") as f:
                json.dump(payload, f, indent=2, sort_keys=True, default=str)

        await asyncio.to_thread(_dump_json)

        payload["input_snapshot_path"] = input_snapshot_path
        return payload, input_snapshot_path

    async def _fetch_ml_insights(self) -> dict[str, Any] | None:
        """Fetch latest ML pattern insights for LLM context."""
        try:

            async def query(session: Any) -> list[Any]:
                # Get most recent insight per model type
                # Use ROW_NUMBER() for PostgreSQL+SQLite compatibility
                stmt = text(
                    """
                    SELECT * FROM (
                        SELECT
                            insight_id, model_type, created_at_utc,
                            sample_size, positive_rate, holdout_auc,
                            top_rules_json, top_features_json,
                            degraded_features_json, emerging_patterns_json,
                            ROW_NUMBER() OVER (PARTITION BY model_type ORDER BY created_at_utc DESC) as rn
                        FROM ml_pattern_insights
                    ) WHERE rn = 1
                """
                )
                result = await session.execute(stmt)
                return result.fetchall()

            rows = await db_query(query)
            if not rows:
                return None

            insights = {}
            for row in rows:
                insights[row[1]] = {
                    "insight_id": row[0],
                    "created_at": row[2].isoformat() if row[2] else None,
                    "sample_size": row[3],
                    "positive_rate": row[4],
                    "holdout_auc": row[5],
                    "top_rules": row[6] or [],
                    "top_features": row[7] or [],
                    "degraded_features": row[8] or [],
                    "emerging_patterns": row[9] or [],
                }

            return {"insights": insights}

        except Exception as e:
            logger.debug(f"ML insights fetch failed (may not be available yet): {e}")
            return None

    async def _fetch_rag_context(self, query: str) -> str:
        try:
            # RAG search is async
            docs = await self.vector_store.search(query, k=3)
            context = "\n".join([f"- {d.content}" for d in docs])
            return f"## Historical Context (RAG)\n{context}\n"
        except Exception as e:
            logger.warning(f"RAG fetch failed: {e}")
            return ""

    async def _fetch_trading_book_context(self, performance_issues: str) -> str:
        """
        Fetch insights from TradingRAG (indexed trading books).

        Provides theoretical/best-practice guidance for performance issues.
        """
        try:
            from orion.clients.trading_rag import get_rag_client

            rag = get_rag_client()

            # Build query based on performance issues
            query = f"Trading strategy improvements for: {performance_issues}"

            result = await rag.answer(query, top_k=3)

            if result.get("answer"):
                return f"## Trading Book Insights\n{result['answer']}\n"
            return ""
        except Exception as e:
            logger.debug(f"TradingRAG context fetch failed (non-fatal): {e}")
            return ""

    async def _generate_analysis(
        self, data: dict[str, Any], rag_context: str, valid_solver_ids: list[str] | None = None
    ) -> dict[str, Any]:
        valid_solver_ids = valid_solver_ids or []
        system_prompt = """You are the Orion EOD Review Agent - analyzing today's trading performance.
## Your Capabilities
- You have access to codebase tools: `read_file`, `write_file`, and `run_command`.
- If you run into errors or missing information, USE YOUR TOOLS to search the codebase, read files, edit your own code, and fix bugs directly.
- You are fully capable of solving your own problems and editing your own code.

## Your Data Sources
1. **ML Pattern Insights** (in `ml_insights`): Pre-computed rules from LightGBM models showing what conditions predict success
   - Models per bucket: 0DTE, SHORT_SWING, SWING, POSITION
   - Look at AUC scores (>0.6 = useful), top rules, and feature importance
2. **Today's Decisions**: Execute/Skip actions and their outcomes
3. **Trade Journal**: P&L, execution quality, slippage
4. **Regime Data**: Current market regime (vol, trend, risk, session)
5. **DLQ Events**: System errors that need attention

## Your Task
Analyze today's performance and identify:
1. **What worked**: Successful patterns, good decisions
2. **What failed**: Losses, missed opportunities, errors
3. **Actionable improvements**: Config changes, rule adjustments, or NEW SOLVER MUTATIONS

## Output Format
```json
{
  "analysis": "## Summary\\n...",
  "key_metrics": {
    "total_trades": N,
    "win_rate": 0.XX,
    "pnl": X.XX,
    "regime": "..."
  },
  "proposals": [
    {
      "type": "solver_edit",
      "priority": 1,
      "rationale": "Brief reason with data reference",
      "evidence_pointers": {"ml_insight": "AUC=0.85 for iv_rank feature", "drift_psi": 0.02},
      "test_plan": ["Backtest with 30-day window", "Verify win_rate improvement"],
      "target_solver_id": "<MUST be one of the valid solver ids listed below>",
      "ops": [
        {"op": "modify_param", "param_name": "exit_logic.take_profit_atr_multiple", "new_value": 2.5, "reasoning": "..."},
        {"op": "add_rule", "new_value": "rule_iv_rank_v1", "reasoning": "ML shows IV rank is predictive"},
        {"op": "toggle_feature", "feature_name": "vol_oi_ratio", "new_value": true, "reasoning": "..."}
      ]
    },
    {
      "type": "config_patch",
      "rationale": "Reduce position size in high-vol regime",
      "evidence_pointers": {"regime": "HIGH_VOL", "loss_rate": 0.65},
      "test_plan": ["Verify sizing reduction in paper"],
      "changes": {"risk.max_position_size_pct": 0.02}
    },
    {
      "type": "do_not_trade",
      "rationale": "Extreme drift detected",
      "evidence_pointers": {"psi_close": 4.2, "psi_volume": 3.8},
      "test_plan": ["Monitor drift for 2 days"],
      "recommendation": "Pause trading until PSI < 0.25"
    }
  ]
}
```

## When to propose solver_edit
- When ML insights reveal strong predictive features not in current solvers
- When a pattern works well for specific bucket (e.g., 0DTE) but current solver doesn't exploit it
- When win rate could improve with tighter/looser exit logic based on today's data
- Edits start in 'research' stage - they gather data but don't trade live until promoted
- target_solver_id MUST be one of the valid solver ids listed below — do NOT invent ids

## Rules
- Ground ALL proposals in data from the input - no speculation
- If ML insights show low AUC (<0.55), note that model needs more data
- Prioritize high-impact, low-risk changes
- If nothing actionable, say so clearly
- REQUIRED fields for all proposals: type, rationale, evidence_pointers (dict), test_plan (list)
- For solver_edit: also need target_solver_id, ops (list)"""

        if valid_solver_ids:
            valid_ids_block = ", ".join(valid_solver_ids)
            system_prompt += (
                "\n\n## Valid solver ids (for target_solver_id)\n"
                f"target_solver_id MUST be EXACTLY one of: {valid_ids_block}.\n"
                "Any other value (including 'paper_v1') is invalid and the proposal will be discarded."
            )
        else:
            system_prompt += (
                "\n\n## Valid solver ids (for target_solver_id)\n"
                "No solvers are currently available — do NOT emit any solver_edit proposals."
            )

        user_prompt = f"## Today's Snapshot\n```json\n{json.dumps(data, indent=2, default=str)}\n```\n\n{rag_context}"

        try:
            from orion.config import agent_settings

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            if self.llm_client is not None:
                completion = await self.llm_client.chat.completions.create(
                    model=agent_settings.model_name,
                    messages=messages,
                )
                content = completion.choices[0].message.content
                return extract_json_from_response(content)

            # Call AI Gateway via codex_client
            response = await run_codex_completion(
                messages=messages,
                model=agent_settings.model_name,
            )

            return extract_json_from_response(response)
        except Exception as e:
            logger.error(f"LLM Failed: {e}")
            return {"analysis": f"Error: {e}", "proposals": []}


if __name__ == "__main__":
    setup_logging()

    agent = EODReviewAgent()
    asyncio.run(agent.run_review())
