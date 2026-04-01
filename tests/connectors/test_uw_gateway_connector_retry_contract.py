from __future__ import annotations

from datetime import date
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
            import httpx

            raise httpx.HTTPStatusError(
                f"status={self.status_code}",
                request=httpx.Request("GET", "http://test"),
                response=self,
            )

    def json(self) -> dict[str, Any]:
        return self._payload


_FETCH_CASES = [
    (tide_module, UWMarketTideConnector, "_fetch_market_tide", (None,)),
    (greek_module, UWGreekExposureConnector, "_fetch_greek_exposure", ("AAPL",)),
    (max_pain_module, UWMaxPainConnector, "_fetch_max_pain", ("AAPL",)),
    (iv_module, UWIVRankConnector, "_fetch_iv_rank", ("AAPL",)),
]


import orion.connectors.base_gateway as base_gw_module


@pytest.mark.parametrize(
    "connector_cls",
    [UWMarketTideConnector, UWGreekExposureConnector, UWMaxPainConnector, UWIVRankConnector],
)
def test_connector_requires_gateway_key(connector_cls: Any) -> None:
    with pytest.raises(ValueError, match="DATA_GATEWAY_API_KEY/GATEWAY_API_KEY"):
        connector_cls(gateway_url="http://gateway:8080", gateway_key="")


@pytest.mark.parametrize(
    "connector_cls",
    [UWMarketTideConnector, UWGreekExposureConnector, UWMaxPainConnector, UWIVRankConnector],
)
def test_connector_requires_gateway_url(connector_cls: Any) -> None:
    with pytest.raises(ValueError, match="DATA_GATEWAY_URL/GATEWAY_URL"):
        connector_cls(gateway_url="", gateway_key="gw-key")


@pytest.mark.parametrize("module, connector_cls, method_name, call_args", _FETCH_CASES)
def test_fetch_retries_transient_503_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    connector_cls: Any,
    method_name: str,
    call_args: tuple[Any, ...],
) -> None:
    connector = connector_cls(gateway_url="http://gateway:8080", gateway_key="gw-key")
    # Retry is on the base class _gateway_get, not on individual _fetch_* methods
    connector._gateway_get.retry.wait = wait_none()

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    responses = [
        _FakeResponse(503),
        _FakeResponse(200, {"data": [{"ok": True}]}),
    ]

    def _fake_get(*args: Any, **kwargs: Any) -> _FakeResponse:
        calls.append((args, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(base_gw_module.httpx, "get", _fake_get)

    fetch = getattr(connector, method_name)
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
    connector._gateway_get.retry.wait = wait_none()

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _fake_get(*args: Any, **kwargs: Any) -> _FakeResponse:
        calls.append((args, kwargs))
        return _FakeResponse(404)

    monkeypatch.setattr(base_gw_module.httpx, "get", _fake_get)

    fetch = getattr(connector, method_name)
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


@pytest.mark.asyncio
async def test_market_tide_fetch_and_store_avoids_local_db_write(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = UWMarketTideConnector(gateway_url="http://gateway:8080", gateway_key="gw-key")

    async def _fail_db_write(_fn: Any) -> Any:
        raise AssertionError("local db_write should not be used")

    monkeypatch.setattr(tide_module, "db_write", _fail_db_write, raising=False)
    monkeypatch.setattr(
        connector,
        "_fetch_market_tide",
        lambda _market_date=None: {
            "data": [
                {
                    "timestamp": "2026-02-11T20:00:00Z",
                    "net_call_premium": 100.0,
                    "net_put_premium": 75.0,
                    "net_volume": 42,
                }
            ]
        },
    )

    stored = await connector.fetch_and_store(date(2026, 2, 11))

    assert stored == 1


@pytest.mark.asyncio
async def test_greek_exposure_fetch_and_store_avoids_local_db_write(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = UWGreekExposureConnector(gateway_url="http://gateway:8080", gateway_key="gw-key")

    async def _fail_db_write(_fn: Any) -> Any:
        raise AssertionError("local db_write should not be used")

    monkeypatch.setattr(greek_module, "db_write", _fail_db_write, raising=False)
    monkeypatch.setattr(
        connector,
        "_fetch_greek_exposure",
        lambda _ticker: {
            "data": {
                "call_gamma": 10.0,
                "put_gamma": 5.0,
                "call_vanna": 1.0,
                "put_vanna": 2.0,
                "call_charm": 3.0,
                "put_charm": 4.0,
                "call_delta": 0.25,
                "put_delta": -0.15,
            }
        },
    )

    async def _fast_sleep(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(greek_module.asyncio, "sleep", _fast_sleep)

    stored = await connector.fetch_and_store(["AAPL"])

    assert stored == 1


@pytest.mark.asyncio
async def test_iv_rank_fetch_and_store_avoids_local_db_write(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = UWIVRankConnector(gateway_url="http://gateway:8080", gateway_key="gw-key")

    async def _fail_db_write(_fn: Any) -> Any:
        raise AssertionError("local db_write should not be used")

    monkeypatch.setattr(iv_module, "db_write", _fail_db_write, raising=False)
    monkeypatch.setattr(
        connector,
        "_fetch_iv_rank",
        lambda _ticker: {
            "data": {
                "iv_rank": 55.0,
                "iv_percentile": 62.0,
                "current_iv": 0.38,
                "iv_high": 0.65,
                "iv_low": 0.21,
                "iv_30d": 0.4,
            }
        },
    )

    async def _fast_sleep(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(iv_module.asyncio, "sleep", _fast_sleep)

    stored = await connector.fetch_and_store(["AAPL"])

    assert stored == 1


@pytest.mark.asyncio
async def test_max_pain_fetch_and_store_avoids_local_db_write(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = UWMaxPainConnector(gateway_url="http://gateway:8080", gateway_key="gw-key")

    async def _fail_db_write(_fn: Any) -> Any:
        raise AssertionError("local db_write should not be used")

    monkeypatch.setattr(max_pain_module, "db_write", _fail_db_write, raising=False)
    monkeypatch.setattr(
        connector,
        "_fetch_max_pain",
        lambda _ticker: {
            "data": [
                {
                    "expiry": "2026-03-20",
                    "max_pain": 510,
                    "price": 500,
                }
            ]
        },
    )

    async def _fake_price(_ticker: str) -> float:
        return 500.0

    monkeypatch.setattr(connector, "_get_current_price", _fake_price)

    async def _fast_sleep(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(max_pain_module.asyncio, "sleep", _fast_sleep)

    stored = await connector.fetch_and_store(["AAPL"])

    assert stored == 1
