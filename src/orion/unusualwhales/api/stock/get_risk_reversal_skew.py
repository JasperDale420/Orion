from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.historical_risk_reversal_skew_results import HistoricalRiskReversalSkewResults
from ...types import UNSET, Response, Unset


def _get_kwargs(
    ticker: str,
    *,
    date: Union[Unset, str] = UNSET,
    expiry: str,
    timeframe: Union[Unset, str] = UNSET,
    delta: Any,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["date"] = date

    params["expiry"] = expiry

    params["timeframe"] = timeframe

    params["delta"] = delta

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/stock/{ticker}/historical-risk-reversal-skew",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[ErrorMessage, HistoricalRiskReversalSkewResults, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = HistoricalRiskReversalSkewResults.from_dict(response.json())

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
) -> Response[Union[ErrorMessage, HistoricalRiskReversalSkewResults, str]]:
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
    date: Union[Unset, str] = UNSET,
    expiry: str,
    timeframe: Union[Unset, str] = UNSET,
    delta: Any,
) -> Response[Union[ErrorMessage, HistoricalRiskReversalSkewResults, str]]:
    """Historical Risk Reversal Skew by Expiry and Ticker

     Returns the historical risk reversal skew (the difference between put and call volatility) at a
    delta of 0.25 or 0.1 for a given expiry date.

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        expiry (str): A single expiry date in ISO date format. Example: 2024-02-02.
        timeframe (Union[Unset, str]): The timeframe of the data to return. Default 1Y
            Can be one of the following formats:
            - YTD
            - 1D, 2D, etc.
            - 1W, 2W, etc.
            - 1M, 2M, etc.
            - 1Y, 2Y, etc.
             Example: 2M.
        delta (Any): A delta of either 10 or 25.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, HistoricalRiskReversalSkewResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        date=date,
        expiry=expiry,
        timeframe=timeframe,
        delta=delta,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: Union[Unset, str] = UNSET,
    expiry: str,
    timeframe: Union[Unset, str] = UNSET,
    delta: Any,
) -> Optional[Union[ErrorMessage, HistoricalRiskReversalSkewResults, str]]:
    """Historical Risk Reversal Skew by Expiry and Ticker

     Returns the historical risk reversal skew (the difference between put and call volatility) at a
    delta of 0.25 or 0.1 for a given expiry date.

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        expiry (str): A single expiry date in ISO date format. Example: 2024-02-02.
        timeframe (Union[Unset, str]): The timeframe of the data to return. Default 1Y
            Can be one of the following formats:
            - YTD
            - 1D, 2D, etc.
            - 1W, 2W, etc.
            - 1M, 2M, etc.
            - 1Y, 2Y, etc.
             Example: 2M.
        delta (Any): A delta of either 10 or 25.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, HistoricalRiskReversalSkewResults, str]
    """

    return sync_detailed(
        ticker=ticker,
        client=client,
        date=date,
        expiry=expiry,
        timeframe=timeframe,
        delta=delta,
    ).parsed


async def asyncio_detailed(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: Union[Unset, str] = UNSET,
    expiry: str,
    timeframe: Union[Unset, str] = UNSET,
    delta: Any,
) -> Response[Union[ErrorMessage, HistoricalRiskReversalSkewResults, str]]:
    """Historical Risk Reversal Skew by Expiry and Ticker

     Returns the historical risk reversal skew (the difference between put and call volatility) at a
    delta of 0.25 or 0.1 for a given expiry date.

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        expiry (str): A single expiry date in ISO date format. Example: 2024-02-02.
        timeframe (Union[Unset, str]): The timeframe of the data to return. Default 1Y
            Can be one of the following formats:
            - YTD
            - 1D, 2D, etc.
            - 1W, 2W, etc.
            - 1M, 2M, etc.
            - 1Y, 2Y, etc.
             Example: 2M.
        delta (Any): A delta of either 10 or 25.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, HistoricalRiskReversalSkewResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        date=date,
        expiry=expiry,
        timeframe=timeframe,
        delta=delta,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: Union[Unset, str] = UNSET,
    expiry: str,
    timeframe: Union[Unset, str] = UNSET,
    delta: Any,
) -> Optional[Union[ErrorMessage, HistoricalRiskReversalSkewResults, str]]:
    """Historical Risk Reversal Skew by Expiry and Ticker

     Returns the historical risk reversal skew (the difference between put and call volatility) at a
    delta of 0.25 or 0.1 for a given expiry date.

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        expiry (str): A single expiry date in ISO date format. Example: 2024-02-02.
        timeframe (Union[Unset, str]): The timeframe of the data to return. Default 1Y
            Can be one of the following formats:
            - YTD
            - 1D, 2D, etc.
            - 1W, 2W, etc.
            - 1M, 2M, etc.
            - 1Y, 2Y, etc.
             Example: 2M.
        delta (Any): A delta of either 10 or 25.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, HistoricalRiskReversalSkewResults, str]
    """

    return (
        await asyncio_detailed(
            ticker=ticker,
            client=client,
            date=date,
            expiry=expiry,
            timeframe=timeframe,
            delta=delta,
        )
    ).parsed
