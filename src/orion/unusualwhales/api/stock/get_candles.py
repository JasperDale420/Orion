from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.candle_data_results import CandleDataResults
from ...models.candle_size import CandleSize
from ...models.error_message import ErrorMessage
from ...types import UNSET, Response, Unset


def _get_kwargs(
    ticker: str,
    candle_size: CandleSize,
    *,
    timeframe: Union[Unset, str] = UNSET,
    trading_day: Union[Unset, str] = UNSET,
    limit: Union[Unset, int] = UNSET,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["timeframe"] = timeframe

    params["trading_day"] = trading_day

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/stock/{ticker}/ohlc/{candle_size}",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[CandleDataResults, ErrorMessage, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = CandleDataResults.from_dict(response.json())

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
) -> Response[Union[CandleDataResults, ErrorMessage, str]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    ticker: str,
    candle_size: CandleSize,
    *,
    client: UnusualWhalesClient,
    timeframe: Union[Unset, str] = UNSET,
    trading_day: Union[Unset, str] = UNSET,
    limit: Union[Unset, int] = UNSET,
) -> Response[Union[CandleDataResults, ErrorMessage, str]]:
    """OHLC

     Returns the Open High Low Close (OHLC) candle data for a given ticker.

    Results are limitted to 2,500 elements even if there are more available.

    Args:
        ticker (str): A single ticker Example: AAPL.
        candle_size (CandleSize): The size of the candles. Example: 5m.
        timeframe (Union[Unset, str]): The timeframe of the data to return. Default 1Y
            Can be one of the following formats:
            - YTD
            - 1D, 2D, etc.
            - 1W, 2W, etc.
            - 1M, 2M, etc.
            - 1Y, 2Y, etc.
             Example: 2M.
        trading_day (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        limit (Union[Unset, int]): How many items to return. Max: 2500. Min: 1. Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[CandleDataResults, ErrorMessage, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        candle_size=candle_size,
        timeframe=timeframe,
        trading_day=trading_day,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ticker: str,
    candle_size: CandleSize,
    *,
    client: UnusualWhalesClient,
    timeframe: Union[Unset, str] = UNSET,
    trading_day: Union[Unset, str] = UNSET,
    limit: Union[Unset, int] = UNSET,
) -> Optional[Union[CandleDataResults, ErrorMessage, str]]:
    """OHLC

     Returns the Open High Low Close (OHLC) candle data for a given ticker.

    Results are limitted to 2,500 elements even if there are more available.

    Args:
        ticker (str): A single ticker Example: AAPL.
        candle_size (CandleSize): The size of the candles. Example: 5m.
        timeframe (Union[Unset, str]): The timeframe of the data to return. Default 1Y
            Can be one of the following formats:
            - YTD
            - 1D, 2D, etc.
            - 1W, 2W, etc.
            - 1M, 2M, etc.
            - 1Y, 2Y, etc.
             Example: 2M.
        trading_day (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        limit (Union[Unset, int]): How many items to return. Max: 2500. Min: 1. Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[CandleDataResults, ErrorMessage, str]
    """

    return sync_detailed(
        ticker=ticker,
        candle_size=candle_size,
        client=client,
        timeframe=timeframe,
        trading_day=trading_day,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    ticker: str,
    candle_size: CandleSize,
    *,
    client: UnusualWhalesClient,
    timeframe: Union[Unset, str] = UNSET,
    trading_day: Union[Unset, str] = UNSET,
    limit: Union[Unset, int] = UNSET,
) -> Response[Union[CandleDataResults, ErrorMessage, str]]:
    """OHLC

     Returns the Open High Low Close (OHLC) candle data for a given ticker.

    Results are limitted to 2,500 elements even if there are more available.

    Args:
        ticker (str): A single ticker Example: AAPL.
        candle_size (CandleSize): The size of the candles. Example: 5m.
        timeframe (Union[Unset, str]): The timeframe of the data to return. Default 1Y
            Can be one of the following formats:
            - YTD
            - 1D, 2D, etc.
            - 1W, 2W, etc.
            - 1M, 2M, etc.
            - 1Y, 2Y, etc.
             Example: 2M.
        trading_day (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        limit (Union[Unset, int]): How many items to return. Max: 2500. Min: 1. Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[CandleDataResults, ErrorMessage, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        candle_size=candle_size,
        timeframe=timeframe,
        trading_day=trading_day,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ticker: str,
    candle_size: CandleSize,
    *,
    client: UnusualWhalesClient,
    timeframe: Union[Unset, str] = UNSET,
    trading_day: Union[Unset, str] = UNSET,
    limit: Union[Unset, int] = UNSET,
) -> Optional[Union[CandleDataResults, ErrorMessage, str]]:
    """OHLC

     Returns the Open High Low Close (OHLC) candle data for a given ticker.

    Results are limitted to 2,500 elements even if there are more available.

    Args:
        ticker (str): A single ticker Example: AAPL.
        candle_size (CandleSize): The size of the candles. Example: 5m.
        timeframe (Union[Unset, str]): The timeframe of the data to return. Default 1Y
            Can be one of the following formats:
            - YTD
            - 1D, 2D, etc.
            - 1W, 2W, etc.
            - 1M, 2M, etc.
            - 1Y, 2Y, etc.
             Example: 2M.
        trading_day (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        limit (Union[Unset, int]): How many items to return. Max: 2500. Min: 1. Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[CandleDataResults, ErrorMessage, str]
    """

    return (
        await asyncio_detailed(
            ticker=ticker,
            candle_size=candle_size,
            client=client,
            timeframe=timeframe,
            trading_day=trading_day,
            limit=limit,
        )
    ).parsed
