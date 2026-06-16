"""greek_exposure connector must hit the working /gex aggregate endpoint and use
the latest time-series row.

2026-06-09: the connector hit /api/v1/uw/{ticker}/spot-exposures, whose Gateway
provider deserializes via the vendored UW SDK's SpotGreekExposuresByStrike model
-- a single-row model wrongly applied to a {"data":[...]} wrapper, yielding an
empty .data -> the Gateway returns data:[] -> the connector stored 0 records on
every cycle all day (89/89 zero, 2026-06-09). The aggregate /api/v1/uw/gex/{ticker}
route returns the exact fields the connector needs (call_gamma/put_gamma/
call_vanna/put_vanna/call_charm/put_charm/call_delta/put_delta) as a daily time
series, so the connector must point there and use the MOST RECENT row (the old
list branch summed across strikes; summing across days would be wrong).
"""

from __future__ import annotations

from typing import Any

import pytest

import orion.connectors.base_gateway as base_gw_module
import orion.connectors.uw_greek_exposure_connector as greek_module
from orion.connectors.uw_greek_exposure_connector import UWGreekExposureConnector


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._p = payload
        self.status_code = 200

    def raise_for_status(self) -> None:  # pragma: no cover - 200 path
        return None

    def json(self) -> dict[str, Any]:
        return self._p


def test_fetch_greek_exposure_hits_gex_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = UWGreekExposureConnector(gateway_url="http://gw:8080", gateway_key="k")
    seen: dict[str, str] = {}

    def _fake_get(url: str, *a: Any, **k: Any) -> _Resp:
        seen["url"] = url
        return _Resp({"data": []})

    monkeypatch.setattr(base_gw_module.httpx, "get", _fake_get)

    connector._fetch_greek_exposure("SPY")

    assert seen["url"].endswith("/api/v1/uw/gex/SPY")
    assert "spot-exposures" not in seen["url"]


@pytest.mark.asyncio
async def test_fetch_and_store_uses_latest_timeseries_row(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = UWGreekExposureConnector(gateway_url="http://gw:8080", gateway_key="k")

    # /gex returns one row per day, ascending; the LATEST row must win (not a sum).
    monkeypatch.setattr(
        connector,
        "_fetch_greek_exposure",
        lambda _t: {
            "data": [
                {
                    "timestamp": "2026-06-05T00:00:00",
                    "call_gamma": "1000",
                    "put_gamma": "-100",
                    "call_vanna": "10",
                    "put_vanna": "-1",
                    "call_charm": "5",
                    "put_charm": "-2",
                    "call_delta": "111",
                    "put_delta": "-11",
                },
                {
                    "timestamp": "2026-06-09T00:00:00",
                    "call_gamma": "2000",
                    "put_gamma": "-300",
                    "call_vanna": "20",
                    "put_vanna": "-3",
                    "call_charm": "7",
                    "put_charm": "-4",
                    "call_delta": "222",
                    "put_delta": "-22",
                },
            ]
        },
    )

    async def _fast_sleep(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(greek_module.asyncio, "sleep", _fast_sleep)

    stored = await connector.fetch_and_store(["SPY"])

    assert stored == 1
    rec = connector._latest_exposures[-1]
    # latest row only: 2000 + (-300) = 1700  (NOT 900 + 1700 summed across days)
    assert rec["gex_oi"] == pytest.approx(1700.0)
    assert rec["vex_oi"] == pytest.approx(17.0)
    assert rec["cex_oi"] == pytest.approx(3.0)
    assert rec["call_delta"] == pytest.approx(222.0)
    assert rec["put_delta"] == pytest.approx(-22.0)


@pytest.mark.asyncio
async def test_fetch_and_store_zero_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = UWGreekExposureConnector(gateway_url="http://gw:8080", gateway_key="k")
    monkeypatch.setattr(connector, "_fetch_greek_exposure", lambda _t: {"data": []})

    async def _fast_sleep(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(greek_module.asyncio, "sleep", _fast_sleep)

    stored = await connector.fetch_and_store(["SPY"])
    assert stored == 0
