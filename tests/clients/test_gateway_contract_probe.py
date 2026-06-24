from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

import orion.jobs.gateway_contract_probe as probe


@dataclass
class _FakeHttpResponse:
    status_code: int
    payload: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return dict(self.payload)


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient: pops queued responses, records URLs."""

    def __init__(self, responses: list[_FakeHttpResponse], urls: list[str] | None = None) -> None:
        self._responses = responses
        self._urls = urls

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, url: str, timeout: float) -> _FakeHttpResponse:
        if self._urls is not None:
            self._urls.append(url)
        if not self._responses:
            raise AssertionError("No fake HTTP responses remaining")
        return self._responses.pop(0)


def _patch_http(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[_FakeHttpResponse],
    urls: list[str] | None = None,
) -> None:
    client = _FakeAsyncClient(responses, urls)
    monkeypatch.setattr(probe.httpx, "AsyncClient", lambda *a, **k: client)


class _FakeWebSocket:
    def __init__(self, responses: Sequence[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.sent_payloads: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent_payloads.append(json.loads(payload))

    async def recv(self) -> str:
        if not self._responses:
            raise AssertionError("No fake WS responses remaining")
        return json.dumps(self._responses.pop(0))

    async def close(self) -> None:
        self.closed = True


def test_normalize_http_gateway_base_url() -> None:
    assert probe._normalize_http_gateway_base_url("http://localhost:8080") == "http://localhost:8080"
    assert probe._normalize_http_gateway_base_url("http://localhost:8080/api/v1") == "http://localhost:8080"
    assert probe._normalize_http_gateway_base_url("ws://localhost:8080/api/v1") == "http://localhost:8080"
    assert probe._normalize_http_gateway_base_url("wss://gw.example.com") == "https://gw.example.com"


@pytest.mark.asyncio
async def test_gateway_contract_probe_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    health_urls: list[str] = []
    _patch_http(
        monkeypatch,
        [_FakeHttpResponse(status_code=200, payload={"status": "ok"})],
        health_urls,
    )

    fake_ws = _FakeWebSocket(
        [
            {"type": "auth_result", "status": "ok", "client_id": "orion"},
            {
                "type": "subscription_ack",
                "status": "ok",
                "provider": "alpaca",
                "feeds": ["bars"],
                "subscribed": ["AAPL"],
                "failed": [],
            },
            {"type": "error", "error_code": "GW-E3001", "message": "Unknown action"},
            {
                "type": "data",
                "feed": "bars",
                "event_id": "evt-1",
                "symbol": "AAPL",
                "envelope": {"event_id": "evt-1", "instrument_key": "equity:AAPL"},
                "data": {"S": "AAPL", "c": 123.0},
            },
        ]
    )

    async def _fake_connect(*args, **kwargs) -> _FakeWebSocket:
        return fake_ws

    monkeypatch.setattr(probe.websockets, "connect", _fake_connect)

    summary = await probe.run_gateway_contract_probe(
        gateway_url="http://localhost:8080/api/v1",
        api_key="gw-key",
        symbol="AAPL",
        health_retries=1,
        health_retry_delay_seconds=0,
        receive_timeout_seconds=0.2,
        data_wait_seconds=0.2,
    )

    assert summary["health_ok"] is True
    assert summary["auth_ok"] is True
    assert summary["subscription_ok"] is True
    assert summary["unknown_action_error_code"] == "GW-E3001"
    assert summary["data_event_seen"] is True
    assert summary["data_event_schema_ok"] is True
    assert summary["health_attempts"] == 1
    assert health_urls == ["http://localhost:8080/health"]

    actions = [payload.get("action") for payload in fake_ws.sent_payloads]
    assert actions[:3] == ["auth", "subscribe", "__probe_unknown_action__"]
    assert fake_ws.closed is True


@pytest.mark.asyncio
async def test_gateway_contract_probe_retries_health_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_http(
        monkeypatch,
        [
            _FakeHttpResponse(status_code=503, payload={"status": "not_ready"}),
            _FakeHttpResponse(status_code=200, payload={"status": "ok"}),
        ],
    )

    fake_ws = _FakeWebSocket(
        [
            {"type": "auth_result", "status": "ok", "client_id": "orion"},
            {
                "type": "subscription_ack",
                "status": "ok",
                "provider": "alpaca",
                "feeds": ["bars"],
                "subscribed": ["AAPL"],
                "failed": [],
            },
            {"type": "error", "error_code": "GW-E3001", "message": "Unknown action"},
        ]
    )

    async def _fake_connect(*args, **kwargs) -> _FakeWebSocket:
        return fake_ws

    monkeypatch.setattr(probe.websockets, "connect", _fake_connect)

    summary = await probe.run_gateway_contract_probe(
        gateway_url="http://localhost:8080",
        api_key="gw-key",
        symbol="AAPL",
        health_retries=2,
        health_retry_delay_seconds=0,
        receive_timeout_seconds=0.2,
        data_wait_seconds=0,
    )

    assert summary["health_ok"] is True
    assert summary["health_attempts"] == 2
    assert summary["data_event_seen"] is False
    assert summary["data_event_schema_ok"] is False


@pytest.mark.asyncio
async def test_gateway_contract_probe_returns_auth_error_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_http(
        monkeypatch,
        [_FakeHttpResponse(status_code=200, payload={"status": "ok"})],
    )

    fake_ws = _FakeWebSocket(
        [
            {
                "type": "auth_result",
                "status": "error",
                "error_code": "GW-E2001",
                "message": "Invalid API key",
            }
        ]
    )

    async def _fake_connect(*args, **kwargs) -> _FakeWebSocket:
        return fake_ws

    monkeypatch.setattr(probe.websockets, "connect", _fake_connect)

    summary = await probe.run_gateway_contract_probe(
        gateway_url="http://localhost:8080",
        api_key="bad-key",
        symbol="AAPL",
        health_retries=1,
        health_retry_delay_seconds=0,
        receive_timeout_seconds=0.2,
        data_wait_seconds=0,
    )

    assert summary["health_ok"] is True
    assert summary["auth_ok"] is False
    assert summary["auth_error_code"] == "GW-E2001"
    assert summary["subscription_ok"] is False
    assert summary["unknown_action_error_code"] is None
