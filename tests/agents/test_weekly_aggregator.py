"""Unit tests for WeeklyDataAggregator."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.agents.weekly_aggregator import WeeklyDataAggregator


@pytest.mark.unit
class TestAggregateEodReports:
    """Tests for aggregate_eod_reports — reads JSON files from disk."""

    @pytest.mark.asyncio
    async def test_no_artifacts_dir(self, tmp_path):
        """When artifacts_dir does not exist, returns empty summary."""
        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path / "nonexistent"))
        result = await agg.aggregate_eod_reports(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
        )

        assert result["total_reports"] == 0
        assert result["trading_days"] == 0
        assert result["total_decisions"] == 0
        assert result["ingestion_issues"] == []

    @pytest.mark.asyncio
    async def test_no_matching_files(self, tmp_path):
        """Files in dir that don't match pattern are ignored."""
        (tmp_path / "some_other_file.json").write_text("{}")
        (tmp_path / "not_eod.txt").write_text("hello")

        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))
        result = await agg.aggregate_eod_reports(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
        )

        assert result["total_reports"] == 0

    @pytest.mark.asyncio
    async def test_files_outside_date_range_excluded(self, tmp_path):
        """EOD files outside the requested date range are not aggregated."""
        data = {"decisions": {"total": 5, "executed_count": 3, "skipped_count": 2}}
        (tmp_path / "eod_input_2025-12-30_abc.json").write_text(json.dumps(data))

        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))
        result = await agg.aggregate_eod_reports(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
        )

        assert result["total_reports"] == 0

    @pytest.mark.asyncio
    async def test_single_report_aggregated(self, tmp_path):
        """A single EOD input file is read and its metrics aggregated."""
        data = {
            "decisions": {"total": 10, "executed_count": 7, "skipped_count": 3},
            "fills": {"total": 5},
            "ml_insights": {},
            "errors_incidents": {},
        }
        (tmp_path / "eod_input_2026-01-03_abc.json").write_text(json.dumps(data))

        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))
        result = await agg.aggregate_eod_reports(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
        )

        assert result["total_reports"] == 1
        assert result["trading_days"] == 1
        assert result["total_decisions"] == 10
        assert result["executed_count"] == 7
        assert result["skipped_count"] == 3
        assert result["total_fills"] == 5

    @pytest.mark.asyncio
    async def test_multiple_reports_aggregated(self, tmp_path):
        """Multiple EOD files within range are summed."""
        day1 = {
            "decisions": {"total": 4, "executed_count": 2, "skipped_count": 2},
            "fills": {"total": 1},
        }
        day2 = {
            "decisions": {"total": 6, "executed_count": 5, "skipped_count": 1},
            "fills": {"total": 3},
        }
        (tmp_path / "eod_input_2026-01-02_a.json").write_text(json.dumps(day1))
        (tmp_path / "eod_input_2026-01-04_b.json").write_text(json.dumps(day2))

        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))
        result = await agg.aggregate_eod_reports(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
        )

        assert result["total_reports"] == 2
        assert result["trading_days"] == 2
        assert result["total_decisions"] == 10
        assert result["executed_count"] == 7
        assert result["total_fills"] == 4

    @pytest.mark.asyncio
    async def test_ml_auc_scores_collected(self, tmp_path):
        """ML insights with holdout_auc are collected per bucket."""
        data = {
            "decisions": {"total": 1},
            "ml_insights": {
                "insights": {
                    "bucket_a": {"holdout_auc": 0.85, "top_features": [{"feature": "vwap_ratio"}]},
                }
            },
        }
        (tmp_path / "eod_input_2026-01-03_a.json").write_text(json.dumps(data))

        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))
        result = await agg.aggregate_eod_reports(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
        )

        assert "bucket_a" in result["ml_auc_scores"]
        assert len(result["ml_auc_scores"]["bucket_a"]) == 1
        assert result["ml_auc_scores"]["bucket_a"][0]["auc"] == 0.85
        assert "vwap_ratio" in result["top_features"]

    @pytest.mark.asyncio
    async def test_ingestion_issues_tracked(self, tmp_path):
        """Sources with lag_max_s > 300 are flagged as ingestion issues."""
        data = {
            "decisions": {"total": 1},
            "errors_incidents": {
                "ingestion": {
                    "sources": {
                        "alpaca": {"lag_max_s": 500},
                        "finnhub": {"lag_max_s": 100},
                    }
                }
            },
        }
        (tmp_path / "eod_input_2026-01-03_a.json").write_text(json.dumps(data))

        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))
        result = await agg.aggregate_eod_reports(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
        )

        assert len(result["ingestion_issues"]) == 1
        assert result["ingestion_issues"][0]["source"] == "alpaca"
        assert result["ingestion_issues"][0]["max_lag_s"] == 500

    @pytest.mark.asyncio
    async def test_malformed_file_skipped(self, tmp_path):
        """Malformed JSON files are logged and skipped, not crashing."""
        (tmp_path / "eod_input_2026-01-03_bad.json").write_text("NOT JSON")

        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))
        result = await agg.aggregate_eod_reports(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
        )

        assert result["total_reports"] == 0

    @pytest.mark.asyncio
    async def test_empty_decisions_not_counted_as_trading_day(self, tmp_path):
        """Reports with decisions.total=0 do not count as trading days."""
        data = {"decisions": {"total": 0}}
        (tmp_path / "eod_input_2026-01-03_a.json").write_text(json.dumps(data))

        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))
        result = await agg.aggregate_eod_reports(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
        )

        assert result["total_reports"] == 1
        assert result["trading_days"] == 0

    @pytest.mark.asyncio
    async def test_top_features_sorted_by_frequency(self, tmp_path):
        """Top features are sorted descending by occurrence count."""
        data1 = {
            "decisions": {"total": 1},
            "ml_insights": {
                "insights": {
                    "b1": {
                        "holdout_auc": 0.8,
                        "top_features": [
                            {"feature": "feat_a"},
                            {"feature": "feat_b"},
                        ],
                    },
                }
            },
        }
        data2 = {
            "decisions": {"total": 1},
            "ml_insights": {
                "insights": {
                    "b1": {
                        "holdout_auc": 0.82,
                        "top_features": [
                            {"feature": "feat_a"},
                            {"feature": "feat_c"},
                        ],
                    },
                }
            },
        }
        (tmp_path / "eod_input_2026-01-02_a.json").write_text(json.dumps(data1))
        (tmp_path / "eod_input_2026-01-03_b.json").write_text(json.dumps(data2))

        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))
        result = await agg.aggregate_eod_reports(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
        )

        features = result["top_features"]
        keys = list(features.keys())
        assert keys[0] == "feat_a"
        assert features["feat_a"] == 2


@pytest.mark.unit
class TestAggregateTradeData:
    """Tests for aggregate_trade_data — queries the DB."""

    @pytest.mark.asyncio
    async def test_returns_data_on_success(self):
        """Happy path: db_query returns trade stats dict."""
        expected = {
            "decisions": {"total": 10, "executed": 7},
            "trades": {
                "total_orders": 5,
                "filled": 4,
                "rejected": 1,
                "cancelled": 0,
                "pending": 0,
                "total_qty": 200,
                "tickers": ["AAPL", "MSFT"],
                "fill_rate": 0.8,
            },
        }

        agg = WeeklyDataAggregator()
        with patch("orion.agents.weekly_aggregator.db_query", new_callable=AsyncMock, return_value=expected):
            result = await agg.aggregate_trade_data(
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 7, tzinfo=UTC),
            )

        assert result == expected

    @pytest.mark.asyncio
    async def test_returns_error_on_db_failure(self):
        """On DB error, returns error dict instead of raising."""
        agg = WeeklyDataAggregator()
        with patch(
            "orion.agents.weekly_aggregator.db_query",
            new_callable=AsyncMock,
            side_effect=Exception("Connection refused"),
        ):
            result = await agg.aggregate_trade_data(
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 7, tzinfo=UTC),
            )

        assert "error" in result
        assert "Connection refused" in result["error"]
        assert result["decisions"] == {}
        assert result["trades"] == {}


@pytest.mark.unit
class TestAggregateMlInsights:
    """Tests for aggregate_ml_insights — pure data transformation."""

    @pytest.mark.asyncio
    async def test_no_auc_data(self):
        """With no ml_auc_scores in eod_data, returns empty analysis."""
        agg = WeeklyDataAggregator()
        result = await agg.aggregate_ml_insights(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
            eod_data={},
        )

        assert result["buckets"] == []
        assert result["total_insights"] == 0
        assert result["drift_analysis"] == {}

    @pytest.mark.asyncio
    async def test_single_data_point_insufficient(self):
        """One AUC score per bucket yields 'insufficient' trend."""
        eod_data = {
            "ml_auc_scores": {
                "bucket_a": [{"date": "2026-01-03", "auc": 0.8}],
            }
        }
        agg = WeeklyDataAggregator()
        result = await agg.aggregate_ml_insights(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
            eod_data=eod_data,
        )

        assert result["buckets"] == ["bucket_a"]
        assert result["total_insights"] == 1
        assert result["drift_analysis"]["bucket_a"]["trend"] == "insufficient"
        assert result["drift_analysis"]["bucket_a"]["current_auc"] == 0.8
        assert result["drift_analysis"]["bucket_a"]["drift"] == 0.0

    @pytest.mark.asyncio
    async def test_degrading_trend_detected(self):
        """AUC dropping from first half to second half is classified as degrading."""
        eod_data = {
            "ml_auc_scores": {
                "bucket_a": [
                    {"date": "2026-01-01", "auc": 0.90},
                    {"date": "2026-01-02", "auc": 0.88},
                    {"date": "2026-01-03", "auc": 0.82},
                    {"date": "2026-01-04", "auc": 0.80},
                ],
            }
        }
        agg = WeeklyDataAggregator()
        result = await agg.aggregate_ml_insights(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
            eod_data=eod_data,
        )

        assert result["drift_analysis"]["bucket_a"]["trend"] == "degrading"
        assert result["drift_analysis"]["bucket_a"]["drift"] < -0.02

    @pytest.mark.asyncio
    async def test_improving_trend_detected(self):
        """AUC rising from first half to second half is classified as improving."""
        eod_data = {
            "ml_auc_scores": {
                "bucket_a": [
                    {"date": "2026-01-01", "auc": 0.70},
                    {"date": "2026-01-02", "auc": 0.72},
                    {"date": "2026-01-03", "auc": 0.80},
                    {"date": "2026-01-04", "auc": 0.85},
                ],
            }
        }
        agg = WeeklyDataAggregator()
        result = await agg.aggregate_ml_insights(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
            eod_data=eod_data,
        )

        assert result["drift_analysis"]["bucket_a"]["trend"] == "improving"
        assert result["drift_analysis"]["bucket_a"]["drift"] > 0.02

    @pytest.mark.asyncio
    async def test_stable_trend(self):
        """AUC with small drift (<0.02) is classified as stable."""
        eod_data = {
            "ml_auc_scores": {
                "bucket_a": [
                    {"date": "2026-01-01", "auc": 0.80},
                    {"date": "2026-01-02", "auc": 0.81},
                    {"date": "2026-01-03", "auc": 0.80},
                    {"date": "2026-01-04", "auc": 0.81},
                ],
            }
        }
        agg = WeeklyDataAggregator()
        result = await agg.aggregate_ml_insights(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
            eod_data=eod_data,
        )

        assert result["drift_analysis"]["bucket_a"]["trend"] == "stable"
        assert abs(result["drift_analysis"]["bucket_a"]["drift"]) <= 0.02

    @pytest.mark.asyncio
    async def test_multiple_buckets(self):
        """Drift is computed independently per bucket."""
        eod_data = {
            "ml_auc_scores": {
                "bucket_a": [
                    {"date": "2026-01-01", "auc": 0.90},
                    {"date": "2026-01-02", "auc": 0.70},
                ],
                "bucket_b": [
                    {"date": "2026-01-01", "auc": 0.70},
                    {"date": "2026-01-02", "auc": 0.90},
                ],
            }
        }
        agg = WeeklyDataAggregator()
        result = await agg.aggregate_ml_insights(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
            eod_data=eod_data,
        )

        assert len(result["buckets"]) == 2
        assert result["total_insights"] == 4
        assert result["drift_analysis"]["bucket_a"]["trend"] == "degrading"
        assert result["drift_analysis"]["bucket_b"]["trend"] == "improving"

    @pytest.mark.asyncio
    async def test_none_eod_data(self):
        """When eod_data is None, returns empty analysis."""
        agg = WeeklyDataAggregator()
        result = await agg.aggregate_ml_insights(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
            eod_data=None,
        )

        assert result["buckets"] == []
        assert result["total_insights"] == 0

    @pytest.mark.asyncio
    async def test_empty_scores_list_skipped(self):
        """Bucket with empty scores list is skipped."""
        eod_data = {
            "ml_auc_scores": {
                "bucket_a": [],
            }
        }
        agg = WeeklyDataAggregator()
        result = await agg.aggregate_ml_insights(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 7, tzinfo=UTC),
            eod_data=eod_data,
        )

        assert result["buckets"] == []
        assert "bucket_a" not in result["drift_analysis"]


@pytest.mark.unit
class TestAggregateWeek:
    """Tests for the top-level aggregate_week orchestrator."""

    @pytest.mark.asyncio
    async def test_aggregate_week_calls_sub_methods(self, tmp_path):
        """aggregate_week invokes all three sub-methods and returns combined dict."""
        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))

        mock_eod = {
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

        with patch.object(agg, "aggregate_eod_reports", new_callable=AsyncMock, return_value=mock_eod):
            with patch.object(
                agg, "aggregate_trade_data", new_callable=AsyncMock, return_value={"decisions": {}, "trades": {}}
            ):
                week_end = datetime(2026, 1, 7, tzinfo=UTC)
                result = await agg.aggregate_week(week_end)

        assert "period" in result
        assert "eod_reports" in result
        assert "trade_execution" in result
        assert "ml_insights" in result

        # Verify period boundaries
        expected_start = (week_end - timedelta(days=7)).isoformat()
        assert result["period"]["start"] == expected_start
        assert result["period"]["end"] == week_end.isoformat()

    @pytest.mark.asyncio
    async def test_aggregate_week_default_end_date(self, tmp_path):
        """When week_end is None, defaults to now(UTC)."""
        agg = WeeklyDataAggregator(artifacts_dir=str(tmp_path))

        with patch.object(agg, "aggregate_eod_reports", new_callable=AsyncMock) as mock_eod:
            mock_eod.return_value = {
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
            with patch.object(
                agg, "aggregate_trade_data", new_callable=AsyncMock, return_value={"decisions": {}, "trades": {}}
            ):
                result = await agg.aggregate_week(None)

        # Should have been called; period end should be approximately now
        assert mock_eod.called
        assert result["period"]["end"] is not None
