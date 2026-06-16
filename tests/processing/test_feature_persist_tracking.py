"""Tests for tracked background feature persistence (FeatureEngine).

A failing persist must be logged (not silently swallowed by a GC'd task), and
drain() must await outstanding tasks.
"""

import asyncio

import pytest

from orion.processing.feature_engine import FeatureEngine


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failing_persist_is_logged_not_swallowed():
    engine = FeatureEngine()

    async def boom() -> None:
        raise RuntimeError("persist exploded")

    logged: list[str] = []

    # Schedule a failing task through the same tracking machinery.
    task = asyncio.ensure_future(boom())
    engine._persist_tasks.add(task)

    import orion.processing.feature_engine as fe

    orig_error = fe.logger.error

    def capture_error(event, *args, **kwargs):
        logged.append(event)
        return orig_error(event, *args, **kwargs)

    fe.logger.error = capture_error
    try:
        task.add_done_callback(engine._on_persist_done)
        await engine.drain()
    finally:
        fe.logger.error = orig_error

    assert "feature_persist_failed" in logged
    # Task is discarded from the tracking set after completion.
    assert task not in engine._persist_tasks


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drain_awaits_outstanding_tasks():
    engine = FeatureEngine()
    completed = []

    async def slow() -> None:
        await asyncio.sleep(0.01)
        completed.append(True)

    task = asyncio.ensure_future(slow())
    engine._persist_tasks.add(task)
    task.add_done_callback(engine._on_persist_done)

    await engine.drain()

    assert completed == [True]
    assert len(engine._persist_tasks) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drain_noop_when_no_tasks():
    engine = FeatureEngine()
    # Should not raise with an empty set.
    await engine.drain()
