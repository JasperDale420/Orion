from __future__ import annotations

from typing import Any

import pytest
from tenacity import wait_none

import orion.connectors.uw_greek_exposure_connector as greek_module
import orion.connectors.uw_iv_rank_connector as iv_module
import orion.connectors.uw_market_tide_connector as tide_module
import orion.connectors.uw_max_pain_connector as max_pain_module
from orion.connectors.uw_greek_exposure_connector import UWGreekExposureConnector
from orion.connectors.uw_iv_rank_connector import UWIVRankConnector
from orion.connectors.uw_market_tide_connector import UWMarketTideConnector
from orion.connectors.uw_max_pain_connector import UWMaxPainConnector


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {"data": []}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"status={self.status_code}", response=self)

    def json(self) -> dict[str, Any]:
        return self._payload


_FETCH_CASES = [
    (tide_module, UWMarketTideConnector, "_fetch_market_tide", (None,)),
    (greek_module, UWGreekExposureConnector, "_fetch_greek_exposure", ("AAPL",)),
    (max_pain_module, UWMaxPainConnector, "_fetch_max_pain", ("AAPL",)),
    (iv_module, UWIVRankConnector, "_fetch_iv_rank", ("AAPL",)),
]


@pytest.mark.parametrize("module, connector_cls, method_name, call_args", _FETCH_CASES)
def test_fetch_retries_transient_503_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    connector_cls: Any,
    method_name: str,
    call_args: tuple[Any, ...],
) -> None:
    connector = connector_cls(gateway_url="http://gateway:8080", gateway_key="gw-key")
    fetch = getattr(connector, method_name)
    fetch.retry.wait = wait_none()

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    responses = [
        _FakeResponse(503),
        _FakeResponse(200, {"data": [{"ok": True}]}),
    ]

    def _fake_get(*args: Any, **kwargs: Any) -> _FakeResponse:
        calls.append((args, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(module.requests, "get", _fake_get)

    result = fetch(*call_args)

    assert result == {"data": [{"ok": True}]}
    assert len(calls) == 2


@pytest.mark.parametrize("module, connector_cls, method_name, call_args", _FETCH_CASES)
def test_fetch_does_not_retry_non_retryable_404(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    connector_cls: Any,
    method_name: str,
    call_args: tuple[Any, ...],
) -> None:
    connector = connector_cls(gateway_url="http://gateway:8080", gateway_key="gw-key")
    fetch = getattr(connector, method_name)
    fetch.retry.wait = wait_none()

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _fake_get(*args: Any, **kwargs: Any) -> _FakeResponse:
        calls.append((args, kwargs))
        return _FakeResponse(404)

    monkeypatch.setattr(module.requests, "get", _fake_get)

    result = fetch(*call_args)

    assert result is None
    assert len(calls) == 1


_STORE_CASES = [
    (tide_module, UWMarketTideConnector, (None,)),
    (greek_module, UWGreekExposureConnector, (["AAPL"],)),
    (max_pain_module, UWMaxPainConnector, (["AAPL"],)),
    (iv_module, UWIVRankConnector, (["AAPL"],)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("module, connector_cls, call_args", _STORE_CASES)
async def test_fetch_and_store_handles_retry_exhaustion_gracefully(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    connector_cls: Any,
    call_args: tuple[Any, ...],
) -> None:
    connector = connector_cls(gateway_url="http://gateway:8080", gateway_key="gw-key")

    async def _fail_to_thread(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("gateway temporarily unavailable")

    monkeypatch.setattr(module.asyncio, "to_thread", _fail_to_thread)

    stored = await connector.fetch_and_store(*call_args)
    assert stored == 0
