import asyncio

import pytest

from orion import main_labeler, main_option_quote_tracker, main_pattern_miner, main_price_target_labeler
from orion.jobs import nightly_backfill, quality_guardrails


def test_option_quote_tracker_specific_gate_overrides_global_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER", "false")

    assert main_option_quote_tracker._legacy_label_pipelines_enabled() is False


def test_option_quote_tracker_control_key_prefers_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER", "false")

    enabled, key, raw = main_option_quote_tracker._legacy_label_pipeline_control()

    assert enabled is False
    assert key == "ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER"
    assert raw == "false"


def test_flow_labeler_specific_gate_overrides_global_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_FLOW_LABELER", "false")

    assert main_labeler._legacy_label_pipelines_enabled() is False


def test_flow_labeler_control_key_falls_back_to_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "false")
    monkeypatch.delenv("ORION_ENABLE_LEGACY_FLOW_LABELER", raising=False)

    enabled, key, raw = main_labeler._legacy_label_pipeline_control()

    assert enabled is False
    assert key == "ORION_ENABLE_LEGACY_LABEL_PIPELINES"
    assert raw == "false"


def test_price_target_labeler_specific_gate_overrides_global_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER", "false")

    assert main_price_target_labeler._legacy_label_pipelines_enabled() is False


def test_price_target_labeler_control_key_prefers_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER", "false")

    enabled, key, raw = main_price_target_labeler._legacy_label_pipeline_control()

    assert enabled is False
    assert key == "ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER"
    assert raw == "false"


def test_pattern_miner_specific_gate_overrides_global_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_PATTERN_MINER", "false")

    assert main_pattern_miner._legacy_label_pipelines_enabled() is False


def test_pattern_miner_control_key_prefers_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_PATTERN_MINER", "false")

    enabled, key, raw = main_pattern_miner._legacy_label_pipeline_control()

    assert enabled is False
    assert key == "ORION_ENABLE_LEGACY_PATTERN_MINER"
    assert raw == "false"


def test_nightly_backfill_specific_gate_overrides_global_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_NIGHTLY_BACKFILL", "false")

    assert nightly_backfill._legacy_label_pipelines_enabled() is False


def test_quality_guardrails_specific_gate_overrides_global_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_QUALITY_GUARDRAILS", "false")

    assert quality_guardrails._legacy_label_pipelines_enabled() is False


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


@pytest.mark.asyncio
async def test_flow_labeler_does_not_init_db_when_specific_gate_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_FLOW_LABELER", "false")

    async def _fail_init_db() -> None:
        raise AssertionError("init_db should not be called when flow labeler is disabled")

    monkeypatch.setattr(main_labeler, "init_db", _fail_init_db)

    await asyncio.wait_for(main_labeler.run_labeling_loop(asyncio.Event()), timeout=0.5)


@pytest.mark.asyncio
async def test_price_target_labeler_does_not_init_db_when_specific_gate_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER", "false")

    async def _fail_init_db() -> None:
        raise AssertionError("init_db should not be called when price-target labeler is disabled")

    monkeypatch.setattr(main_price_target_labeler, "init_db", _fail_init_db)

    await asyncio.wait_for(main_price_target_labeler.run_labeling_loop(asyncio.Event()), timeout=0.5)


@pytest.mark.asyncio
async def test_pattern_miner_does_not_init_db_when_specific_gate_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_PATTERN_MINER", "false")

    async def _fail_init_db() -> None:
        raise AssertionError("init_db should not be called when pattern miner is disabled")

    monkeypatch.setattr(main_pattern_miner, "init_db", _fail_init_db)

    await asyncio.wait_for(main_pattern_miner.run_mining_job(), timeout=0.5)


@pytest.mark.asyncio
async def test_nightly_backfill_does_not_init_db_when_specific_gate_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_NIGHTLY_BACKFILL", "false")

    async def _fail_init_db() -> None:
        raise AssertionError("init_db should not be called when nightly backfill is disabled")

    monkeypatch.setattr(nightly_backfill, "init_db", _fail_init_db)

    await asyncio.wait_for(nightly_backfill.main(), timeout=0.5)


@pytest.mark.asyncio
async def test_quality_guardrails_does_not_init_db_when_specific_gate_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_ENABLE_LEGACY_LABEL_PIPELINES", "true")
    monkeypatch.setenv("ORION_ENABLE_LEGACY_QUALITY_GUARDRAILS", "false")

    async def _fail_init_db() -> None:
        raise AssertionError("init_db should not be called when quality guardrails is disabled")

    monkeypatch.setattr(quality_guardrails, "init_db", _fail_init_db)

    await asyncio.wait_for(quality_guardrails.run_guardrail_loop(), timeout=0.5)
