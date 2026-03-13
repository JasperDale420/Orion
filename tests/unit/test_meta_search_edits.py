import sys
from unittest.mock import MagicMock

# Mock litellm before imports
sys.modules["litellm"] = MagicMock()

from unittest.mock import patch

import pytest

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.core.solver_schema import EditOp, EditOpType, SolverConfig, SolverEdit, SolverFeatures


@pytest.fixture
def agent():
    with patch("orion.agents.meta_agent.MetaAgent") as MockMeta:  # noqa: N806
        agent = MetaSearchAgent()
        # Ensure meta_agent inside is also a mock
        agent.meta_agent = MockMeta.return_value
        return agent


@pytest.fixture
def base_config():
    return SolverConfig(
        version_id="base",
        rules=["rule1", "rule2"],
        features=SolverFeatures(feature_set_id="v1_legacy", event_features=["f1", "f2"], window_features=["w1"]),
    )


def test_toggle_feature_enable(agent, base_config):
    edit = SolverEdit(
        base_solver_id="base",
        new_solver_id="new",
        generated_by="test",
        ops=[EditOp(op=EditOpType.TOGGLE_FEATURE, feature_name="f3_new", new_value=True, reasoning="test")],
    )

    new_cfg = agent.apply_edit(base_config, edit)
    assert "f3_new" in new_cfg.features.event_features
    assert "f1" in new_cfg.features.event_features


def test_toggle_feature_disable(agent, base_config):
    edit = SolverEdit(
        base_solver_id="base",
        new_solver_id="new",
        generated_by="test",
        ops=[EditOp(op=EditOpType.TOGGLE_FEATURE, feature_name="f1", new_value=False, reasoning="test")],
    )

    new_cfg = agent.apply_edit(base_config, edit)
    assert "f1" not in new_cfg.features.event_features
    assert "f2" in new_cfg.features.event_features


def test_add_rule(agent, base_config):
    edit = SolverEdit(
        base_solver_id="base",
        new_solver_id="new",
        generated_by="test",
        ops=[EditOp(op=EditOpType.ADD_RULE, new_value="rule3_new", reasoning="test")],
    )

    new_cfg = agent.apply_edit(base_config, edit)
    assert "rule3_new" in new_cfg.rules
    assert len(new_cfg.rules) == 3


def test_add_rule_duplicate(agent, base_config):
    edit = SolverEdit(
        base_solver_id="base",
        new_solver_id="new",
        generated_by="test",
        ops=[
            EditOp(
                op=EditOpType.ADD_RULE,
                new_value="rule1",  # Already exists
                reasoning="test",
            )
        ],
    )
    new_cfg = agent.apply_edit(base_config, edit)
    assert len(new_cfg.rules) == 2  # Should not add


def test_remove_rule(agent, base_config):
    edit = SolverEdit(
        base_solver_id="base",
        new_solver_id="new",
        generated_by="test",
        ops=[
            EditOp(
                op=EditOpType.REMOVE_RULE,
                rule_id="rule1",
                new_value=None,  # or whatever
                reasoning="test",
            )
        ],
    )
    new_cfg = agent.apply_edit(base_config, edit)
    assert "rule1" not in new_cfg.rules
    assert "rule2" in new_cfg.rules
    assert len(new_cfg.rules) == 1


def test_remove_rule_by_value_fallback(agent, base_config):
    edit = SolverEdit(
        base_solver_id="base",
        new_solver_id="new",
        generated_by="test",
        ops=[
            EditOp(
                op=EditOpType.REMOVE_RULE,
                new_value="rule2",  # Fallback logic uses new_value if rule_id missing in my impl
                reasoning="test",
            )
        ],
    )
    new_cfg = agent.apply_edit(base_config, edit)
    assert "rule2" not in new_cfg.rules
    assert "rule1" in new_cfg.rules
