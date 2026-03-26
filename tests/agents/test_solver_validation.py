import pytest
from pydantic import ValidationError

from orion.core.solver_schema import SolverFeatures, SolverRiskConfig


def test_risk_validation_limits():
    # Valid Risk
    risk = SolverRiskConfig(risk_per_trade_bps=100, max_open_positions=5)
    assert risk.risk_per_trade_bps == 100

    # Invalid Risk (BPS too high)
    with pytest.raises(ValidationError) as excinfo:
        SolverRiskConfig(risk_per_trade_bps=1000)  # System limit is 500
    assert "exceeds system limit" in str(excinfo.value)

    # Invalid Risk (Positions too high)
    with pytest.raises(ValidationError) as excinfo:
        SolverRiskConfig(max_open_positions=100)  # System limit defined in config usually 5-20
    assert "exceeds system limit" in str(excinfo.value)


def test_feature_set_validation():
    # Valid
    sf = SolverFeatures(feature_set_id="v1_legacy")
    assert sf.feature_set_id == "v1_legacy"

    # Invalid ID
    with pytest.raises(ValidationError) as excinfo:
        SolverFeatures(feature_set_id="invalid_set_xyz")
    assert "Invalid feature_set_id" in str(excinfo.value)
