"""Import-compatibility smoke tests for the extracted feature-extraction module.

`main_price_target_labeler.py` was archived (2026-06-10). Its live feature
helpers were moved to `orion.labeler.feature_extraction`. These tests pin the
extraction contract:

1. Every public feature function is importable from the new location.
2. `enrich_flow_for_scoring` still lives at `orion.ml.flow_enricher` with an
   unchanged module path — `orion.execution.position_monitor` imports it from
   there and must need zero changes.
3. The backfill job's importers resolve.

Plus two pure-function unit tests on extracted helpers.
"""

from datetime import UTC, datetime

import pytest

# The 21 public feature functions that were extracted and repointed.
EXTRACTED_PUBLIC_FUNCTIONS = [
    "get_darkpool_metrics",
    "get_earnings_proximity",
    "get_entry_time_features",
    "get_flow_aggression",
    "get_flow_greeks",
    "get_gex_at_entry",
    "get_gex_rolling_averages",
    "get_institutional_flow_1w",
    "get_iv_rank_at_entry",
    "get_market_tide_before_entry",
    "get_max_pain_distance",
    "get_p2_features",
    "get_p3_features",
    "get_phase1_bucket_features",
    "get_regime_at_entry",
    "get_rvol_metrics",
    "get_sector_correlation_features",
    "get_ticker_info",
    "get_underlying_price_at_entry",
    "get_underlying_price_at_offset",
    "get_window_features_at_entry",
]


@pytest.mark.unit
def test_all_public_functions_importable_from_new_location():
    import orion.labeler.feature_extraction as fe

    missing = [name for name in EXTRACTED_PUBLIC_FUNCTIONS if not hasattr(fe, name)]
    assert not missing, f"Functions missing from orion.labeler.feature_extraction: {missing}"
    for name in EXTRACTED_PUBLIC_FUNCTIONS:
        assert callable(getattr(fe, name)), f"{name} is not callable"


@pytest.mark.unit
def test_enrich_flow_for_scoring_import_contract():
    # position_monitor imports this exact public API; the path must not change.
    from orion.ml.flow_enricher import enrich_flow_for_scoring

    assert callable(enrich_flow_for_scoring)
    assert enrich_flow_for_scoring.__module__ == "orion.ml.flow_enricher"


@pytest.mark.unit
def test_backfill_importers_resolve():
    # Importing the module proves its repointed top-of-file imports all resolve.
    import orion.jobs.backfill_ml_features as backfill

    assert hasattr(backfill, "get_gex_at_entry")
    assert hasattr(backfill, "get_max_pain_distance")


@pytest.mark.unit
def test_get_entry_time_features_pure():
    from orion.labeler.feature_extraction import get_entry_time_features

    # 2024-01-08 is a Monday; 14:00 UTC (before 15:00) -> OPEN session.
    ts = datetime(2024, 1, 8, 14, 30, tzinfo=UTC)
    feats = get_entry_time_features(ts)
    assert feats == {
        "entry_hour": 14,
        "entry_session": "OPEN",
        "entry_day_of_week": 0,
    }

    # 20:00 UTC (>= 19) -> CLOSE session.
    ts_close = datetime(2024, 1, 12, 20, 0, tzinfo=UTC)  # Friday
    feats_close = get_entry_time_features(ts_close)
    assert feats_close["entry_session"] == "CLOSE"
    assert feats_close["entry_day_of_week"] == 4


@pytest.mark.unit
def test_market_tide_direction_pure():
    from orion.labeler.feature_extraction import _market_tide_direction

    assert _market_tide_direction(100.0) == "BULLISH"
    assert _market_tide_direction(-100.0) == "BEARISH"
    assert _market_tide_direction(0.0) == "NEUTRAL"
