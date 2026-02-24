## 2026-02-24 - Naive heartbeat timestamps
**Finding:** System monitor silently coerced naive heartbeat timestamps to UTC.
**Risk:** Naive timestamps can hide clock drift and inflate lag calculations without an audit trail.
**Fix pattern:** Emit a structured warning with service key + original timestamp before normalization.
**Next time:** Treat naive timestamps as observability defects; log and track counts.
