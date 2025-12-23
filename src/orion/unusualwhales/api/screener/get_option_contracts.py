from http import HTTPStatus
from typing import Any, Dict, List, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.option_contract_screener_results import OptionContractScreenerResults
from ...models.option_type import OptionType
from ...models.order_direction import OrderDirection
from ...models.screener_contract_order_by_field import ScreenerContractOrderByField
from ...models.single_issue_type import SingleIssueType
from ...models.single_sector import SingleSector
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    ticker_symbol: Union[Unset, str] = UNSET,
    sectors: Union[Unset, List[SingleSector]] = UNSET,
    min_underlying_price: Union[Unset, int] = UNSET,
    max_underlying_price: Union[Unset, int] = UNSET,
    is_otm: Union[Unset, bool] = UNSET,
    min_dte: Union[Unset, int] = UNSET,
    max_dte: Union[Unset, int] = UNSET,
    min_diff: Union[Unset, int] = UNSET,
    max_diff: Union[Unset, int] = UNSET,
    min_strike: Union[Unset, int] = UNSET,
    max_strike: Union[Unset, int] = UNSET,
    type: Union[Unset, OptionType] = UNSET,
    expiry_dates: Union[Unset, str] = UNSET,
    min_marketcap: Union[Unset, int] = UNSET,
    max_marketcap: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_multileg_volume_ratio: Union[Unset, int] = UNSET,
    max_multileg_volume_ratio: Union[Unset, int] = UNSET,
    min_floor_volume_ratio: Union[Unset, int] = UNSET,
    max_floor_volume_ratio: Union[Unset, int] = UNSET,
    min_perc_change: Union[Unset, int] = UNSET,
    max_perc_change: Union[Unset, int] = UNSET,
    min_daily_perc_change: Union[Unset, int] = UNSET,
    max_daily_perc_change: Union[Unset, int] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_volume_oi_ratio: Union[Unset, int] = UNSET,
    max_volume_oi_ratio: Union[Unset, int] = UNSET,
    min_open_interest: Union[Unset, int] = UNSET,
    max_open_interest: Union[Unset, int] = UNSET,
    min_floor_volume: Union[Unset, int] = UNSET,
    max_floor_volume: Union[Unset, int] = UNSET,
    vol_greater_oi: Union[Unset, bool] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    order: Union[Unset, ScreenerContractOrderByField] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["ticker_symbol"] = ticker_symbol

    json_sectors: Union[Unset, List[str]] = UNSET
    if not isinstance(sectors, Unset):
        json_sectors = []
        for componentsschemas_sectors_item_data in sectors:
            componentsschemas_sectors_item = componentsschemas_sectors_item_data.value
            json_sectors.append(componentsschemas_sectors_item)

    params["sectors[]"] = json_sectors

    params["min_underlying_price"] = min_underlying_price

    params["max_underlying_price"] = max_underlying_price

    params["is_otm"] = is_otm

    params["min_dte"] = min_dte

    params["max_dte"] = max_dte

    params["min_diff"] = min_diff

    params["max_diff"] = max_diff

    params["min_strike"] = min_strike

    params["max_strike"] = max_strike

    json_type: Union[Unset, str] = UNSET
    if not isinstance(type, Unset):
        json_type = type.value

    params["type"] = json_type

    params["expiry_dates[]"] = expiry_dates

    params["min_marketcap"] = min_marketcap

    params["max_marketcap"] = max_marketcap

    params["min_volume"] = min_volume

    params["max_volume"] = max_volume

    params["min_multileg_volume_ratio"] = min_multileg_volume_ratio

    params["max_multileg_volume_ratio"] = max_multileg_volume_ratio

    params["min_floor_volume_ratio"] = min_floor_volume_ratio

    params["max_floor_volume_ratio"] = max_floor_volume_ratio

    params["min_perc_change"] = min_perc_change

    params["max_perc_change"] = max_perc_change

    params["min_daily_perc_change"] = min_daily_perc_change

    params["max_daily_perc_change"] = max_daily_perc_change

    params["min_premium"] = min_premium

    params["max_premium"] = max_premium

    params["min_volume_oi_ratio"] = min_volume_oi_ratio

    params["max_volume_oi_ratio"] = max_volume_oi_ratio

    params["min_open_interest"] = min_open_interest

    params["max_open_interest"] = max_open_interest

    params["min_floor_volume"] = min_floor_volume

    params["max_floor_volume"] = max_floor_volume

    params["vol_greater_oi"] = vol_greater_oi

    json_issue_types: Union[Unset, List[str]] = UNSET
    if not isinstance(issue_types, Unset):
        json_issue_types = []
        for componentsschemas_issue_types_item_data in issue_types:
            componentsschemas_issue_types_item = componentsschemas_issue_types_item_data.value
            json_issue_types.append(componentsschemas_issue_types_item)

    params["issue_types[]"] = json_issue_types

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
        "url": "/api/screener/option-contracts",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[ErrorMessage, OptionContractScreenerResults, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = OptionContractScreenerResults.from_dict(response.json())

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
) -> Response[Union[ErrorMessage, OptionContractScreenerResults, str]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: UnusualWhalesClient,
    ticker_symbol: Union[Unset, str] = UNSET,
    sectors: Union[Unset, List[SingleSector]] = UNSET,
    min_underlying_price: Union[Unset, int] = UNSET,
    max_underlying_price: Union[Unset, int] = UNSET,
    is_otm: Union[Unset, bool] = UNSET,
    min_dte: Union[Unset, int] = UNSET,
    max_dte: Union[Unset, int] = UNSET,
    min_diff: Union[Unset, int] = UNSET,
    max_diff: Union[Unset, int] = UNSET,
    min_strike: Union[Unset, int] = UNSET,
    max_strike: Union[Unset, int] = UNSET,
    type: Union[Unset, OptionType] = UNSET,
    expiry_dates: Union[Unset, str] = UNSET,
    min_marketcap: Union[Unset, int] = UNSET,
    max_marketcap: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_multileg_volume_ratio: Union[Unset, int] = UNSET,
    max_multileg_volume_ratio: Union[Unset, int] = UNSET,
    min_floor_volume_ratio: Union[Unset, int] = UNSET,
    max_floor_volume_ratio: Union[Unset, int] = UNSET,
    min_perc_change: Union[Unset, int] = UNSET,
    max_perc_change: Union[Unset, int] = UNSET,
    min_daily_perc_change: Union[Unset, int] = UNSET,
    max_daily_perc_change: Union[Unset, int] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_volume_oi_ratio: Union[Unset, int] = UNSET,
    max_volume_oi_ratio: Union[Unset, int] = UNSET,
    min_open_interest: Union[Unset, int] = UNSET,
    max_open_interest: Union[Unset, int] = UNSET,
    min_floor_volume: Union[Unset, int] = UNSET,
    max_floor_volume: Union[Unset, int] = UNSET,
    vol_greater_oi: Union[Unset, bool] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    order: Union[Unset, ScreenerContractOrderByField] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Response[Union[ErrorMessage, OptionContractScreenerResults, str]]:
    """Screener for Option Contracts

     A contract screener endpoint to screen the market for contracts by a variety of filter options.

    For an example of what can be build with this endpoint check out the [Hottest
    Contracts](https://unusualwhales.com/hottest-contracts?limit=100&hide_index_etf=true)
    on unusualwhales.

    Args:
        ticker_symbol (Union[Unset, str]): A comma separated list of tickers. To exclude certain
            tickers prefix the first ticker with a `-`. Example: AAPL,INTC.
        sectors (Union[Unset, List[SingleSector]]): An array of 1 or more sectors. Example:
            ['Consumer Cyclical', 'Technology', 'Utilities'].
        min_underlying_price (Union[Unset, int]): The minimum stock price. Min: 0. Example: 5.23.
        max_underlying_price (Union[Unset, int]): The maximum stock price. Min: 0. Example: 10.53.
        is_otm (Union[Unset, bool]): Only include contracts which are currently out of the money.
            Example: True.
        min_dte (Union[Unset, int]): The minimum days to expiry. Min: 0. Example: 1.
        max_dte (Union[Unset, int]): The maximum days to expiry. Min: 0. Example: 3.
        min_diff (Union[Unset, int]): The minimum OTM diff of a contract.
            Given a strike price of 120 and an underlying price of 98
            the diff for a call option would equal to:
            (120 - 98) / 98 = 0.2245

            The diff for a put option would equal to:
            -1 * (120 - 98) / 98 = -0.2245.
             Example: 0.53.
        max_diff (Union[Unset, int]): The minimum OTM diff of a contract.
            Given a strike price of 120 and an underlying price of 98
            the diff for a call option would equal to:
            (120 - 98) / 98 = 0.2245

            The diff for a put option would equal to:
            -1 * (120 - 98) / 98 = -0.2245.
             Example: 1.34.
        min_strike (Union[Unset, int]): The minimum strike. Min: 0. Example: 120.5.
        max_strike (Union[Unset, int]): The minimum strike. Min: 0. Example: 120.5.
        type (Union[Unset, OptionType]): The option type to filter by if specified.
        expiry_dates (Union[Unset, str]): A single expiry date in ISO date format. Example:
            2024-02-02.
        min_marketcap (Union[Unset, int]): The minimum marketcap. Min: 0. Example: 1000000.
        max_marketcap (Union[Unset, int]): The maximum marketcap. Min: 0. Example: 250000000.
        min_volume (Union[Unset, int]): The minimum volume on that contract. Min: 0. Example:
            12300.
        max_volume (Union[Unset, int]): The maximum volume on that contract. Min: 0. Example:
            55600.
        min_multileg_volume_ratio (Union[Unset, int]): The minimum multi leg volume to contract
            volume ratio. Min: 0. Example: 0.3.
        max_multileg_volume_ratio (Union[Unset, int]): The maximum multi leg volume to contract
            volume ratio. Min: 0. Example: 0.5.
        min_floor_volume_ratio (Union[Unset, int]): The minimum floor volume to contract volume
            ratio. Min: 0. Example: 0.2.
        max_floor_volume_ratio (Union[Unset, int]): The maximum floor volume to contract volume
            ratio. Min: 0. Example: 0.45.
        min_perc_change (Union[Unset, int]): The minimum % price change of the contract to the
            previous day. Min: 0. Example: 0.5.
        max_perc_change (Union[Unset, int]): The maximum % price change of the contract to the
            previous day. Min: 0. Example: 0.68.
        min_daily_perc_change (Union[Unset, int]): The minimum intraday price change of the
            contract from open till now.

            If the first traded contract filled at a price of 1.2, and the most recent trade was made
            at 1.5,
            then the intraday price change is: (1.5 - 1.2) / 1.2 = 0.25.
            Min: 0. Example: 0.2.
        max_daily_perc_change (Union[Unset, int]): The maximum intraday price change for the
            contract since market open.

            If the first traded contract filled at a price of 1.2, and the most recent trade was made
            at 1.5,
            then the intraday price change is: (1.5 - 1.2) / 1.2 = 0.25.
            Min: 0. Example: 0.6.
        min_premium (Union[Unset, int]): The minimum premium on that contract. Min: 0. Example:
            12500.5.
        max_premium (Union[Unset, int]): The maximum premium on that contract. Min: 0. Example:
            53100.32.
        min_volume_oi_ratio (Union[Unset, int]): The minimum contract volume to open interest
            ratio. Min: 0. Example: 0.32.
        max_volume_oi_ratio (Union[Unset, int]): The maximum contract volume to open interest
            ratio. Min: 0. Example: 1.58.
        min_open_interest (Union[Unset, int]): The minimum open interest on that contract. Min: 0.
            Example: 12300.
        max_open_interest (Union[Unset, int]): The maximum open interest on that contract. Min: 0.
            Example: 55600.
        min_floor_volume (Union[Unset, int]): The minimum floor volume on that contract. Min: 0.
            Example: 12300.
        max_floor_volume (Union[Unset, int]): The maximum floor volume on that contract. Min: 0.
            Example: 55800.
        vol_greater_oi (Union[Unset, bool]): Only include contracts where the volume is greater
            than the open interest. Example: True.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        order (Union[Unset, ScreenerContractOrderByField]): The field to order by. Example:
            volume.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, OptionContractScreenerResults, str]]
    """

    kwargs = _get_kwargs(
        ticker_symbol=ticker_symbol,
        sectors=sectors,
        min_underlying_price=min_underlying_price,
        max_underlying_price=max_underlying_price,
        is_otm=is_otm,
        min_dte=min_dte,
        max_dte=max_dte,
        min_diff=min_diff,
        max_diff=max_diff,
        min_strike=min_strike,
        max_strike=max_strike,
        type=type,
        expiry_dates=expiry_dates,
        min_marketcap=min_marketcap,
        max_marketcap=max_marketcap,
        min_volume=min_volume,
        max_volume=max_volume,
        min_multileg_volume_ratio=min_multileg_volume_ratio,
        max_multileg_volume_ratio=max_multileg_volume_ratio,
        min_floor_volume_ratio=min_floor_volume_ratio,
        max_floor_volume_ratio=max_floor_volume_ratio,
        min_perc_change=min_perc_change,
        max_perc_change=max_perc_change,
        min_daily_perc_change=min_daily_perc_change,
        max_daily_perc_change=max_daily_perc_change,
        min_premium=min_premium,
        max_premium=max_premium,
        min_volume_oi_ratio=min_volume_oi_ratio,
        max_volume_oi_ratio=max_volume_oi_ratio,
        min_open_interest=min_open_interest,
        max_open_interest=max_open_interest,
        min_floor_volume=min_floor_volume,
        max_floor_volume=max_floor_volume,
        vol_greater_oi=vol_greater_oi,
        issue_types=issue_types,
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
    ticker_symbol: Union[Unset, str] = UNSET,
    sectors: Union[Unset, List[SingleSector]] = UNSET,
    min_underlying_price: Union[Unset, int] = UNSET,
    max_underlying_price: Union[Unset, int] = UNSET,
    is_otm: Union[Unset, bool] = UNSET,
    min_dte: Union[Unset, int] = UNSET,
    max_dte: Union[Unset, int] = UNSET,
    min_diff: Union[Unset, int] = UNSET,
    max_diff: Union[Unset, int] = UNSET,
    min_strike: Union[Unset, int] = UNSET,
    max_strike: Union[Unset, int] = UNSET,
    type: Union[Unset, OptionType] = UNSET,
    expiry_dates: Union[Unset, str] = UNSET,
    min_marketcap: Union[Unset, int] = UNSET,
    max_marketcap: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_multileg_volume_ratio: Union[Unset, int] = UNSET,
    max_multileg_volume_ratio: Union[Unset, int] = UNSET,
    min_floor_volume_ratio: Union[Unset, int] = UNSET,
    max_floor_volume_ratio: Union[Unset, int] = UNSET,
    min_perc_change: Union[Unset, int] = UNSET,
    max_perc_change: Union[Unset, int] = UNSET,
    min_daily_perc_change: Union[Unset, int] = UNSET,
    max_daily_perc_change: Union[Unset, int] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_volume_oi_ratio: Union[Unset, int] = UNSET,
    max_volume_oi_ratio: Union[Unset, int] = UNSET,
    min_open_interest: Union[Unset, int] = UNSET,
    max_open_interest: Union[Unset, int] = UNSET,
    min_floor_volume: Union[Unset, int] = UNSET,
    max_floor_volume: Union[Unset, int] = UNSET,
    vol_greater_oi: Union[Unset, bool] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    order: Union[Unset, ScreenerContractOrderByField] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Optional[Union[ErrorMessage, OptionContractScreenerResults, str]]:
    """Screener for Option Contracts

     A contract screener endpoint to screen the market for contracts by a variety of filter options.

    For an example of what can be build with this endpoint check out the [Hottest
    Contracts](https://unusualwhales.com/hottest-contracts?limit=100&hide_index_etf=true)
    on unusualwhales.

    Args:
        ticker_symbol (Union[Unset, str]): A comma separated list of tickers. To exclude certain
            tickers prefix the first ticker with a `-`. Example: AAPL,INTC.
        sectors (Union[Unset, List[SingleSector]]): An array of 1 or more sectors. Example:
            ['Consumer Cyclical', 'Technology', 'Utilities'].
        min_underlying_price (Union[Unset, int]): The minimum stock price. Min: 0. Example: 5.23.
        max_underlying_price (Union[Unset, int]): The maximum stock price. Min: 0. Example: 10.53.
        is_otm (Union[Unset, bool]): Only include contracts which are currently out of the money.
            Example: True.
        min_dte (Union[Unset, int]): The minimum days to expiry. Min: 0. Example: 1.
        max_dte (Union[Unset, int]): The maximum days to expiry. Min: 0. Example: 3.
        min_diff (Union[Unset, int]): The minimum OTM diff of a contract.
            Given a strike price of 120 and an underlying price of 98
            the diff for a call option would equal to:
            (120 - 98) / 98 = 0.2245

            The diff for a put option would equal to:
            -1 * (120 - 98) / 98 = -0.2245.
             Example: 0.53.
        max_diff (Union[Unset, int]): The minimum OTM diff of a contract.
            Given a strike price of 120 and an underlying price of 98
            the diff for a call option would equal to:
            (120 - 98) / 98 = 0.2245

            The diff for a put option would equal to:
            -1 * (120 - 98) / 98 = -0.2245.
             Example: 1.34.
        min_strike (Union[Unset, int]): The minimum strike. Min: 0. Example: 120.5.
        max_strike (Union[Unset, int]): The minimum strike. Min: 0. Example: 120.5.
        type (Union[Unset, OptionType]): The option type to filter by if specified.
        expiry_dates (Union[Unset, str]): A single expiry date in ISO date format. Example:
            2024-02-02.
        min_marketcap (Union[Unset, int]): The minimum marketcap. Min: 0. Example: 1000000.
        max_marketcap (Union[Unset, int]): The maximum marketcap. Min: 0. Example: 250000000.
        min_volume (Union[Unset, int]): The minimum volume on that contract. Min: 0. Example:
            12300.
        max_volume (Union[Unset, int]): The maximum volume on that contract. Min: 0. Example:
            55600.
        min_multileg_volume_ratio (Union[Unset, int]): The minimum multi leg volume to contract
            volume ratio. Min: 0. Example: 0.3.
        max_multileg_volume_ratio (Union[Unset, int]): The maximum multi leg volume to contract
            volume ratio. Min: 0. Example: 0.5.
        min_floor_volume_ratio (Union[Unset, int]): The minimum floor volume to contract volume
            ratio. Min: 0. Example: 0.2.
        max_floor_volume_ratio (Union[Unset, int]): The maximum floor volume to contract volume
            ratio. Min: 0. Example: 0.45.
        min_perc_change (Union[Unset, int]): The minimum % price change of the contract to the
            previous day. Min: 0. Example: 0.5.
        max_perc_change (Union[Unset, int]): The maximum % price change of the contract to the
            previous day. Min: 0. Example: 0.68.
        min_daily_perc_change (Union[Unset, int]): The minimum intraday price change of the
            contract from open till now.

            If the first traded contract filled at a price of 1.2, and the most recent trade was made
            at 1.5,
            then the intraday price change is: (1.5 - 1.2) / 1.2 = 0.25.
            Min: 0. Example: 0.2.
        max_daily_perc_change (Union[Unset, int]): The maximum intraday price change for the
            contract since market open.

            If the first traded contract filled at a price of 1.2, and the most recent trade was made
            at 1.5,
            then the intraday price change is: (1.5 - 1.2) / 1.2 = 0.25.
            Min: 0. Example: 0.6.
        min_premium (Union[Unset, int]): The minimum premium on that contract. Min: 0. Example:
            12500.5.
        max_premium (Union[Unset, int]): The maximum premium on that contract. Min: 0. Example:
            53100.32.
        min_volume_oi_ratio (Union[Unset, int]): The minimum contract volume to open interest
            ratio. Min: 0. Example: 0.32.
        max_volume_oi_ratio (Union[Unset, int]): The maximum contract volume to open interest
            ratio. Min: 0. Example: 1.58.
        min_open_interest (Union[Unset, int]): The minimum open interest on that contract. Min: 0.
            Example: 12300.
        max_open_interest (Union[Unset, int]): The maximum open interest on that contract. Min: 0.
            Example: 55600.
        min_floor_volume (Union[Unset, int]): The minimum floor volume on that contract. Min: 0.
            Example: 12300.
        max_floor_volume (Union[Unset, int]): The maximum floor volume on that contract. Min: 0.
            Example: 55800.
        vol_greater_oi (Union[Unset, bool]): Only include contracts where the volume is greater
            than the open interest. Example: True.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        order (Union[Unset, ScreenerContractOrderByField]): The field to order by. Example:
            volume.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, OptionContractScreenerResults, str]
    """

    return sync_detailed(
        client=client,
        ticker_symbol=ticker_symbol,
        sectors=sectors,
        min_underlying_price=min_underlying_price,
        max_underlying_price=max_underlying_price,
        is_otm=is_otm,
        min_dte=min_dte,
        max_dte=max_dte,
        min_diff=min_diff,
        max_diff=max_diff,
        min_strike=min_strike,
        max_strike=max_strike,
        type=type,
        expiry_dates=expiry_dates,
        min_marketcap=min_marketcap,
        max_marketcap=max_marketcap,
        min_volume=min_volume,
        max_volume=max_volume,
        min_multileg_volume_ratio=min_multileg_volume_ratio,
        max_multileg_volume_ratio=max_multileg_volume_ratio,
        min_floor_volume_ratio=min_floor_volume_ratio,
        max_floor_volume_ratio=max_floor_volume_ratio,
        min_perc_change=min_perc_change,
        max_perc_change=max_perc_change,
        min_daily_perc_change=min_daily_perc_change,
        max_daily_perc_change=max_daily_perc_change,
        min_premium=min_premium,
        max_premium=max_premium,
        min_volume_oi_ratio=min_volume_oi_ratio,
        max_volume_oi_ratio=max_volume_oi_ratio,
        min_open_interest=min_open_interest,
        max_open_interest=max_open_interest,
        min_floor_volume=min_floor_volume,
        max_floor_volume=max_floor_volume,
        vol_greater_oi=vol_greater_oi,
        issue_types=issue_types,
        order=order,
        order_direction=order_direction,
    ).parsed


async def asyncio_detailed(
    *,
    client: UnusualWhalesClient,
    ticker_symbol: Union[Unset, str] = UNSET,
    sectors: Union[Unset, List[SingleSector]] = UNSET,
    min_underlying_price: Union[Unset, int] = UNSET,
    max_underlying_price: Union[Unset, int] = UNSET,
    is_otm: Union[Unset, bool] = UNSET,
    min_dte: Union[Unset, int] = UNSET,
    max_dte: Union[Unset, int] = UNSET,
    min_diff: Union[Unset, int] = UNSET,
    max_diff: Union[Unset, int] = UNSET,
    min_strike: Union[Unset, int] = UNSET,
    max_strike: Union[Unset, int] = UNSET,
    type: Union[Unset, OptionType] = UNSET,
    expiry_dates: Union[Unset, str] = UNSET,
    min_marketcap: Union[Unset, int] = UNSET,
    max_marketcap: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_multileg_volume_ratio: Union[Unset, int] = UNSET,
    max_multileg_volume_ratio: Union[Unset, int] = UNSET,
    min_floor_volume_ratio: Union[Unset, int] = UNSET,
    max_floor_volume_ratio: Union[Unset, int] = UNSET,
    min_perc_change: Union[Unset, int] = UNSET,
    max_perc_change: Union[Unset, int] = UNSET,
    min_daily_perc_change: Union[Unset, int] = UNSET,
    max_daily_perc_change: Union[Unset, int] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_volume_oi_ratio: Union[Unset, int] = UNSET,
    max_volume_oi_ratio: Union[Unset, int] = UNSET,
    min_open_interest: Union[Unset, int] = UNSET,
    max_open_interest: Union[Unset, int] = UNSET,
    min_floor_volume: Union[Unset, int] = UNSET,
    max_floor_volume: Union[Unset, int] = UNSET,
    vol_greater_oi: Union[Unset, bool] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    order: Union[Unset, ScreenerContractOrderByField] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Response[Union[ErrorMessage, OptionContractScreenerResults, str]]:
    """Screener for Option Contracts

     A contract screener endpoint to screen the market for contracts by a variety of filter options.

    For an example of what can be build with this endpoint check out the [Hottest
    Contracts](https://unusualwhales.com/hottest-contracts?limit=100&hide_index_etf=true)
    on unusualwhales.

    Args:
        ticker_symbol (Union[Unset, str]): A comma separated list of tickers. To exclude certain
            tickers prefix the first ticker with a `-`. Example: AAPL,INTC.
        sectors (Union[Unset, List[SingleSector]]): An array of 1 or more sectors. Example:
            ['Consumer Cyclical', 'Technology', 'Utilities'].
        min_underlying_price (Union[Unset, int]): The minimum stock price. Min: 0. Example: 5.23.
        max_underlying_price (Union[Unset, int]): The maximum stock price. Min: 0. Example: 10.53.
        is_otm (Union[Unset, bool]): Only include contracts which are currently out of the money.
            Example: True.
        min_dte (Union[Unset, int]): The minimum days to expiry. Min: 0. Example: 1.
        max_dte (Union[Unset, int]): The maximum days to expiry. Min: 0. Example: 3.
        min_diff (Union[Unset, int]): The minimum OTM diff of a contract.
            Given a strike price of 120 and an underlying price of 98
            the diff for a call option would equal to:
            (120 - 98) / 98 = 0.2245

            The diff for a put option would equal to:
            -1 * (120 - 98) / 98 = -0.2245.
             Example: 0.53.
        max_diff (Union[Unset, int]): The minimum OTM diff of a contract.
            Given a strike price of 120 and an underlying price of 98
            the diff for a call option would equal to:
            (120 - 98) / 98 = 0.2245

            The diff for a put option would equal to:
            -1 * (120 - 98) / 98 = -0.2245.
             Example: 1.34.
        min_strike (Union[Unset, int]): The minimum strike. Min: 0. Example: 120.5.
        max_strike (Union[Unset, int]): The minimum strike. Min: 0. Example: 120.5.
        type (Union[Unset, OptionType]): The option type to filter by if specified.
        expiry_dates (Union[Unset, str]): A single expiry date in ISO date format. Example:
            2024-02-02.
        min_marketcap (Union[Unset, int]): The minimum marketcap. Min: 0. Example: 1000000.
        max_marketcap (Union[Unset, int]): The maximum marketcap. Min: 0. Example: 250000000.
        min_volume (Union[Unset, int]): The minimum volume on that contract. Min: 0. Example:
            12300.
        max_volume (Union[Unset, int]): The maximum volume on that contract. Min: 0. Example:
            55600.
        min_multileg_volume_ratio (Union[Unset, int]): The minimum multi leg volume to contract
            volume ratio. Min: 0. Example: 0.3.
        max_multileg_volume_ratio (Union[Unset, int]): The maximum multi leg volume to contract
            volume ratio. Min: 0. Example: 0.5.
        min_floor_volume_ratio (Union[Unset, int]): The minimum floor volume to contract volume
            ratio. Min: 0. Example: 0.2.
        max_floor_volume_ratio (Union[Unset, int]): The maximum floor volume to contract volume
            ratio. Min: 0. Example: 0.45.
        min_perc_change (Union[Unset, int]): The minimum % price change of the contract to the
            previous day. Min: 0. Example: 0.5.
        max_perc_change (Union[Unset, int]): The maximum % price change of the contract to the
            previous day. Min: 0. Example: 0.68.
        min_daily_perc_change (Union[Unset, int]): The minimum intraday price change of the
            contract from open till now.

            If the first traded contract filled at a price of 1.2, and the most recent trade was made
            at 1.5,
            then the intraday price change is: (1.5 - 1.2) / 1.2 = 0.25.
            Min: 0. Example: 0.2.
        max_daily_perc_change (Union[Unset, int]): The maximum intraday price change for the
            contract since market open.

            If the first traded contract filled at a price of 1.2, and the most recent trade was made
            at 1.5,
            then the intraday price change is: (1.5 - 1.2) / 1.2 = 0.25.
            Min: 0. Example: 0.6.
        min_premium (Union[Unset, int]): The minimum premium on that contract. Min: 0. Example:
            12500.5.
        max_premium (Union[Unset, int]): The maximum premium on that contract. Min: 0. Example:
            53100.32.
        min_volume_oi_ratio (Union[Unset, int]): The minimum contract volume to open interest
            ratio. Min: 0. Example: 0.32.
        max_volume_oi_ratio (Union[Unset, int]): The maximum contract volume to open interest
            ratio. Min: 0. Example: 1.58.
        min_open_interest (Union[Unset, int]): The minimum open interest on that contract. Min: 0.
            Example: 12300.
        max_open_interest (Union[Unset, int]): The maximum open interest on that contract. Min: 0.
            Example: 55600.
        min_floor_volume (Union[Unset, int]): The minimum floor volume on that contract. Min: 0.
            Example: 12300.
        max_floor_volume (Union[Unset, int]): The maximum floor volume on that contract. Min: 0.
            Example: 55800.
        vol_greater_oi (Union[Unset, bool]): Only include contracts where the volume is greater
            than the open interest. Example: True.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        order (Union[Unset, ScreenerContractOrderByField]): The field to order by. Example:
            volume.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, OptionContractScreenerResults, str]]
    """

    kwargs = _get_kwargs(
        ticker_symbol=ticker_symbol,
        sectors=sectors,
        min_underlying_price=min_underlying_price,
        max_underlying_price=max_underlying_price,
        is_otm=is_otm,
        min_dte=min_dte,
        max_dte=max_dte,
        min_diff=min_diff,
        max_diff=max_diff,
        min_strike=min_strike,
        max_strike=max_strike,
        type=type,
        expiry_dates=expiry_dates,
        min_marketcap=min_marketcap,
        max_marketcap=max_marketcap,
        min_volume=min_volume,
        max_volume=max_volume,
        min_multileg_volume_ratio=min_multileg_volume_ratio,
        max_multileg_volume_ratio=max_multileg_volume_ratio,
        min_floor_volume_ratio=min_floor_volume_ratio,
        max_floor_volume_ratio=max_floor_volume_ratio,
        min_perc_change=min_perc_change,
        max_perc_change=max_perc_change,
        min_daily_perc_change=min_daily_perc_change,
        max_daily_perc_change=max_daily_perc_change,
        min_premium=min_premium,
        max_premium=max_premium,
        min_volume_oi_ratio=min_volume_oi_ratio,
        max_volume_oi_ratio=max_volume_oi_ratio,
        min_open_interest=min_open_interest,
        max_open_interest=max_open_interest,
        min_floor_volume=min_floor_volume,
        max_floor_volume=max_floor_volume,
        vol_greater_oi=vol_greater_oi,
        issue_types=issue_types,
        order=order,
        order_direction=order_direction,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: UnusualWhalesClient,
    ticker_symbol: Union[Unset, str] = UNSET,
    sectors: Union[Unset, List[SingleSector]] = UNSET,
    min_underlying_price: Union[Unset, int] = UNSET,
    max_underlying_price: Union[Unset, int] = UNSET,
    is_otm: Union[Unset, bool] = UNSET,
    min_dte: Union[Unset, int] = UNSET,
    max_dte: Union[Unset, int] = UNSET,
    min_diff: Union[Unset, int] = UNSET,
    max_diff: Union[Unset, int] = UNSET,
    min_strike: Union[Unset, int] = UNSET,
    max_strike: Union[Unset, int] = UNSET,
    type: Union[Unset, OptionType] = UNSET,
    expiry_dates: Union[Unset, str] = UNSET,
    min_marketcap: Union[Unset, int] = UNSET,
    max_marketcap: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_multileg_volume_ratio: Union[Unset, int] = UNSET,
    max_multileg_volume_ratio: Union[Unset, int] = UNSET,
    min_floor_volume_ratio: Union[Unset, int] = UNSET,
    max_floor_volume_ratio: Union[Unset, int] = UNSET,
    min_perc_change: Union[Unset, int] = UNSET,
    max_perc_change: Union[Unset, int] = UNSET,
    min_daily_perc_change: Union[Unset, int] = UNSET,
    max_daily_perc_change: Union[Unset, int] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_volume_oi_ratio: Union[Unset, int] = UNSET,
    max_volume_oi_ratio: Union[Unset, int] = UNSET,
    min_open_interest: Union[Unset, int] = UNSET,
    max_open_interest: Union[Unset, int] = UNSET,
    min_floor_volume: Union[Unset, int] = UNSET,
    max_floor_volume: Union[Unset, int] = UNSET,
    vol_greater_oi: Union[Unset, bool] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    order: Union[Unset, ScreenerContractOrderByField] = UNSET,
    order_direction: Union[Unset, OrderDirection] = UNSET,
) -> Optional[Union[ErrorMessage, OptionContractScreenerResults, str]]:
    """Screener for Option Contracts

     A contract screener endpoint to screen the market for contracts by a variety of filter options.

    For an example of what can be build with this endpoint check out the [Hottest
    Contracts](https://unusualwhales.com/hottest-contracts?limit=100&hide_index_etf=true)
    on unusualwhales.

    Args:
        ticker_symbol (Union[Unset, str]): A comma separated list of tickers. To exclude certain
            tickers prefix the first ticker with a `-`. Example: AAPL,INTC.
        sectors (Union[Unset, List[SingleSector]]): An array of 1 or more sectors. Example:
            ['Consumer Cyclical', 'Technology', 'Utilities'].
        min_underlying_price (Union[Unset, int]): The minimum stock price. Min: 0. Example: 5.23.
        max_underlying_price (Union[Unset, int]): The maximum stock price. Min: 0. Example: 10.53.
        is_otm (Union[Unset, bool]): Only include contracts which are currently out of the money.
            Example: True.
        min_dte (Union[Unset, int]): The minimum days to expiry. Min: 0. Example: 1.
        max_dte (Union[Unset, int]): The maximum days to expiry. Min: 0. Example: 3.
        min_diff (Union[Unset, int]): The minimum OTM diff of a contract.
            Given a strike price of 120 and an underlying price of 98
            the diff for a call option would equal to:
            (120 - 98) / 98 = 0.2245

            The diff for a put option would equal to:
            -1 * (120 - 98) / 98 = -0.2245.
             Example: 0.53.
        max_diff (Union[Unset, int]): The minimum OTM diff of a contract.
            Given a strike price of 120 and an underlying price of 98
            the diff for a call option would equal to:
            (120 - 98) / 98 = 0.2245

            The diff for a put option would equal to:
            -1 * (120 - 98) / 98 = -0.2245.
             Example: 1.34.
        min_strike (Union[Unset, int]): The minimum strike. Min: 0. Example: 120.5.
        max_strike (Union[Unset, int]): The minimum strike. Min: 0. Example: 120.5.
        type (Union[Unset, OptionType]): The option type to filter by if specified.
        expiry_dates (Union[Unset, str]): A single expiry date in ISO date format. Example:
            2024-02-02.
        min_marketcap (Union[Unset, int]): The minimum marketcap. Min: 0. Example: 1000000.
        max_marketcap (Union[Unset, int]): The maximum marketcap. Min: 0. Example: 250000000.
        min_volume (Union[Unset, int]): The minimum volume on that contract. Min: 0. Example:
            12300.
        max_volume (Union[Unset, int]): The maximum volume on that contract. Min: 0. Example:
            55600.
        min_multileg_volume_ratio (Union[Unset, int]): The minimum multi leg volume to contract
            volume ratio. Min: 0. Example: 0.3.
        max_multileg_volume_ratio (Union[Unset, int]): The maximum multi leg volume to contract
            volume ratio. Min: 0. Example: 0.5.
        min_floor_volume_ratio (Union[Unset, int]): The minimum floor volume to contract volume
            ratio. Min: 0. Example: 0.2.
        max_floor_volume_ratio (Union[Unset, int]): The maximum floor volume to contract volume
            ratio. Min: 0. Example: 0.45.
        min_perc_change (Union[Unset, int]): The minimum % price change of the contract to the
            previous day. Min: 0. Example: 0.5.
        max_perc_change (Union[Unset, int]): The maximum % price change of the contract to the
            previous day. Min: 0. Example: 0.68.
        min_daily_perc_change (Union[Unset, int]): The minimum intraday price change of the
            contract from open till now.

            If the first traded contract filled at a price of 1.2, and the most recent trade was made
            at 1.5,
            then the intraday price change is: (1.5 - 1.2) / 1.2 = 0.25.
            Min: 0. Example: 0.2.
        max_daily_perc_change (Union[Unset, int]): The maximum intraday price change for the
            contract since market open.

            If the first traded contract filled at a price of 1.2, and the most recent trade was made
            at 1.5,
            then the intraday price change is: (1.5 - 1.2) / 1.2 = 0.25.
            Min: 0. Example: 0.6.
        min_premium (Union[Unset, int]): The minimum premium on that contract. Min: 0. Example:
            12500.5.
        max_premium (Union[Unset, int]): The maximum premium on that contract. Min: 0. Example:
            53100.32.
        min_volume_oi_ratio (Union[Unset, int]): The minimum contract volume to open interest
            ratio. Min: 0. Example: 0.32.
        max_volume_oi_ratio (Union[Unset, int]): The maximum contract volume to open interest
            ratio. Min: 0. Example: 1.58.
        min_open_interest (Union[Unset, int]): The minimum open interest on that contract. Min: 0.
            Example: 12300.
        max_open_interest (Union[Unset, int]): The maximum open interest on that contract. Min: 0.
            Example: 55600.
        min_floor_volume (Union[Unset, int]): The minimum floor volume on that contract. Min: 0.
            Example: 12300.
        max_floor_volume (Union[Unset, int]): The maximum floor volume on that contract. Min: 0.
            Example: 55800.
        vol_greater_oi (Union[Unset, bool]): Only include contracts where the volume is greater
            than the open interest. Example: True.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        order (Union[Unset, ScreenerContractOrderByField]): The field to order by. Example:
            volume.
        order_direction (Union[Unset, OrderDirection]): Whether to sort descending or ascending.
            Descending by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, OptionContractScreenerResults, str]
    """

    return (
        await asyncio_detailed(
            client=client,
            ticker_symbol=ticker_symbol,
            sectors=sectors,
            min_underlying_price=min_underlying_price,
            max_underlying_price=max_underlying_price,
            is_otm=is_otm,
            min_dte=min_dte,
            max_dte=max_dte,
            min_diff=min_diff,
            max_diff=max_diff,
            min_strike=min_strike,
            max_strike=max_strike,
            type=type,
            expiry_dates=expiry_dates,
            min_marketcap=min_marketcap,
            max_marketcap=max_marketcap,
            min_volume=min_volume,
            max_volume=max_volume,
            min_multileg_volume_ratio=min_multileg_volume_ratio,
            max_multileg_volume_ratio=max_multileg_volume_ratio,
            min_floor_volume_ratio=min_floor_volume_ratio,
            max_floor_volume_ratio=max_floor_volume_ratio,
            min_perc_change=min_perc_change,
            max_perc_change=max_perc_change,
            min_daily_perc_change=min_daily_perc_change,
            max_daily_perc_change=max_daily_perc_change,
            min_premium=min_premium,
            max_premium=max_premium,
            min_volume_oi_ratio=min_volume_oi_ratio,
            max_volume_oi_ratio=max_volume_oi_ratio,
            min_open_interest=min_open_interest,
            max_open_interest=max_open_interest,
            min_floor_volume=min_floor_volume,
            max_floor_volume=max_floor_volume,
            vol_greater_oi=vol_greater_oi,
            issue_types=issue_types,
            order=order,
            order_direction=order_direction,
        )
    ).parsed
