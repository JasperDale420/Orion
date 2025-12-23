from http import HTTPStatus
from typing import Any, Dict, Optional

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...types import Response


def _get_kwargs(
    *,
    tide_type: Optional[str] = None,
    moneyness: Optional[str] = None,
    expiration: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if tide_type is not None:
        params["tide_type"] = tide_type
    if moneyness is not None:
        params["moneyness"] = moneyness
    if expiration is not None:
        params["expiration"] = expiration

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": "/api/net-flow/expiry",
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
    tide_type: Optional[str] = None,
    moneyness: Optional[str] = None,
    expiration: Optional[str] = None,
) -> Response[Dict[str, Any]]:
    """Net Flow Expiry

    Returns net premium flow by tide_type category, moneyness category, and expiration category.
    """

    kwargs = _get_kwargs(
        tide_type=tide_type,
        moneyness=moneyness,
        expiration=expiration,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: UnusualWhalesClient,
    tide_type: Optional[str] = None,
    moneyness: Optional[str] = None,
    expiration: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Net Flow Expiry

    Returns net premium flow by tide_type category, moneyness category, and expiration category.
    """

    return sync_detailed(
        client=client,
        tide_type=tide_type,
        moneyness=moneyness,
        expiration=expiration,
    ).parsed


async def asyncio_detailed(
    *,
    client: UnusualWhalesClient,
    tide_type: Optional[str] = None,
    moneyness: Optional[str] = None,
    expiration: Optional[str] = None,
) -> Response[Dict[str, Any]]:
    """Net Flow Expiry

    Returns net premium flow by tide_type category, moneyness category, and expiration category.
    """

    kwargs = _get_kwargs(
        tide_type=tide_type,
        moneyness=moneyness,
        expiration=expiration,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: UnusualWhalesClient,
    tide_type: Optional[str] = None,
    moneyness: Optional[str] = None,
    expiration: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Net Flow Expiry

    Returns net premium flow by tide_type category, moneyness category, and expiration category.
    """

    return (
        await asyncio_detailed(
            client=client,
            tide_type=tide_type,
            moneyness=moneyness,
            expiration=expiration,
        )
    ).parsed
