from http import HTTPStatus
from typing import Any, Dict, Optional

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...types import Response


def _get_kwargs(
    name: str,
) -> Dict[str, Any]:
    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/institution/{name}/activity",
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
    name: str,
    *,
    client: UnusualWhalesClient,
) -> Response[Dict[str, Any]]:
    """Institutional Activity

    The trading activities for a given institution.
    """

    kwargs = _get_kwargs(
        name=name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    name: str,
    *,
    client: UnusualWhalesClient,
) -> Optional[Dict[str, Any]]:
    """Institutional Activity

    The trading activities for a given institution.
    """

    return sync_detailed(
        name=name,
        client=client,
    ).parsed


async def asyncio_detailed(
    name: str,
    *,
    client: UnusualWhalesClient,
) -> Response[Dict[str, Any]]:
    """Institutional Activity

    The trading activities for a given institution.
    """

    kwargs = _get_kwargs(
        name=name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    name: str,
    *,
    client: UnusualWhalesClient,
) -> Optional[Dict[str, Any]]:
    """Institutional Activity

    The trading activities for a given institution.
    """

    return (
        await asyncio_detailed(
            name=name,
            client=client,
        )
    ).parsed
