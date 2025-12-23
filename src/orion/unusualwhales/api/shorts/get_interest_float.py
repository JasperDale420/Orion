from http import HTTPStatus
from typing import Any, Dict, Optional

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...types import Response


def _get_kwargs(
    ticker: str,
) -> Dict[str, Any]:
    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/shorts/{ticker}/interest-float",
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
    ticker: str,
    *,
    client: UnusualWhalesClient,
) -> Response[Dict[str, Any]]:
    """Short Interest and Float

    Returns short interest and float data for percentage calculations for a ticker.
    """

    kwargs = _get_kwargs(
        ticker=ticker,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ticker: str,
    *,
    client: UnusualWhalesClient,
) -> Optional[Dict[str, Any]]:
    """Short Interest and Float

    Returns short interest and float data for percentage calculations for a ticker.
    """

    return sync_detailed(
        ticker=ticker,
        client=client,
    ).parsed


async def asyncio_detailed(
    ticker: str,
    *,
    client: UnusualWhalesClient,
) -> Response[Dict[str, Any]]:
    """Short Interest and Float

    Returns short interest and float data for percentage calculations for a ticker.
    """

    kwargs = _get_kwargs(
        ticker=ticker,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ticker: str,
    *,
    client: UnusualWhalesClient,
) -> Optional[Dict[str, Any]]:
    """Short Interest and Float

    Returns short interest and float data for percentage calculations for a ticker.
    """

    return (
        await asyncio_detailed(
            ticker=ticker,
            client=client,
        )
    ).parsed
