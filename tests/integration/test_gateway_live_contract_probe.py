from __future__ import annotations

import os

import pytest

from orion.jobs.gateway_contract_probe import run_gateway_contract_probe


@pytest.mark.asyncio
async def test_gateway_live_contract_probe_env_gated() -> None:
    api_key = os.getenv("ORION_GATEWAY_LIVE_API_KEY", "").strip()
    gateway_url = os.getenv("ORION_GATEWAY_LIVE_URL", "http://localhost:8080").strip()

    if not api_key:
        pytest.skip("Set ORION_GATEWAY_LIVE_API_KEY to run live gateway contract probe")

    summary = await run_gateway_contract_probe(
        gateway_url=gateway_url,
        api_key=api_key,
        symbol=os.getenv("ORION_GATEWAY_LIVE_SYMBOL", "AAPL").strip() or "AAPL",
        health_retries=2,
        health_retry_delay_seconds=0.5,
        health_timeout_seconds=3.0,
        receive_timeout_seconds=3.0,
        data_wait_seconds=2.0,
    )

    assert summary["health_ok"] is True
    assert summary["auth_ok"] is True
    assert summary["subscription_ok"] is True
    assert summary["unknown_action_error_code"] == "GW-E3001"
