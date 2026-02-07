import asyncio

import pytest
from orion import main_labeler, main_option_quote_tracker, main_price_target_labeler


def test_option_quote_tracker_specific_gate_overrides_global_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER", "false")

    assert main_option_quote_tracker._legacy_label_pipelines_enabled() is False


def test_flow_labeler_specific_gate_overrides_global_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_FLOW_LABELER", "false")

    assert main_labeler._legacy_label_pipelines_enabled() is False


def test_price_target_labeler_specific_gate_overrides_global_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER", "false")

    assert main_price_target_labeler._legacy_label_pipelines_enabled() is False


@pytest.mark.asyncio
async def test_option_quote_tracker_returns_early_when_specific_gate_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER", "false")
    monkeypatch.setattr(main_option_quote_tracker.signal, "signal", lambda *args, **kwargs: None)

    class _FailIfConstructed:
        def __init__(self) -> None:
            raise AssertionError("connector should not be created when pipeline is disabled")

    monkeypatch.setattr(main_option_quote_tracker, "AlpacaOptionGreeksConnector", _FailIfConstructed)

    await asyncio.wait_for(main_option_quote_tracker.run_quote_tracker(), timeout=0.5)
