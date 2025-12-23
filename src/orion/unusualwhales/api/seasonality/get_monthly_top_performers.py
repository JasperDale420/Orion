from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.order_direction import OrderDirection
from ...models.seasonality_performance_order_by import SeasonalityPerformanceOrderBy
from ...models.seasonality_performers_results import SeasonalityPerformersResults
from ...models.single_month_number import SingleMonthNumber
from ...types import UNSET, Response, Unset


def _get_kwargs(
    month: SingleMonthNumber,
    *,
    min_years: Union[Unset, int] = UNSET,
    ticker_for_sector: Union[Unset, str] = UNSET,
    s_p_500_nasdaq_only: Union[Unset, bool] = UNSET,
    min_oi: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
    order: Union[Unset, SeasonalityPerformanceOrderBy] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["min_years"] = min_years

    params["ticker_for_sector"] = ticker_for_sector

    params["s_p_500_nasdaq_only"] = s_p_500_nasdaq_only

    params["min_oi"] = min_oi

    params["limit"] = limit

    json_order: Union[Unset, str] = UNSET
    if not isinstance(order, Unset):
        json_order = order.value

    params["order"] = json_order

    json_order_direction: Union[Unset, str] = UNSET
    if not isinstance(order_direction, Unset):
        json_order_direction = order_direction.value

    params["order_direction"] = json_order_direction

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/seasonality/{month}/performers",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[ErrorMessage, SeasonalityPerformersResults, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = SeasonalityPerformersResults.from_dict(response.json())

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
) -> Response[Union[ErrorMessage, SeasonalityPerformersResults, str]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    month: SingleMonthNumber,
    *,
    client: UnusualWhalesClient,
    min_years: Union[Unset, int] = UNSET,
    ticker_for_sector: Union[Unset, str] = UNSET,
    s_p_500_nasdaq_only: Union[Unset, bool] = UNSET,
    min_oi: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
    order: Union[Unset, SeasonalityPerformanceOrderBy] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Response[Union[ErrorMessage, SeasonalityPerformersResults, str]]:
    """Get Top Performers for a Month

     Returns the tickers with the highest performance in terms of price change in the month over the
    years.
    Per default the result is ordered by 'positive_months_perc' descending, then 'median_change'
    descending, then 'marketcap' descending.

    Args:
        month (SingleMonthNumber): A month number indicating the month, e.g. 1 -> January, 2 ->
            February, ... Example: 3.
        min_years (Union[Unset, int]): The minimum amount of years data for the month need to be
            available for the ticker. Default: 10. Min: 1. Example: 3.
        ticker_for_sector (Union[Unset, str]): A single ticker. The result will only contain
            tickers in the same sector as the given ticker, e.g. 'MSFT' will only yield result tickers
            in sector 'Technology'. Example: AAPL.
        s_p_500_nasdaq_only (Union[Unset, bool]): Only return tickers, that are in the S&P 500
            index or the Nasdaq 100 index. Example: True.
        min_oi (Union[Unset, int]): The minimum open interest. Min: 0. Example: 10000.
        limit (Union[Unset, int]): How many items to return. Default: 50. Min: 1. Example: 10.
        order (Union[Unset, SeasonalityPerformanceOrderBy]): Optional columns to order the result
            by Example: ticker.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, SeasonalityPerformersResults, str]]
    """

    kwargs = _get_kwargs(
        month=month,
        min_years=min_years,
        ticker_for_sector=ticker_for_sector,
        s_p_500_nasdaq_only=s_p_500_nasdaq_only,
        min_oi=min_oi,
        limit=limit,
        order=order,
        order_direction=order_direction,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    month: SingleMonthNumber,
    *,
    client: UnusualWhalesClient,
    min_years: Union[Unset, int] = UNSET,
    ticker_for_sector: Union[Unset, str] = UNSET,
    s_p_500_nasdaq_only: Union[Unset, bool] = UNSET,
    min_oi: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
    order: Union[Unset, SeasonalityPerformanceOrderBy] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Optional[Union[ErrorMessage, SeasonalityPerformersResults, str]]:
    """Get Top Performers for a Month

     Returns the tickers with the highest performance in terms of price change in the month over the
    years.
    Per default the result is ordered by 'positive_months_perc' descending, then 'median_change'
    descending, then 'marketcap' descending.

    Args:
        month (SingleMonthNumber): A month number indicating the month, e.g. 1 -> January, 2 ->
            February, ... Example: 3.
        min_years (Union[Unset, int]): The minimum amount of years data for the month need to be
            available for the ticker. Default: 10. Min: 1. Example: 3.
        ticker_for_sector (Union[Unset, str]): A single ticker. The result will only contain
            tickers in the same sector as the given ticker, e.g. 'MSFT' will only yield result tickers
            in sector 'Technology'. Example: AAPL.
        s_p_500_nasdaq_only (Union[Unset, bool]): Only return tickers, that are in the S&P 500
            index or the Nasdaq 100 index. Example: True.
        min_oi (Union[Unset, int]): The minimum open interest. Min: 0. Example: 10000.
        limit (Union[Unset, int]): How many items to return. Default: 50. Min: 1. Example: 10.
        order (Union[Unset, SeasonalityPerformanceOrderBy]): Optional columns to order the result
            by Example: ticker.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, SeasonalityPerformersResults, str]
    """

    return sync_detailed(
        month=month,
        client=client,
        min_years=min_years,
        ticker_for_sector=ticker_for_sector,
        s_p_500_nasdaq_only=s_p_500_nasdaq_only,
        min_oi=min_oi,
        limit=limit,
        order=order,
        order_direction=order_direction,
    ).parsed


async def asyncio_detailed(
    month: SingleMonthNumber,
    *,
    client: UnusualWhalesClient,
    min_years: Union[Unset, int] = UNSET,
    ticker_for_sector: Union[Unset, str] = UNSET,
    s_p_500_nasdaq_only: Union[Unset, bool] = UNSET,
    min_oi: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
    order: Union[Unset, SeasonalityPerformanceOrderBy] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Response[Union[ErrorMessage, SeasonalityPerformersResults, str]]:
    """Get Top Performers for a Month

     Returns the tickers with the highest performance in terms of price change in the month over the
    years.
    Per default the result is ordered by 'positive_months_perc' descending, then 'median_change'
    descending, then 'marketcap' descending.

    Args:
        month (SingleMonthNumber): A month number indicating the month, e.g. 1 -> January, 2 ->
            February, ... Example: 3.
        min_years (Union[Unset, int]): The minimum amount of years data for the month need to be
            available for the ticker. Default: 10. Min: 1. Example: 3.
        ticker_for_sector (Union[Unset, str]): A single ticker. The result will only contain
            tickers in the same sector as the given ticker, e.g. 'MSFT' will only yield result tickers
            in sector 'Technology'. Example: AAPL.
        s_p_500_nasdaq_only (Union[Unset, bool]): Only return tickers, that are in the S&P 500
            index or the Nasdaq 100 index. Example: True.
        min_oi (Union[Unset, int]): The minimum open interest. Min: 0. Example: 10000.
        limit (Union[Unset, int]): How many items to return. Default: 50. Min: 1. Example: 10.
        order (Union[Unset, SeasonalityPerformanceOrderBy]): Optional columns to order the result
            by Example: ticker.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, SeasonalityPerformersResults, str]]
    """

    kwargs = _get_kwargs(
        month=month,
        min_years=min_years,
        ticker_for_sector=ticker_for_sector,
        s_p_500_nasdaq_only=s_p_500_nasdaq_only,
        min_oi=min_oi,
        limit=limit,
        order=order,
        order_direction=order_direction,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    month: SingleMonthNumber,
    *,
    client: UnusualWhalesClient,
    min_years: Union[Unset, int] = UNSET,
    ticker_for_sector: Union[Unset, str] = UNSET,
    s_p_500_nasdaq_only: Union[Unset, bool] = UNSET,
    min_oi: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
    order: Union[Unset, SeasonalityPerformanceOrderBy] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Optional[Union[ErrorMessage, SeasonalityPerformersResults, str]]:
    """Get Top Performers for a Month

     Returns the tickers with the highest performance in terms of price change in the month over the
    years.
    Per default the result is ordered by 'positive_months_perc' descending, then 'median_change'
    descending, then 'marketcap' descending.

    Args:
        month (SingleMonthNumber): A month number indicating the month, e.g. 1 -> January, 2 ->
            February, ... Example: 3.
        min_years (Union[Unset, int]): The minimum amount of years data for the month need to be
            available for the ticker. Default: 10. Min: 1. Example: 3.
        ticker_for_sector (Union[Unset, str]): A single ticker. The result will only contain
            tickers in the same sector as the given ticker, e.g. 'MSFT' will only yield result tickers
            in sector 'Technology'. Example: AAPL.
        s_p_500_nasdaq_only (Union[Unset, bool]): Only return tickers, that are in the S&P 500
            index or the Nasdaq 100 index. Example: True.
        min_oi (Union[Unset, int]): The minimum open interest. Min: 0. Example: 10000.
        limit (Union[Unset, int]): How many items to return. Default: 50. Min: 1. Example: 10.
        order (Union[Unset, SeasonalityPerformanceOrderBy]): Optional columns to order the result
            by Example: ticker.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, SeasonalityPerformersResults, str]
    """

    return (
        await asyncio_detailed(
            month=month,
            client=client,
            min_years=min_years,
            ticker_for_sector=ticker_for_sector,
            s_p_500_nasdaq_only=s_p_500_nasdaq_only,
            min_oi=min_oi,
            limit=limit,
            order=order,
            order_direction=order_direction,
        )
    ).parsed
