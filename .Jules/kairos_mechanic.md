## 2026-02-13 - Data quality market-hours gate uses exchange calendar
**Finding:** Data quality staleness checks used fixed UTC hours, which ignores DST/holiday schedules.
**Risk:** False stale alerts during DST shifts or market holidays; missed alerts when market is open outside the fixed window.
**Fix pattern:** Use `MarketSchedule.is_market_open` with a logged fallback to the legacy UTC-hour gate.
**Next time:** Prefer exchange-calendars helpers for any market-hours logic in monitoring jobs.
