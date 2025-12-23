import datetime
from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...types import Response


def _get_kwargs(
    *,
    date: Optional[Union[datetime.date, str]] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if date is not None:
        if isinstance(date, datetime.date):
            params["date"] = date.isoformat()
        else:
            params["date"] = date

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": "/api/politician-portfolios/recent_trades",
        "params": params,
    }

    return _kwargs


def _parse_response(*, client: UnusualWhalesClient, response: httpx.Response) -> Optional[Dict[str, Any]]:
    if response.status_code == HTTPStatus.OK:
        response_200 = response.json()
        return response_200
    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: UnusualWhalesClient, response: httpx.Response) -> Response[Dict[str, Any]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: UnusualWhalesClient,
    date: Optional[Union[datetime.date, str]] = None,
) -> Response[Dict[str, Any]]:
    """Politician Trades

    Returns the latest transacted trades by congress members.
    """

    kwargs = _get_kwargs(
        date=date,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: UnusualWhalesClient,
    date: Optional[Union[datetime.date, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Politician Trades

    Returns the latest transacted trades by congress members.
    """

    return sync_detailed(
        client=client,
        date=date,
    ).parsed


async def asyncio_detailed(
    *,
    client: UnusualWhalesClient,
    date: Optional[Union[datetime.date, str]] = None,
) -> Response[Dict[str, Any]]:
    """Politician Trades

    Returns the latest transacted trades by congress members.
    """

    kwargs = _get_kwargs(
        date=date,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: UnusualWhalesClient,
    date: Optional[Union[datetime.date, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Politician Trades

    Returns the latest transacted trades by congress members.
    """

    return (
        await asyncio_detailed(
            client=client,
            date=date,
        )
    ).parsed
