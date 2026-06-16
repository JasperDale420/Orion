"""Tests for the unified liveness publisher (orion.shared.liveness)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from orion.shared.liveness import publish_liveness
from orion.storage.db import async_session_factory
from orion.storage.models_liveness import ServiceLiveness

pytestmark = pytest.mark.asyncio


async def _fetch(service: str) -> ServiceLiveness | None:
    async with async_session_factory() as session:
        result = await session.execute(select(ServiceLiveness).where(ServiceLiveness.service == service))
        return result.scalar_one_or_none()


async def test_publish_inserts_first_row():
    await publish_liveness("ingestion", cadence_budget_seconds=300)

    row = await _fetch("ingestion")
    assert row is not None
    assert row.service == "ingestion"
    assert row.cycle_count == 1
    assert row.cadence_budget_seconds == 300
    assert row.last_error is None
    assert row.last_success_ts_utc is not None
    # Freshly published — should be within a few seconds of now.
    last = row.last_success_ts_utc
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    assert (datetime.now(UTC) - last) < timedelta(seconds=30)


async def test_publish_upserts_and_increments_cycle_count():
    await publish_liveness("execution", cadence_budget_seconds=300)
    await publish_liveness("execution", cadence_budget_seconds=300)
    await publish_liveness("execution", cadence_budget_seconds=300)

    row = await _fetch("execution")
    assert row is not None
    assert row.cycle_count == 3  # one INSERT + two ON CONFLICT increments


async def test_publish_records_and_clears_last_error():
    # Register with a healthy cycle first: error publishes are UPDATE-only and
    # deliberately no-op for never-registered services (adversarial-review
    # semantics — an error must never advance last_success/cycle_count).
    await publish_liveness("feature_enrichment", cadence_budget_seconds=900)
    row = await _fetch("feature_enrichment")
    assert row is not None
    success_ts = row.last_success_ts_utc

    await publish_liveness("feature_enrichment", cadence_budget_seconds=900, error="connector timeout")
    row = await _fetch("feature_enrichment")
    assert row is not None
    assert row.last_error == "connector timeout"
    # The error did NOT advance the heartbeat.
    assert row.cycle_count == 1
    assert row.last_success_ts_utc == success_ts

    # A subsequent healthy cycle clears the error back to NULL and advances.
    await publish_liveness("feature_enrichment", cadence_budget_seconds=900)
    row = await _fetch("feature_enrichment")
    assert row is not None
    assert row.last_error is None
    assert row.cycle_count == 2


async def test_error_publish_noops_for_unregistered_service():
    await publish_liveness("never_registered", cadence_budget_seconds=300, error="boom")
    row = await _fetch("never_registered")
    assert row is None


async def test_publish_updates_budget_on_conflict():
    await publish_liveness("meta_search", cadence_budget_seconds=100)
    await publish_liveness("meta_search", cadence_budget_seconds=129600)
    row = await _fetch("meta_search")
    assert row is not None
    assert row.cadence_budget_seconds == 129600


async def test_publish_never_raises_on_db_error():
    """A failing session must be swallowed, not propagated into the loop."""

    def _boom():
        raise RuntimeError("db down")

    with patch("orion.shared.liveness.async_session_factory", side_effect=_boom):
        # Must not raise.
        await publish_liveness("ingestion", cadence_budget_seconds=300)

    # And nothing was written.
    assert await _fetch("ingestion") is None


async def test_publish_never_raises_on_unknown_dialect():
    """An unsupported dialect is swallowed (debug-logged), never raised."""
    # Patch the dialect-name lookup path by forcing _build_upsert to reject.
    with patch("orion.shared.liveness._build_upsert", side_effect=ValueError("unsupported dialect")):
        await publish_liveness("ingestion", cadence_budget_seconds=300)
    assert await _fetch("ingestion") is None
