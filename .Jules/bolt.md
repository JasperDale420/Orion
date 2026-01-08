## 2024-05-22 - [SQLAlchemy Performance]
**Learning:** Using `scalars().all()` to fetch full ORM objects for read-only endpoints is significantly slower than selecting specific columns due to object hydration overhead.
**Action:** Use `select(Model.col1, ...)` and return `Row` objects or tuples for high-volume read endpoints.
