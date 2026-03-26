"""Unit tests for MetaSearchAgent — focused on pure/isolated methods."""

from unittest.mock import MagicMock, patch

import pytest

from orion.core.solver_schema import (
    EditOp,
    EditOpType,
    ExitLogic,
    SolverConfig,
    SolverEdit,
    SolverFeatures,
    SolverRiskConfig,
)
from orion.storage.models_solvers import SolverMetrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_solver_config(**overrides):
    """Build a minimal SolverConfig suitable for testing."""
    defaults = {
        "version_id": "test_v1",
        "rules": [],
        "features": SolverFeatures(feature_set_id="v1_legacy"),
        "risk": SolverRiskConfig(risk_per_trade_bps=100, max_open_positions=5),
        "exit_logic": ExitLogic(take_profit_atr_multiple=2.0, stop_loss_atr_multiple=1.0),
    }
    defaults.update(overrides)
    return SolverConfig(**defaults)


def _make_metrics(**overrides):
    """Build a SolverMetrics with sensible defaults for scoring tests."""
    defaults = {
        "id": "metric_1",
        "solver_id": "solver_1",
        "dataset_tag": "validation",
        "sharpe_ratio": 1.0,
        "profit_factor": 1.5,
        "info_ratio": 0.5,
        "stability_score": 0.8,
        "max_dd_pct": 5.0,
    }
    defaults.update(overrides)
    return SolverMetrics(**defaults)


def _make_agent():
    """Create a MetaSearchAgent with mocked internal dependencies.

    Both MetaAgent and VectorStore are imported inside __init__, so we
    patch them at their *source* module paths.
    """
    with (
        patch("orion.agents.meta_agent.MetaAgent"),
        patch("orion.rag.vector_store.VectorStore", side_effect=Exception("skip")),
    ):
        from orion.agents.meta_search_agent import MetaSearchAgent

        agent = MetaSearchAgent()
    return agent


# ---------------------------------------------------------------------------
# _calculate_composite_score
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCalculateCompositeScore:
    """Test _calculate_composite_score (pure computation)."""

    def test_default_weights(self):
        """Score uses default weights: sharpe=0.4, pf=0.3, ir=0.2, stability=0.1."""
        agent = _make_agent()
        metrics = _make_metrics(sharpe_ratio=2.0, profit_factor=2.0, info_ratio=1.0, stability_score=1.0)

        score = agent._calculate_composite_score(metrics)

        # (0.4*2.0) + (0.3*2.0) + (0.2*1.0) + (0.1*1.0) = 0.8 + 0.6 + 0.2 + 0.1 = 1.7
        assert score == pytest.approx(1.7, abs=0.001)

    def test_custom_weights(self):
        """Custom weights override defaults."""
        agent = _make_agent()
        metrics = _make_metrics(sharpe_ratio=1.0, profit_factor=1.0, info_ratio=1.0, stability_score=1.0)

        score = agent._calculate_composite_score(
            metrics, weights={"sharpe": 1.0, "profit_factor": 0.0, "info_ratio": 0.0, "stability": 0.0}
        )

        assert score == pytest.approx(1.0, abs=0.001)

    def test_drawdown_penalty(self):
        """Drawdown > 25% applies -2.0 penalty."""
        agent = _make_agent()
        metrics = _make_metrics(max_dd_pct=30.0)

        score_with_dd = agent._calculate_composite_score(metrics)

        metrics_no_dd = _make_metrics(max_dd_pct=5.0)
        score_without_dd = agent._calculate_composite_score(metrics_no_dd)

        assert score_with_dd == pytest.approx(score_without_dd - 2.0, abs=0.001)

    def test_drawdown_exactly_25_no_penalty(self):
        """Drawdown of exactly 25% does NOT trigger penalty."""
        agent = _make_agent()
        metrics_25 = _make_metrics(max_dd_pct=25.0)
        metrics_5 = _make_metrics(max_dd_pct=5.0)

        assert agent._calculate_composite_score(metrics_25) == pytest.approx(
            agent._calculate_composite_score(metrics_5), abs=0.001
        )

    def test_clamping_sharpe_upper(self):
        """Sharpe is clamped to max 5.0."""
        agent = _make_agent()
        metrics = _make_metrics(sharpe_ratio=10.0, profit_factor=0, info_ratio=0, stability_score=0)

        score = agent._calculate_composite_score(metrics)

        # Sharpe clamped to 5.0: 0.4 * 5.0 = 2.0
        assert score == pytest.approx(2.0, abs=0.001)

    def test_clamping_sharpe_lower(self):
        """Sharpe is clamped to min -3.0."""
        agent = _make_agent()
        metrics = _make_metrics(sharpe_ratio=-10.0, profit_factor=0, info_ratio=0, stability_score=0)

        score = agent._calculate_composite_score(metrics)

        # Sharpe clamped to -3.0: 0.4 * (-3.0) = -1.2
        assert score == pytest.approx(-1.2, abs=0.001)

    def test_none_values_treated_as_zero(self):
        """None metrics values are treated as 0."""
        agent = _make_agent()
        metrics = _make_metrics(sharpe_ratio=None, profit_factor=None, info_ratio=None, stability_score=None)
        metrics.sharpe_ratio = None
        metrics.profit_factor = None
        metrics.info_ratio = None
        metrics.stability_score = None

        score = agent._calculate_composite_score(metrics)

        assert score == pytest.approx(0.0, abs=0.001)

    def test_zero_metrics(self):
        """All-zero metrics returns 0 score."""
        agent = _make_agent()
        metrics = _make_metrics(sharpe_ratio=0, profit_factor=0, info_ratio=0, stability_score=0, max_dd_pct=0)

        score = agent._calculate_composite_score(metrics)

        assert score == pytest.approx(0.0, abs=0.001)


# ---------------------------------------------------------------------------
# apply_edit + _apply_edit_op
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyEdit:
    """Test apply_edit and internal _apply_edit_op methods."""

    def test_modify_param_risk_per_trade_bps(self):
        """MODIFY_RISK changes risk.risk_per_trade_bps."""
        agent = _make_agent()
        base = _make_solver_config()

        edit = SolverEdit(
            base_solver_id="test_v1",
            new_solver_id="test_v2",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.MODIFY_RISK,
                    param_name="risk_per_trade_bps",
                    old_value=100,
                    new_value=80,
                    reasoning="Reduce risk",
                )
            ],
        )

        result = agent.apply_edit(base, edit)

        assert result.version_id == "test_v2"
        assert result.risk.risk_per_trade_bps == 80

    def test_modify_param_exit_logic(self):
        """MODIFY_PARAM changes exit_logic.take_profit_atr_multiple."""
        agent = _make_agent()
        base = _make_solver_config()

        edit = SolverEdit(
            base_solver_id="test_v1",
            new_solver_id="test_v2",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.MODIFY_PARAM,
                    param_name="exit_logic.take_profit_atr_multiple",
                    old_value=2.0,
                    new_value=3.0,
                    reasoning="Widen TP",
                )
            ],
        )

        result = agent.apply_edit(base, edit)

        assert result.exit_logic.take_profit_atr_multiple == 3.0

    def test_modify_param_backward_compat_bare_risk(self):
        """Bare 'risk_per_trade_bps' is auto-prefixed with 'risk.'."""
        agent = _make_agent()
        base = _make_solver_config()

        edit = SolverEdit(
            base_solver_id="test_v1",
            new_solver_id="test_v2",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.MODIFY_PARAM,
                    param_name="risk_per_trade_bps",
                    old_value=100,
                    new_value=50,
                    reasoning="Compat test",
                )
            ],
        )

        result = agent.apply_edit(base, edit)

        assert result.risk.risk_per_trade_bps == 50

    def test_toggle_feature_enable(self):
        """TOGGLE_FEATURE adds a feature to event_features."""
        agent = _make_agent()
        base = _make_solver_config()

        edit = SolverEdit(
            base_solver_id="test_v1",
            new_solver_id="test_v2",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.TOGGLE_FEATURE,
                    feature_name="new_signal",
                    new_value=True,
                    reasoning="Add signal",
                )
            ],
        )

        result = agent.apply_edit(base, edit)

        assert "new_signal" in result.features.event_features

    def test_toggle_feature_disable(self):
        """TOGGLE_FEATURE removes a feature from event_features."""
        agent = _make_agent()
        base = _make_solver_config()
        base.features.event_features = ["sig_a", "sig_b"]

        edit = SolverEdit(
            base_solver_id="test_v1",
            new_solver_id="test_v2",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.TOGGLE_FEATURE,
                    feature_name="sig_a",
                    new_value=False,
                    reasoning="Remove signal",
                )
            ],
        )

        result = agent.apply_edit(base, edit)

        assert "sig_a" not in result.features.event_features
        assert "sig_b" in result.features.event_features

    def test_add_rule(self):
        """ADD_RULE appends a rule string to config.rules."""
        agent = _make_agent()
        base = _make_solver_config(rules=["rule_a"])

        edit = SolverEdit(
            base_solver_id="test_v1",
            new_solver_id="test_v2",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.ADD_RULE,
                    new_value="rule_b",
                    reasoning="Add rule",
                )
            ],
        )

        result = agent.apply_edit(base, edit)

        assert "rule_a" in result.rules
        assert "rule_b" in result.rules

    def test_add_rule_no_duplicates(self):
        """ADD_RULE does not add duplicate rules."""
        agent = _make_agent()
        base = _make_solver_config(rules=["rule_a"])

        edit = SolverEdit(
            base_solver_id="test_v1",
            new_solver_id="test_v2",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.ADD_RULE,
                    new_value="rule_a",
                    reasoning="Duplicate attempt",
                )
            ],
        )

        result = agent.apply_edit(base, edit)

        assert result.rules.count("rule_a") == 1

    def test_remove_rule(self):
        """REMOVE_RULE removes a rule from config.rules."""
        agent = _make_agent()
        base = _make_solver_config(rules=["rule_a", "rule_b"])

        edit = SolverEdit(
            base_solver_id="test_v1",
            new_solver_id="test_v2",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.REMOVE_RULE,
                    new_value="rule_a",
                    reasoning="Remove rule",
                )
            ],
        )

        result = agent.apply_edit(base, edit)

        assert "rule_a" not in result.rules
        assert "rule_b" in result.rules

    def test_multiple_ops_applied(self):
        """Multiple edit operations are applied sequentially."""
        agent = _make_agent()
        base = _make_solver_config()

        edit = SolverEdit(
            base_solver_id="test_v1",
            new_solver_id="test_v2",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.MODIFY_RISK,
                    param_name="risk_per_trade_bps",
                    old_value=100,
                    new_value=80,
                    reasoning="Reduce risk",
                ),
                EditOp(
                    op=EditOpType.MODIFY_PARAM,
                    param_name="exit_logic.take_profit_atr_multiple",
                    old_value=2.0,
                    new_value=2.5,
                    reasoning="Widen TP",
                ),
            ],
        )

        result = agent.apply_edit(base, edit)

        assert result.risk.risk_per_trade_bps == 80
        assert result.exit_logic.take_profit_atr_multiple == 2.5

    def test_apply_edit_does_not_mutate_base(self):
        """apply_edit returns a new config; base config is unchanged."""
        agent = _make_agent()
        base = _make_solver_config()

        edit = SolverEdit(
            base_solver_id="test_v1",
            new_solver_id="test_v2",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.MODIFY_RISK,
                    param_name="risk_per_trade_bps",
                    old_value=100,
                    new_value=50,
                    reasoning="Test immutability",
                )
            ],
        )

        result = agent.apply_edit(base, edit)

        assert base.risk.risk_per_trade_bps == 100
        assert result.risk.risk_per_trade_bps == 50
        assert base.version_id == "test_v1"


# ---------------------------------------------------------------------------
# _normalize_ticker (static method)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeTicker:
    """Test the static _normalize_ticker method."""

    def test_basic_uppercase(self):
        from orion.agents.meta_search_agent import MetaSearchAgent

        assert MetaSearchAgent._normalize_ticker("aapl") == "AAPL"

    def test_already_uppercase(self):
        from orion.agents.meta_search_agent import MetaSearchAgent

        assert MetaSearchAgent._normalize_ticker("MSFT") == "MSFT"

    def test_strips_whitespace(self):
        from orion.agents.meta_search_agent import MetaSearchAgent

        assert MetaSearchAgent._normalize_ticker("  SPY  ") == "SPY"

    def test_colon_prefix_stripped(self):
        """Instrument key format 'equity:AAPL' is reduced to 'AAPL'."""
        from orion.agents.meta_search_agent import MetaSearchAgent

        assert MetaSearchAgent._normalize_ticker("equity:AAPL") == "AAPL"

    def test_none_returns_none(self):
        from orion.agents.meta_search_agent import MetaSearchAgent

        assert MetaSearchAgent._normalize_ticker(None) is None

    def test_empty_string_returns_none(self):
        from orion.agents.meta_search_agent import MetaSearchAgent

        assert MetaSearchAgent._normalize_ticker("") is None

    def test_whitespace_only_returns_none(self):
        from orion.agents.meta_search_agent import MetaSearchAgent

        assert MetaSearchAgent._normalize_ticker("   ") is None


# ---------------------------------------------------------------------------
# _next_stage / _previous_stage (pure logic)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStageNavigation:
    """Test _next_stage and _previous_stage helper methods."""

    def test_next_stage_normal(self):
        agent = _make_agent()
        stages = ["research", "paper", "live"]

        assert agent._next_stage(stages, "research") == "paper"
        assert agent._next_stage(stages, "paper") == "live"

    def test_next_stage_at_end_returns_none(self):
        agent = _make_agent()
        stages = ["research", "paper", "live"]

        assert agent._next_stage(stages, "live") is None

    def test_next_stage_unknown_returns_none(self):
        agent = _make_agent()
        stages = ["research", "paper", "live"]

        assert agent._next_stage(stages, "unknown") is None

    def test_previous_stage_normal(self):
        agent = _make_agent()
        stages = ["research", "paper", "live"]

        assert agent._previous_stage(stages, "live") == "paper"
        assert agent._previous_stage(stages, "paper") == "research"

    def test_previous_stage_at_start_returns_self(self):
        agent = _make_agent()
        stages = ["research", "paper", "live"]

        assert agent._previous_stage(stages, "research") == "research"

    def test_previous_stage_unknown_returns_self(self):
        agent = _make_agent()
        stages = ["research", "paper", "live"]

        assert agent._previous_stage(stages, "unknown") == "unknown"


# ---------------------------------------------------------------------------
# _analyze_execution_quality (pure computation)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnalyzeExecutionQuality:
    """Test _analyze_execution_quality (pure dict transformation)."""

    def test_excellent_execution(self):
        agent = _make_agent()
        week_data = {
            "trade_execution": {
                "trades": {
                    "total_orders": 100,
                    "fill_rate": 0.95,
                    "rejected": 2,
                    "tickers": ["AAPL", "MSFT", "GOOG"],
                }
            }
        }

        result = agent._analyze_execution_quality(week_data)

        assert result["execution_health"] == "excellent"
        assert result["total_orders"] == 100
        assert result["fill_rate"] == 0.95
        assert result["rejection_rate"] == pytest.approx(0.02)
        assert result["unique_tickers"] == 3

    def test_good_execution(self):
        agent = _make_agent()
        week_data = {
            "trade_execution": {"trades": {"total_orders": 50, "fill_rate": 0.75, "rejected": 5, "tickers": ["AAPL"]}}
        }

        result = agent._analyze_execution_quality(week_data)

        assert result["execution_health"] == "good"

    def test_degraded_execution(self):
        agent = _make_agent()
        week_data = {
            "trade_execution": {"trades": {"total_orders": 20, "fill_rate": 0.55, "rejected": 3, "tickers": []}}
        }

        result = agent._analyze_execution_quality(week_data)

        assert result["execution_health"] == "degraded"

    def test_poor_execution(self):
        agent = _make_agent()
        week_data = {
            "trade_execution": {"trades": {"total_orders": 10, "fill_rate": 0.3, "rejected": 5, "tickers": []}}
        }

        result = agent._analyze_execution_quality(week_data)

        assert result["execution_health"] == "poor"

    def test_no_orders(self):
        agent = _make_agent()
        week_data = {"trade_execution": {"trades": {"total_orders": 0, "fill_rate": 0, "rejected": 0, "tickers": []}}}

        result = agent._analyze_execution_quality(week_data)

        assert result["execution_health"] == "unknown"
        assert result["rejection_rate"] == 0.0

    def test_missing_trade_data(self):
        agent = _make_agent()
        week_data = {"trade_execution": {}}

        result = agent._analyze_execution_quality(week_data)

        assert result["execution_health"] == "unknown"
        assert result["total_orders"] == 0


# ---------------------------------------------------------------------------
# _analyze_ml_drift (pure computation)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnalyzeMlDrift:
    """Test _analyze_ml_drift (pure dict transformation)."""

    def test_healthy_no_degrading(self):
        agent = _make_agent()
        week_data = {
            "eod_reports": {"top_features": {"feat_a": 5}},
            "ml_insights": {
                "drift_analysis": {
                    "bucket_a": {"trend": "stable", "drift": 0.0, "current_auc": 0.85},
                    "bucket_b": {"trend": "improving", "drift": 0.03, "current_auc": 0.90},
                }
            },
        }

        result = agent._analyze_ml_drift(week_data)

        assert result["overall_health"] == "healthy"
        assert len(result["degrading_buckets"]) == 0
        assert "bucket_b" in result["improving_buckets"]

    def test_significant_drift(self):
        agent = _make_agent()
        week_data = {
            "eod_reports": {"top_features": {}},
            "ml_insights": {
                "drift_analysis": {
                    "bucket_a": {"trend": "degrading", "drift": -0.05, "current_auc": 0.70},
                    "bucket_b": {"trend": "degrading", "drift": -0.04, "current_auc": 0.72},
                    "bucket_c": {"trend": "stable", "drift": 0.0, "current_auc": 0.85},
                }
            },
        }

        result = agent._analyze_ml_drift(week_data)

        assert result["overall_health"] == "significant_drift"
        assert len(result["degrading_buckets"]) == 2

    def test_minor_drift(self):
        agent = _make_agent()
        week_data = {
            "eod_reports": {"top_features": {}},
            "ml_insights": {
                "drift_analysis": {
                    "bucket_a": {"trend": "degrading", "drift": -0.03, "current_auc": 0.78},
                    "bucket_b": {"trend": "stable", "drift": 0.0, "current_auc": 0.85},
                    "bucket_c": {"trend": "stable", "drift": 0.0, "current_auc": 0.87},
                    "bucket_d": {"trend": "stable", "drift": 0.0, "current_auc": 0.82},
                }
            },
        }

        result = agent._analyze_ml_drift(week_data)

        assert result["overall_health"] == "minor_drift"

    def test_no_data(self):
        agent = _make_agent()
        week_data = {"eod_reports": {}, "ml_insights": {"drift_analysis": {}}}

        result = agent._analyze_ml_drift(week_data)

        assert result["overall_health"] == "no_data"

    def test_insufficient_data(self):
        """All buckets have 'insufficient' trend."""
        agent = _make_agent()
        week_data = {
            "eod_reports": {"top_features": {}},
            "ml_insights": {
                "drift_analysis": {
                    "bucket_a": {"trend": "insufficient", "data_points": 1},
                }
            },
        }

        result = agent._analyze_ml_drift(week_data)

        assert result["overall_health"] == "insufficient_data"
        assert "bucket_a" in result["insufficient_buckets"]

    def test_top_features_propagated(self):
        agent = _make_agent()
        week_data = {
            "eod_reports": {"top_features": {"vwap_ratio": 3, "volume_zscore": 2}},
            "ml_insights": {"drift_analysis": {}},
        }

        result = agent._analyze_ml_drift(week_data)

        assert result["top_features"]["vwap_ratio"] == 3
        assert result["top_features"]["volume_zscore"] == 2


# ---------------------------------------------------------------------------
# _rule_matches (pure logic)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRuleMatches:
    """Test _rule_matches helper."""

    def test_string_match(self):
        agent = _make_agent()
        assert agent._rule_matches("rule_a", "rule_a") is True

    def test_string_mismatch(self):
        agent = _make_agent()
        assert agent._rule_matches("rule_a", "rule_b") is False

    def test_object_with_id_match(self):
        agent = _make_agent()
        rule_obj = MagicMock()
        rule_obj.id = "rule_x"
        assert agent._rule_matches(rule_obj, "rule_x") is True

    def test_object_with_id_mismatch(self):
        agent = _make_agent()
        rule_obj = MagicMock()
        rule_obj.id = "rule_x"
        assert agent._rule_matches(rule_obj, "rule_y") is False


# ---------------------------------------------------------------------------
# _build_solver_edit (pure logic)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSolverEdit:
    """Test _build_solver_edit (reconstructs SolverEdit from DB row)."""

    def test_basic_build(self):
        agent = _make_agent()
        mock_edit = MagicMock()
        mock_edit.base_solver_id = "base_v1"
        mock_edit.new_solver_id = "new_v1"
        mock_edit.generated_by = "meta_agent"
        mock_edit.edit_json = {
            "ops": [
                {
                    "op": "modify_risk",
                    "param_name": "risk_per_trade_bps",
                    "old_value": 100,
                    "new_value": 50,
                    "reasoning": "Reduce risk",
                }
            ]
        }

        result = agent._build_solver_edit(mock_edit)

        assert isinstance(result, SolverEdit)
        assert result.base_solver_id == "base_v1"
        assert result.new_solver_id == "new_v1"
        assert len(result.ops) == 1
        assert result.ops[0].op == EditOpType.MODIFY_RISK

    def test_empty_ops(self):
        agent = _make_agent()
        mock_edit = MagicMock()
        mock_edit.base_solver_id = "b"
        mock_edit.new_solver_id = "n"
        mock_edit.generated_by = "test"
        mock_edit.edit_json = {"ops": []}

        result = agent._build_solver_edit(mock_edit)

        assert result.ops == []


# ---------------------------------------------------------------------------
# _maybe_add_expiry_dte (pure helper)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMaybeAddExpiryDte:
    """Test _maybe_add_expiry_dte helper."""

    def test_no_expiry_value(self):
        from orion.agents.heber_event_mapper import _maybe_add_expiry_dte

        payload = {}
        _maybe_add_expiry_dte(payload, None, MagicMock())

        assert "expiry" not in payload
        assert "dte" not in payload

    def test_valid_expiry_adds_dte(self):
        import pandas as pd

        from orion.agents.heber_event_mapper import _maybe_add_expiry_dte

        payload = {}
        flow_ts = pd.Timestamp("2026-01-10", tz="UTC")
        expiry_value = "2026-01-17"

        _maybe_add_expiry_dte(payload, expiry_value, flow_ts)

        assert payload["expiry"] == "2026-01-17"
        assert payload["dte"] == 7

    def test_invalid_expiry_no_dte(self):
        import pandas as pd

        from orion.agents.heber_event_mapper import _maybe_add_expiry_dte

        payload = {}
        flow_ts = pd.Timestamp("2026-01-10", tz="UTC")
        expiry_value = "not_a_date"

        _maybe_add_expiry_dte(payload, expiry_value, flow_ts)

        # Expiry is still set but dte is not computed
        assert payload["expiry"] == "not_a_date"
        assert "dte" not in payload


# ---------------------------------------------------------------------------
# _load_yaml_artifact (pure file I/O)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadYamlArtifact:
    """Test _load_yaml_artifact static method."""

    def test_loads_valid_yaml(self, tmp_path):
        from orion.agents.meta_search_agent import MetaSearchAgent

        yaml_content = "meta:\n  status: PROPOSED\nproposal:\n  type: solver_edit\n"
        path = tmp_path / "test.yaml"
        path.write_text(yaml_content)

        result = MetaSearchAgent._load_yaml_artifact(str(path))

        assert result["meta"]["status"] == "PROPOSED"
        assert result["proposal"]["type"] == "solver_edit"

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        from orion.agents.meta_search_agent import MetaSearchAgent

        path = tmp_path / "empty.yaml"
        path.write_text("")

        result = MetaSearchAgent._load_yaml_artifact(str(path))

        assert result == {}
