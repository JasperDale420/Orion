"""Tests for the Discord alerter (orion.shared.alerts)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import orion.shared.alerts as alerts


@pytest.fixture(autouse=True)
def _clear_dedupe_state():
    """Each test starts with a clean dedupe table."""
    alerts._last_sent.clear()
    yield
    alerts._last_sent.clear()


def _mock_client_cm(post_mock):
    """Build an async-context-manager mock whose client.post is post_mock."""
    client = MagicMock()
    client.post = post_mock
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_discord_alert_posts_when_configured():
    post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
    with (
        patch.object(alerts.system_settings, "discord_webhook_url", "https://discord.test/webhook"),
        patch.object(alerts, "create_async_http_client", return_value=_mock_client_cm(post)),
    ):
        result = await alerts.send_discord_alert("ingestion down")

    assert result is True
    post.assert_awaited_once()
    _, kwargs = post.call_args
    assert kwargs["json"]["content"] == "[Orion] ingestion down"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_discord_alert_noop_when_unset():
    post = AsyncMock()
    with (
        patch.object(alerts.system_settings, "discord_webhook_url", None),
        patch.object(alerts, "create_async_http_client", return_value=_mock_client_cm(post)),
    ):
        result = await alerts.send_discord_alert("nothing")

    assert result is False
    post.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_discord_alert_blocks_real_webhook_during_pytest(monkeypatch):
    """Pytest must never post to a real operator Discord webhook by default."""
    post = AsyncMock()
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/shared/test_alerts.py::test")
    monkeypatch.delenv("ORION_ALLOW_DISCORD_IN_TESTS", raising=False)
    with (
        patch.object(alerts.system_settings, "discord_webhook_url", "https://discord.com/api/webhooks/real"),
        patch.object(alerts, "create_async_http_client", return_value=_mock_client_cm(post)),
    ):
        result = await alerts.send_discord_alert("fixture alert")

    assert result is False
    post.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_discord_alert_dedupes_within_window():
    post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
    with (
        patch.object(alerts.system_settings, "discord_webhook_url", "https://discord.test/webhook"),
        patch.object(alerts, "create_async_http_client", return_value=_mock_client_cm(post)),
    ):
        first = await alerts.send_discord_alert("dup", dedupe_key="k1")
        second = await alerts.send_discord_alert("dup", dedupe_key="k1")

    assert first is True
    assert second is False
    assert post.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_discord_alert_dedupe_expires_after_window():
    post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
    with (
        patch.object(alerts.system_settings, "discord_webhook_url", "https://discord.test/webhook"),
        patch.object(alerts, "create_async_http_client", return_value=_mock_client_cm(post)),
    ):
        await alerts.send_discord_alert("dup", dedupe_key="k1")
        # Simulate the window elapsing by aging the recorded send time.
        alerts._last_sent["k1"] -= alerts._DEDUPE_WINDOW_SECONDS + 1
        result = await alerts.send_discord_alert("dup", dedupe_key="k1")

    assert result is True
    assert post.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_discord_alert_never_raises_on_exception():
    post = AsyncMock(side_effect=RuntimeError("network boom"))
    with (
        patch.object(alerts.system_settings, "discord_webhook_url", "https://discord.test/webhook"),
        patch.object(alerts, "create_async_http_client", return_value=_mock_client_cm(post)),
    ):
        result = await alerts.send_discord_alert("boom", dedupe_key="k2")

    assert result is False
    # A failed send must not poison the dedupe table.
    assert "k2" not in alerts._last_sent
