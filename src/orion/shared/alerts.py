"""Discord alerting for Orion silent-failure modes.

Posts a simple ``{"content": message}`` payload to a Discord webhook (same
shape as 3Roses' notifier). Designed to be safe to call from anywhere in the
pipeline: it never raises, no-ops cleanly when unconfigured, and de-duplicates
repeated alerts for the same key within a short window so a degraded loop does
not spam the channel every cycle.
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlparse

from empire_core.http_client import create_async_http_client

from orion.config import system_settings
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.alerts")

_ALERT_TIMEOUT_SECONDS = 10.0
_DEDUPE_WINDOW_SECONDS = 15 * 60
_MESSAGE_PREFIX = "[Orion] "
_ALLOW_DISCORD_IN_TESTS_ENV = "ORION_ALLOW_DISCORD_IN_TESTS"

# Module-level dedupe state: dedupe_key -> monotonic timestamp of last send.
_last_sent: dict[str, float] = {}


def _is_duplicate(dedupe_key: str | None) -> bool:
    """Return True when an alert with this key was sent within the window."""
    if dedupe_key is None:
        return False
    now = time.monotonic()
    last = _last_sent.get(dedupe_key)
    if last is not None and (now - last) < _DEDUPE_WINDOW_SECONDS:
        return True
    return False


def _is_safe_test_webhook_url(webhook_url: str) -> bool:
    """Return True for reserved/local webhook hosts used by alert unit tests."""
    hostname = (urlparse(webhook_url).hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".test")


def _blocks_real_webhook_during_pytest(webhook_url: str) -> bool:
    """Protect the operator Discord channel when tests inherit local env."""
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return False
    if os.environ.get(_ALLOW_DISCORD_IN_TESTS_ENV, "").lower() in {"1", "true", "yes"}:
        return False
    return not _is_safe_test_webhook_url(webhook_url)


async def send_discord_alert(message: str, *, dedupe_key: str | None = None) -> bool:
    """Send a Discord alert. Returns True on a successful POST, else False.

    No-op (returns False) when no webhook is configured. Never raises — all
    failures are caught and ERROR-logged. When ``dedupe_key`` is provided,
    repeat sends for the same key within 15 minutes are suppressed.
    """
    webhook_url = system_settings.discord_webhook_url
    if not webhook_url:
        logger.debug("discord_alert_skipped_no_webhook", message=message)
        return False

    if _blocks_real_webhook_during_pytest(str(webhook_url)):
        logger.warning(
            "discord_alert_skipped_pytest_real_webhook",
            dedupe_key=dedupe_key,
            allow_env=_ALLOW_DISCORD_IN_TESTS_ENV,
        )
        return False

    if _is_duplicate(dedupe_key):
        logger.debug("discord_alert_deduped", dedupe_key=dedupe_key)
        return False

    full_message = f"{_MESSAGE_PREFIX}{message}"

    try:
        async with create_async_http_client(timeout=_ALERT_TIMEOUT_SECONDS) as client:
            resp = await client.post(webhook_url, json={"content": full_message})
            resp.raise_for_status()
    except Exception as exc:
        logger.error("discord_alert_failed", error=str(exc), exc_info=True)
        return False

    if dedupe_key is not None:
        _last_sent[dedupe_key] = time.monotonic()
    logger.info("discord_alert_sent", dedupe_key=dedupe_key)
    return True
