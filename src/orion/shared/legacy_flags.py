"""Shared legacy pipeline control helpers.

Centralizes the env-var check pattern used by all legacy label pipeline services
(price-target labeler, option-quote tracker, quality guardrails, nightly backfill).
"""

from __future__ import annotations

import os

_FALSE_VALUES = {"0", "false", "no", "off", "n"}


def legacy_pipeline_control(specific_key: str) -> tuple[bool, str, str]:
    """Check if a legacy pipeline is enabled via env var.

    Priority:
      1. If *specific_key* is set, honour it directly.
      2. Otherwise, fall back to the global ``ORION_ENABLE_LEGACY_LABEL_PIPELINES``
         flag (default ``"true"``).

    Returns:
        (enabled, resolved_key, raw_value)
    """
    specific_raw = os.getenv(specific_key)
    if specific_raw is not None:
        enabled = specific_raw.lower() not in _FALSE_VALUES
        return enabled, specific_key, specific_raw

    global_key = "ORION_ENABLE_LEGACY_LABEL_PIPELINES"
    global_raw = os.getenv(global_key, "true")
    enabled = global_raw.lower() not in _FALSE_VALUES
    return enabled, global_key, global_raw


def is_legacy_pipeline_enabled(specific_key: str) -> bool:
    """Convenience: return only the boolean for *specific_key*."""
    enabled, _, _ = legacy_pipeline_control(specific_key)
    return enabled
