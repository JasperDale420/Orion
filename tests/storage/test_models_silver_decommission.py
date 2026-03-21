from __future__ import annotations

from orion.storage import models_silver


def test_legacy_silver_table_models_are_decommissioned() -> None:
    assert not hasattr(models_silver, "SilverOptionFlow")
    assert not hasattr(models_silver, "SilverDarkPool")
    assert not hasattr(models_silver, "SilverAlpacaBar")
    assert not hasattr(models_silver, "SilverOptionQuote")
