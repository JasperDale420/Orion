"""
Integration test for queue optimization and metrics integration (Refactor Slices 2 & 3).
Tests candidate queue flow from ingestion to execution.
"""
from datetime import UTC, datetime

import pytest
from orion.main_ingest import save_candidates_to_db
from orion.shared.candidate_queue import CandidateQueue
from orion.shared.metrics import Metrics
from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_candidate_queue_and_metrics():
    """
    Test the candidate queue integration:
    1. Create candidates
    2. Save to DB (should push to queue)
    3. Verify queue contains candidate IDs
    4. Verify metrics are tracked (if enabled)
    """
    now = datetime.now(UTC)

    # Create test candidates
    candidates = [
        CandidateTrade(
            candidate_id=f"test_cand_{i}",
            timestamp_utc=now,
            ticker="SPY",
            direction="LONG",
            rule_id="test_rule",
            signal_ids=[f"sig_{i}"],
            model_version="test_v1",
            expected_return=10.0,
            p_take=0.6,
            risk_score=0.3,
            entry_logic={"order_type": "LIMIT", "limit_price": 500.0},
            exit_rules={"stop_loss_pct": 0.02},
            evidence={},
        )
        for i in range(5)
    ]

    # Save candidates (should push to queue)
    await save_candidates_to_db(candidates)

    # Verify queue contains candidate IDs
    queue = await CandidateQueue.get_instance()
    queue_size = queue.qsize()
    assert queue_size == 5, f"Expected 5 candidates in queue, got {queue_size}"

    # Drain queue and verify IDs match
    popped_ids = []
    for _ in range(5):
        cid = await queue.pop(timeout=0.1)
        assert cid is not None
        popped_ids.append(cid)

    expected_ids = {c.candidate_id for c in candidates}
    assert set(popped_ids) == expected_ids

    # Verify queue is empty
    assert queue.qsize() == 0
    empty_pop = await queue.pop(timeout=0.1)
    assert empty_pop is None

    # Test metrics (if enabled)
    try:
        metrics = await Metrics.get_instance()
        # Verify metrics exist (we don't check values as they depend on test order)
        assert hasattr(metrics, "execution_queue_depth")
        assert hasattr(metrics, "risk_equity")
    except ImportError:
        # Metrics not available, skip
        pass


@pytest.mark.asyncio
async def test_queue_backfill():
    """
    Test queue backfill on restart:
    1. Create candidates in DB without queue
    2. Simulate backfill logic
    3. Verify queue is populated
    """
    from orion.execution.service import ExecutionService
    from sqlalchemy import delete

    now = datetime.now(UTC)
    test_candidate_id = "backfill_test_cand"

    # Create unprocessed candidate (no decision)
    candidate = CandidateTrade(
        candidate_id=test_candidate_id,
        timestamp_utc=now,
        ticker="SPY",
        direction="LONG",
        rule_id="test_rule",
        signal_ids=["sig_1"],
        model_version="test_v1",
        expected_return=10.0,
        p_take=0.6,
        risk_score=0.3,
        entry_logic={"order_type": "LIMIT", "limit_price": 500.0},
        exit_rules={"stop_loss_pct": 0.02},
        evidence={},
    )

    try:
        # Save directly to DB (bypass queue push)
        async with async_session_factory() as session:
            session.add(candidate)
            await session.commit()

        # Backfill queue
        service = ExecutionService()
        await service._backfill_queue()

        # Verify queue contains the candidate
        queue = await CandidateQueue.get_instance()
        cid = await queue.pop(timeout=0.1)
        assert cid == test_candidate_id
    finally:
        # M8 remediation: Clean up test data to prevent pollution
        async with async_session_factory() as session:
            await session.execute(delete(CandidateTrade).where(CandidateTrade.candidate_id == test_candidate_id))
            await session.commit()
