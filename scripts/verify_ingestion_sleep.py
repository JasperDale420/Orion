from datetime import datetime, timedelta

from zoneinfo import ZoneInfo


def verify_sleep_logic():
    eastern = ZoneInfo("America/New_York")
    test_cases = [
        # (Current Time ET description, simulated datetime object)
        ("Weekday 11:00 AM (Active)", datetime(2023, 10, 25, 11, 0, 0, tzinfo=eastern)),
        ("Weekday 03:00 AM (Pre-market - Inactive)", datetime(2023, 10, 25, 3, 0, 0, tzinfo=eastern)),
        ("Weekday 09:00 PM (Post-market - Inactive)", datetime(2023, 10, 25, 21, 0, 0, tzinfo=eastern)),
        ("Saturday 12:00 PM (Weekend - Inactive)", datetime(2023, 10, 28, 12, 0, 0, tzinfo=eastern)),
        ("Sunday 11:00 PM (Weekend - Inactive)", datetime(2023, 10, 29, 23, 0, 0, tzinfo=eastern)),
    ]

    for desc, now_et in test_cases:
        print(f"\n--- Testing: {desc} ---")
        print(f"Current Time (ET): {now_et}")

        is_weekday = now_et.weekday() < 5
        is_active_time = 4 <= now_et.hour < 20

        should_sleep = not (is_weekday and is_active_time)
        print(f"Should Sleep: {should_sleep}")

        if should_sleep:
            next_wake = now_et.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=1)

            # Same logic as implementation
            if is_weekday and now_et.hour < 4:
                next_wake = now_et.replace(hour=4, minute=0, second=0, microsecond=0)

            while next_wake.weekday() >= 5:
                next_wake += timedelta(days=1)

            print(f"Next Wake (ET): {next_wake}")

            # Expected outcomes for manual verification
            if "Weekday 03:00 AM" in desc:
                # Should wake same day at 4am
                assert next_wake.date() == now_et.date()
                assert next_wake.hour == 4
            elif "Weekday 09:00 PM" in desc:
                # Should wake next day at 4am
                assert next_wake.date() == (now_et + timedelta(days=1)).date()
                assert next_wake.hour == 4
            elif "Saturday" in desc:
                # Should wake Monday at 4am
                # Sat is 28th, Mon is 30th
                assert next_wake.day == 30


if __name__ == "__main__":
    verify_sleep_logic()
