# Bolt's Journal

## 2024-05-22 - [Timestamp Parsing Optimization]

**Learning:** `datetime.fromisoformat()` is significantly faster (~150x) than `dateutil.parser.parse` for standard ISO 8601 strings.
**Action:** Always prefer `fromisoformat()` when the format is known or standard ISO. Fallback to `dateutil` only when necessary.

## 2025-02-18 - [ORM Hydration vs Column Selection]
**Learning:** Pydantic V2 `from_attributes=True` handles SQLAlchemy `Row` objects (named tuples) seamlessly. Fetching full ORM objects (e.g., `scalars().all()`) incurs significant overhead and fetches unused large columns (like JSON blobs).
**Action:** Use `select(Model.col1, ...)` instead of `select(Model)` for list endpoints, especially when models have large unused fields (JSON/Text).
