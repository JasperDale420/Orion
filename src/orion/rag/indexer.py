import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

from orion.core.timekeeping import derive_trading_date_and_session
from orion.rag.vector_store import VectorStore
from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_gold import CandidateTrade, StrategyDecision
from orion.storage.models_solvers import MetaExperiment, Solver, SolverMetrics

logger = setup_struct_logger("orion.indexer")


class IndexerService:
    def __init__(self):
        self.store = VectorStore()

    async def index_all(self):
        """
        Main entry point to index all key entities.
        """
        await init_db()
        await self.index_candidates()
        await self.index_solver_metrics()
        await self.index_meta_experiments()
        await self.index_solver_profiles()
        await self.index_strategy_decisions()
        logger.info("Full indexing complete.")

    async def index_candidates(self, limit: int = 1000):
        """
        Indexes CandidateTrades.
        """
        async with async_session_factory() as session:
            stmt = select(CandidateTrade).order_by(CandidateTrade.timestamp_utc.desc()).limit(limit)
            result = await session.execute(stmt)
            candidates = result.scalars().all()

            logger.info(f"Indexing {len(candidates)} candidates...")

            for c in candidates:
                ts = getattr(c, "timestamp_utc", None)
                if isinstance(ts, datetime):
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    else:
                        ts = ts.astimezone(timezone.utc)
                else:
                    ts = datetime.now(timezone.utc)
                td, sess = derive_trading_date_and_session(ts)
                text_rep = (
                    f"Candidate Trade: {c.direction} on {c.ticker} at {c.timestamp_utc}.\n"
                    f"Rule: {c.rule_id}. Confidence: {c.confidence}.\n"
                    f"Evidence: {c.evidence}"
                )

                doc_id = f"cand_{c.candidate_id}"
                pointers: dict = {"candidate_id": c.candidate_id}
                if isinstance(c.evidence, dict):
                    for key in ("event_ids", "rollup_ids", "signal_ids", "segments"):
                        if key in c.evidence:
                            pointers[key] = c.evidence.get(key)
                metadata = {
                    "entity": "candidate",
                    # PRDv2 14.1 doc types (closest mapping for event-level trade cards)
                    "doc_type": "major_event_card",
                    "ticker": c.ticker,
                    "rule_id": c.rule_id,
                    "timestamp": c.timestamp_utc.isoformat() if c.timestamp_utc else None,
                    "trading_date": td.isoformat() if td else None,
                    "session": sess,
                    "pointers": pointers,
                }

                await self.store.add_document(
                    doc_id=doc_id,
                    content=text_rep,
                    source_type="CANDIDATE_TRADE",
                    source_id=c.candidate_id,
                    metadata=metadata,
                )

    async def index_solver_metrics(self, limit: int = 500):
        """
        Indexes SolverMetrics (Backtest Reports).
        PRD 14.1: strategy_backtest_report
        """
        async with async_session_factory() as session:
            # Join with Solver to get Family Name?
            # For now just fetch metrics
            stmt = select(SolverMetrics).order_by(SolverMetrics.evaluated_at_utc.desc()).limit(limit)
            result = await session.execute(stmt)
            metrics_list = result.scalars().all()

            logger.info(f"Indexing {len(metrics_list)} solver metrics...")

            for m in metrics_list:
                text_rep = (
                    f"Solver Backtest Report: Solver {m.solver_id} (Tag: {m.dataset_tag})\n"
                    f"Sharpe: {m.sharpe_ratio:.2f}, PF: {m.profit_factor:.2f}, DD: {m.max_dd_pct:.2f}%\n"
                    f"Trades: {m.num_trades}. Stability: {m.stability_score:.2f}\n"
                    f"Evaluated At: {m.evaluated_at_utc}"
                )

                doc_id = f"metrics_{m.id}"
                metadata = {
                    "entity": "solver_metrics",
                    "doc_type": "strategy_backtest_report",
                    "solver_id": m.solver_id,
                    "dataset_tag": m.dataset_tag,
                    "timestamp": m.evaluated_at_utc.isoformat() if m.evaluated_at_utc else None,
                }

                await self.store.add_document(
                    doc_id=doc_id, content=text_rep, source_type="SOLVER_METRICS", source_id=m.id, metadata=metadata
                )

    async def index_strategy_decisions(self, limit: int = 1000):
        """
        Indexes StrategyDecisions (Trade Journals).
        PRD 14.1: trade_journal_doc
        """
        async with async_session_factory() as session:
            stmt = select(StrategyDecision).order_by(StrategyDecision.timestamp_utc.desc()).limit(limit)
            result = await session.execute(stmt)
            decisions = result.scalars().all()

            logger.info(f"Indexing {len(decisions)} strategy decisions...")

            for d in decisions:
                ts = getattr(d, "timestamp_utc", None)
                if isinstance(ts, datetime):
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    else:
                        ts = ts.astimezone(timezone.utc)
                else:
                    ts = datetime.now(timezone.utc)
                td, sess = derive_trading_date_and_session(ts)
                text_rep = (
                    f"Trade Decision: {d.decision} on {d.ticker} by Solver {d.strategy_version_id}.\n"
                    f"Reason: {d.reason}\n"
                    f"Executed: {d.executed_successfully}. Trace: {d.decision_trace_json}"
                )

                doc_id = f"decision_{d.decision_id}"
                metadata = {
                    "entity": "strategy_decision",
                    "doc_type": "trade_journal_doc",
                    "ticker": d.ticker,
                    "solver_id": d.strategy_version_id,
                    "model_version": d.model_version,
                    "decision": d.decision,
                    "timestamp": d.timestamp_utc.isoformat() if d.timestamp_utc else None,
                    "trading_date": td.isoformat() if td else None,
                    "session": sess,
                    "pointers": {"decision_id": d.decision_id, "candidate_id": d.candidate_id},
                }

                await self.store.add_document(
                    doc_id=doc_id,
                    content=text_rep,
                    source_type="STRATEGY_DECISION",
                    source_id=d.decision_id,
                    metadata=metadata,
                )

    async def index_meta_experiments(self, limit: int = 100):
        """
        Indexes MetaExperiments (Evolution Cycles).
        """
        async with async_session_factory() as session:
            stmt = select(MetaExperiment).order_by(MetaExperiment.start_time_utc.desc()).limit(limit)
            result = await session.execute(stmt)
            experiments = result.scalars().all()

            logger.info(f"Indexing {len(experiments)} meta experiments...")

            for exp in experiments:
                text_rep = (
                    f"Meta Experiment: {exp.description} (ID: {exp.experiment_id}).\n"
                    f"Status: {exp.status}. Started: {exp.start_time_utc}.\n"
                    f"Best Solver Found: {exp.best_solver_id}"
                )

                doc_id = f"meta_exp_{exp.experiment_id}"
                metadata = {
                    "entity": "meta_experiment",
                    "doc_type": "meta_experiment_report",
                    "status": exp.status,
                    "best_solver": exp.best_solver_id,
                    "timestamp": exp.start_time_utc.isoformat() if exp.start_time_utc else None,
                }

                await self.store.add_document(
                    doc_id=doc_id,
                    content=text_rep,
                    source_type="META_EXPERIMENT",
                    source_id=exp.experiment_id,
                    metadata=metadata,
                )

    async def index_solver_profiles(self, limit: int = 200):
        """
        Indexes Solver DNA and status.
        """
        async with async_session_factory() as session:
            stmt = (
                select(Solver)
                .where((Solver.status == "active") | ((Solver.status.is_(None)) & (Solver.is_active)))
                .limit(limit)
            )
            result = await session.execute(stmt)
            solvers = result.scalars().all()

            logger.info(f"Indexing {len(solvers)} solver profiles...")

            for s in solvers:
                # Parse config safely
                try:
                    import json

                    cfg_blob = s.definition_json or s.config
                    cfg_str = json.dumps(cfg_blob, indent=2) if isinstance(cfg_blob, dict) else str(cfg_blob)
                except Exception:
                    cfg_str = str(s.definition_json or s.config)

                text_rep = (
                    f"Solver Profile: {s.family_name} (ID: {s.solver_id}).\n"
                    f"Stage: {s.stage}. Active: {s.is_active}.\n"
                    f"Stats: Sharpe {float(s.sharpe_ratio or 0.0):.2f}, Win Rate {float(s.win_rate or 0.0):.2f} (Trades: {int(s.trades_count or 0)})\n"
                    f"Config DNA:\n{cfg_str}"
                )

                doc_id = f"solver_{s.solver_id}"
                metadata = {
                    "entity": "solver_profile",
                    "doc_type": "solver_profile_doc",
                    "stage": s.stage,
                    "family": s.family_name,
                    "solver_id": s.solver_id,
                }

                await self.store.add_document(
                    doc_id=doc_id,
                    content=text_rep,
                    source_type="SOLVER_PROFILE",
                    source_id=s.solver_id,
                    metadata=metadata,
                )

    async def index_generic_text(self, content: str, doc_id: str, metadata: dict, source_type: str):
        """
        Generic indexer for external callers (e.g. EOD Agent saving a report).
        """
        await self.store.add_document(
            doc_id=doc_id,
            content=content,
            source_type=source_type,
            source_id=metadata.get("source_id", doc_id),
            metadata=metadata,
        )
        logger.info(f"Indexed {source_type}: {doc_id}")

    async def run_daemon(self, interval_seconds: int = 3600):
        """
        Runs the indexer in a continuous loop.
        """
        await init_db()
        logger.info(f"Starting Indexer Daemon (Interval: {interval_seconds}s)")

        while True:
            try:
                logger.info("Starting indexing cycle...")
                # We don't call init_db again inside index_all if we call specific methods,
                # but index_all calls init_db. Let's call methods directly or factor out init_db.
                # index_all calls init_db() at start. That's safe (idempotent usually).
                await self.index_all()
                logger.info("Cycle complete.")
            except Exception as e:
                logger.error(f"Indexing cycle failed: {e}")

            logger.info(f"Sleeping for {interval_seconds}s...")
            await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Orion RAG Indexer")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous daemon mode")
    parser.add_argument("--interval", type=int, default=3600, help="Interval in seconds for daemon mode")
    args = parser.parse_args()

    service = IndexerService()

    if args.daemon:
        asyncio.run(service.run_daemon(args.interval))
    else:
        asyncio.run(service.index_all())
