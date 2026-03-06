from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.flow_per_expiry_results import FlowPerExpiryResults
from ...models.side import Side
from ...types import UNSET, Response, Unset


def _get_kwargs(
    ticker: str,
    *,
    side: Unset | Side = UNSET,
    min_premium: Unset | int = UNSET,
) -> dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: dict[str, Any] = {}

    json_side: Unset | str = UNSET
    if not isinstance(side, Unset):
        json_side = side.value

    params["side"] = json_side

    params["min_premium"] = min_premium

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": f"/api/stock/{ticker}/flow-recent",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> ErrorMessage | FlowPerExpiryResults | str | None:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = FlowPerExpiryResults.from_dict(response.json())

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
) -> Response[ErrorMessage | FlowPerExpiryResults | str]:
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
    side: Unset | Side = UNSET,
    min_premium: Unset | int = UNSET,
) -> Response[ErrorMessage | FlowPerExpiryResults | str]:
    """Option Order Flow By Date

     Returns the latest flows for the given ticker. Optionally a min premium and a side can be supplied
    in the query for further filtering.

    Args:
        ticker (str): A single ticker Example: AAPL.
        side (Union[Unset, Side]): The side of a stock trade. Must be one of ASK, BID, MID. If not
            set, will return all side's trades. Example: ASK.
        min_premium (Union[Unset, int]): The minimum premium requested trades should have. Must be
            a positive integer. Example: 50000.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, FlowPerExpiryResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        side=side,
        min_premium=min_premium,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    side: Unset | Side = UNSET,
    min_premium: Unset | int = UNSET,
) -> ErrorMessage | FlowPerExpiryResults | str | None:
    """Option Order Flow By Date

     Returns the latest flows for the given ticker. Optionally a min premium and a side can be supplied
    in the query for further filtering.

    Args:
        ticker (str): A single ticker Example: AAPL.
        side (Union[Unset, Side]): The side of a stock trade. Must be one of ASK, BID, MID. If not
            set, will return all side's trades. Example: ASK.
        min_premium (Union[Unset, int]): The minimum premium requested trades should have. Must be
            a positive integer. Example: 50000.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, FlowPerExpiryResults, str]
    """

    return sync_detailed(
        ticker=ticker,
        client=client,
        side=side,
        min_premium=min_premium,
    ).parsed


async def asyncio_detailed(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    side: Unset | Side = UNSET,
    min_premium: Unset | int = UNSET,
) -> Response[ErrorMessage | FlowPerExpiryResults | str]:
    """Option Order Flow By Date

     Returns the latest flows for the given ticker. Optionally a min premium and a side can be supplied
    in the query for further filtering.

    Args:
        ticker (str): A single ticker Example: AAPL.
        side (Union[Unset, Side]): The side of a stock trade. Must be one of ASK, BID, MID. If not
            set, will return all side's trades. Example: ASK.
        min_premium (Union[Unset, int]): The minimum premium requested trades should have. Must be
            a positive integer. Example: 50000.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, FlowPerExpiryResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        side=side,
        min_premium=min_premium,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    side: Unset | Side = UNSET,
    min_premium: Unset | int = UNSET,
) -> ErrorMessage | FlowPerExpiryResults | str | None:
    """Option Order Flow By Date

     Returns the latest flows for the given ticker. Optionally a min premium and a side can be supplied
    in the query for further filtering.

    Args:
        ticker (str): A single ticker Example: AAPL.
        side (Union[Unset, Side]): The side of a stock trade. Must be one of ASK, BID, MID. If not
            set, will return all side's trades. Example: ASK.
        min_premium (Union[Unset, int]): The minimum premium requested trades should have. Must be
            a positive integer. Example: 50000.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, FlowPerExpiryResults, str]
    """

    return (
        await asyncio_detailed(
            ticker=ticker,
            client=client,
            side=side,
            min_premium=min_premium,
        )
    ).parsed
