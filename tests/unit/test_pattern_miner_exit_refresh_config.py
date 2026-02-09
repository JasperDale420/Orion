from __future__ import annotations

import pytest

import orion.ml.pattern_miner as pattern_miner


def test_exit_classifier_schema_refresh_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY", raising=False)
    monkeypatch.delenv("ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH", raising=False)
    monkeypatch.delenv("ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET", raising=False)

    force_refresh, refresh_each_bucket = pattern_miner._exit_classifier_schema_refresh_config_from_env()

    assert force_refresh is False
    assert refresh_each_bucket is False


def test_exit_classifier_schema_refresh_config_disables_invalid_each_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.delenv("ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY", raising=False)
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH", "false")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET", "true")

    def _fake_warning(message: str, *, extra: dict[str, object]) -> None:
        captured["message"] = message
        captured["extra"] = extra

    monkeypatch.setattr(pattern_miner.logger, "warning", _fake_warning, raising=False)

    force_refresh, refresh_each_bucket = pattern_miner._exit_classifier_schema_refresh_config_from_env()

    assert force_refresh is False
    assert refresh_each_bucket is False
    assert captured["extra"] == {
        "event": "exit_training_schema_refresh_config_invalid",
        "force_schema_refresh": False,
        "refresh_each_bucket": True,
    }


def test_exit_classifier_schema_refresh_strategy_per_bucket_overrides_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY", "per_bucket")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH", "false")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET", "false")

    force_refresh, refresh_each_bucket = pattern_miner._exit_classifier_schema_refresh_config_from_env()

    assert force_refresh is True
    assert refresh_each_bucket is True


def test_exit_classifier_schema_refresh_strategy_invalid_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY", "not-a-real-mode")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH", "true")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET", "false")

    def _fake_warning(_message: str, *, extra: dict[str, object]) -> None:
        captured.append(extra)

    monkeypatch.setattr(pattern_miner.logger, "warning", _fake_warning, raising=False)

    force_refresh, refresh_each_bucket = pattern_miner._exit_classifier_schema_refresh_config_from_env()

    assert force_refresh is True
    assert refresh_each_bucket is False
    assert any(item.get("event") == "exit_training_schema_refresh_strategy_invalid" for item in captured)


def test_exit_classifier_schema_refresh_config_details_tracks_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY", "prefetch_once")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH", "false")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET", "true")

    force_refresh, refresh_each_bucket, source = pattern_miner._exit_classifier_schema_refresh_config_details_from_env()

    assert force_refresh is True
    assert refresh_each_bucket is False
    assert source == "strategy_env"


def test_exit_classifier_schema_refresh_mode_labels() -> None:
    assert pattern_miner._exit_classifier_schema_refresh_mode(False, False) == "off"
    assert pattern_miner._exit_classifier_schema_refresh_mode(True, False) == "prefetch_once"
    assert pattern_miner._exit_classifier_schema_refresh_mode(True, True) == "per_bucket"


@pytest.mark.asyncio
async def test_run_all_pattern_mining_passes_exit_refresh_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH", "true")
    monkeypatch.setenv("ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET", "true")
    monkeypatch.delenv("ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY", raising=False)
    monkeypatch.setattr(pattern_miner, "TRADE_BUCKET_CONFIGS", {}, raising=False)
    monkeypatch.setattr(pattern_miner, "TARGETS", {}, raising=False)

    async def _fake_train_all_exit_classifiers(
        force_schema_refresh: bool = False,
        refresh_each_bucket: bool = False,
    ) -> dict[str, object]:
        captured["force_schema_refresh"] = force_schema_refresh
        captured["refresh_each_bucket"] = refresh_each_bucket
        return {}

    import orion.ml.exit_classifier as exit_classifier

    monkeypatch.setattr(
        exit_classifier,
        "train_all_exit_classifiers",
        _fake_train_all_exit_classifiers,
        raising=False,
    )
    captured_refresh_log: dict[str, object] = {}

    def _fake_info(_message: str, *args: object, extra: dict[str, object] | None = None) -> None:
        if extra and extra.get("event") == "exit_training_schema_refresh_config_resolved":
            captured_refresh_log["extra"] = extra

    monkeypatch.setattr(pattern_miner.logger, "info", _fake_info, raising=False)

    summary = await pattern_miner.run_all_pattern_mining()

    assert captured == {
        "force_schema_refresh": True,
        "refresh_each_bucket": True,
    }
    assert captured_refresh_log["extra"] == {
        "event": "exit_training_schema_refresh_config_resolved",
        "refresh_mode": "per_bucket",
        "refresh_source": "legacy_flags",
        "force_schema_refresh": True,
        "refresh_each_bucket": True,
    }
    assert summary.insights == {}
