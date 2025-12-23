from http import HTTPStatus
from typing import Any, Dict, Optional

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...types import Response


def _get_kwargs(
    *,
    newer_than: Optional[str] = None,
    older_than: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if newer_than is not None:
        params["newer_than"] = newer_than
    if older_than is not None:
        params["older_than"] = older_than

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": "/alerts",
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
    newer_than: Optional[str] = None,
    older_than: Optional[str] = None,
) -> Response[Dict[str, Any]]:
    """Alerts

    Returns all the alerts that have been triggered for the user.
    """

    kwargs = _get_kwargs(
        newer_than=newer_than,
        older_than=older_than,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: UnusualWhalesClient,
    newer_than: Optional[str] = None,
    older_than: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Alerts

    Returns all the alerts that have been triggered for the user.
    """

    return sync_detailed(
        client=client,
        newer_than=newer_than,
        older_than=older_than,
    ).parsed


async def asyncio_detailed(
    *,
    client: UnusualWhalesClient,
    newer_than: Optional[str] = None,
    older_than: Optional[str] = None,
) -> Response[Dict[str, Any]]:
    """Alerts

    Returns all the alerts that have been triggered for the user.
    """

    kwargs = _get_kwargs(
        newer_than=newer_than,
        older_than=older_than,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: UnusualWhalesClient,
    newer_than: Optional[str] = None,
    older_than: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Alerts

    Returns all the alerts that have been triggered for the user.
    """

    return (
        await asyncio_detailed(
            client=client,
            newer_than=newer_than,
            older_than=older_than,
        )
    ).parsed
