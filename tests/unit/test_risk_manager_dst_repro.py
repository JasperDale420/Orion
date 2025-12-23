from datetime import datetime, timezone

from orion.config import RiskSettings
from orion.execution.risk_manager import RiskManager


def test_dst_time_handling():
    """
    Verifies that RiskManager appropriately handles DST.
    Standard EST is UTC-5. EDT is UTC-4.
    The buggy implementation hardcodes UTC-5.

    Test Date: July 10, 2025 (Summer, EDT).
    Market Open: 9:30 AM EDT = 13:30 UTC.

    If we simulate 13:32 UTC (9:32 AM EDT), it IS inside the "First 5 Min" ban (9:30-9:35).

    Buggy Logic: 13:32 - 5h = 8:32. 8:32 is NOT in 9:30-9:35.
    So buggy logic will ALLOW the trade (returning True), failing the ban check.

    Correct Logic should return False (reject trade).
    """
    settings = RiskSettings(time_of_day_bans=["FIRST_5_MIN"])
    rm = RiskManager(config=settings)

    # 9:32 AM EDT = 13:32 UTC
    summer_time = datetime(2025, 7, 10, 13, 32, 0, tzinfo=timezone.utc)

    # Expect False (Trade Rejected due to Ban)
    # If it returns True, the bug is present.
    allowed = rm.check_order("AAPL", 10, 150.0, "buy", timestamp=summer_time)

    assert allowed is False, "RiskManager failed to detect FIRST_5_MIN ban during DST (Summer)."
