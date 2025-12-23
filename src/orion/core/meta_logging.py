from __future__ import annotations

import logging
from typing import Any, Mapping

_SEVERITY_TO_LEVEL: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def log_meta_event(
    logger: logging.Logger,
    *,
    component: str,
    severity: str,
    entity_type: str,
    entity_id: str,
    message: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """
    PRDv2 §6.1: meta-layer JSON logs must include:
      component, severity, entity_type, entity_id, message, metadata
    We emit these as structured `extra` fields so the JSON formatter can include them.
    """
    level = _SEVERITY_TO_LEVEL.get(severity.upper(), logging.INFO)
    logger.log(
        level,
        message,
        extra={
            "component": component,
            "severity": severity.upper(),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "metadata": dict(metadata or {}),
        },
    )
