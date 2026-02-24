## 2026-02-23 - Alpaca Bar Timestamp Warnings
**Finding:** Alpaca bar normalization silently dropped missing/invalid timestamps without structured logs.
**Risk:** Hard to diagnose missing bar timing issues; silent data gaps can mask ingestion problems.
**Fix pattern:** Emit structured warnings with event_type/source/symbol when timestamp is missing or unparsable.
**Next time:** Add a small log-based test for critical normalizers that accept external timestamps.
