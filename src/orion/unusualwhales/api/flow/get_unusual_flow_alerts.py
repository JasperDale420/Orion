from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.flow_alert_results import FlowAlertResults
from ...types import UNSET, Response, Unset


def _get_kwargs(
    ticker: str,
    *,
    limit: Union[Unset, int] = UNSET,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/stock/{ticker}/flow-alerts",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[ErrorMessage, FlowAlertResults, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = FlowAlertResults.from_dict(response.json())

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
) -> Response[Union[ErrorMessage, FlowAlertResults, str]]:
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
    limit: Union[Unset, int] = UNSET,
) -> Response[Union[ErrorMessage, FlowAlertResults, str]]:
    """Unusual Whales Alerts for a Ticker

     Returns the latest unusual whales flow alerts for the given ticker.

    Args:
        ticker (str): A single ticker Example: AAPL.
        limit (Union[Unset, int]): How many items to return. Default: 100. Max: 200. Min: 1.
            Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, FlowAlertResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    limit: Union[Unset, int] = UNSET,
) -> Optional[Union[ErrorMessage, FlowAlertResults, str]]:
    """Unusual Whales Alerts for a Ticker

     Returns the latest unusual whales flow alerts for the given ticker.

    Args:
        ticker (str): A single ticker Example: AAPL.
        limit (Union[Unset, int]): How many items to return. Default: 100. Max: 200. Min: 1.
            Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, FlowAlertResults, str]
    """

    return sync_detailed(
        ticker=ticker,
        client=client,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    limit: Union[Unset, int] = UNSET,
) -> Response[Union[ErrorMessage, FlowAlertResults, str]]:
    """Unusual Whales Alerts for a Ticker

     Returns the latest unusual whales flow alerts for the given ticker.

    Args:
        ticker (str): A single ticker Example: AAPL.
        limit (Union[Unset, int]): How many items to return. Default: 100. Max: 200. Min: 1.
            Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, FlowAlertResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    limit: Union[Unset, int] = UNSET,
) -> Optional[Union[ErrorMessage, FlowAlertResults, str]]:
    """Unusual Whales Alerts for a Ticker

     Returns the latest unusual whales flow alerts for the given ticker.

    Args:
        ticker (str): A single ticker Example: AAPL.
        limit (Union[Unset, int]): How many items to return. Default: 100. Max: 200. Min: 1.
            Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, FlowAlertResults, str]
    """

    return (
        await asyncio_detailed(
            ticker=ticker,
            client=client,
            limit=limit,
        )
    ).parsed
