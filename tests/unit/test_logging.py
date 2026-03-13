"""Tests for the structlog-based logging configuration."""

import json
import logging

from orion.shared.logger import setup_struct_logger


def _first_json(caplog_records, captured_stdout: str) -> dict:
    """Find the first structlog JSON record from either caplog or stdout.

    structlog routes through stdlib logging, which pytest may capture via
    its log-capture handler (visible in caplog) or via sys.stdout (visible
    via capsys), depending on which other tests have run first and how
    pytest's internal log-capture plugin is configured.  We check both
    locations so the test is robust to test-ordering effects.
    """
    candidates: list[str] = []

    # 1. Check caplog records (pytest log-capture handler)
    for record in caplog_records:
        candidates.append(record.getMessage())

    # 2. Check captured stdout (empire_core._StdoutProxy writes to sys.stdout)
    candidates.extend(captured_stdout.strip().splitlines())

    for msg in candidates:
        msg = msg.strip()
        if msg.startswith("{"):
            try:
                return json.loads(msg)
            except json.JSONDecodeError:
                continue

    raise AssertionError(
        "No JSON log line found in either caplog or captured stdout.\n"
        "caplog records:\n" + "\n".join(repr(r.getMessage()[:120]) for r in caplog_records) + "\n"
        "stdout lines:\n" + "\n".join(repr(line) for line in captured_stdout.strip().splitlines())
    )


def test_json_output_structure(caplog, capsys):
    """Verify setup_struct_logger produces JSON with expected fields."""
    logger = setup_struct_logger("test_json_structure")

    with caplog.at_level(logging.DEBUG):
        logger.info("Test message", extra_field="extra_value")

    data = _first_json(caplog.records, capsys.readouterr().out)

    assert data.get("message") == "Test message" or data.get("event") == "Test message"
    assert data.get("level") == "INFO"
    assert data.get("extra_field") == "extra_value"


def test_json_output_with_exception(caplog, capsys):
    """Verify exception info is included in output dictionary."""
    logger = setup_struct_logger("test_json_exception")

    with caplog.at_level(logging.DEBUG):
        try:
            raise ValueError("Oops")
        except ValueError:
            logger.exception("Error occurred")

    data = _first_json(caplog.records, capsys.readouterr().out)

    assert data.get("message") == "Error occurred" or data.get("event") == "Error occurred"
    assert "exception" in data or "exc_info" in data
