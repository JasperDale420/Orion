import asyncio
import json
import logging
import math
import os
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from sqlalchemy import select
from zoneinfo import ZoneInfo

load_dotenv()

from orion.agents.base import BaseAgent
from orion.agents.codex_client import (
    build_chat_prompt,
    extract_json_from_response,
    run_codex_completion,
)
from orion.agents.proposal_builder import ProposalBuilder
from orion.core.id_utils import deterministic_solver_id
from orion.rag.vector_store import VectorStore
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.models import BronzeEvent
from orion.storage.models_gold import StrategyDecision
from orion.storage.models_silver import SilverSignal
from orion.storage.models_solvers import SolverEdits

logger = setup_struct_logger("orion.agents.eod_review_agent")


class EODReviewAgent(BaseAgent):
    """
    PRD 17: Daily EOD Review Agent.
    - Inspects trades/outcomes
    - Uses RAG for historical context
    - Proposes SolverEdits for Meta-Search Layer
    """

    def __init__(
        self,
        *,
        vector_store: Optional[Any] = None,
        proposal_builder: Optional[ProposalBuilder] = None,
    ):
        from orion.config import agent_settings

        super().__init__(name="EODReview", model=agent_settings.model_name)
        self.vector_store = vector_store or VectorStore()
        self.proposal_builder = proposal_builder or ProposalBuilder()

    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Satisfies BaseAgent interface. Wraps run_review.
        """
        target_date = context.get("date")
        result = await self.run_review(target_date)
        return {"status": "completed", **(result or {})}

    async def run_review(self, target_date: datetime.date = None) -> Dict[str, Any]:
        if not target_date:
            target_date = datetime.now(timezone.utc).date()

        logger.info(f"Starting EOD Review for {target_date}...")
        run_id = str(uuid.uuid4())

        # Ensure artifacts directory exists
        from orion.config import system_settings

        reports_dir = os.path.join(system_settings.artifacts_dir, "reports")
        if not os.path.exists(reports_dir):
            os.makedirs(reports_dir, exist_ok=True)

        # 1. Gather Data
        data, input_snapshot_path = await self._gather_data(target_date, run_id=run_id, reports_dir=reports_dir)

        # 2. RAG Context Lookup (VS5)
        # Search for recent similar performance issues or strategy docs
        rag_context = await self._fetch_rag_context("performance drift strategy issues")

        # 3. LLM Analysis & Proposal Generation
        analysis_json = await self._generate_analysis(data, rag_context)

        # 4. Save Artifacts
        # Save Markdown Report
        report_text = analysis_json.get("analysis", "No analysis generated.")
        filename = f"eod_report_{target_date}.md"
        file_path = os.path.join(reports_dir, filename)

        with open(file_path, "w") as f:
            f.write(report_text)

        # Save Proposals
        proposals = analysis_json.get("proposals", [])
        saved_paths = []

        # PRDv2 §5.7.2: EOD agent writes proposals into solver_edits with generated_by='llm_eod_agent' and reward=NULL.
        await self._persist_solver_edits(proposals, run_id)

        # Proposal Builder should ideally also check config, but for now we pass paths?
        # Actually ProposalBuilder uses a default 'proposals' dir.
        # Ideally we update ProposalBuilder too, but scope is 'EODReviewAgent'.
        # Let's leave ProposalBuilder as is for now or update it?
        # Compliance Requirement: "EOD Report Location".

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
        return {
            "run_id": run_id,
            "date": str(target_date),
            "report_path": file_path,
            "input_snapshot_path": input_snapshot_path,
            "proposal_paths": saved_paths,
            "proposals_count": len(proposals),
        }

    async def _persist_solver_edits(self, proposals: List[Dict[str, Any]], run_id: str) -> None:
        if not proposals:
            return

        async def save_edits(session: Any) -> None:
            for p in proposals:
                if p.get("type") != "solver_edit":
                    continue

                base_id = p.get("target_solver_id")
                ops_data = p.get("ops", [])
                if not base_id or not isinstance(ops_data, list) or not ops_data:
                    continue

                new_solver_id = deterministic_solver_id(
                    base_solver_id=str(base_id),
                    edit_ops={"ops": ops_data},
                    prefix="eod",
                )

                session.add(
                    SolverEdits(
                        id=str(uuid.uuid4()),
                        experiment_id=None,
                        base_solver_id=str(base_id),
                        new_solver_id=new_solver_id,
                        edit_json={"ops": ops_data, "run_id": run_id},
                        generated_by="llm_eod_agent",
                        reward=None,
                    )
                )

        await db_write(save_edits)

    def _day_bounds_utc(self, date: datetime.date) -> Tuple[datetime, datetime]:
        start_ts = datetime.combine(date, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_ts = start_ts + timedelta(days=1)
        return start_ts, end_ts

    def _classify_session(self, ts_utc: datetime | None) -> str:
        if ts_utc is None:
            return "UNKNOWN"
        try:
            et = ts_utc.astimezone(ZoneInfo("America/New_York"))
        except Exception:
            return "UNKNOWN"

        t = et.timetz().replace(tzinfo=None)
        if time(4, 0) <= t < time(9, 30):
            return "PRE"
        if time(9, 30) <= t < time(16, 0):
            return "REG"
        if time(16, 0) <= t < time(20, 0):
            return "POST"
        return "OFF"

    def _psi(self, baseline: List[float], current: List[float], *, bins: int = 10) -> float | None:
        """
        Population Stability Index between baseline and current.
        Uses baseline quantiles to form bins (common PSI approach).
        Returns None if not computable.
        """
        b = [x for x in baseline if x is not None and math.isfinite(x)]
        c = [x for x in current if x is not None and math.isfinite(x)]
        if len(b) < 20 or len(c) < 20:
            return None
        b_sorted = sorted(b)
        edges: List[float] = []
        for i in range(1, bins):
            q = i / bins
            idx = int(q * (len(b_sorted) - 1))
            edges.append(b_sorted[idx])
        # Deduplicate edges (constant series)
        edges = sorted(set(edges))
        if not edges:
            return None

        def bin_idx(x: float) -> int:
            # returns 0..len(edges)
            lo = 0
            hi = len(edges)
            while lo < hi:
                mid = (lo + hi) // 2
                if x <= edges[mid]:
                    hi = mid
                else:
                    lo = mid + 1
            return lo

        b_counts = [0] * (len(edges) + 1)
        c_counts = [0] * (len(edges) + 1)
        for x in b:
            b_counts[bin_idx(x)] += 1
        for x in c:
            c_counts[bin_idx(x)] += 1

        eps = 1e-6
        b_total = float(len(b))
        c_total = float(len(c))
        psi = 0.0
        for bc, cc in zip(b_counts, c_counts, strict=False):
            bp = max(bc / b_total, eps)
            cp = max(cc / c_total, eps)
            psi += (cp - bp) * math.log(cp / bp)
        return float(psi)

    def _adverse_slippage_bps(
        self, *, side: Optional[str], limit_price: Optional[float], fill_price: Optional[float]
    ) -> Optional[float]:
        if limit_price is None or fill_price is None:
            return None
        if limit_price <= 0:
            return None
        if not side:
            return None
        s = side.lower()
        if s in {"buy", "long"}:
            return (fill_price - limit_price) / limit_price * 10000.0
        if s in {"sell", "short"}:
            return (limit_price - fill_price) / limit_price * 10000.0
        return None

    async def _gather_data(self, date: datetime.date, *, run_id: str, reports_dir: str) -> Tuple[Dict[str, Any], str]:
        """
        Gather metrics, decisions, and outcomes for the day.
        """
        from orion.storage.models_dlq import DeadLetterQueue
        from orion.storage.models_execution import FillRecord, OrderRecord
        from orion.storage.models_signals import SignalLive
        from orion.storage.models_silver import SilverAlpacaBar
        from orion.storage.models_trade_journal import TradeJournalEntry

        start_ts, end_ts = self._day_bounds_utc(date)

        async def fetch_all_data(session: Any) -> Dict[str, Any]:
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
                (
                    ((FillRecord.filled_at_utc >= start_ts) & (FillRecord.filled_at_utc < end_ts))
                    | (
                        (FillRecord.filled_at_utc.is_(None))
                        & (FillRecord.created_at_utc >= start_ts)
                        & (FillRecord.created_at_utc < end_ts)
                    )
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
                (
                    ((FillRecord.filled_at_utc >= baseline_start) & (FillRecord.filled_at_utc < start_ts))
                    | (
                        (FillRecord.filled_at_utc.is_(None))
                        & (FillRecord.created_at_utc >= baseline_start)
                        & (FillRecord.created_at_utc < start_ts)
                    )
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

            # Feature drift data: pull daily OHLCV signals and a rolling baseline window (previous 20d)
            silver_today_stmt = select(SilverSignal).where(
                SilverSignal.signal_ts_utc >= start_ts,
                SilverSignal.signal_ts_utc < end_ts,
            )
            silver_base_stmt = select(SilverSignal).where(
                SilverSignal.signal_ts_utc >= baseline_start,
                SilverSignal.signal_ts_utc < start_ts,
            )
            silver_today = (await session.execute(silver_today_stmt)).scalars().all()
            silver_baseline = (await session.execute(silver_base_stmt)).scalars().all()

            # Volatility regime from bars (intraday realized vol)
            tickers_for_regime = {s.ticker for s in sigs if s.ticker} | {t.ticker for t in trade_journal if t.ticker}

            if tickers_for_regime:
                bars_stmt = select(SilverAlpacaBar).where(
                    SilverAlpacaBar.ticker.in_(sorted(tickers_for_regime)),
                    SilverAlpacaBar.bar_start_ts_utc >= start_ts,
                    SilverAlpacaBar.bar_start_ts_utc < end_ts,
                )
                bars = (await session.execute(bars_stmt)).scalars().all()
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

        # Extract data from the returned dictionary
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
        # Naive execution count
        executed_decisions = [d for d in decisions if d.decision == "EXECUTE"]
        skipped_count = len([d for d in decisions if d.decision == "SKIP"])

        # --- slippage joins ---
        baseline_orders_by_broker: Dict[str, OrderRecord] = {}
        for o in baseline_orders:
            if o.broker_order_id:
                baseline_orders_by_broker[o.broker_order_id] = o

        orders_by_broker: Dict[str, OrderRecord] = {}
        for o in orders:
            if o.broker_order_id:
                orders_by_broker[o.broker_order_id] = o

        slippage_rows: List[Dict[str, Any]] = []
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

        slippage_bps_vals = [
            r["adverse_slippage_bps"] for r in slippage_rows if r.get("adverse_slippage_bps") is not None
        ]
        slippage_summary = {
            "fills_count": len(fills),
            "linked_fills_count": len([r for r in slippage_rows if r["linked_order"]]),
            "unlinked_fills_count": len([r for r in slippage_rows if not r["linked_order"]]),
            "mean_adverse_slippage_bps": (
                (sum(slippage_bps_vals) / len(slippage_bps_vals)) if slippage_bps_vals else None
            ),
            "worst_adverse_slippage_bps": max(slippage_bps_vals) if slippage_bps_vals else None,
        }

        baseline_slippage_rows: List[Dict[str, Any]] = []
        for f in baseline_fills:
            order = baseline_orders_by_broker.get(f.broker_order_id)
            limit_price = order.limit_price if order is not None else None
            adverse_bps = self._adverse_slippage_bps(
                side=f.side, limit_price=limit_price, fill_price=f.filled_avg_price
            )
            baseline_slippage_rows.append(
                {
                    "adverse_slippage_bps": adverse_bps,
                    "linked_order": order is not None,
                }
            )
        baseline_bps_vals = [
            r["adverse_slippage_bps"] for r in baseline_slippage_rows if r.get("adverse_slippage_bps") is not None
        ]
        baseline_slippage_summary = {
            "window_utc": {"start": baseline_start.isoformat(), "end": start_ts.isoformat()},
            "fills_count": len(baseline_fills),
            "linked_fills_count": len([r for r in baseline_slippage_rows if r["linked_order"]]),
            "unlinked_fills_count": len([r for r in baseline_slippage_rows if not r["linked_order"]]),
            "mean_adverse_slippage_bps": (
                (sum(baseline_bps_vals) / len(baseline_bps_vals)) if baseline_bps_vals else None
            ),
            "worst_adverse_slippage_bps": max(baseline_bps_vals) if baseline_bps_vals else None,
        }

        # --- volatility regimes ---
        # Compute intraday realized vol per ticker using 1m closes.
        vol_by_ticker: Dict[str, float] = {}
        closes_by_ticker: Dict[str, List[float]] = {}
        regime_map: Dict[str, str] = {}
        regime_stats: Dict[str, Any] = {}
        for b in bars:
            if b.ticker and b.close is not None:
                closes_by_ticker.setdefault(b.ticker, []).append(float(b.close))
        for tkr, closes in closes_by_ticker.items():
            if len(closes) < 30:
                continue
            rets = []
            prev = None
            for cpx in closes:
                if prev is not None and prev > 0 and cpx > 0:
                    rets.append(math.log(cpx / prev))
                prev = cpx
            if len(rets) < 20:
                continue
            mean = sum(rets) / len(rets)
            var = sum((x - mean) ** 2 for x in rets) / max(len(rets) - 1, 1)
            vol_by_ticker[tkr] = math.sqrt(var) * math.sqrt(390.0)  # annualized-ish per trading day

        if vol_by_ticker:
            vols = sorted(vol_by_ticker.values())
            q1 = vols[int(0.33 * (len(vols) - 1))] if len(vols) > 1 else vols[0]
            q2 = vols[int(0.66 * (len(vols) - 1))] if len(vols) > 1 else vols[0]
            for tkr, v in vol_by_ticker.items():
                if v <= q1:
                    regime_map[tkr] = "LOW_VOL"
                elif v <= q2:
                    regime_map[tkr] = "MID_VOL"
                else:
                    regime_map[tkr] = "HIGH_VOL"
            regime_stats.update(
                {"available": True, "bucket_quantiles": {"q33": q1, "q66": q2}, "computed": len(regime_map)}
            )
        else:
            regime_stats.update({"available": False, "computed": 0})

        # --- ingestion lag/gap stats ---
        lag_by_key: Dict[str, List[float]] = {}
        gaps_by_key: Dict[str, float] = {}
        last_recv_by_key: Dict[str, datetime] = {}
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

        def _pct(vals: List[float], p: float) -> float | None:
            if not vals:
                return None
            s = sorted(vals)
            idx = int(p * (len(s) - 1))
            return float(s[idx])

        ingestion_stats: Dict[str, Any] = {"sources": {}}
        for key, vals in lag_by_key.items():
            ingestion_stats["sources"][key] = {
                "count": len(vals),
                "lag_p50_s": _pct(vals, 0.50),
                "lag_p95_s": _pct(vals, 0.95),
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

        def _agg(rows: List[dict[str, Any]], key: str) -> Dict[str, Any]:
            out: Dict[str, Any] = {}
            groups: Dict[str, List[dict[str, Any]]] = {}
            for r in rows:
                k = r.get(key) or "UNKNOWN"
                groups.setdefault(str(k), []).append(r)
            for k, rs in groups.items():
                pnls = [x["realized_pnl"] for x in rs if x.get("realized_pnl") is not None]
                out[k] = {
                    "trades": len(rs),
                    "pnl_count": len(pnls),
                    "pnl_sum": float(sum(pnls)) if pnls else None,
                    "pnl_mean": float(sum(pnls) / len(pnls)) if pnls else None,
                }
            return out

        performance_metrics = {
            "by_rule": _agg(trade_rows, "rule_id"),
            "by_model": _agg(trade_rows, "model_version"),
            "by_ticker": _agg(trade_rows, "ticker"),
            "by_session": _agg(trade_rows, "session"),
            "by_regime": _agg(trade_rows, "regime"),
        }

        baseline_trade_rows = []
        for t in baseline_trade_journal:
            baseline_trade_rows.append({"realized_pnl": t.realized_pnl})
        baseline_pnls = [r["realized_pnl"] for r in baseline_trade_rows if r.get("realized_pnl") is not None]
        today_pnls = [r["realized_pnl"] for r in trade_rows if r.get("realized_pnl") is not None]

        # --- drift metrics (feature PSI + slippage drift) ---
        def _extract_feature(rows: List[SilverSignal], feature_key: str) -> List[float]:
            vals: List[float] = []
            for r in rows:
                if not r.features:
                    continue
                v = r.features.get(feature_key)
                if isinstance(v, (int, float)) and math.isfinite(float(v)):
                    vals.append(float(v))
            return vals

        drift_features = ["close", "volume", "vwap", "flow_net_premium_15m", "flow_count_15m"]
        feature_shift: Dict[str, Any] = {}
        for fk in drift_features:
            bvals = _extract_feature(silver_baseline, fk)
            cvals = _extract_feature(silver_today, fk)
            feature_shift[fk] = {
                "baseline_n": len(bvals),
                "current_n": len(cvals),
                "psi": self._psi(bvals, cvals, bins=10),
            }

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

        payload: Dict[str, Any] = {
            "run_id": run_id,
            "date": str(date),
            "window_utc": {"start": start_ts.isoformat(), "end": end_ts.isoformat()},
            "decisions": {
                "total": total_decisions,
                "executed_count": len(executed_decisions),
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

        input_snapshot_path = os.path.join(reports_dir, f"eod_input_{date}_{run_id}.json")
        with open(input_snapshot_path, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True, default=str)

        payload["input_snapshot_path"] = input_snapshot_path
        return payload, input_snapshot_path

    async def _fetch_ml_insights(self) -> Optional[Dict[str, Any]]:
        """Fetch latest ML pattern insights for LLM context."""
        try:
            from orion.shared.db_utils import db_query
            from sqlalchemy import text

            async def query(session: Any) -> List[Any]:
                # Get most recent insight per model type
                stmt = text(
                    """
                    SELECT DISTINCT ON (model_type)
                        insight_id, model_type, created_at_utc,
                        sample_size, positive_rate, holdout_auc,
                        top_rules_json, top_features_json,
                        degraded_features_json, emerging_patterns_json
                    FROM ml_pattern_insights
                    ORDER BY model_type, created_at_utc DESC
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

    async def _generate_analysis(self, data: Dict[str, Any], rag_context: str) -> Dict[str, Any]:
        system_prompt = """You are the Orion EOD Review Agent - analyzing today's trading performance.

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
      "type": "config_patch|do_not_trade|rule_change|solver_mutation",
      "priority": 1-3,
      "rationale": "Brief reason with data reference",
      "action": "Specific change to make",
      "mutation": {  // ONLY for solver_mutation type
        "base_solver_id": "existing solver to mutate OR null for new",
        "ops": [
          {"op": "modify_param", "param_name": "exit_logic.take_profit_atr_multiple", "new_value": 2.5, "reasoning": "..."},
          {"op": "add_rule", "new_value": "rule_iv_rank_v1", "reasoning": "ML shows IV rank is predictive"},
          {"op": "toggle_feature", "feature_name": "vol_oi_ratio", "new_value": true, "reasoning": "..."}
        ]
      }
    }
  ]
}
```

## When to propose solver_mutation
- When ML insights reveal strong predictive features not in current solvers
- When a pattern works well for specific bucket (e.g., 0DTE) but current solver doesn't exploit it
- When win rate could improve with tighter/looser exit logic based on today's data
- Mutations start in 'research' stage - they gather data but don't trade live until promoted

## Rules
- Ground ALL proposals in data from the input - no speculation
- If ML insights show low AUC (<0.55), note that model needs more data
- Prioritize high-impact, low-risk changes
- If nothing actionable, say so clearly"""

        user_prompt = f"## Today's Snapshot\n```json\n{json.dumps(data, indent=2, default=str)}\n```\n\n{rag_context}"

        try:
            from orion.config import agent_settings

            # Build combined prompt for codex CLI
            full_prompt = build_chat_prompt(system_prompt, user_prompt)

            # Call codex CLI instead of OpenAI API
            response = await run_codex_completion(
                prompt=full_prompt,
                model=agent_settings.model_name,
                reasoning_level=getattr(agent_settings, "reasoning_level", "extra_high"),
            )

            return extract_json_from_response(response)
        except Exception as e:
            logger.error(f"LLM Failed: {e}")
            return {"analysis": f"Error: {e}", "proposals": []}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    agent = EODReviewAgent()
    asyncio.run(agent.run_review())
