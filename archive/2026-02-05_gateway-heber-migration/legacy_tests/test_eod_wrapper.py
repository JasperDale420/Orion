from unittest.mock import AsyncMock, patch

import pytest
from orion.main_ingest import run_eod_task


@pytest.mark.asyncio
async def test_run_eod_task_wrapper():
    with patch("orion.agents.eod_review_agent.EODReviewAgent") as MockAgent:
        mock_agent_instance = AsyncMock()
        MockAgent.return_value = mock_agent_instance

        await run_eod_task()

        # Verify instantiation and run
        assert MockAgent.called
        assert mock_agent_instance.run_review.called
