"""
Unit tests for CandidateQueue module (Refactor Slice 2).
"""

import pytest
from orion.shared.candidate_queue import CandidateQueue


@pytest.mark.asyncio
async def test_candidate_queue_singleton():
    """Test queue is a singleton."""
    queue1 = await CandidateQueue.get_instance()
    queue2 = await CandidateQueue.get_instance()
    assert queue1 is queue2


@pytest.mark.asyncio
async def test_queue_push_and_pop():
    """Test basic push and pop operations."""
    queue = await CandidateQueue.get_instance()

    # Clear queue first
    while queue.qsize() > 0:
        await queue.pop(timeout=0.1)

    # Push candidates
    await queue.push("cand_1")
    await queue.push("cand_2")
    await queue.push("cand_3")

    assert queue.qsize() == 3

    # Pop in order
    assert await queue.pop(timeout=0.1) == "cand_1"
    assert await queue.pop(timeout=0.1) == "cand_2"
    assert await queue.pop(timeout=0.1) == "cand_3"
    assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_queue_pop_timeout():
    """Test pop timeout when queue is empty."""
    queue = await CandidateQueue.get_instance()

    # Clear queue
    while queue.qsize() > 0:
        await queue.pop(timeout=0.1)

    # Pop from empty queue should return None after timeout
    result = await queue.pop(timeout=0.1)
    assert result is None


@pytest.mark.asyncio
async def test_queue_full_drops_candidates():
    """Test that queue drops candidates when full."""
    # Create fresh instance for this test
    test_queue = CandidateQueue()

    # Fill queue to max (10000)
    for i in range(10000):
        await test_queue.push(f"cand_{i}")

    assert test_queue.qsize() == 10000

    # Try to push more (should be dropped)
    initial_drops = test_queue.dropped_count
    await test_queue.push("overflow_1")
    await test_queue.push("overflow_2")

    # Queue size should still be 10000
    assert test_queue.qsize() == 10000
    # Dropped count should increase
    assert test_queue.dropped_count > initial_drops
