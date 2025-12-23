from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.market_sector_ticker_results import MarketSectorTickerResults
from ...models.sector import Sector
from ...types import Response


def _get_kwargs(
    sector: Sector,
) -> Dict[str, Any]:
    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/stock/{sector}/tickers",
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[ErrorMessage, MarketSectorTickerResults, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = MarketSectorTickerResults.from_dict(response.json())

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
) -> Response[Union[ErrorMessage, MarketSectorTickerResults, str]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    sector: Sector,
    *,
    client: UnusualWhalesClient,
) -> Response[Union[ErrorMessage, MarketSectorTickerResults, str]]:
    """Return Tickers for a Given Sector

     Returns a list of tickers which are in the given sector.

    Args:
        sector (Sector): A financial sector. Example: Technology.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, MarketSectorTickerResults, str]]
    """

    kwargs = _get_kwargs(
        sector=sector,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    sector: Sector,
    *,
    client: UnusualWhalesClient,
) -> Optional[Union[ErrorMessage, MarketSectorTickerResults, str]]:
    """Return Tickers for a Given Sector

     Returns a list of tickers which are in the given sector.

    Args:
        sector (Sector): A financial sector. Example: Technology.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, MarketSectorTickerResults, str]
    """

    return sync_detailed(
        sector=sector,
        client=client,
    ).parsed


async def asyncio_detailed(
    sector: Sector,
    *,
    client: UnusualWhalesClient,
) -> Response[Union[ErrorMessage, MarketSectorTickerResults, str]]:
    """Return Tickers for a Given Sector

     Returns a list of tickers which are in the given sector.

    Args:
        sector (Sector): A financial sector. Example: Technology.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, MarketSectorTickerResults, str]]
    """

    kwargs = _get_kwargs(
        sector=sector,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    sector: Sector,
    *,
    client: UnusualWhalesClient,
) -> Optional[Union[ErrorMessage, MarketSectorTickerResults, str]]:
    """Return Tickers for a Given Sector

     Returns a list of tickers which are in the given sector.

    Args:
        sector (Sector): A financial sector. Example: Technology.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, MarketSectorTickerResults, str]
    """

    return (
        await asyncio_detailed(
            sector=sector,
            client=client,
        )
    ).parsed
