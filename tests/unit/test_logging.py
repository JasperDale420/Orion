"""Tests for the structlog-based logging configuration."""

import io
import json
import logging

from orion.shared.logger import setup_struct_logger


def test_json_output_structure():
    """Verify setup_struct_logger produces JSON with expected fields."""
    logger = setup_struct_logger("test_json_structure")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    # We want to use the exact same formatter configured by setup_struct_logger
    handler.setFormatter(logging.getLogger().handlers[0].formatter)
    logging.getLogger().addHandler(handler)

    logger.info("Test message", extra_field="extra_value")

    out = stream.getvalue().strip().splitlines()
    assert len(out) >= 1
    data = json.loads(out[-1])

    assert data.get("message") == "Test message" or data.get("event") == "Test message"
    assert data.get("level") == "INFO"
    assert data.get("extra_field") == "extra_value"


def test_json_output_with_exception():
    """Verify exception info is included in output dictionary."""
    logger = setup_struct_logger("test_json_exception")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.getLogger().handlers[0].formatter)
    logging.getLogger().addHandler(handler)

    try:
        raise ValueError("Oops")
    except ValueError:
        logger.exception("Error occurred")

    out = stream.getvalue().strip().splitlines()
    # Filter to only JSON lines (skip traceback printed to stderr)
    json_lines = [line for line in out if line.startswith("{")]
    assert len(json_lines) >= 1
    data = json.loads(json_lines[-1])

    assert data.get("message") == "Error occurred" or data.get("event") == "Error occurred"
    assert "exception" in data or "exc_info" in data
