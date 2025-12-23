from http import HTTPStatus
from typing import Any, Dict, List, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.order_direction import OrderDirection
from ...models.screener_order_by_field import ScreenerOrderByField
from ...models.single_issue_type import SingleIssueType
from ...models.single_sector import SingleSector
from ...models.stock_screener_response import StockScreenerResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    ticker: Union[Unset, str] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    min_change: Union[Unset, int] = UNSET,
    max_change: Union[Unset, int] = UNSET,
    min_underlying_price: Union[Unset, int] = UNSET,
    max_underlying_price: Union[Unset, int] = UNSET,
    is_s_p_500: Union[Unset, bool] = UNSET,
    has_dividends: Union[Unset, bool] = UNSET,
    sectors: Union[Unset, List[SingleSector]] = UNSET,
    min_marketcap: Union[Unset, int] = UNSET,
    max_marketcap: Union[Unset, int] = UNSET,
    min_perc_3_day_total: Union[Unset, int] = UNSET,
    max_perc_3_day_total: Union[Unset, int] = UNSET,
    min_perc_3_day_call: Union[Unset, int] = UNSET,
    max_perc_3_day_call: Union[Unset, int] = UNSET,
    min_perc_3_day_put: Union[Unset, int] = UNSET,
    max_perc_3_day_put: Union[Unset, int] = UNSET,
    min_perc_30_day_total: Union[Unset, int] = UNSET,
    max_perc_30_day_total: Union[Unset, int] = UNSET,
    min_perc_30_day_call: Union[Unset, int] = UNSET,
    max_perc_30_day_call: Union[Unset, int] = UNSET,
    min_perc_30_day_put: Union[Unset, int] = UNSET,
    max_perc_30_day_put: Union[Unset, int] = UNSET,
    min_total_oi_change_perc: Union[Unset, int] = UNSET,
    max_total_oi_change_perc: Union[Unset, int] = UNSET,
    min_call_oi_change_perc: Union[Unset, int] = UNSET,
    max_call_oi_change_perc: Union[Unset, int] = UNSET,
    min_put_oi_change_perc: Union[Unset, int] = UNSET,
    max_put_oi_change_perc: Union[Unset, int] = UNSET,
    min_implied_move: Union[Unset, int] = UNSET,
    max_implied_move: Union[Unset, int] = UNSET,
    min_implied_move_perc: Union[Unset, int] = UNSET,
    max_implied_move_perc: Union[Unset, int] = UNSET,
    min_volatility: Union[Unset, int] = UNSET,
    max_volatility: Union[Unset, int] = UNSET,
    min_iv_rank: Union[Unset, int] = UNSET,
    max_iv_rank: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_call_volume: Union[Unset, int] = UNSET,
    max_call_volume: Union[Unset, int] = UNSET,
    min_put_volume: Union[Unset, int] = UNSET,
    max_put_volume: Union[Unset, int] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_call_premium: Union[Unset, int] = UNSET,
    max_call_premium: Union[Unset, int] = UNSET,
    min_put_premium: Union[Unset, int] = UNSET,
    max_put_premium: Union[Unset, int] = UNSET,
    min_net_premium: Union[Unset, int] = UNSET,
    max_net_premium: Union[Unset, int] = UNSET,
    min_oi: Union[Unset, int] = UNSET,
    max_oi: Union[Unset, int] = UNSET,
    min_oi_vs_vol: Union[Unset, int] = UNSET,
    max_oi_vs_vol: Union[Unset, int] = UNSET,
    min_put_call_ratio: Union[Unset, int] = UNSET,
    max_put_call_ratio: Union[Unset, int] = UNSET,
    order: Union[Unset, ScreenerOrderByField] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["ticker"] = ticker

    json_issue_types: Union[Unset, List[str]] = UNSET
    if not isinstance(issue_types, Unset):
        json_issue_types = []
        for componentsschemas_issue_types_item_data in issue_types:
            componentsschemas_issue_types_item = componentsschemas_issue_types_item_data.value
            json_issue_types.append(componentsschemas_issue_types_item)

    params["issue_types[]"] = json_issue_types

    params["min_change"] = min_change

    params["max_change"] = max_change

    params["min_underlying_price"] = min_underlying_price

    params["max_underlying_price"] = max_underlying_price

    params["is_s_p_500"] = is_s_p_500

    params["has_dividends"] = has_dividends

    json_sectors: Union[Unset, List[str]] = UNSET
    if not isinstance(sectors, Unset):
        json_sectors = []
        for componentsschemas_sectors_item_data in sectors:
            componentsschemas_sectors_item = componentsschemas_sectors_item_data.value
            json_sectors.append(componentsschemas_sectors_item)

    params["sectors[]"] = json_sectors

    params["min_marketcap"] = min_marketcap

    params["max_marketcap"] = max_marketcap

    params["min_perc_3_day_total"] = min_perc_3_day_total

    params["max_perc_3_day_total"] = max_perc_3_day_total

    params["min_perc_3_day_call"] = min_perc_3_day_call

    params["max_perc_3_day_call"] = max_perc_3_day_call

    params["min_perc_3_day_put"] = min_perc_3_day_put

    params["max_perc_3_day_put"] = max_perc_3_day_put

    params["min_perc_30_day_total"] = min_perc_30_day_total

    params["max_perc_30_day_total"] = max_perc_30_day_total

    params["min_perc_30_day_call"] = min_perc_30_day_call

    params["max_perc_30_day_call"] = max_perc_30_day_call

    params["min_perc_30_day_put"] = min_perc_30_day_put

    params["max_perc_30_day_put"] = max_perc_30_day_put

    params["min_total_oi_change_perc"] = min_total_oi_change_perc

    params["max_total_oi_change_perc"] = max_total_oi_change_perc

    params["min_call_oi_change_perc"] = min_call_oi_change_perc

    params["max_call_oi_change_perc"] = max_call_oi_change_perc

    params["min_put_oi_change_perc"] = min_put_oi_change_perc

    params["max_put_oi_change_perc"] = max_put_oi_change_perc

    params["min_implied_move"] = min_implied_move

    params["max_implied_move"] = max_implied_move

    params["min_implied_move_perc"] = min_implied_move_perc

    params["max_implied_move_perc"] = max_implied_move_perc

    params["min_volatility"] = min_volatility

    params["max_volatility"] = max_volatility

    params["min_iv_rank"] = min_iv_rank

    params["max_iv_rank"] = max_iv_rank

    params["min_volume"] = min_volume

    params["max_volume"] = max_volume

    params["min_call_volume"] = min_call_volume

    params["max_call_volume"] = max_call_volume

    params["min_put_volume"] = min_put_volume

    params["max_put_volume"] = max_put_volume

    params["min_premium"] = min_premium

    params["max_premium"] = max_premium

    params["min_call_premium"] = min_call_premium

    params["max_call_premium"] = max_call_premium

    params["min_put_premium"] = min_put_premium

    params["max_put_premium"] = max_put_premium

    params["min_net_premium"] = min_net_premium

    params["max_net_premium"] = max_net_premium

    params["min_oi"] = min_oi

    params["max_oi"] = max_oi

    params["min_oi_vs_vol"] = min_oi_vs_vol

    params["max_oi_vs_vol"] = max_oi_vs_vol

    params["min_put_call_ratio"] = min_put_call_ratio

    params["max_put_call_ratio"] = max_put_call_ratio

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
        "url": "/api/screener/stocks",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[ErrorMessage, StockScreenerResponse, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = StockScreenerResponse.from_dict(response.json())

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
) -> Response[Union[ErrorMessage, StockScreenerResponse, str]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: UnusualWhalesClient,
    ticker: Union[Unset, str] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    min_change: Union[Unset, int] = UNSET,
    max_change: Union[Unset, int] = UNSET,
    min_underlying_price: Union[Unset, int] = UNSET,
    max_underlying_price: Union[Unset, int] = UNSET,
    is_s_p_500: Union[Unset, bool] = UNSET,
    has_dividends: Union[Unset, bool] = UNSET,
    sectors: Union[Unset, List[SingleSector]] = UNSET,
    min_marketcap: Union[Unset, int] = UNSET,
    max_marketcap: Union[Unset, int] = UNSET,
    min_perc_3_day_total: Union[Unset, int] = UNSET,
    max_perc_3_day_total: Union[Unset, int] = UNSET,
    min_perc_3_day_call: Union[Unset, int] = UNSET,
    max_perc_3_day_call: Union[Unset, int] = UNSET,
    min_perc_3_day_put: Union[Unset, int] = UNSET,
    max_perc_3_day_put: Union[Unset, int] = UNSET,
    min_perc_30_day_total: Union[Unset, int] = UNSET,
    max_perc_30_day_total: Union[Unset, int] = UNSET,
    min_perc_30_day_call: Union[Unset, int] = UNSET,
    max_perc_30_day_call: Union[Unset, int] = UNSET,
    min_perc_30_day_put: Union[Unset, int] = UNSET,
    max_perc_30_day_put: Union[Unset, int] = UNSET,
    min_total_oi_change_perc: Union[Unset, int] = UNSET,
    max_total_oi_change_perc: Union[Unset, int] = UNSET,
    min_call_oi_change_perc: Union[Unset, int] = UNSET,
    max_call_oi_change_perc: Union[Unset, int] = UNSET,
    min_put_oi_change_perc: Union[Unset, int] = UNSET,
    max_put_oi_change_perc: Union[Unset, int] = UNSET,
    min_implied_move: Union[Unset, int] = UNSET,
    max_implied_move: Union[Unset, int] = UNSET,
    min_implied_move_perc: Union[Unset, int] = UNSET,
    max_implied_move_perc: Union[Unset, int] = UNSET,
    min_volatility: Union[Unset, int] = UNSET,
    max_volatility: Union[Unset, int] = UNSET,
    min_iv_rank: Union[Unset, int] = UNSET,
    max_iv_rank: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_call_volume: Union[Unset, int] = UNSET,
    max_call_volume: Union[Unset, int] = UNSET,
    min_put_volume: Union[Unset, int] = UNSET,
    max_put_volume: Union[Unset, int] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_call_premium: Union[Unset, int] = UNSET,
    max_call_premium: Union[Unset, int] = UNSET,
    min_put_premium: Union[Unset, int] = UNSET,
    max_put_premium: Union[Unset, int] = UNSET,
    min_net_premium: Union[Unset, int] = UNSET,
    max_net_premium: Union[Unset, int] = UNSET,
    min_oi: Union[Unset, int] = UNSET,
    max_oi: Union[Unset, int] = UNSET,
    min_oi_vs_vol: Union[Unset, int] = UNSET,
    max_oi_vs_vol: Union[Unset, int] = UNSET,
    min_put_call_ratio: Union[Unset, int] = UNSET,
    max_put_call_ratio: Union[Unset, int] = UNSET,
    order: Union[Unset, ScreenerOrderByField] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Response[Union[ErrorMessage, StockScreenerResponse, str]]:
    """Stock Screener

     A stock screener endpoint to screen the market for stocks by a variety of filter options.

    For an example of what can be build with this endpoint check out the [Stock
    Screener](https://unusualwhales.com/flow/ticker_flows)
    on unusualwhales.com

    Args:
        ticker (Union[Unset, str]): A comma separated list of tickers. To exclude certain tickers
            prefix the first ticker with a `-`. Example: AAPL,INTC.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        min_change (Union[Unset, int]): The minimum % change to the previous Trading Day. Min: -1.
            Example: -0.45.
        max_change (Union[Unset, int]): The maximum % change to the previous Trading Day. Min: -1.
            Example: 0.2.
        min_underlying_price (Union[Unset, int]): The minimum stock price. Min: 0. Example: 5.23.
        max_underlying_price (Union[Unset, int]): The maximum stock price. Min: 0. Example: 10.53.
        is_s_p_500 (Union[Unset, bool]): Boolean whether to only include stocks which are part of
            the S&P 500. Setting this to false has no effect. Example: True.
        has_dividends (Union[Unset, bool]): Boolean wheter to only include stocks which pay
            dividends. Setting this to false has no effect. Example: True.
        sectors (Union[Unset, List[SingleSector]]): An array of 1 or more sectors. Example:
            ['Consumer Cyclical', 'Technology', 'Utilities'].
        min_marketcap (Union[Unset, int]): The minimum marketcap. Min: 0. Example: 1000000.
        max_marketcap (Union[Unset, int]): The maximum marketcap. Min: 0. Example: 250000000.
        min_perc_3_day_total (Union[Unset, int]): The minimum ratio of options volume vs 3 day avg
            options volume. Min: 0. Example: 0.25.
        max_perc_3_day_total (Union[Unset, int]): The maximum ratio of options volume vs 3 day avg
            options volume. Min: 0. Example: 1.72.
        min_perc_3_day_call (Union[Unset, int]): The minimum ratio of call options volume vs 3 day
            avg call options volume. Min: 0. Example: 0.25.
        max_perc_3_day_call (Union[Unset, int]): The maximum ratio of call options volume vs 3 day
            avg call options volume. Min: 0. Example: 1.72.
        min_perc_3_day_put (Union[Unset, int]): The minimum ratio of put options volume vs 3 day
            avg put options volume. Min: 0. Example: 0.25.
        max_perc_3_day_put (Union[Unset, int]): The maximum ratio of put options volume vs 3 day
            avg put options volume. Min: 0. Example: 1.72.
        min_perc_30_day_total (Union[Unset, int]): The minimum ratio of options volume vs 30 day
            avg options volume. Min: 0. Example: 0.25.
        max_perc_30_day_total (Union[Unset, int]): The maximum ratio of options volume vs 30 day
            avg options volume. Min: 0. Example: 1.72.
        min_perc_30_day_call (Union[Unset, int]): The minimum ratio of call options volume vs 30
            day avg call options volume. Min: 0. Example: 0.25.
        max_perc_30_day_call (Union[Unset, int]): The maximum ratio of call options volume vs 30
            day avg call options volume. Min: 0. Example: 1.72.
        min_perc_30_day_put (Union[Unset, int]): The minimum ratio of put options volume vs 30 day
            avg put options volume. Min: 0. Example: 0.25.
        max_perc_30_day_put (Union[Unset, int]): The maximum ratio of put options volume vs 30 day
            avg put options volume. Min: 0. Example: 1.72.
        min_total_oi_change_perc (Union[Unset, int]): The minimum open interest change compared to
            the previous day. Min: -1. Example: -0.45.
        max_total_oi_change_perc (Union[Unset, int]): The maximum open interest change compared to
            the previous day. Min: -1. Example: 0.2.
        min_call_oi_change_perc (Union[Unset, int]): The minimum open interest change of call
            contracts compared to the previous day. Min: -1. Example: -0.45.
        max_call_oi_change_perc (Union[Unset, int]): The maximum open interest change of call
            contracts compared to the previous day. Min: -1. Example: 0.2.
        min_put_oi_change_perc (Union[Unset, int]): The minimum open interest change of put
            contracts compared to the previous day. Min: -1. Example: -0.45.
        max_put_oi_change_perc (Union[Unset, int]): The maximum open interest change of put
            contracts compared to the previous day. Min: -1. Example: 0.2.
        min_implied_move (Union[Unset, int]): The minimum implied move. Min: 0. Example: 0.45.
        max_implied_move (Union[Unset, int]): The maximum implied move. Min: 0. Example: 1.4.
        min_implied_move_perc (Union[Unset, int]): The minimum implied move perc. Max: 1. Min: 0.
            Example: 0.15.
        max_implied_move_perc (Union[Unset, int]): The maximum implied move perc. Max: 1. Min: 0.
            Example: 0.6.
        min_volatility (Union[Unset, int]): The minimum volatility. Min: 0. Example: 0.15.
        max_volatility (Union[Unset, int]): The maximum volatility. Min: 0. Example: 0.6.
        min_iv_rank (Union[Unset, int]): The minimum iv rank. Max: 100. Min: 0. Example: 0.15.
        max_iv_rank (Union[Unset, int]): The maximum iv rank. Max: 100. Min: 0. Example: 22.6.
        min_volume (Union[Unset, int]): The minimum options volume. Min: 0. Example: 10000.
        max_volume (Union[Unset, int]): The maximum options volume. Min: 0. Example: 35000.
        min_call_volume (Union[Unset, int]): The minimum call options volume. Min: 0. Example:
            10000.
        max_call_volume (Union[Unset, int]): The maximum call options volume. Min: 0. Example:
            35000.
        min_put_volume (Union[Unset, int]): The minimum put options volume. Min: 0. Example:
            10000.
        max_put_volume (Union[Unset, int]): The maximum put options volume. Min: 0. Example:
            35000.
        min_premium (Union[Unset, int]): The minimum options premium. Min: 0. Example: 10000.
        max_premium (Union[Unset, int]): The minimum options premium. Min: 0. Example: 35000.
        min_call_premium (Union[Unset, int]): The minimum call options premium. Min: 0. Example:
            10000.
        max_call_premium (Union[Unset, int]): The minimum call options premium. Min: 0. Example:
            35000.
        min_put_premium (Union[Unset, int]): The minimum put options premium. Min: 0. Example:
            10000.
        max_put_premium (Union[Unset, int]): The minimum put options premium. Min: 0. Example:
            35000.
        min_net_premium (Union[Unset, int]): The minimum net options premium. Min: 0. Example:
            10000.
        max_net_premium (Union[Unset, int]): The minimum net options premium. Min: 0. Example:
            35000.
        min_oi (Union[Unset, int]): The minimum open interest. Min: 0. Example: 10000.
        max_oi (Union[Unset, int]): The maximum open interest. Min: 0. Example: 35000.
        min_oi_vs_vol (Union[Unset, int]): The minimum open interest vs options volume ratio. Min:
            0. Example: 0.5.
        max_oi_vs_vol (Union[Unset, int]): The maximum open interest vs options volume ratio. Min:
            0. Example: 1.5.
        min_put_call_ratio (Union[Unset, int]): The minimum put to call ratio. Min: 0. Example:
            0.5.
        max_put_call_ratio (Union[Unset, int]): The maximum put to call ratio. Min: 0. Example:
            1.5.
        order (Union[Unset, ScreenerOrderByField]): The field to order by. Example: premium.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, StockScreenerResponse, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        issue_types=issue_types,
        min_change=min_change,
        max_change=max_change,
        min_underlying_price=min_underlying_price,
        max_underlying_price=max_underlying_price,
        is_s_p_500=is_s_p_500,
        has_dividends=has_dividends,
        sectors=sectors,
        min_marketcap=min_marketcap,
        max_marketcap=max_marketcap,
        min_perc_3_day_total=min_perc_3_day_total,
        max_perc_3_day_total=max_perc_3_day_total,
        min_perc_3_day_call=min_perc_3_day_call,
        max_perc_3_day_call=max_perc_3_day_call,
        min_perc_3_day_put=min_perc_3_day_put,
        max_perc_3_day_put=max_perc_3_day_put,
        min_perc_30_day_total=min_perc_30_day_total,
        max_perc_30_day_total=max_perc_30_day_total,
        min_perc_30_day_call=min_perc_30_day_call,
        max_perc_30_day_call=max_perc_30_day_call,
        min_perc_30_day_put=min_perc_30_day_put,
        max_perc_30_day_put=max_perc_30_day_put,
        min_total_oi_change_perc=min_total_oi_change_perc,
        max_total_oi_change_perc=max_total_oi_change_perc,
        min_call_oi_change_perc=min_call_oi_change_perc,
        max_call_oi_change_perc=max_call_oi_change_perc,
        min_put_oi_change_perc=min_put_oi_change_perc,
        max_put_oi_change_perc=max_put_oi_change_perc,
        min_implied_move=min_implied_move,
        max_implied_move=max_implied_move,
        min_implied_move_perc=min_implied_move_perc,
        max_implied_move_perc=max_implied_move_perc,
        min_volatility=min_volatility,
        max_volatility=max_volatility,
        min_iv_rank=min_iv_rank,
        max_iv_rank=max_iv_rank,
        min_volume=min_volume,
        max_volume=max_volume,
        min_call_volume=min_call_volume,
        max_call_volume=max_call_volume,
        min_put_volume=min_put_volume,
        max_put_volume=max_put_volume,
        min_premium=min_premium,
        max_premium=max_premium,
        min_call_premium=min_call_premium,
        max_call_premium=max_call_premium,
        min_put_premium=min_put_premium,
        max_put_premium=max_put_premium,
        min_net_premium=min_net_premium,
        max_net_premium=max_net_premium,
        min_oi=min_oi,
        max_oi=max_oi,
        min_oi_vs_vol=min_oi_vs_vol,
        max_oi_vs_vol=max_oi_vs_vol,
        min_put_call_ratio=min_put_call_ratio,
        max_put_call_ratio=max_put_call_ratio,
        order=order,
        order_direction=order_direction,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: UnusualWhalesClient,
    ticker: Union[Unset, str] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    min_change: Union[Unset, int] = UNSET,
    max_change: Union[Unset, int] = UNSET,
    min_underlying_price: Union[Unset, int] = UNSET,
    max_underlying_price: Union[Unset, int] = UNSET,
    is_s_p_500: Union[Unset, bool] = UNSET,
    has_dividends: Union[Unset, bool] = UNSET,
    sectors: Union[Unset, List[SingleSector]] = UNSET,
    min_marketcap: Union[Unset, int] = UNSET,
    max_marketcap: Union[Unset, int] = UNSET,
    min_perc_3_day_total: Union[Unset, int] = UNSET,
    max_perc_3_day_total: Union[Unset, int] = UNSET,
    min_perc_3_day_call: Union[Unset, int] = UNSET,
    max_perc_3_day_call: Union[Unset, int] = UNSET,
    min_perc_3_day_put: Union[Unset, int] = UNSET,
    max_perc_3_day_put: Union[Unset, int] = UNSET,
    min_perc_30_day_total: Union[Unset, int] = UNSET,
    max_perc_30_day_total: Union[Unset, int] = UNSET,
    min_perc_30_day_call: Union[Unset, int] = UNSET,
    max_perc_30_day_call: Union[Unset, int] = UNSET,
    min_perc_30_day_put: Union[Unset, int] = UNSET,
    max_perc_30_day_put: Union[Unset, int] = UNSET,
    min_total_oi_change_perc: Union[Unset, int] = UNSET,
    max_total_oi_change_perc: Union[Unset, int] = UNSET,
    min_call_oi_change_perc: Union[Unset, int] = UNSET,
    max_call_oi_change_perc: Union[Unset, int] = UNSET,
    min_put_oi_change_perc: Union[Unset, int] = UNSET,
    max_put_oi_change_perc: Union[Unset, int] = UNSET,
    min_implied_move: Union[Unset, int] = UNSET,
    max_implied_move: Union[Unset, int] = UNSET,
    min_implied_move_perc: Union[Unset, int] = UNSET,
    max_implied_move_perc: Union[Unset, int] = UNSET,
    min_volatility: Union[Unset, int] = UNSET,
    max_volatility: Union[Unset, int] = UNSET,
    min_iv_rank: Union[Unset, int] = UNSET,
    max_iv_rank: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_call_volume: Union[Unset, int] = UNSET,
    max_call_volume: Union[Unset, int] = UNSET,
    min_put_volume: Union[Unset, int] = UNSET,
    max_put_volume: Union[Unset, int] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_call_premium: Union[Unset, int] = UNSET,
    max_call_premium: Union[Unset, int] = UNSET,
    min_put_premium: Union[Unset, int] = UNSET,
    max_put_premium: Union[Unset, int] = UNSET,
    min_net_premium: Union[Unset, int] = UNSET,
    max_net_premium: Union[Unset, int] = UNSET,
    min_oi: Union[Unset, int] = UNSET,
    max_oi: Union[Unset, int] = UNSET,
    min_oi_vs_vol: Union[Unset, int] = UNSET,
    max_oi_vs_vol: Union[Unset, int] = UNSET,
    min_put_call_ratio: Union[Unset, int] = UNSET,
    max_put_call_ratio: Union[Unset, int] = UNSET,
    order: Union[Unset, ScreenerOrderByField] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Optional[Union[ErrorMessage, StockScreenerResponse, str]]:
    """Stock Screener

     A stock screener endpoint to screen the market for stocks by a variety of filter options.

    For an example of what can be build with this endpoint check out the [Stock
    Screener](https://unusualwhales.com/flow/ticker_flows)
    on unusualwhales.com

    Args:
        ticker (Union[Unset, str]): A comma separated list of tickers. To exclude certain tickers
            prefix the first ticker with a `-`. Example: AAPL,INTC.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        min_change (Union[Unset, int]): The minimum % change to the previous Trading Day. Min: -1.
            Example: -0.45.
        max_change (Union[Unset, int]): The maximum % change to the previous Trading Day. Min: -1.
            Example: 0.2.
        min_underlying_price (Union[Unset, int]): The minimum stock price. Min: 0. Example: 5.23.
        max_underlying_price (Union[Unset, int]): The maximum stock price. Min: 0. Example: 10.53.
        is_s_p_500 (Union[Unset, bool]): Boolean whether to only include stocks which are part of
            the S&P 500. Setting this to false has no effect. Example: True.
        has_dividends (Union[Unset, bool]): Boolean wheter to only include stocks which pay
            dividends. Setting this to false has no effect. Example: True.
        sectors (Union[Unset, List[SingleSector]]): An array of 1 or more sectors. Example:
            ['Consumer Cyclical', 'Technology', 'Utilities'].
        min_marketcap (Union[Unset, int]): The minimum marketcap. Min: 0. Example: 1000000.
        max_marketcap (Union[Unset, int]): The maximum marketcap. Min: 0. Example: 250000000.
        min_perc_3_day_total (Union[Unset, int]): The minimum ratio of options volume vs 3 day avg
            options volume. Min: 0. Example: 0.25.
        max_perc_3_day_total (Union[Unset, int]): The maximum ratio of options volume vs 3 day avg
            options volume. Min: 0. Example: 1.72.
        min_perc_3_day_call (Union[Unset, int]): The minimum ratio of call options volume vs 3 day
            avg call options volume. Min: 0. Example: 0.25.
        max_perc_3_day_call (Union[Unset, int]): The maximum ratio of call options volume vs 3 day
            avg call options volume. Min: 0. Example: 1.72.
        min_perc_3_day_put (Union[Unset, int]): The minimum ratio of put options volume vs 3 day
            avg put options volume. Min: 0. Example: 0.25.
        max_perc_3_day_put (Union[Unset, int]): The maximum ratio of put options volume vs 3 day
            avg put options volume. Min: 0. Example: 1.72.
        min_perc_30_day_total (Union[Unset, int]): The minimum ratio of options volume vs 30 day
            avg options volume. Min: 0. Example: 0.25.
        max_perc_30_day_total (Union[Unset, int]): The maximum ratio of options volume vs 30 day
            avg options volume. Min: 0. Example: 1.72.
        min_perc_30_day_call (Union[Unset, int]): The minimum ratio of call options volume vs 30
            day avg call options volume. Min: 0. Example: 0.25.
        max_perc_30_day_call (Union[Unset, int]): The maximum ratio of call options volume vs 30
            day avg call options volume. Min: 0. Example: 1.72.
        min_perc_30_day_put (Union[Unset, int]): The minimum ratio of put options volume vs 30 day
            avg put options volume. Min: 0. Example: 0.25.
        max_perc_30_day_put (Union[Unset, int]): The maximum ratio of put options volume vs 30 day
            avg put options volume. Min: 0. Example: 1.72.
        min_total_oi_change_perc (Union[Unset, int]): The minimum open interest change compared to
            the previous day. Min: -1. Example: -0.45.
        max_total_oi_change_perc (Union[Unset, int]): The maximum open interest change compared to
            the previous day. Min: -1. Example: 0.2.
        min_call_oi_change_perc (Union[Unset, int]): The minimum open interest change of call
            contracts compared to the previous day. Min: -1. Example: -0.45.
        max_call_oi_change_perc (Union[Unset, int]): The maximum open interest change of call
            contracts compared to the previous day. Min: -1. Example: 0.2.
        min_put_oi_change_perc (Union[Unset, int]): The minimum open interest change of put
            contracts compared to the previous day. Min: -1. Example: -0.45.
        max_put_oi_change_perc (Union[Unset, int]): The maximum open interest change of put
            contracts compared to the previous day. Min: -1. Example: 0.2.
        min_implied_move (Union[Unset, int]): The minimum implied move. Min: 0. Example: 0.45.
        max_implied_move (Union[Unset, int]): The maximum implied move. Min: 0. Example: 1.4.
        min_implied_move_perc (Union[Unset, int]): The minimum implied move perc. Max: 1. Min: 0.
            Example: 0.15.
        max_implied_move_perc (Union[Unset, int]): The maximum implied move perc. Max: 1. Min: 0.
            Example: 0.6.
        min_volatility (Union[Unset, int]): The minimum volatility. Min: 0. Example: 0.15.
        max_volatility (Union[Unset, int]): The maximum volatility. Min: 0. Example: 0.6.
        min_iv_rank (Union[Unset, int]): The minimum iv rank. Max: 100. Min: 0. Example: 0.15.
        max_iv_rank (Union[Unset, int]): The maximum iv rank. Max: 100. Min: 0. Example: 22.6.
        min_volume (Union[Unset, int]): The minimum options volume. Min: 0. Example: 10000.
        max_volume (Union[Unset, int]): The maximum options volume. Min: 0. Example: 35000.
        min_call_volume (Union[Unset, int]): The minimum call options volume. Min: 0. Example:
            10000.
        max_call_volume (Union[Unset, int]): The maximum call options volume. Min: 0. Example:
            35000.
        min_put_volume (Union[Unset, int]): The minimum put options volume. Min: 0. Example:
            10000.
        max_put_volume (Union[Unset, int]): The maximum put options volume. Min: 0. Example:
            35000.
        min_premium (Union[Unset, int]): The minimum options premium. Min: 0. Example: 10000.
        max_premium (Union[Unset, int]): The minimum options premium. Min: 0. Example: 35000.
        min_call_premium (Union[Unset, int]): The minimum call options premium. Min: 0. Example:
            10000.
        max_call_premium (Union[Unset, int]): The minimum call options premium. Min: 0. Example:
            35000.
        min_put_premium (Union[Unset, int]): The minimum put options premium. Min: 0. Example:
            10000.
        max_put_premium (Union[Unset, int]): The minimum put options premium. Min: 0. Example:
            35000.
        min_net_premium (Union[Unset, int]): The minimum net options premium. Min: 0. Example:
            10000.
        max_net_premium (Union[Unset, int]): The minimum net options premium. Min: 0. Example:
            35000.
        min_oi (Union[Unset, int]): The minimum open interest. Min: 0. Example: 10000.
        max_oi (Union[Unset, int]): The maximum open interest. Min: 0. Example: 35000.
        min_oi_vs_vol (Union[Unset, int]): The minimum open interest vs options volume ratio. Min:
            0. Example: 0.5.
        max_oi_vs_vol (Union[Unset, int]): The maximum open interest vs options volume ratio. Min:
            0. Example: 1.5.
        min_put_call_ratio (Union[Unset, int]): The minimum put to call ratio. Min: 0. Example:
            0.5.
        max_put_call_ratio (Union[Unset, int]): The maximum put to call ratio. Min: 0. Example:
            1.5.
        order (Union[Unset, ScreenerOrderByField]): The field to order by. Example: premium.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, StockScreenerResponse, str]
    """

    return sync_detailed(
        client=client,
        ticker=ticker,
        issue_types=issue_types,
        min_change=min_change,
        max_change=max_change,
        min_underlying_price=min_underlying_price,
        max_underlying_price=max_underlying_price,
        is_s_p_500=is_s_p_500,
        has_dividends=has_dividends,
        sectors=sectors,
        min_marketcap=min_marketcap,
        max_marketcap=max_marketcap,
        min_perc_3_day_total=min_perc_3_day_total,
        max_perc_3_day_total=max_perc_3_day_total,
        min_perc_3_day_call=min_perc_3_day_call,
        max_perc_3_day_call=max_perc_3_day_call,
        min_perc_3_day_put=min_perc_3_day_put,
        max_perc_3_day_put=max_perc_3_day_put,
        min_perc_30_day_total=min_perc_30_day_total,
        max_perc_30_day_total=max_perc_30_day_total,
        min_perc_30_day_call=min_perc_30_day_call,
        max_perc_30_day_call=max_perc_30_day_call,
        min_perc_30_day_put=min_perc_30_day_put,
        max_perc_30_day_put=max_perc_30_day_put,
        min_total_oi_change_perc=min_total_oi_change_perc,
        max_total_oi_change_perc=max_total_oi_change_perc,
        min_call_oi_change_perc=min_call_oi_change_perc,
        max_call_oi_change_perc=max_call_oi_change_perc,
        min_put_oi_change_perc=min_put_oi_change_perc,
        max_put_oi_change_perc=max_put_oi_change_perc,
        min_implied_move=min_implied_move,
        max_implied_move=max_implied_move,
        min_implied_move_perc=min_implied_move_perc,
        max_implied_move_perc=max_implied_move_perc,
        min_volatility=min_volatility,
        max_volatility=max_volatility,
        min_iv_rank=min_iv_rank,
        max_iv_rank=max_iv_rank,
        min_volume=min_volume,
        max_volume=max_volume,
        min_call_volume=min_call_volume,
        max_call_volume=max_call_volume,
        min_put_volume=min_put_volume,
        max_put_volume=max_put_volume,
        min_premium=min_premium,
        max_premium=max_premium,
        min_call_premium=min_call_premium,
        max_call_premium=max_call_premium,
        min_put_premium=min_put_premium,
        max_put_premium=max_put_premium,
        min_net_premium=min_net_premium,
        max_net_premium=max_net_premium,
        min_oi=min_oi,
        max_oi=max_oi,
        min_oi_vs_vol=min_oi_vs_vol,
        max_oi_vs_vol=max_oi_vs_vol,
        min_put_call_ratio=min_put_call_ratio,
        max_put_call_ratio=max_put_call_ratio,
        order=order,
        order_direction=order_direction,
    ).parsed


async def asyncio_detailed(
    *,
    client: UnusualWhalesClient,
    ticker: Union[Unset, str] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    min_change: Union[Unset, int] = UNSET,
    max_change: Union[Unset, int] = UNSET,
    min_underlying_price: Union[Unset, int] = UNSET,
    max_underlying_price: Union[Unset, int] = UNSET,
    is_s_p_500: Union[Unset, bool] = UNSET,
    has_dividends: Union[Unset, bool] = UNSET,
    sectors: Union[Unset, List[SingleSector]] = UNSET,
    min_marketcap: Union[Unset, int] = UNSET,
    max_marketcap: Union[Unset, int] = UNSET,
    min_perc_3_day_total: Union[Unset, int] = UNSET,
    max_perc_3_day_total: Union[Unset, int] = UNSET,
    min_perc_3_day_call: Union[Unset, int] = UNSET,
    max_perc_3_day_call: Union[Unset, int] = UNSET,
    min_perc_3_day_put: Union[Unset, int] = UNSET,
    max_perc_3_day_put: Union[Unset, int] = UNSET,
    min_perc_30_day_total: Union[Unset, int] = UNSET,
    max_perc_30_day_total: Union[Unset, int] = UNSET,
    min_perc_30_day_call: Union[Unset, int] = UNSET,
    max_perc_30_day_call: Union[Unset, int] = UNSET,
    min_perc_30_day_put: Union[Unset, int] = UNSET,
    max_perc_30_day_put: Union[Unset, int] = UNSET,
    min_total_oi_change_perc: Union[Unset, int] = UNSET,
    max_total_oi_change_perc: Union[Unset, int] = UNSET,
    min_call_oi_change_perc: Union[Unset, int] = UNSET,
    max_call_oi_change_perc: Union[Unset, int] = UNSET,
    min_put_oi_change_perc: Union[Unset, int] = UNSET,
    max_put_oi_change_perc: Union[Unset, int] = UNSET,
    min_implied_move: Union[Unset, int] = UNSET,
    max_implied_move: Union[Unset, int] = UNSET,
    min_implied_move_perc: Union[Unset, int] = UNSET,
    max_implied_move_perc: Union[Unset, int] = UNSET,
    min_volatility: Union[Unset, int] = UNSET,
    max_volatility: Union[Unset, int] = UNSET,
    min_iv_rank: Union[Unset, int] = UNSET,
    max_iv_rank: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_call_volume: Union[Unset, int] = UNSET,
    max_call_volume: Union[Unset, int] = UNSET,
    min_put_volume: Union[Unset, int] = UNSET,
    max_put_volume: Union[Unset, int] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_call_premium: Union[Unset, int] = UNSET,
    max_call_premium: Union[Unset, int] = UNSET,
    min_put_premium: Union[Unset, int] = UNSET,
    max_put_premium: Union[Unset, int] = UNSET,
    min_net_premium: Union[Unset, int] = UNSET,
    max_net_premium: Union[Unset, int] = UNSET,
    min_oi: Union[Unset, int] = UNSET,
    max_oi: Union[Unset, int] = UNSET,
    min_oi_vs_vol: Union[Unset, int] = UNSET,
    max_oi_vs_vol: Union[Unset, int] = UNSET,
    min_put_call_ratio: Union[Unset, int] = UNSET,
    max_put_call_ratio: Union[Unset, int] = UNSET,
    order: Union[Unset, ScreenerOrderByField] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Response[Union[ErrorMessage, StockScreenerResponse, str]]:
    """Stock Screener

     A stock screener endpoint to screen the market for stocks by a variety of filter options.

    For an example of what can be build with this endpoint check out the [Stock
    Screener](https://unusualwhales.com/flow/ticker_flows)
    on unusualwhales.com

    Args:
        ticker (Union[Unset, str]): A comma separated list of tickers. To exclude certain tickers
            prefix the first ticker with a `-`. Example: AAPL,INTC.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        min_change (Union[Unset, int]): The minimum % change to the previous Trading Day. Min: -1.
            Example: -0.45.
        max_change (Union[Unset, int]): The maximum % change to the previous Trading Day. Min: -1.
            Example: 0.2.
        min_underlying_price (Union[Unset, int]): The minimum stock price. Min: 0. Example: 5.23.
        max_underlying_price (Union[Unset, int]): The maximum stock price. Min: 0. Example: 10.53.
        is_s_p_500 (Union[Unset, bool]): Boolean whether to only include stocks which are part of
            the S&P 500. Setting this to false has no effect. Example: True.
        has_dividends (Union[Unset, bool]): Boolean wheter to only include stocks which pay
            dividends. Setting this to false has no effect. Example: True.
        sectors (Union[Unset, List[SingleSector]]): An array of 1 or more sectors. Example:
            ['Consumer Cyclical', 'Technology', 'Utilities'].
        min_marketcap (Union[Unset, int]): The minimum marketcap. Min: 0. Example: 1000000.
        max_marketcap (Union[Unset, int]): The maximum marketcap. Min: 0. Example: 250000000.
        min_perc_3_day_total (Union[Unset, int]): The minimum ratio of options volume vs 3 day avg
            options volume. Min: 0. Example: 0.25.
        max_perc_3_day_total (Union[Unset, int]): The maximum ratio of options volume vs 3 day avg
            options volume. Min: 0. Example: 1.72.
        min_perc_3_day_call (Union[Unset, int]): The minimum ratio of call options volume vs 3 day
            avg call options volume. Min: 0. Example: 0.25.
        max_perc_3_day_call (Union[Unset, int]): The maximum ratio of call options volume vs 3 day
            avg call options volume. Min: 0. Example: 1.72.
        min_perc_3_day_put (Union[Unset, int]): The minimum ratio of put options volume vs 3 day
            avg put options volume. Min: 0. Example: 0.25.
        max_perc_3_day_put (Union[Unset, int]): The maximum ratio of put options volume vs 3 day
            avg put options volume. Min: 0. Example: 1.72.
        min_perc_30_day_total (Union[Unset, int]): The minimum ratio of options volume vs 30 day
            avg options volume. Min: 0. Example: 0.25.
        max_perc_30_day_total (Union[Unset, int]): The maximum ratio of options volume vs 30 day
            avg options volume. Min: 0. Example: 1.72.
        min_perc_30_day_call (Union[Unset, int]): The minimum ratio of call options volume vs 30
            day avg call options volume. Min: 0. Example: 0.25.
        max_perc_30_day_call (Union[Unset, int]): The maximum ratio of call options volume vs 30
            day avg call options volume. Min: 0. Example: 1.72.
        min_perc_30_day_put (Union[Unset, int]): The minimum ratio of put options volume vs 30 day
            avg put options volume. Min: 0. Example: 0.25.
        max_perc_30_day_put (Union[Unset, int]): The maximum ratio of put options volume vs 30 day
            avg put options volume. Min: 0. Example: 1.72.
        min_total_oi_change_perc (Union[Unset, int]): The minimum open interest change compared to
            the previous day. Min: -1. Example: -0.45.
        max_total_oi_change_perc (Union[Unset, int]): The maximum open interest change compared to
            the previous day. Min: -1. Example: 0.2.
        min_call_oi_change_perc (Union[Unset, int]): The minimum open interest change of call
            contracts compared to the previous day. Min: -1. Example: -0.45.
        max_call_oi_change_perc (Union[Unset, int]): The maximum open interest change of call
            contracts compared to the previous day. Min: -1. Example: 0.2.
        min_put_oi_change_perc (Union[Unset, int]): The minimum open interest change of put
            contracts compared to the previous day. Min: -1. Example: -0.45.
        max_put_oi_change_perc (Union[Unset, int]): The maximum open interest change of put
            contracts compared to the previous day. Min: -1. Example: 0.2.
        min_implied_move (Union[Unset, int]): The minimum implied move. Min: 0. Example: 0.45.
        max_implied_move (Union[Unset, int]): The maximum implied move. Min: 0. Example: 1.4.
        min_implied_move_perc (Union[Unset, int]): The minimum implied move perc. Max: 1. Min: 0.
            Example: 0.15.
        max_implied_move_perc (Union[Unset, int]): The maximum implied move perc. Max: 1. Min: 0.
            Example: 0.6.
        min_volatility (Union[Unset, int]): The minimum volatility. Min: 0. Example: 0.15.
        max_volatility (Union[Unset, int]): The maximum volatility. Min: 0. Example: 0.6.
        min_iv_rank (Union[Unset, int]): The minimum iv rank. Max: 100. Min: 0. Example: 0.15.
        max_iv_rank (Union[Unset, int]): The maximum iv rank. Max: 100. Min: 0. Example: 22.6.
        min_volume (Union[Unset, int]): The minimum options volume. Min: 0. Example: 10000.
        max_volume (Union[Unset, int]): The maximum options volume. Min: 0. Example: 35000.
        min_call_volume (Union[Unset, int]): The minimum call options volume. Min: 0. Example:
            10000.
        max_call_volume (Union[Unset, int]): The maximum call options volume. Min: 0. Example:
            35000.
        min_put_volume (Union[Unset, int]): The minimum put options volume. Min: 0. Example:
            10000.
        max_put_volume (Union[Unset, int]): The maximum put options volume. Min: 0. Example:
            35000.
        min_premium (Union[Unset, int]): The minimum options premium. Min: 0. Example: 10000.
        max_premium (Union[Unset, int]): The minimum options premium. Min: 0. Example: 35000.
        min_call_premium (Union[Unset, int]): The minimum call options premium. Min: 0. Example:
            10000.
        max_call_premium (Union[Unset, int]): The minimum call options premium. Min: 0. Example:
            35000.
        min_put_premium (Union[Unset, int]): The minimum put options premium. Min: 0. Example:
            10000.
        max_put_premium (Union[Unset, int]): The minimum put options premium. Min: 0. Example:
            35000.
        min_net_premium (Union[Unset, int]): The minimum net options premium. Min: 0. Example:
            10000.
        max_net_premium (Union[Unset, int]): The minimum net options premium. Min: 0. Example:
            35000.
        min_oi (Union[Unset, int]): The minimum open interest. Min: 0. Example: 10000.
        max_oi (Union[Unset, int]): The maximum open interest. Min: 0. Example: 35000.
        min_oi_vs_vol (Union[Unset, int]): The minimum open interest vs options volume ratio. Min:
            0. Example: 0.5.
        max_oi_vs_vol (Union[Unset, int]): The maximum open interest vs options volume ratio. Min:
            0. Example: 1.5.
        min_put_call_ratio (Union[Unset, int]): The minimum put to call ratio. Min: 0. Example:
            0.5.
        max_put_call_ratio (Union[Unset, int]): The maximum put to call ratio. Min: 0. Example:
            1.5.
        order (Union[Unset, ScreenerOrderByField]): The field to order by. Example: premium.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, StockScreenerResponse, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        issue_types=issue_types,
        min_change=min_change,
        max_change=max_change,
        min_underlying_price=min_underlying_price,
        max_underlying_price=max_underlying_price,
        is_s_p_500=is_s_p_500,
        has_dividends=has_dividends,
        sectors=sectors,
        min_marketcap=min_marketcap,
        max_marketcap=max_marketcap,
        min_perc_3_day_total=min_perc_3_day_total,
        max_perc_3_day_total=max_perc_3_day_total,
        min_perc_3_day_call=min_perc_3_day_call,
        max_perc_3_day_call=max_perc_3_day_call,
        min_perc_3_day_put=min_perc_3_day_put,
        max_perc_3_day_put=max_perc_3_day_put,
        min_perc_30_day_total=min_perc_30_day_total,
        max_perc_30_day_total=max_perc_30_day_total,
        min_perc_30_day_call=min_perc_30_day_call,
        max_perc_30_day_call=max_perc_30_day_call,
        min_perc_30_day_put=min_perc_30_day_put,
        max_perc_30_day_put=max_perc_30_day_put,
        min_total_oi_change_perc=min_total_oi_change_perc,
        max_total_oi_change_perc=max_total_oi_change_perc,
        min_call_oi_change_perc=min_call_oi_change_perc,
        max_call_oi_change_perc=max_call_oi_change_perc,
        min_put_oi_change_perc=min_put_oi_change_perc,
        max_put_oi_change_perc=max_put_oi_change_perc,
        min_implied_move=min_implied_move,
        max_implied_move=max_implied_move,
        min_implied_move_perc=min_implied_move_perc,
        max_implied_move_perc=max_implied_move_perc,
        min_volatility=min_volatility,
        max_volatility=max_volatility,
        min_iv_rank=min_iv_rank,
        max_iv_rank=max_iv_rank,
        min_volume=min_volume,
        max_volume=max_volume,
        min_call_volume=min_call_volume,
        max_call_volume=max_call_volume,
        min_put_volume=min_put_volume,
        max_put_volume=max_put_volume,
        min_premium=min_premium,
        max_premium=max_premium,
        min_call_premium=min_call_premium,
        max_call_premium=max_call_premium,
        min_put_premium=min_put_premium,
        max_put_premium=max_put_premium,
        min_net_premium=min_net_premium,
        max_net_premium=max_net_premium,
        min_oi=min_oi,
        max_oi=max_oi,
        min_oi_vs_vol=min_oi_vs_vol,
        max_oi_vs_vol=max_oi_vs_vol,
        min_put_call_ratio=min_put_call_ratio,
        max_put_call_ratio=max_put_call_ratio,
        order=order,
        order_direction=order_direction,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: UnusualWhalesClient,
    ticker: Union[Unset, str] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    min_change: Union[Unset, int] = UNSET,
    max_change: Union[Unset, int] = UNSET,
    min_underlying_price: Union[Unset, int] = UNSET,
    max_underlying_price: Union[Unset, int] = UNSET,
    is_s_p_500: Union[Unset, bool] = UNSET,
    has_dividends: Union[Unset, bool] = UNSET,
    sectors: Union[Unset, List[SingleSector]] = UNSET,
    min_marketcap: Union[Unset, int] = UNSET,
    max_marketcap: Union[Unset, int] = UNSET,
    min_perc_3_day_total: Union[Unset, int] = UNSET,
    max_perc_3_day_total: Union[Unset, int] = UNSET,
    min_perc_3_day_call: Union[Unset, int] = UNSET,
    max_perc_3_day_call: Union[Unset, int] = UNSET,
    min_perc_3_day_put: Union[Unset, int] = UNSET,
    max_perc_3_day_put: Union[Unset, int] = UNSET,
    min_perc_30_day_total: Union[Unset, int] = UNSET,
    max_perc_30_day_total: Union[Unset, int] = UNSET,
    min_perc_30_day_call: Union[Unset, int] = UNSET,
    max_perc_30_day_call: Union[Unset, int] = UNSET,
    min_perc_30_day_put: Union[Unset, int] = UNSET,
    max_perc_30_day_put: Union[Unset, int] = UNSET,
    min_total_oi_change_perc: Union[Unset, int] = UNSET,
    max_total_oi_change_perc: Union[Unset, int] = UNSET,
    min_call_oi_change_perc: Union[Unset, int] = UNSET,
    max_call_oi_change_perc: Union[Unset, int] = UNSET,
    min_put_oi_change_perc: Union[Unset, int] = UNSET,
    max_put_oi_change_perc: Union[Unset, int] = UNSET,
    min_implied_move: Union[Unset, int] = UNSET,
    max_implied_move: Union[Unset, int] = UNSET,
    min_implied_move_perc: Union[Unset, int] = UNSET,
    max_implied_move_perc: Union[Unset, int] = UNSET,
    min_volatility: Union[Unset, int] = UNSET,
    max_volatility: Union[Unset, int] = UNSET,
    min_iv_rank: Union[Unset, int] = UNSET,
    max_iv_rank: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_call_volume: Union[Unset, int] = UNSET,
    max_call_volume: Union[Unset, int] = UNSET,
    min_put_volume: Union[Unset, int] = UNSET,
    max_put_volume: Union[Unset, int] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_call_premium: Union[Unset, int] = UNSET,
    max_call_premium: Union[Unset, int] = UNSET,
    min_put_premium: Union[Unset, int] = UNSET,
    max_put_premium: Union[Unset, int] = UNSET,
    min_net_premium: Union[Unset, int] = UNSET,
    max_net_premium: Union[Unset, int] = UNSET,
    min_oi: Union[Unset, int] = UNSET,
    max_oi: Union[Unset, int] = UNSET,
    min_oi_vs_vol: Union[Unset, int] = UNSET,
    max_oi_vs_vol: Union[Unset, int] = UNSET,
    min_put_call_ratio: Union[Unset, int] = UNSET,
    max_put_call_ratio: Union[Unset, int] = UNSET,
    order: Union[Unset, ScreenerOrderByField] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Optional[Union[ErrorMessage, StockScreenerResponse, str]]:
    """Stock Screener

     A stock screener endpoint to screen the market for stocks by a variety of filter options.

    For an example of what can be build with this endpoint check out the [Stock
    Screener](https://unusualwhales.com/flow/ticker_flows)
    on unusualwhales.com

    Args:
        ticker (Union[Unset, str]): A comma separated list of tickers. To exclude certain tickers
            prefix the first ticker with a `-`. Example: AAPL,INTC.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        min_change (Union[Unset, int]): The minimum % change to the previous Trading Day. Min: -1.
            Example: -0.45.
        max_change (Union[Unset, int]): The maximum % change to the previous Trading Day. Min: -1.
            Example: 0.2.
        min_underlying_price (Union[Unset, int]): The minimum stock price. Min: 0. Example: 5.23.
        max_underlying_price (Union[Unset, int]): The maximum stock price. Min: 0. Example: 10.53.
        is_s_p_500 (Union[Unset, bool]): Boolean whether to only include stocks which are part of
            the S&P 500. Setting this to false has no effect. Example: True.
        has_dividends (Union[Unset, bool]): Boolean wheter to only include stocks which pay
            dividends. Setting this to false has no effect. Example: True.
        sectors (Union[Unset, List[SingleSector]]): An array of 1 or more sectors. Example:
            ['Consumer Cyclical', 'Technology', 'Utilities'].
        min_marketcap (Union[Unset, int]): The minimum marketcap. Min: 0. Example: 1000000.
        max_marketcap (Union[Unset, int]): The maximum marketcap. Min: 0. Example: 250000000.
        min_perc_3_day_total (Union[Unset, int]): The minimum ratio of options volume vs 3 day avg
            options volume. Min: 0. Example: 0.25.
        max_perc_3_day_total (Union[Unset, int]): The maximum ratio of options volume vs 3 day avg
            options volume. Min: 0. Example: 1.72.
        min_perc_3_day_call (Union[Unset, int]): The minimum ratio of call options volume vs 3 day
            avg call options volume. Min: 0. Example: 0.25.
        max_perc_3_day_call (Union[Unset, int]): The maximum ratio of call options volume vs 3 day
            avg call options volume. Min: 0. Example: 1.72.
        min_perc_3_day_put (Union[Unset, int]): The minimum ratio of put options volume vs 3 day
            avg put options volume. Min: 0. Example: 0.25.
        max_perc_3_day_put (Union[Unset, int]): The maximum ratio of put options volume vs 3 day
            avg put options volume. Min: 0. Example: 1.72.
        min_perc_30_day_total (Union[Unset, int]): The minimum ratio of options volume vs 30 day
            avg options volume. Min: 0. Example: 0.25.
        max_perc_30_day_total (Union[Unset, int]): The maximum ratio of options volume vs 30 day
            avg options volume. Min: 0. Example: 1.72.
        min_perc_30_day_call (Union[Unset, int]): The minimum ratio of call options volume vs 30
            day avg call options volume. Min: 0. Example: 0.25.
        max_perc_30_day_call (Union[Unset, int]): The maximum ratio of call options volume vs 30
            day avg call options volume. Min: 0. Example: 1.72.
        min_perc_30_day_put (Union[Unset, int]): The minimum ratio of put options volume vs 30 day
            avg put options volume. Min: 0. Example: 0.25.
        max_perc_30_day_put (Union[Unset, int]): The maximum ratio of put options volume vs 30 day
            avg put options volume. Min: 0. Example: 1.72.
        min_total_oi_change_perc (Union[Unset, int]): The minimum open interest change compared to
            the previous day. Min: -1. Example: -0.45.
        max_total_oi_change_perc (Union[Unset, int]): The maximum open interest change compared to
            the previous day. Min: -1. Example: 0.2.
        min_call_oi_change_perc (Union[Unset, int]): The minimum open interest change of call
            contracts compared to the previous day. Min: -1. Example: -0.45.
        max_call_oi_change_perc (Union[Unset, int]): The maximum open interest change of call
            contracts compared to the previous day. Min: -1. Example: 0.2.
        min_put_oi_change_perc (Union[Unset, int]): The minimum open interest change of put
            contracts compared to the previous day. Min: -1. Example: -0.45.
        max_put_oi_change_perc (Union[Unset, int]): The maximum open interest change of put
            contracts compared to the previous day. Min: -1. Example: 0.2.
        min_implied_move (Union[Unset, int]): The minimum implied move. Min: 0. Example: 0.45.
        max_implied_move (Union[Unset, int]): The maximum implied move. Min: 0. Example: 1.4.
        min_implied_move_perc (Union[Unset, int]): The minimum implied move perc. Max: 1. Min: 0.
            Example: 0.15.
        max_implied_move_perc (Union[Unset, int]): The maximum implied move perc. Max: 1. Min: 0.
            Example: 0.6.
        min_volatility (Union[Unset, int]): The minimum volatility. Min: 0. Example: 0.15.
        max_volatility (Union[Unset, int]): The maximum volatility. Min: 0. Example: 0.6.
        min_iv_rank (Union[Unset, int]): The minimum iv rank. Max: 100. Min: 0. Example: 0.15.
        max_iv_rank (Union[Unset, int]): The maximum iv rank. Max: 100. Min: 0. Example: 22.6.
        min_volume (Union[Unset, int]): The minimum options volume. Min: 0. Example: 10000.
        max_volume (Union[Unset, int]): The maximum options volume. Min: 0. Example: 35000.
        min_call_volume (Union[Unset, int]): The minimum call options volume. Min: 0. Example:
            10000.
        max_call_volume (Union[Unset, int]): The maximum call options volume. Min: 0. Example:
            35000.
        min_put_volume (Union[Unset, int]): The minimum put options volume. Min: 0. Example:
            10000.
        max_put_volume (Union[Unset, int]): The maximum put options volume. Min: 0. Example:
            35000.
        min_premium (Union[Unset, int]): The minimum options premium. Min: 0. Example: 10000.
        max_premium (Union[Unset, int]): The minimum options premium. Min: 0. Example: 35000.
        min_call_premium (Union[Unset, int]): The minimum call options premium. Min: 0. Example:
            10000.
        max_call_premium (Union[Unset, int]): The minimum call options premium. Min: 0. Example:
            35000.
        min_put_premium (Union[Unset, int]): The minimum put options premium. Min: 0. Example:
            10000.
        max_put_premium (Union[Unset, int]): The minimum put options premium. Min: 0. Example:
            35000.
        min_net_premium (Union[Unset, int]): The minimum net options premium. Min: 0. Example:
            10000.
        max_net_premium (Union[Unset, int]): The minimum net options premium. Min: 0. Example:
            35000.
        min_oi (Union[Unset, int]): The minimum open interest. Min: 0. Example: 10000.
        max_oi (Union[Unset, int]): The maximum open interest. Min: 0. Example: 35000.
        min_oi_vs_vol (Union[Unset, int]): The minimum open interest vs options volume ratio. Min:
            0. Example: 0.5.
        max_oi_vs_vol (Union[Unset, int]): The maximum open interest vs options volume ratio. Min:
            0. Example: 1.5.
        min_put_call_ratio (Union[Unset, int]): The minimum put to call ratio. Min: 0. Example:
            0.5.
        max_put_call_ratio (Union[Unset, int]): The maximum put to call ratio. Min: 0. Example:
            1.5.
        order (Union[Unset, ScreenerOrderByField]): The field to order by. Example: premium.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, StockScreenerResponse, str]
    """

    return (
        await asyncio_detailed(
            client=client,
            ticker=ticker,
            issue_types=issue_types,
            min_change=min_change,
            max_change=max_change,
            min_underlying_price=min_underlying_price,
            max_underlying_price=max_underlying_price,
            is_s_p_500=is_s_p_500,
            has_dividends=has_dividends,
            sectors=sectors,
            min_marketcap=min_marketcap,
            max_marketcap=max_marketcap,
            min_perc_3_day_total=min_perc_3_day_total,
            max_perc_3_day_total=max_perc_3_day_total,
            min_perc_3_day_call=min_perc_3_day_call,
            max_perc_3_day_call=max_perc_3_day_call,
            min_perc_3_day_put=min_perc_3_day_put,
            max_perc_3_day_put=max_perc_3_day_put,
            min_perc_30_day_total=min_perc_30_day_total,
            max_perc_30_day_total=max_perc_30_day_total,
            min_perc_30_day_call=min_perc_30_day_call,
            max_perc_30_day_call=max_perc_30_day_call,
            min_perc_30_day_put=min_perc_30_day_put,
            max_perc_30_day_put=max_perc_30_day_put,
            min_total_oi_change_perc=min_total_oi_change_perc,
            max_total_oi_change_perc=max_total_oi_change_perc,
            min_call_oi_change_perc=min_call_oi_change_perc,
            max_call_oi_change_perc=max_call_oi_change_perc,
            min_put_oi_change_perc=min_put_oi_change_perc,
            max_put_oi_change_perc=max_put_oi_change_perc,
            min_implied_move=min_implied_move,
            max_implied_move=max_implied_move,
            min_implied_move_perc=min_implied_move_perc,
            max_implied_move_perc=max_implied_move_perc,
            min_volatility=min_volatility,
            max_volatility=max_volatility,
            min_iv_rank=min_iv_rank,
            max_iv_rank=max_iv_rank,
            min_volume=min_volume,
            max_volume=max_volume,
            min_call_volume=min_call_volume,
            max_call_volume=max_call_volume,
            min_put_volume=min_put_volume,
            max_put_volume=max_put_volume,
            min_premium=min_premium,
            max_premium=max_premium,
            min_call_premium=min_call_premium,
            max_call_premium=max_call_premium,
            min_put_premium=min_put_premium,
            max_put_premium=max_put_premium,
            min_net_premium=min_net_premium,
            max_net_premium=max_net_premium,
            min_oi=min_oi,
            max_oi=max_oi,
            min_oi_vs_vol=min_oi_vs_vol,
            max_oi_vs_vol=max_oi_vs_vol,
            min_put_call_ratio=min_put_call_ratio,
            max_put_call_ratio=max_put_call_ratio,
            order=order,
            order_direction=order_direction,
        )
    ).parsed
