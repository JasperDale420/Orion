"""Tests for the structlog-based logging configuration."""

import json

from orion.shared.logger import setup_struct_logger


def test_json_output_structure(capsys):
    """Verify setup_struct_logger produces JSON with expected fields."""
    logger = setup_struct_logger("test_json_structure")
    logger.info("Test message", extra_field="extra_value")

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) >= 1
    data = json.loads(out[-1])

    assert data["message"] == "Test message"
    assert data["level"] == "INFO"
    assert data["extra_field"] == "extra_value"
    assert "timestamp" in data


def test_json_output_with_exception(capsys):
    """Verify exception info is included in JSON output."""
    logger = setup_struct_logger("test_json_exception")
    try:
        raise ValueError("Oops")
    except ValueError:
        logger.exception("Error occurred")

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) >= 1
    data = json.loads(out[-1])

    assert data["message"] == "Error occurred"
    assert "exception" in data or "exc_info" in data
