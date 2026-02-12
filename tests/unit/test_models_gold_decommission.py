from __future__ import annotations

from orion.storage import models_gold


def test_gold_feature_window_model_is_decommissioned() -> None:
    assert not hasattr(models_gold, "GoldFeatureWindow")
