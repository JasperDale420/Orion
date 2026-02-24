## 2026-02-24 - Alpaca bar timestamp guard
**Finding:** Alpaca bar normalization only parsed ISO strings; unix seconds and invalid timestamps slipped through as `None`.
**Risk:** Bars with missing/invalid timestamps can corrupt ordering, dedupe, and downstream signal windows.
**Fix pattern:** Use strict `parse_timestamptz` in the normalizer, add tests for unix seconds and invalid inputs.
**Next time:** When normalizing provider timestamps, always cover iso + unix seconds/ms and fail fast on invalid values.
