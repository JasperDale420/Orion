from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.daily_market_tide_response import DailyMarketTideResponse
from ...models.error_message import ErrorMessage
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    date: Union[Unset, str] = UNSET,
    otm_only: Union[Unset, bool] = UNSET,
    interval_5m: Union[Unset, bool] = UNSET,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["date"] = date

    params["otm_only"] = otm_only

    params["interval_5m"] = interval_5m

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": "/api/market/market-tide",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[DailyMarketTideResponse, ErrorMessage, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = DailyMarketTideResponse.from_dict(response.json())

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
) -> Response[Union[DailyMarketTideResponse, ErrorMessage, str]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: UnusualWhalesClient,
    date: Union[Unset, str] = UNSET,
    otm_only: Union[Unset, bool] = UNSET,
    interval_5m: Union[Unset, bool] = UNSET,
) -> Response[Union[DailyMarketTideResponse, ErrorMessage, str]]:
    """Returns The Unusual Whales Market Tide Data

     Market Tide is a proprietary tool that can be viewed from the Market Overview page. The Market Tide
    chart provides real time data based on a proprietary formula that examines market wide options
    activity and filters out 'noise'.

    Date must be the current or a past date. If no date is given, returns data for the current/last
    market day.

    Per default data are returned in 1 minute intervals. Use `interval_5m=true` to have this return data
    in 5 minute intervals instead.


    for example
    - $15,000 in calls transacted at the ask has the effect of increasing the daily net call premium by
    $15,000.
    - $10,000 in calls transacted at the bid has the effect of decreasing the daily net call premium by
    $10,000.

    The resulting net premium from both of these trades would be $5000 (+ $15,000 - $10,000).

    Transactions taking place at the mid are not accounted for.

    In theory:

    The sentiment in the options market becomes increasingly bullish if:
    1. The aggregated CALL PREMIUM is increasing at a faster rate.
    2. The aggregated PUT PREMIUM is decreasing at a faster rate.

    The sentiment in the options market becomes increasingly bearish if:
    1. The aggregated CALL PREMIUM is decreasing at a faster rate.
    2. The aggregated PUT PREMIUM is increasing at a faster rate.

    ----
    This can be used to build a market overview such as:

    ![market tide](https://i.imgur.com/tuwTCDc.png)

    Args:
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        otm_only (Union[Unset, bool]): Only include out of the money transactions. Example: True.
        interval_5m (Union[Unset, bool]): Return data in 5 minutes intervals.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[DailyMarketTideResponse, ErrorMessage, str]]
    """

    kwargs = _get_kwargs(
        date=date,
        otm_only=otm_only,
        interval_5m=interval_5m,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: UnusualWhalesClient,
    date: Union[Unset, str] = UNSET,
    otm_only: Union[Unset, bool] = UNSET,
    interval_5m: Union[Unset, bool] = UNSET,
) -> Optional[Union[DailyMarketTideResponse, ErrorMessage, str]]:
    """Returns The Unusual Whales Market Tide Data

     Market Tide is a proprietary tool that can be viewed from the Market Overview page. The Market Tide
    chart provides real time data based on a proprietary formula that examines market wide options
    activity and filters out 'noise'.

    Date must be the current or a past date. If no date is given, returns data for the current/last
    market day.

    Per default data are returned in 1 minute intervals. Use `interval_5m=true` to have this return data
    in 5 minute intervals instead.


    for example
    - $15,000 in calls transacted at the ask has the effect of increasing the daily net call premium by
    $15,000.
    - $10,000 in calls transacted at the bid has the effect of decreasing the daily net call premium by
    $10,000.

    The resulting net premium from both of these trades would be $5000 (+ $15,000 - $10,000).

    Transactions taking place at the mid are not accounted for.

    In theory:

    The sentiment in the options market becomes increasingly bullish if:
    1. The aggregated CALL PREMIUM is increasing at a faster rate.
    2. The aggregated PUT PREMIUM is decreasing at a faster rate.

    The sentiment in the options market becomes increasingly bearish if:
    1. The aggregated CALL PREMIUM is decreasing at a faster rate.
    2. The aggregated PUT PREMIUM is increasing at a faster rate.

    ----
    This can be used to build a market overview such as:

    ![market tide](https://i.imgur.com/tuwTCDc.png)

    Args:
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        otm_only (Union[Unset, bool]): Only include out of the money transactions. Example: True.
        interval_5m (Union[Unset, bool]): Return data in 5 minutes intervals.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[DailyMarketTideResponse, ErrorMessage, str]
    """

    return sync_detailed(
        client=client,
        date=date,
        otm_only=otm_only,
        interval_5m=interval_5m,
    ).parsed


async def asyncio_detailed(
    *,
    client: UnusualWhalesClient,
    date: Union[Unset, str] = UNSET,
    otm_only: Union[Unset, bool] = UNSET,
    interval_5m: Union[Unset, bool] = UNSET,
) -> Response[Union[DailyMarketTideResponse, ErrorMessage, str]]:
    """Returns The Unusual Whales Market Tide Data

     Market Tide is a proprietary tool that can be viewed from the Market Overview page. The Market Tide
    chart provides real time data based on a proprietary formula that examines market wide options
    activity and filters out 'noise'.

    Date must be the current or a past date. If no date is given, returns data for the current/last
    market day.

    Per default data are returned in 1 minute intervals. Use `interval_5m=true` to have this return data
    in 5 minute intervals instead.


    for example
    - $15,000 in calls transacted at the ask has the effect of increasing the daily net call premium by
    $15,000.
    - $10,000 in calls transacted at the bid has the effect of decreasing the daily net call premium by
    $10,000.

    The resulting net premium from both of these trades would be $5000 (+ $15,000 - $10,000).

    Transactions taking place at the mid are not accounted for.

    In theory:

    The sentiment in the options market becomes increasingly bullish if:
    1. The aggregated CALL PREMIUM is increasing at a faster rate.
    2. The aggregated PUT PREMIUM is decreasing at a faster rate.

    The sentiment in the options market becomes increasingly bearish if:
    1. The aggregated CALL PREMIUM is decreasing at a faster rate.
    2. The aggregated PUT PREMIUM is increasing at a faster rate.

    ----
    This can be used to build a market overview such as:

    ![market tide](https://i.imgur.com/tuwTCDc.png)

    Args:
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        otm_only (Union[Unset, bool]): Only include out of the money transactions. Example: True.
        interval_5m (Union[Unset, bool]): Return data in 5 minutes intervals.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[DailyMarketTideResponse, ErrorMessage, str]]
    """

    kwargs = _get_kwargs(
        date=date,
        otm_only=otm_only,
        interval_5m=interval_5m,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: UnusualWhalesClient,
    date: Union[Unset, str] = UNSET,
    otm_only: Union[Unset, bool] = UNSET,
    interval_5m: Union[Unset, bool] = UNSET,
) -> Optional[Union[DailyMarketTideResponse, ErrorMessage, str]]:
    """Returns The Unusual Whales Market Tide Data

     Market Tide is a proprietary tool that can be viewed from the Market Overview page. The Market Tide
    chart provides real time data based on a proprietary formula that examines market wide options
    activity and filters out 'noise'.

    Date must be the current or a past date. If no date is given, returns data for the current/last
    market day.

    Per default data are returned in 1 minute intervals. Use `interval_5m=true` to have this return data
    in 5 minute intervals instead.


    for example
    - $15,000 in calls transacted at the ask has the effect of increasing the daily net call premium by
    $15,000.
    - $10,000 in calls transacted at the bid has the effect of decreasing the daily net call premium by
    $10,000.

    The resulting net premium from both of these trades would be $5000 (+ $15,000 - $10,000).

    Transactions taking place at the mid are not accounted for.

    In theory:

    The sentiment in the options market becomes increasingly bullish if:
    1. The aggregated CALL PREMIUM is increasing at a faster rate.
    2. The aggregated PUT PREMIUM is decreasing at a faster rate.

    The sentiment in the options market becomes increasingly bearish if:
    1. The aggregated CALL PREMIUM is decreasing at a faster rate.
    2. The aggregated PUT PREMIUM is increasing at a faster rate.

    ----
    This can be used to build a market overview such as:

    ![market tide](https://i.imgur.com/tuwTCDc.png)

    Args:
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        otm_only (Union[Unset, bool]): Only include out of the money transactions. Example: True.
        interval_5m (Union[Unset, bool]): Return data in 5 minutes intervals.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[DailyMarketTideResponse, ErrorMessage, str]
    """

    return (
        await asyncio_detailed(
            client=client,
            date=date,
            otm_only=otm_only,
            interval_5m=interval_5m,
        )
    ).parsed
