from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.flow_per_expiry import FlowPerExpiry
from ...models.side import Side
from ...types import UNSET, Response, Unset


def _get_kwargs(
    id: str,
    *,
    side: Union[Unset, Side] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
    date: Union[Unset, str] = UNSET,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    json_side: Union[Unset, str] = UNSET
    if not isinstance(side, Unset):
        json_side = side.value

    params["side"] = json_side

    params["min_premium"] = min_premium

    params["limit"] = limit

    params["date"] = date

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/option-contract/{id}/flow",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[ErrorMessage, FlowPerExpiry, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = FlowPerExpiry.from_dict(response.json())

        return response_200
    if response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY:
        response_422 = ErrorMessage.from_dict(response.json())

        return response_422
    if response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR:
        response_500 = response.text
        return response_500
    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Response[Union[ErrorMessage, FlowPerExpiry, str]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    id: str,
    *,
    client: UnusualWhalesClient,
    side: Union[Unset, Side] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
    date: Union[Unset, str] = UNSET,
) -> Response[Union[ErrorMessage, FlowPerExpiry, str]]:
    """Get Option Order Flow for a Given Contract

     Option Contract Order Flow

    Args:
        id (str): An option contract in the ISO format. Example: TSLA230526P00167500.
        side (Union[Unset, Side]): The side of a stock trade. Must be one of ASK, BID, MID. If not
            set, will return all side's trades. Example: ASK.
        min_premium (Union[Unset, int]): The minimum premium requested trades should have. Must be
            a positive integer. Example: 50000.
        limit (Union[Unset, int]): How many items to return. If no limit is given, returns all
            matching data. Min: 1. Example: 10.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, FlowPerExpiry, str]]
    """

    kwargs = _get_kwargs(
        id=id,
        side=side,
        min_premium=min_premium,
        limit=limit,
        date=date,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    id: str,
    *,
    client: UnusualWhalesClient,
    side: Union[Unset, Side] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
    date: Union[Unset, str] = UNSET,
) -> Optional[Union[ErrorMessage, FlowPerExpiry, str]]:
    """Get Option Order Flow for a Given Contract

     Option Contract Order Flow

    Args:
        id (str): An option contract in the ISO format. Example: TSLA230526P00167500.
        side (Union[Unset, Side]): The side of a stock trade. Must be one of ASK, BID, MID. If not
            set, will return all side's trades. Example: ASK.
        min_premium (Union[Unset, int]): The minimum premium requested trades should have. Must be
            a positive integer. Example: 50000.
        limit (Union[Unset, int]): How many items to return. If no limit is given, returns all
            matching data. Min: 1. Example: 10.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, FlowPerExpiry, str]
    """

    return sync_detailed(
        id=id,
        client=client,
        side=side,
        min_premium=min_premium,
        limit=limit,
        date=date,
    ).parsed


async def asyncio_detailed(
    id: str,
    *,
    client: UnusualWhalesClient,
    side: Union[Unset, Side] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
    date: Union[Unset, str] = UNSET,
) -> Response[Union[ErrorMessage, FlowPerExpiry, str]]:
    """Get Option Order Flow for a Given Contract

     Option Contract Order Flow

    Args:
        id (str): An option contract in the ISO format. Example: TSLA230526P00167500.
        side (Union[Unset, Side]): The side of a stock trade. Must be one of ASK, BID, MID. If not
            set, will return all side's trades. Example: ASK.
        min_premium (Union[Unset, int]): The minimum premium requested trades should have. Must be
            a positive integer. Example: 50000.
        limit (Union[Unset, int]): How many items to return. If no limit is given, returns all
            matching data. Min: 1. Example: 10.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, FlowPerExpiry, str]]
    """

    kwargs = _get_kwargs(
        id=id,
        side=side,
        min_premium=min_premium,
        limit=limit,
        date=date,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    id: str,
    *,
    client: UnusualWhalesClient,
    side: Union[Unset, Side] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
    date: Union[Unset, str] = UNSET,
) -> Optional[Union[ErrorMessage, FlowPerExpiry, str]]:
    """Get Option Order Flow for a Given Contract

     Option Contract Order Flow

    Args:
        id (str): An option contract in the ISO format. Example: TSLA230526P00167500.
        side (Union[Unset, Side]): The side of a stock trade. Must be one of ASK, BID, MID. If not
            set, will return all side's trades. Example: ASK.
        min_premium (Union[Unset, int]): The minimum premium requested trades should have. Must be
            a positive integer. Example: 50000.
        limit (Union[Unset, int]): How many items to return. If no limit is given, returns all
            matching data. Min: 1. Example: 10.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, FlowPerExpiry, str]
    """

    return (
        await asyncio_detailed(
            id=id,
            client=client,
            side=side,
            min_premium=min_premium,
            limit=limit,
            date=date,
        )
    ).parsed
