"""
Tests for classify_close_failure (audit #5).

Only an HTTP 4xx is a CONFIRMED rejection (safe to escalate to a native
flatten). Everything else — 5xx, sub-400, missing/None status_code, or any
non-int shape — is AMBIGUOUS and MUST defer (never escalate), to avoid the
double-close / naked-short hole of the reverted-a388337 bug.

bool is an int subclass in Python; a boolean status_code is a shape error and
is treated as ambiguous (decision: unclassifiable → ambiguous, logged).
"""

import pytest

from orion.execution.execution_engine import classify_close_failure


@pytest.mark.parametrize("code", [400, 403, 404, 422, 499])
def test_4xx_is_confirmed_rejection(code):
    assert classify_close_failure({"status_code": code}) == "confirmed_rejection"


def test_429_is_ambiguous_not_confirmed_rejection():
    # A 429 is a transient rate-limit storm, NOT a real broker rejection. The
    # limit may still rest once the storm clears, so escalating to a native
    # (un-attributed) flatten would blind the kill switch. Defer instead.
    assert classify_close_failure({"status_code": 429}) == "ambiguous"


@pytest.mark.parametrize("code", [500, 502, 503, 504, 599])
def test_5xx_is_ambiguous(code):
    assert classify_close_failure({"status_code": code}) == "ambiguous"


@pytest.mark.parametrize("code", [0, 1, 100, 200, 301, 399, -1])
def test_sub_400_is_ambiguous(code):
    assert classify_close_failure({"status_code": code}) == "ambiguous"


def test_none_status_code_is_ambiguous():
    assert classify_close_failure({"status_code": None}) == "ambiguous"


def test_missing_status_code_is_ambiguous():
    # The common transport-error shape: {"error": "..."} with no status_code.
    assert classify_close_failure({"error": "connection reset"}) == "ambiguous"


def test_empty_dict_is_ambiguous():
    assert classify_close_failure({}) == "ambiguous"


@pytest.mark.parametrize("code", ["403", "not a number", "", "4xx"])
def test_str_status_code_is_ambiguous(code):
    assert classify_close_failure({"status_code": code}) == "ambiguous"


@pytest.mark.parametrize("code", [403.0, 0.0, float("nan")])
def test_float_status_code_is_ambiguous(code):
    assert classify_close_failure({"status_code": code}) == "ambiguous"


@pytest.mark.parametrize("code", [True, False])
def test_bool_status_code_is_ambiguous(code):
    # bool is an int subclass, but True/False must NOT masquerade as HTTP 1/0.
    assert classify_close_failure({"status_code": code}) == "ambiguous"


def test_list_status_code_is_ambiguous():
    assert classify_close_failure({"status_code": [404]}) == "ambiguous"


def test_non_int_non_none_shape_logs_warning(caplog):
    """A str/float/list shape logs close_failure_unclassified (surfaces a
    Gateway error-shape drift instead of silently misclassifying)."""
    import logging

    with caplog.at_level(logging.WARNING):
        classify_close_failure({"status_code": "403"})
    assert any("close_failure_unclassified" in r.getMessage() for r in caplog.records)


def test_bool_shape_logs_warning(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        classify_close_failure({"status_code": True})
    assert any("close_failure_unclassified" in r.getMessage() for r in caplog.records)


def test_none_does_not_log_warning(caplog):
    """None/missing is the expected transport-error shape — no warning noise."""
    import logging

    with caplog.at_level(logging.WARNING):
        classify_close_failure({"status_code": None})
    assert not any("close_failure_unclassified" in r.getMessage() for r in caplog.records)
