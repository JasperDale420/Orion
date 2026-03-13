import datetime
from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...types import Response


def _get_kwargs(
    ticker: str,
    *,
    date: datetime.date | str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if date is not None:
        if isinstance(date, datetime.date):
            params["date"] = date.isoformat()
        else:
            params["date"] = date

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": f"/api/shorts/{ticker}/ftds",
        "params": params,
    }

    return _kwargs


def _parse_response(*, client: UnusualWhalesClient, response: httpx.Response) -> dict[str, Any] | None:
    if response.status_code == HTTPStatus.OK:
        response_200 = response.json()
        return response_200
    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: UnusualWhalesClient, response: httpx.Response) -> Response[dict[str, Any]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: datetime.date | str | None = None,
) -> Response[dict[str, Any]]:
    """Failures to Deliver

    Returns the short failures to deliver per day for the given ticker starting from the given date.
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        date=date,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: datetime.date | str | None = None,
) -> dict[str, Any] | None:
    """Failures to Deliver

    Returns the short failures to deliver per day for the given ticker starting from the given date.
    """

    return sync_detailed(
        ticker=ticker,
        client=client,
        date=date,
    ).parsed


async def asyncio_detailed(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: datetime.date | str | None = None,
) -> Response[dict[str, Any]]:
    """Failures to Deliver

    Returns the short failures to deliver per day for the given ticker starting from the given date.
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        date=date,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: datetime.date | str | None = None,
) -> dict[str, Any] | None:
    """Failures to Deliver

    Returns the short failures to deliver per day for the given ticker starting from the given date.
    """

    return (
        await asyncio_detailed(
            ticker=ticker,
            client=client,
            date=date,
        )
    ).parsed
