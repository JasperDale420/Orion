"""
Weekly Data Aggregator.

Collects and summarizes weekly data for meta-agent analysis:
- EOD reports from the week
- Live trade execution data
- ML model insights and drift metrics
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, select

from orion.shared.db_utils import db_query

logger = logging.getLogger(__name__)


class WeeklyDataAggregator:
    """
    Aggregates weekly data from EOD reports, trades, and ML insights.
    """

    def __init__(self, artifacts_dir: str = "artifacts/reports") -> None:
        self.artifacts_dir = Path(artifacts_dir)

    async def aggregate_week(self, week_end: datetime | None = None) -> dict[str, Any]:
        """
        Aggregate all data for the week ending on the specified date.

        Args:
            week_end: End of week (default: now). Week starts 7 days prior.

        Returns:
            Combined weekly data for meta-agent analysis.
        """
        if week_end is None:
            week_end = datetime.now(UTC)

        week_start = week_end - timedelta(days=7)

        logger.info(f"Aggregating weekly data: {week_start.date()} to {week_end.date()}")

        eod_data = await self.aggregate_eod_reports(week_start, week_end)
        trade_data = await self.aggregate_trade_data(week_start, week_end)
        ml_data = await self.aggregate_ml_insights(week_start, week_end, eod_data=eod_data)

        return {
            "period": {
                "start": week_start.isoformat(),
                "end": week_end.isoformat(),
            },
            "eod_reports": eod_data,
            "trade_execution": trade_data,
            "ml_insights": ml_data,
        }

    async def aggregate_eod_reports(self, week_start: datetime, week_end: datetime) -> dict[str, Any]:
        """
        Collect and summarize EOD reports from the week.
        """
        reports = []
        summary = {
            "total_reports": 0,
            "trading_days": 0,
            "total_decisions": 0,
            "executed_count": 0,
            "skipped_count": 0,
            "total_fills": 0,
            "realized_pnl": 0.0,
            "ingestion_issues": [],
            "top_features": {},
            "ml_auc_scores": {},
        }

        # Scan for EOD input files
        if self.artifacts_dir.exists():
            for f in self.artifacts_dir.iterdir():
                if not f.name.startswith("eod_input_"):
                    continue
                if not f.name.endswith(".json"):
                    continue

                try:
                    # Parse date from filename: eod_input_2026-01-06_uuid.json
                    date_str = f.name.split("_")[2]
                    file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)

                    if not (week_start <= file_date <= week_end):
                        continue

                    with open(f) as fp:
                        data = json.load(fp)

                    reports.append(
                        {
                            "date": date_str,
                            "file": f.name,
                            "data": data,
                        }
                    )

                except Exception as e:
                    logger.warning(f"Failed to parse EOD file {f.name}: {e}")

        # Aggregate metrics from reports
        for report in reports:
            data = report["data"]
            summary["total_reports"] += 1

            decisions = data.get("decisions", {})
            if decisions.get("total", 0) > 0:
                summary["trading_days"] += 1

            summary["total_decisions"] += decisions.get("total", 0)
            summary["executed_count"] += decisions.get("executed_count", 0)
            summary["skipped_count"] += decisions.get("skipped_count", 0)
            summary["total_fills"] += data.get("fills", {}).get("total", 0)

            # Aggregate ML insights
            ml_insights = data.get("ml_insights", {}).get("insights", {})
            for bucket, insight in ml_insights.items():
                auc = insight.get("holdout_auc")
                if auc:
                    if bucket not in summary["ml_auc_scores"]:
                        summary["ml_auc_scores"][bucket] = []
                    summary["ml_auc_scores"][bucket].append(
                        {
                            "date": report["date"],
                            "auc": auc,
                        }
                    )

                # Track top features
                for feat in insight.get("top_features", [])[:5]:
                    feat_name = feat.get("feature")
                    if feat_name:
                        if feat_name not in summary["top_features"]:
                            summary["top_features"][feat_name] = 0
                        summary["top_features"][feat_name] += 1

            # Track ingestion issues
            ingestion = data.get("errors_incidents", {}).get("ingestion", {})
            for source, stats in ingestion.get("sources", {}).items():
                max_lag = stats.get("lag_max_s", 0)
                if max_lag > 300:  # 5 minute threshold
                    summary["ingestion_issues"].append(
                        {
                            "date": report["date"],
                            "source": source,
                            "max_lag_s": max_lag,
                        }
                    )

        # Sort top features by frequency
        summary["top_features"] = dict(sorted(summary["top_features"].items(), key=lambda x: x[1], reverse=True)[:10])

        return summary

    async def aggregate_trade_data(self, week_start: datetime, week_end: datetime) -> dict[str, Any]:
        """
        Aggregate trade execution data from the database.
        """
        from orion.storage.models_gold import StrategyDecision
        from orion.storage.models_trade_journal import TradeJournalEntry

        async def fetch_trade_stats(session: Any) -> dict[str, Any]:
            # Count decisions
            stmt_decisions = select(
                func.count(StrategyDecision.decision_id).label("total"),
                func.sum(func.cast(StrategyDecision.decision == "EXECUTE", Integer)).label("executed"),
            ).where(
                and_(
                    StrategyDecision.timestamp_utc >= week_start,
                    StrategyDecision.timestamp_utc <= week_end,
                )
            )

            result = await session.execute(stmt_decisions)
            row = result.first()

            decision_stats = {
                "total": row.total if row else 0,
                "executed": row.executed if row else 0,
            }

            # Get trade journal entries
            stmt_trades = select(TradeJournalEntry).where(
                and_(
                    TradeJournalEntry.created_at_utc >= week_start,
                    TradeJournalEntry.created_at_utc <= week_end,
                )
            )

            result = await session.execute(stmt_trades)
            trades = result.scalars().all()

            trade_stats = {
                "total_orders": len(trades),
                "filled": 0,
                "rejected": 0,
                "cancelled": 0,
                "pending": 0,
                "total_qty": 0,
                "tickers": set(),
            }

            for trade in trades:
                status = (trade.broker_status or "").lower()
                if "filled" in status:
                    trade_stats["filled"] += 1
                elif "rejected" in status or "error" in status:
                    trade_stats["rejected"] += 1
                elif "cancelled" in status:
                    trade_stats["cancelled"] += 1
                else:
                    trade_stats["pending"] += 1

                trade_stats["total_qty"] += trade.qty or 0
                if trade.ticker:
                    trade_stats["tickers"].add(trade.ticker)

            trade_stats["tickers"] = list(trade_stats["tickers"])
            trade_stats["fill_rate"] = (
                trade_stats["filled"] / trade_stats["total_orders"] if trade_stats["total_orders"] > 0 else 0.0
            )

            return {
                "decisions": decision_stats,
                "trades": trade_stats,
            }

        try:
            from sqlalchemy import Integer

            return await db_query(fetch_trade_stats)
        except Exception as e:
            logger.error(f"Failed to aggregate trade data: {e}")
            return {"decisions": {}, "trades": {}, "error": str(e)}

    async def aggregate_ml_insights(
        self,
        week_start: datetime,
        week_end: datetime,
        eod_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Aggregate ML model insights and compute per-bucket drift from AUC scores
        already collected in aggregate_eod_reports.
        """
        ml_auc_scores: dict[str, list[dict[str, Any]]] = (eod_data or {}).get("ml_auc_scores", {})

        drift_analysis: dict[str, Any] = {}
        buckets: list[str] = []
        total_insights = 0

        for bucket, scores in ml_auc_scores.items():
            if not scores:
                continue
            buckets.append(bucket)
            total_insights += len(scores)

            # Sort by date to compute trend
            sorted_scores = sorted(scores, key=lambda s: s["date"])
            auc_values = [s["auc"] for s in sorted_scores]

            if len(auc_values) < 2:
                # Not enough data points to compute drift for this bucket
                drift_analysis[bucket] = {
                    "trend": "insufficient",
                    "data_points": len(auc_values),
                    "current_auc": auc_values[-1] if auc_values else None,
                    "drift": 0.0,
                }
                continue

            # Compare first half vs second half averages to detect trend
            mid = len(auc_values) // 2
            first_half_mean = sum(auc_values[:mid]) / mid
            second_half_mean = sum(auc_values[mid:]) / len(auc_values[mid:])
            drift_delta = second_half_mean - first_half_mean
            current_auc = auc_values[-1]

            # Classify trend: degrading if AUC dropped by >0.02, improving if rose >0.02
            if drift_delta < -0.02:
                trend = "degrading"
            elif drift_delta > 0.02:
                trend = "improving"
            else:
                trend = "stable"

            drift_analysis[bucket] = {
                "trend": trend,
                "drift": drift_delta,
                "current_auc": current_auc,
                "data_points": len(auc_values),
                "first_half_mean_auc": round(first_half_mean, 4),
                "second_half_mean_auc": round(second_half_mean, 4),
            }

        return {
            "buckets": buckets,
            "total_insights": total_insights,
            "drift_analysis": drift_analysis,
        }


async def run_weekly_aggregation() -> dict[str, Any]:
    """
    Convenience function to run weekly aggregation.
    """
    aggregator = WeeklyDataAggregator()
    return await aggregator.aggregate_week()
