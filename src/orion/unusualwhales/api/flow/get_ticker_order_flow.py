from http import HTTPStatus
from typing import Any, Dict, List, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.flow_alert import FlowAlert
from ...models.flow_alert_rule import FlowAlertRule
from ...models.single_issue_type import SingleIssueType
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    ticker_symbol: Union[Unset, str] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_size: Union[Unset, int] = UNSET,
    max_size: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_open_interest: Union[Unset, int] = UNSET,
    max_open_interest: Union[Unset, int] = UNSET,
    all_opening: Union[Unset, bool] = UNSET,
    is_floor: Union[Unset, bool] = UNSET,
    is_sweep: Union[Unset, bool] = UNSET,
    is_call: Union[Unset, bool] = UNSET,
    is_put: Union[Unset, bool] = UNSET,
    rule_name: Union[Unset, List[FlowAlertRule]] = UNSET,
    min_diff: Union[Unset, int] = UNSET,
    max_diff: Union[Unset, int] = UNSET,
    min_volume_oi_ratio: Union[Unset, int] = UNSET,
    max_volume_oi_ratio: Union[Unset, int] = UNSET,
    is_otm: Union[Unset, bool] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    min_dte: Union[Unset, int] = UNSET,
    max_dte: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["ticker_symbol"] = ticker_symbol

    params["min_premium"] = min_premium

    params["max_premium"] = max_premium

    params["min_size"] = min_size

    params["max_size"] = max_size

    params["min_volume"] = min_volume

    params["max_volume"] = max_volume

    params["min_open_interest"] = min_open_interest

    params["max_open_interest"] = max_open_interest

    params["all_opening"] = all_opening

    params["is_floor"] = is_floor

    params["is_sweep"] = is_sweep

    params["is_call"] = is_call

    params["is_put"] = is_put

    json_rule_name: Union[Unset, List[str]] = UNSET
    if not isinstance(rule_name, Unset):
        json_rule_name = []
        for componentsschemas_rule_name_item_data in rule_name:
            componentsschemas_rule_name_item = componentsschemas_rule_name_item_data.value
            json_rule_name.append(componentsschemas_rule_name_item)

    params["rule_name[]"] = json_rule_name

    params["min_diff"] = min_diff

    params["max_diff"] = max_diff

    params["min_volume_oi_ratio"] = min_volume_oi_ratio

    params["max_volume_oi_ratio"] = max_volume_oi_ratio

    params["is_otm"] = is_otm

    json_issue_types: Union[Unset, List[str]] = UNSET
    if not isinstance(issue_types, Unset):
        json_issue_types = []
        for componentsschemas_issue_types_item_data in issue_types:
            componentsschemas_issue_types_item = componentsschemas_issue_types_item_data.value
            json_issue_types.append(componentsschemas_issue_types_item)

    params["issue_types[]"] = json_issue_types

    params["min_dte"] = min_dte

    params["max_dte"] = max_dte

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": "/api/option-trades/flow-alerts",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[ErrorMessage, FlowAlert, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = FlowAlert.from_dict(response.json())

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
) -> Response[Union[ErrorMessage, FlowAlert, str]]:
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
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_size: Union[Unset, int] = UNSET,
    max_size: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_open_interest: Union[Unset, int] = UNSET,
    max_open_interest: Union[Unset, int] = UNSET,
    all_opening: Union[Unset, bool] = UNSET,
    is_floor: Union[Unset, bool] = UNSET,
    is_sweep: Union[Unset, bool] = UNSET,
    is_call: Union[Unset, bool] = UNSET,
    is_put: Union[Unset, bool] = UNSET,
    rule_name: Union[Unset, List[FlowAlertRule]] = UNSET,
    min_diff: Union[Unset, int] = UNSET,
    max_diff: Union[Unset, int] = UNSET,
    min_volume_oi_ratio: Union[Unset, int] = UNSET,
    max_volume_oi_ratio: Union[Unset, int] = UNSET,
    is_otm: Union[Unset, bool] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    min_dte: Union[Unset, int] = UNSET,
    max_dte: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
) -> Response[Union[ErrorMessage, FlowAlert, str]]:
    """Search Option Trades With Filters

     Search option trades based on a variety of parameters

    Args:
        ticker_symbol (Union[Unset, str]): A comma separated list of tickers. To exclude certain
            tickers prefix the first ticker with a `-`. Example: AAPL,INTC.
        min_premium (Union[Unset, int]): The minimum premium on that alert. Min: 0. Example:
            12500.5.
        max_premium (Union[Unset, int]): The maximum premium on that alert. Min: 0. Example:
            12500.5.
        min_size (Union[Unset, int]): The minimum size on that alert. Size is defined as the sum
            of the sizes of all transactions that make up the alert. Min: 0. Example: 125.
        max_size (Union[Unset, int]): The maximum size on that alert. Min: 0. Example: 125.
        min_volume (Union[Unset, int]): The minimum volume on that alert's contract at the time of
            the alert. Min: 0. Example: 125.
        max_volume (Union[Unset, int]): The maximum volume on that alert's contract at the time of
            the alert. Min: 0. Example: 125.
        min_open_interest (Union[Unset, int]): The minimum open interest on that alert's contract
            at the time of the alert. Min: 0. Example: 125.
        max_open_interest (Union[Unset, int]): The maximum open interest on that alert's contract
            at the time of the alert. Min: 0. Example: 125.
        all_opening (Union[Unset, bool]): Boolean flag whether all transactions are opening
            transactions based on OI, Size & Volume. Example: True.
        is_floor (Union[Unset, bool]): Boolean flag whether a transaction is from the floor.
            Example: True.
        is_sweep (Union[Unset, bool]): Boolean flag whether a transaction is a intermarket sweep.
            Example: True.
        is_call (Union[Unset, bool]): Boolean flag whether a transaction is a call. Example: True.
        is_put (Union[Unset, bool]): Boolean flag whether a transaction is a put. Example: True.
        rule_name (Union[Unset, List[FlowAlertRule]]): An array of 1 or more rule name. Example:
            ['RepeatedHits', 'RepeatedHitsAscendingFill'].
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
        min_volume_oi_ratio (Union[Unset, int]): The minimum contract volume to open interest
            ratio. Min: 0. Example: 0.32.
        max_volume_oi_ratio (Union[Unset, int]): The maximum contract volume to open interest
            ratio. Min: 0. Example: 1.58.
        is_otm (Union[Unset, bool]): Only include contracts which are currently out of the money.
            Example: True.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        min_dte (Union[Unset, int]): The minimum days to expiry. Min: 0. Example: 1.
        max_dte (Union[Unset, int]): The maximum days to expiry. Min: 0. Example: 3.
        limit (Union[Unset, int]): How many items to return. Default: 100. Max: 200. Min: 1.
            Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, FlowAlert, str]]
    """

    kwargs = _get_kwargs(
        ticker_symbol=ticker_symbol,
        min_premium=min_premium,
        max_premium=max_premium,
        min_size=min_size,
        max_size=max_size,
        min_volume=min_volume,
        max_volume=max_volume,
        min_open_interest=min_open_interest,
        max_open_interest=max_open_interest,
        all_opening=all_opening,
        is_floor=is_floor,
        is_sweep=is_sweep,
        is_call=is_call,
        is_put=is_put,
        rule_name=rule_name,
        min_diff=min_diff,
        max_diff=max_diff,
        min_volume_oi_ratio=min_volume_oi_ratio,
        max_volume_oi_ratio=max_volume_oi_ratio,
        is_otm=is_otm,
        issue_types=issue_types,
        min_dte=min_dte,
        max_dte=max_dte,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: UnusualWhalesClient,
    ticker_symbol: Union[Unset, str] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_size: Union[Unset, int] = UNSET,
    max_size: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_open_interest: Union[Unset, int] = UNSET,
    max_open_interest: Union[Unset, int] = UNSET,
    all_opening: Union[Unset, bool] = UNSET,
    is_floor: Union[Unset, bool] = UNSET,
    is_sweep: Union[Unset, bool] = UNSET,
    is_call: Union[Unset, bool] = UNSET,
    is_put: Union[Unset, bool] = UNSET,
    rule_name: Union[Unset, List[FlowAlertRule]] = UNSET,
    min_diff: Union[Unset, int] = UNSET,
    max_diff: Union[Unset, int] = UNSET,
    min_volume_oi_ratio: Union[Unset, int] = UNSET,
    max_volume_oi_ratio: Union[Unset, int] = UNSET,
    is_otm: Union[Unset, bool] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    min_dte: Union[Unset, int] = UNSET,
    max_dte: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
) -> Optional[Union[ErrorMessage, FlowAlert, str]]:
    """Search Option Trades With Filters

     Search option trades based on a variety of parameters

    Args:
        ticker_symbol (Union[Unset, str]): A comma separated list of tickers. To exclude certain
            tickers prefix the first ticker with a `-`. Example: AAPL,INTC.
        min_premium (Union[Unset, int]): The minimum premium on that alert. Min: 0. Example:
            12500.5.
        max_premium (Union[Unset, int]): The maximum premium on that alert. Min: 0. Example:
            12500.5.
        min_size (Union[Unset, int]): The minimum size on that alert. Size is defined as the sum
            of the sizes of all transactions that make up the alert. Min: 0. Example: 125.
        max_size (Union[Unset, int]): The maximum size on that alert. Min: 0. Example: 125.
        min_volume (Union[Unset, int]): The minimum volume on that alert's contract at the time of
            the alert. Min: 0. Example: 125.
        max_volume (Union[Unset, int]): The maximum volume on that alert's contract at the time of
            the alert. Min: 0. Example: 125.
        min_open_interest (Union[Unset, int]): The minimum open interest on that alert's contract
            at the time of the alert. Min: 0. Example: 125.
        max_open_interest (Union[Unset, int]): The maximum open interest on that alert's contract
            at the time of the alert. Min: 0. Example: 125.
        all_opening (Union[Unset, bool]): Boolean flag whether all transactions are opening
            transactions based on OI, Size & Volume. Example: True.
        is_floor (Union[Unset, bool]): Boolean flag whether a transaction is from the floor.
            Example: True.
        is_sweep (Union[Unset, bool]): Boolean flag whether a transaction is a intermarket sweep.
            Example: True.
        is_call (Union[Unset, bool]): Boolean flag whether a transaction is a call. Example: True.
        is_put (Union[Unset, bool]): Boolean flag whether a transaction is a put. Example: True.
        rule_name (Union[Unset, List[FlowAlertRule]]): An array of 1 or more rule name. Example:
            ['RepeatedHits', 'RepeatedHitsAscendingFill'].
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
        min_volume_oi_ratio (Union[Unset, int]): The minimum contract volume to open interest
            ratio. Min: 0. Example: 0.32.
        max_volume_oi_ratio (Union[Unset, int]): The maximum contract volume to open interest
            ratio. Min: 0. Example: 1.58.
        is_otm (Union[Unset, bool]): Only include contracts which are currently out of the money.
            Example: True.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        min_dte (Union[Unset, int]): The minimum days to expiry. Min: 0. Example: 1.
        max_dte (Union[Unset, int]): The maximum days to expiry. Min: 0. Example: 3.
        limit (Union[Unset, int]): How many items to return. Default: 100. Max: 200. Min: 1.
            Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, FlowAlert, str]
    """

    return sync_detailed(
        client=client,
        ticker_symbol=ticker_symbol,
        min_premium=min_premium,
        max_premium=max_premium,
        min_size=min_size,
        max_size=max_size,
        min_volume=min_volume,
        max_volume=max_volume,
        min_open_interest=min_open_interest,
        max_open_interest=max_open_interest,
        all_opening=all_opening,
        is_floor=is_floor,
        is_sweep=is_sweep,
        is_call=is_call,
        is_put=is_put,
        rule_name=rule_name,
        min_diff=min_diff,
        max_diff=max_diff,
        min_volume_oi_ratio=min_volume_oi_ratio,
        max_volume_oi_ratio=max_volume_oi_ratio,
        is_otm=is_otm,
        issue_types=issue_types,
        min_dte=min_dte,
        max_dte=max_dte,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: UnusualWhalesClient,
    ticker_symbol: Union[Unset, str] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_size: Union[Unset, int] = UNSET,
    max_size: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_open_interest: Union[Unset, int] = UNSET,
    max_open_interest: Union[Unset, int] = UNSET,
    all_opening: Union[Unset, bool] = UNSET,
    is_floor: Union[Unset, bool] = UNSET,
    is_sweep: Union[Unset, bool] = UNSET,
    is_call: Union[Unset, bool] = UNSET,
    is_put: Union[Unset, bool] = UNSET,
    rule_name: Union[Unset, List[FlowAlertRule]] = UNSET,
    min_diff: Union[Unset, int] = UNSET,
    max_diff: Union[Unset, int] = UNSET,
    min_volume_oi_ratio: Union[Unset, int] = UNSET,
    max_volume_oi_ratio: Union[Unset, int] = UNSET,
    is_otm: Union[Unset, bool] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    min_dte: Union[Unset, int] = UNSET,
    max_dte: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
) -> Response[Union[ErrorMessage, FlowAlert, str]]:
    """Search Option Trades With Filters

     Search option trades based on a variety of parameters

    Args:
        ticker_symbol (Union[Unset, str]): A comma separated list of tickers. To exclude certain
            tickers prefix the first ticker with a `-`. Example: AAPL,INTC.
        min_premium (Union[Unset, int]): The minimum premium on that alert. Min: 0. Example:
            12500.5.
        max_premium (Union[Unset, int]): The maximum premium on that alert. Min: 0. Example:
            12500.5.
        min_size (Union[Unset, int]): The minimum size on that alert. Size is defined as the sum
            of the sizes of all transactions that make up the alert. Min: 0. Example: 125.
        max_size (Union[Unset, int]): The maximum size on that alert. Min: 0. Example: 125.
        min_volume (Union[Unset, int]): The minimum volume on that alert's contract at the time of
            the alert. Min: 0. Example: 125.
        max_volume (Union[Unset, int]): The maximum volume on that alert's contract at the time of
            the alert. Min: 0. Example: 125.
        min_open_interest (Union[Unset, int]): The minimum open interest on that alert's contract
            at the time of the alert. Min: 0. Example: 125.
        max_open_interest (Union[Unset, int]): The maximum open interest on that alert's contract
            at the time of the alert. Min: 0. Example: 125.
        all_opening (Union[Unset, bool]): Boolean flag whether all transactions are opening
            transactions based on OI, Size & Volume. Example: True.
        is_floor (Union[Unset, bool]): Boolean flag whether a transaction is from the floor.
            Example: True.
        is_sweep (Union[Unset, bool]): Boolean flag whether a transaction is a intermarket sweep.
            Example: True.
        is_call (Union[Unset, bool]): Boolean flag whether a transaction is a call. Example: True.
        is_put (Union[Unset, bool]): Boolean flag whether a transaction is a put. Example: True.
        rule_name (Union[Unset, List[FlowAlertRule]]): An array of 1 or more rule name. Example:
            ['RepeatedHits', 'RepeatedHitsAscendingFill'].
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
        min_volume_oi_ratio (Union[Unset, int]): The minimum contract volume to open interest
            ratio. Min: 0. Example: 0.32.
        max_volume_oi_ratio (Union[Unset, int]): The maximum contract volume to open interest
            ratio. Min: 0. Example: 1.58.
        is_otm (Union[Unset, bool]): Only include contracts which are currently out of the money.
            Example: True.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        min_dte (Union[Unset, int]): The minimum days to expiry. Min: 0. Example: 1.
        max_dte (Union[Unset, int]): The maximum days to expiry. Min: 0. Example: 3.
        limit (Union[Unset, int]): How many items to return. Default: 100. Max: 200. Min: 1.
            Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, FlowAlert, str]]
    """

    kwargs = _get_kwargs(
        ticker_symbol=ticker_symbol,
        min_premium=min_premium,
        max_premium=max_premium,
        min_size=min_size,
        max_size=max_size,
        min_volume=min_volume,
        max_volume=max_volume,
        min_open_interest=min_open_interest,
        max_open_interest=max_open_interest,
        all_opening=all_opening,
        is_floor=is_floor,
        is_sweep=is_sweep,
        is_call=is_call,
        is_put=is_put,
        rule_name=rule_name,
        min_diff=min_diff,
        max_diff=max_diff,
        min_volume_oi_ratio=min_volume_oi_ratio,
        max_volume_oi_ratio=max_volume_oi_ratio,
        is_otm=is_otm,
        issue_types=issue_types,
        min_dte=min_dte,
        max_dte=max_dte,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: UnusualWhalesClient,
    ticker_symbol: Union[Unset, str] = UNSET,
    min_premium: Union[Unset, int] = UNSET,
    max_premium: Union[Unset, int] = UNSET,
    min_size: Union[Unset, int] = UNSET,
    max_size: Union[Unset, int] = UNSET,
    min_volume: Union[Unset, int] = UNSET,
    max_volume: Union[Unset, int] = UNSET,
    min_open_interest: Union[Unset, int] = UNSET,
    max_open_interest: Union[Unset, int] = UNSET,
    all_opening: Union[Unset, bool] = UNSET,
    is_floor: Union[Unset, bool] = UNSET,
    is_sweep: Union[Unset, bool] = UNSET,
    is_call: Union[Unset, bool] = UNSET,
    is_put: Union[Unset, bool] = UNSET,
    rule_name: Union[Unset, List[FlowAlertRule]] = UNSET,
    min_diff: Union[Unset, int] = UNSET,
    max_diff: Union[Unset, int] = UNSET,
    min_volume_oi_ratio: Union[Unset, int] = UNSET,
    max_volume_oi_ratio: Union[Unset, int] = UNSET,
    is_otm: Union[Unset, bool] = UNSET,
    issue_types: Union[Unset, List[SingleIssueType]] = UNSET,
    min_dte: Union[Unset, int] = UNSET,
    max_dte: Union[Unset, int] = UNSET,
    limit: Union[Unset, int] = UNSET,
) -> Optional[Union[ErrorMessage, FlowAlert, str]]:
    """Search Option Trades With Filters

     Search option trades based on a variety of parameters

    Args:
        ticker_symbol (Union[Unset, str]): A comma separated list of tickers. To exclude certain
            tickers prefix the first ticker with a `-`. Example: AAPL,INTC.
        min_premium (Union[Unset, int]): The minimum premium on that alert. Min: 0. Example:
            12500.5.
        max_premium (Union[Unset, int]): The maximum premium on that alert. Min: 0. Example:
            12500.5.
        min_size (Union[Unset, int]): The minimum size on that alert. Size is defined as the sum
            of the sizes of all transactions that make up the alert. Min: 0. Example: 125.
        max_size (Union[Unset, int]): The maximum size on that alert. Min: 0. Example: 125.
        min_volume (Union[Unset, int]): The minimum volume on that alert's contract at the time of
            the alert. Min: 0. Example: 125.
        max_volume (Union[Unset, int]): The maximum volume on that alert's contract at the time of
            the alert. Min: 0. Example: 125.
        min_open_interest (Union[Unset, int]): The minimum open interest on that alert's contract
            at the time of the alert. Min: 0. Example: 125.
        max_open_interest (Union[Unset, int]): The maximum open interest on that alert's contract
            at the time of the alert. Min: 0. Example: 125.
        all_opening (Union[Unset, bool]): Boolean flag whether all transactions are opening
            transactions based on OI, Size & Volume. Example: True.
        is_floor (Union[Unset, bool]): Boolean flag whether a transaction is from the floor.
            Example: True.
        is_sweep (Union[Unset, bool]): Boolean flag whether a transaction is a intermarket sweep.
            Example: True.
        is_call (Union[Unset, bool]): Boolean flag whether a transaction is a call. Example: True.
        is_put (Union[Unset, bool]): Boolean flag whether a transaction is a put. Example: True.
        rule_name (Union[Unset, List[FlowAlertRule]]): An array of 1 or more rule name. Example:
            ['RepeatedHits', 'RepeatedHitsAscendingFill'].
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
        min_volume_oi_ratio (Union[Unset, int]): The minimum contract volume to open interest
            ratio. Min: 0. Example: 0.32.
        max_volume_oi_ratio (Union[Unset, int]): The maximum contract volume to open interest
            ratio. Min: 0. Example: 1.58.
        is_otm (Union[Unset, bool]): Only include contracts which are currently out of the money.
            Example: True.
        issue_types (Union[Unset, List[SingleIssueType]]): An array of 1 or more issue types.
            Example: ['Common Stock', 'Index'].
        min_dte (Union[Unset, int]): The minimum days to expiry. Min: 0. Example: 1.
        max_dte (Union[Unset, int]): The maximum days to expiry. Min: 0. Example: 3.
        limit (Union[Unset, int]): How many items to return. Default: 100. Max: 200. Min: 1.
            Example: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, FlowAlert, str]
    """

    return (
        await asyncio_detailed(
            client=client,
            ticker_symbol=ticker_symbol,
            min_premium=min_premium,
            max_premium=max_premium,
            min_size=min_size,
            max_size=max_size,
            min_volume=min_volume,
            max_volume=max_volume,
            min_open_interest=min_open_interest,
            max_open_interest=max_open_interest,
            all_opening=all_opening,
            is_floor=is_floor,
            is_sweep=is_sweep,
            is_call=is_call,
            is_put=is_put,
            rule_name=rule_name,
            min_diff=min_diff,
            max_diff=max_diff,
            min_volume_oi_ratio=min_volume_oi_ratio,
            max_volume_oi_ratio=max_volume_oi_ratio,
            is_otm=is_otm,
            issue_types=issue_types,
            min_dte=min_dte,
            max_dte=max_dte,
            limit=limit,
        )
    ).parsed
