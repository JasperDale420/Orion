from __future__ import annotations

from datetime import date

import pytest

from orion.jobs import sync_earnings


def test_gateway_headers_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "orion.jobs.sync_earnings.system_settings.data_gateway_api_key",
        "",
        raising=False,
    )
    with pytest.raises(ValueError, match="DATA_GATEWAY_API_KEY/GATEWAY_API_KEY"):
        sync_earnings._gateway_headers()


@pytest.mark.asyncio
async def test_fetch_gateway_earnings_uses_x_gateway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params=None, headers=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _FakeResponse(
                {
                    "success": True,
                    "data": [
                        {
                            "symbol": "AAPL",
                            "date": "2026-02-07",
                            "time": "premarket",
                        }
                    ],
                }
            )

    monkeypatch.setattr(
        "orion.jobs.sync_earnings.system_settings.data_gateway_url",
        "http://gateway:8080",
        raising=False,
    )
    monkeypatch.setattr(
        "orion.jobs.sync_earnings.system_settings.data_gateway_api_key",
        "test-key",
        raising=False,
    )
    monkeypatch.setattr(sync_earnings.httpx, "AsyncClient", _FakeAsyncClient)

    rows = await sync_earnings._fetch_gateway_earnings(
        endpoint="/api/v1/uw/earnings/premarket",
        params={"date": "2026-02-07", "limit": 100},
    )

    assert rows == [{"symbol": "AAPL", "date": "2026-02-07", "time": "premarket"}]
    assert captured["url"] == "http://gateway:8080/api/v1/uw/earnings/premarket"
    assert captured["params"] == {"date": "2026-02-07", "limit": 100}
    assert captured["headers"] == {"X-Gateway-Key": "test-key"}


@pytest.mark.asyncio
async def test_sync_todays_earnings_uses_gateway_row_date(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(endpoint: str, params=None):
        if endpoint.endswith("/premarket"):
            return [
                {
                    "symbol": "MSFT",
                    "date": "2026-01-31",
                    "time": "premarket",
                    "eps_estimate": "1.23",
                    "eps_actual": "1.11",
                }
            ]
        return []

    writes: list[dict[str, object]] = []

    async def _fake_upsert_direct(**kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(sync_earnings, "_fetch_gateway_earnings", _fake_fetch)
    monkeypatch.setattr(sync_earnings, "_upsert_earnings_direct", _fake_upsert_direct)

    result = await sync_earnings.sync_todays_earnings()

    assert result == {"synced": 1, "errors": 0}
    assert writes[0]["ticker"] == "MSFT"
    assert writes[0]["report_date"] == date(2026, 1, 31)
    assert writes[0]["announce_time"] == "premarket"
    assert writes[0]["eps_estimate"] == 1.23
    assert writes[0]["eps_actual"] == 1.11


@pytest.mark.asyncio
async def test_backfill_ticker_earnings_counts_valid_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(endpoint: str, params=None):
        assert endpoint.endswith("/AAPL")
        return [
            {"date": "2026-02-01", "time": "afterhours", "eps_estimate": "0.77"},
            {"date": "", "time": "afterhours"},
        ]

    writes: list[dict[str, object]] = []

    async def _fake_upsert_direct(**kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(sync_earnings, "_fetch_gateway_earnings", _fake_fetch)
    monkeypatch.setattr(sync_earnings, "_upsert_earnings_direct", _fake_upsert_direct)

    count = await sync_earnings.backfill_ticker_earnings("aapl")

    assert count == 1
    assert writes[0]["ticker"] == "AAPL"
    assert writes[0]["report_date"] == date(2026, 2, 1)
    assert writes[0]["announce_time"] == "afterhours"


@pytest.mark.asyncio
async def test_get_earnings_for_ticker_uses_gateway_data_without_local_db(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(endpoint: str, params=None):
        assert endpoint.endswith("/AAPL")
        assert params == {"limit": 100}
        return [
            {"date": "2026-02-20", "time": "afterhours"},
            {"date": "2026-02-01", "time": "premarket"},
            {"date": "bad-date"},
        ]

    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be used")

    monkeypatch.setattr(sync_earnings, "_fetch_gateway_earnings", _fake_fetch)
    monkeypatch.setattr(sync_earnings, "db_query", _fail_db_query, raising=False)

    result = await sync_earnings.get_earnings_for_ticker("aapl", date(2026, 2, 10))

    assert result == {
        "days_to_earnings": 10,
        "is_post_earnings": False,
        "next_earnings_date": date(2026, 2, 20),
        "last_earnings_date": date(2026, 2, 1),
    }


@pytest.mark.asyncio
async def test_upsert_earnings_direct_noops_without_db_write(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail_db_write(_fn):
        raise AssertionError("local db_write should not be used")

    monkeypatch.setattr(sync_earnings, "db_write", _fail_db_write, raising=False)

    await sync_earnings._upsert_earnings_direct(
        ticker="AAPL",
        report_date=date(2026, 2, 11),
        announce_time="afterhours",
        eps_estimate=1.1,
        eps_actual=1.2,
        revenue_estimate=10.0,
        revenue_actual=12.0,
    )


@pytest.mark.asyncio
async def test_backfill_all_earnings_uses_heber_gold_tickers_without_local_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (asof_time, symbols)
            if dataset == "labels_alert_barriers":
                return [
                    {"underlying": "AAPL"},
                    {"underlying": "MSFT"},
                    {"instrument_key": "equity:QQQ"},
                ]
            if dataset == "meta_label_features":
                return [
                    {"symbol": "NVDA"},
                    {"symbol": "AAPL"},
                ]
            raise AssertionError(f"unexpected dataset requested: {dataset}")

    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be used")

    called: list[str] = []

    async def _fake_backfill_ticker_earnings(ticker: str) -> int:
        called.append(ticker)
        return 2

    async def _fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sync_earnings, "get_heber_reader", lambda: _FakeReader())
    monkeypatch.setattr(sync_earnings, "db_query", _fail_db_query, raising=False)
    monkeypatch.setattr(sync_earnings, "backfill_ticker_earnings", _fake_backfill_ticker_earnings)
    monkeypatch.setattr(sync_earnings.asyncio, "sleep", _fake_sleep)

    result = await sync_earnings.backfill_all_earnings()

    assert result["tickers"] == 4
    assert result["earnings"] == 8
    assert result["errors"] == 0
    assert sorted(called) == ["AAPL", "MSFT", "NVDA", "QQQ"]
