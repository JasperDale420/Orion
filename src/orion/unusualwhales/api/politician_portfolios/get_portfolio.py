from http import HTTPStatus
from typing import Any, Dict, Optional

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...types import Response


def _get_kwargs(
    politician_id: str,
) -> Dict[str, Any]:
    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/politician-portfolios/{politician_id}",
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
    politician_id: str,
    *,
    client: UnusualWhalesClient,
) -> Response[Dict[str, Any]]:
    """Politician Portfolios

    Returns all portfolios and holdings for a politician.
    """

    kwargs = _get_kwargs(
        politician_id=politician_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    politician_id: str,
    *,
    client: UnusualWhalesClient,
) -> Optional[Dict[str, Any]]:
    """Politician Portfolios

    Returns all portfolios and holdings for a politician.
    """

    return sync_detailed(
        politician_id=politician_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    politician_id: str,
    *,
    client: UnusualWhalesClient,
) -> Response[Dict[str, Any]]:
    """Politician Portfolios

    Returns all portfolios and holdings for a politician.
    """

    kwargs = _get_kwargs(
        politician_id=politician_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    politician_id: str,
    *,
    client: UnusualWhalesClient,
) -> Optional[Dict[str, Any]]:
    """Politician Portfolios

    Returns all portfolios and holdings for a politician.
    """

    return (
        await asyncio_detailed(
            politician_id=politician_id,
            client=client,
        )
    ).parsed
