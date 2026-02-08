from __future__ import annotations

from datetime import datetime, timezone

import pytest

import orion.jobs.backfill_ml_features as backfill
import orion.main_price_target_labeler as labeler


@pytest.mark.parametrize(
    "entry_ts,expected_session",
    [
        (datetime(2026, 2, 9, 14, 30, tzinfo=timezone.utc), "OPEN"),
        (datetime(2026, 2, 9, 16, 0, tzinfo=timezone.utc), "MID"),
        (datetime(2026, 2, 9, 19, 0, tzinfo=timezone.utc), "CLOSE"),
    ],
)
def test_backfill_entry_session_matches_live_labeler_contract(
    entry_ts: datetime,
    expected_session: str,
) -> None:
    backfill_features = backfill.get_entry_time_features(entry_ts)
    labeler_features = labeler.get_entry_time_features(entry_ts)

    assert backfill_features == labeler_features
    assert backfill_features["entry_session"] == expected_session
