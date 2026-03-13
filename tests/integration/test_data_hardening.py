from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.core.solver_schema import EvaluationTask, SolverConfig
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
@pytest.mark.skip(reason="Obsolete: _fetch_data_for_eval method removed from MetaSearchAgent")
async def test_pagination_logic():
    """
    Verifies that _fetch_data_for_eval correctly paginates when the DB
    returns data in chunks.
    """

    agent = MetaSearchAgent()

    # Mock Objects
    from orion.core.solver_schema import ExitLogic, SolverRiskConfig

    # Mock Objects with V2 Schema
    config = SolverConfig(
        version_id="v1",
        base_strategy_name="TestStrategy",
        entry_logic={"rules": [{"rule_name": "rule1", "params": []}], "combination_method": "AND"},
        exit_logic=ExitLogic(take_profit_atr_multiple=2.0),
        risk=SolverRiskConfig(risk_per_trade_bps=100),
    )

    task = EvaluationTask(
        task_id="test_task", start_time_utc="2025-01-01T00:00:00", end_time_utc="2025-01-02T00:00:00", dataset_tag="val"
    )

    # Create fake batches
    # We set batch_size=2 in the test by monkeypatching?
    # Or rely on the mock returning finite lists and the loop continuing until empty.
    # The code says `while True: ... if not batch: break ... if len(batch) < batch_size: break`
    # Default batch_size is 5000. Testing that specifically is hard without 5000 items.
    # But we can verify the loop works if we force multiple iterations.

    # We will mock the execute() return values.
    # Iteration 1: Returns 5000 items (full batch)
    # Iteration 2: Returns 100 items (partial batch) -> Should Break

    from datetime import datetime

    batch_1 = [
        CandidateTrade(
            candidate_id=f"c_{i}",
            ticker="AAPL",
            timestamp_utc=datetime.utcnow(),
            rule_id="rule1",
            direction="LONG",
            evidence={},
        )
        for i in range(5000)
    ]
    batch_2 = [
        CandidateTrade(
            candidate_id=f"c_{5000 + i}",
            ticker="AAPL",
            timestamp_utc=datetime.utcnow(),
            rule_id="rule1",
            direction="LONG",
            evidence={},
        )
        for i in range(100)
    ]

    # Mock Session
    mock_session = AsyncMock()

    # Mock Result Proxies
    mock_result_1 = MagicMock()
    mock_result_1.scalars.return_value.all.return_value = batch_1

    mock_result_2 = MagicMock()
    mock_result_2.scalars.return_value.all.return_value = batch_2

    # We also need to handle the PRICE query which happens after.
    # It executes ONE query.
    mock_result_price = MagicMock()
    mock_result_price.scalars.return_value.all.return_value = []

    # Side effect for session.execute
    # 1. Candidates Batch 1
    # 2. Candidates Batch 2
    # 3. Price Data Query
    mock_session.execute.side_effect = [mock_result_1, mock_result_2, mock_result_price]

    # Patch the factory to return our mock session
    # Source: orion.agents.meta_search_agent uses: from orion.storage.db import async_session_factory
    with patch("orion.agents.meta_search_agent.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_session

        candidates, prices = await agent._fetch_data_for_eval(config, task)

        assert len(candidates) == 5100, f"Expected 5100 candidates, got {len(candidates)}"

        # Verify call arguments to ensure offset was updated
        assert mock_session.execute.call_count == 3
