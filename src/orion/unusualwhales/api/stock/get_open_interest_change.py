from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.oi_change_results import OIChangeResults
from ...models.order_direction import OrderDirection
from ...types import UNSET, Response, Unset


def _get_kwargs(
    ticker: str,
    *,
    date: Unset | str = UNSET,
    limit: Unset | int = UNSET,
    order: Unset | OrderDirection = UNSET,
) -> dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: dict[str, Any] = {}

    params["date"] = date

    params["limit"] = limit

    json_order: Unset | str = UNSET
    if not isinstance(order, Unset):
        json_order = order.value

    params["order"] = json_order

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": f"/api/stock/{ticker}/oi-change",
        "params": params,
    }

    return _kwargs


def _parse_response(*, client: UnusualWhalesClient, response: httpx.Response) -> OIChangeResults | None:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = OIChangeResults.from_dict(response.json())

        return response_200
    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: UnusualWhalesClient, response: httpx.Response) -> Response[OIChangeResults]:
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
    date: Unset | str = UNSET,
    limit: Unset | int = UNSET,
    order: Unset | OrderDirection = UNSET,
) -> Response[OIChangeResults]:
    """OI Change

     Returns the tickers contracts' OI change data ordered by absolute OI change (default: descending).
    Date must be the current or a past date. If no date is given, returns data for the current/last
    market day.

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        limit (Union[Unset, int]): How many items to return. If no limit is given, returns all
            matching data. Min: 1. Example: 10.
        order (Union[Unset, OrderDirection]): Whether to sort descending or ascending. Descending
            by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[OIChangeResults]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        date=date,
        limit=limit,
        order=order,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: Unset | str = UNSET,
    limit: Unset | int = UNSET,
    order: Unset | OrderDirection = UNSET,
) -> OIChangeResults | None:
    """OI Change

     Returns the tickers contracts' OI change data ordered by absolute OI change (default: descending).
    Date must be the current or a past date. If no date is given, returns data for the current/last
    market day.

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        limit (Union[Unset, int]): How many items to return. If no limit is given, returns all
            matching data. Min: 1. Example: 10.
        order (Union[Unset, OrderDirection]): Whether to sort descending or ascending. Descending
            by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        OIChangeResults
    """

    return sync_detailed(
        ticker=ticker,
        client=client,
        date=date,
        limit=limit,
        order=order,
    ).parsed


async def asyncio_detailed(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: Unset | str = UNSET,
    limit: Unset | int = UNSET,
    order: Unset | OrderDirection = UNSET,
) -> Response[OIChangeResults]:
    """OI Change

     Returns the tickers contracts' OI change data ordered by absolute OI change (default: descending).
    Date must be the current or a past date. If no date is given, returns data for the current/last
    market day.

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        limit (Union[Unset, int]): How many items to return. If no limit is given, returns all
            matching data. Min: 1. Example: 10.
        order (Union[Unset, OrderDirection]): Whether to sort descending or ascending. Descending
            by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[OIChangeResults]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        date=date,
        limit=limit,
        order=order,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: Unset | str = UNSET,
    limit: Unset | int = UNSET,
    order: Unset | OrderDirection = UNSET,
) -> OIChangeResults | None:
    """OI Change

     Returns the tickers contracts' OI change data ordered by absolute OI change (default: descending).
    Date must be the current or a past date. If no date is given, returns data for the current/last
    market day.

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.
        limit (Union[Unset, int]): How many items to return. If no limit is given, returns all
            matching data. Min: 1. Example: 10.
        order (Union[Unset, OrderDirection]): Whether to sort descending or ascending. Descending
            by default. Example: asc.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        OIChangeResults
    """

    return (
        await asyncio_detailed(
            ticker=ticker,
            client=client,
            date=date,
            limit=limit,
            order=order,
        )
    ).parsed
