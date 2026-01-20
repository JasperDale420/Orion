# Bolt's Journal

## 2024-05-22 - [Timestamp Parsing Optimization]

**Learning:** `datetime.fromisoformat()` is significantly faster (~150x) than `dateutil.parser.parse` for standard ISO 8601 strings.
**Action:** Always prefer `fromisoformat()` when the format is known or standard ISO. Fallback to `dateutil` only when necessary.
