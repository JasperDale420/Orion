import pytest

from orion.labeler.schema_guard import SchemaValidationError, resolve_insert_columns


def test_resolve_insert_columns_returns_ordered_columns() -> None:
    row = {
        "event_id": "evt-1",
        "ticker": "AAPL",
        "return_at_1h": 1.2,
    }
    allowed = {"event_id", "ticker", "return_at_1h"}

    columns = resolve_insert_columns(row, allowed, required_columns={"event_id"})

    assert columns == ["event_id", "ticker", "return_at_1h"]


def test_resolve_insert_columns_rejects_unknown_columns() -> None:
    row = {
        "event_id": "evt-1",
        "unexpected_column": "boom",
    }
    allowed = {"event_id"}

    with pytest.raises(SchemaValidationError) as exc:
        resolve_insert_columns(row, allowed, required_columns={"event_id"})

    assert exc.value.unknown_columns == ("unexpected_column",)


def test_resolve_insert_columns_requires_event_id() -> None:
    row = {"ticker": "AAPL"}
    allowed = {"event_id", "ticker"}

    with pytest.raises(SchemaValidationError) as exc:
        resolve_insert_columns(row, allowed, required_columns={"event_id"})

    assert exc.value.missing_columns == ("event_id",)
