"""Pin the ticker-info degradation contract.

The vendored `orion.unusualwhales` client package was deleted from the repo,
so the UW-backed ticker-info lookups in `orion.labeler.feature_extraction`
must degrade to all-None entries without raising. These tests pin that
contract for the two public consumers (`orion.ml.flow_enricher`,
`orion.jobs.backfill_ml_features`).
"""

from datetime import UTC, datetime

import pytest


@pytest.fixture(autouse=True)
def _reset_ticker_info_cache():
    import orion.labeler.feature_extraction as fe

    fe._ticker_info_cache.clear()
    yield
    fe._ticker_info_cache.clear()


@pytest.mark.unit
def test_vendored_unusualwhales_package_is_absent():
    with pytest.raises(ImportError):
        import orion.unusualwhales  # noqa: F401


@pytest.mark.unit
async def test_get_ticker_info_returns_all_none_entry():
    from orion.labeler.feature_extraction import get_ticker_info

    info = await get_ticker_info("AAPL")
    assert info == {
        "sector": None,
        "next_earnings_date": None,
        "announce_time": None,
        "last_earnings_date": None,
    }
    # Second call is served from cache with the identical contract.
    assert await get_ticker_info("AAPL") == info


@pytest.mark.unit
async def test_get_earnings_proximity_returns_none_fields():
    from orion.labeler.feature_extraction import get_earnings_proximity

    result = await get_earnings_proximity("AAPL", datetime(2026, 1, 5, 15, 0, tzinfo=UTC))
    assert result == {"days_to_earnings": None, "is_post_earnings": None}
