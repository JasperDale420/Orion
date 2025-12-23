from datetime import datetime

import pytest
from orion.core.solver_schema import EditOp, EditOpType, SolverEdit
from pydantic import ValidationError


def test_edit_op_creation():
    """Verify EditOp model creates correctly."""
    op = EditOp(
        op=EditOpType.MODIFY_PARAM,
        param_name="rsi_period",
        old_value=14,
        new_value=21,
        reasoning="Market regime shift to trend",
    )
    assert op.op == EditOpType.MODIFY_PARAM
    assert op.new_value == 21


def test_solver_edit_creation():
    """Verify SolverEdit container holds ops."""
    op1 = EditOp(
        op=EditOpType.MODIFY_RISK, param_name="risk_per_trade_bps", new_value=50, reasoning="Drawdown limit reached"
    )

    edit = SolverEdit(base_solver_id="base_v1", new_solver_id="new_v2", generated_by="meta_agent", ops=[op1])

    assert len(edit.ops) == 1
    assert edit.ops[0].new_value == 50
    assert isinstance(edit.created_at_utc, datetime)


def test_validation_failure():
    """Verify missing fields raise error."""
    with pytest.raises(ValidationError):
        # Missing reasoning
        EditOp(op=EditOpType.REMOVE_RULE, rule_id="rule_abc", new_value="True")


def test_op_types_enum():
    """Verify enum values."""
    assert EditOpType.TOGGLE_FEATURE.value == "toggle_feature"
