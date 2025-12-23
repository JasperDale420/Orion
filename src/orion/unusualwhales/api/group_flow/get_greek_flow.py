import datetime
from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...types import Response


def _get_kwargs(
    flow_group: str,
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
        "url": f"/api/group-flow/{flow_group}/greek-flow",
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
    flow_group: str,
    *,
    client: UnusualWhalesClient,
    date: Optional[Union[datetime.date, str]] = None,
) -> Response[Dict[str, Any]]:
    """Greek flow

    Returns the group flow's greek flow (delta & vega flow) for the given market day broken down per minute.
    """

    kwargs = _get_kwargs(
        flow_group=flow_group,
        date=date,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    flow_group: str,
    *,
    client: UnusualWhalesClient,
    date: Optional[Union[datetime.date, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Greek flow

    Returns the group flow's greek flow (delta & vega flow) for the given market day broken down per minute.
    """

    return sync_detailed(
        flow_group=flow_group,
        client=client,
        date=date,
    ).parsed


async def asyncio_detailed(
    flow_group: str,
    *,
    client: UnusualWhalesClient,
    date: Optional[Union[datetime.date, str]] = None,
) -> Response[Dict[str, Any]]:
    """Greek flow

    Returns the group flow's greek flow (delta & vega flow) for the given market day broken down per minute.
    """

    kwargs = _get_kwargs(
        flow_group=flow_group,
        date=date,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    flow_group: str,
    *,
    client: UnusualWhalesClient,
    date: Optional[Union[datetime.date, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Greek flow

    Returns the group flow's greek flow (delta & vega flow) for the given market day broken down per minute.
    """

    return (
        await asyncio_detailed(
            flow_group=flow_group,
            client=client,
            date=date,
        )
    ).parsed
