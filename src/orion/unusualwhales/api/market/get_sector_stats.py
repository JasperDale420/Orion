from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.sector_etf_results import SectorETFResults
from ...types import Response


def _get_kwargs() -> dict[str, Any]:
    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/market/sector-etfs",
    }

    return _kwargs


def _parse_response(*, client: UnusualWhalesClient, response: httpx.Response) -> SectorETFResults | str | None:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = SectorETFResults.from_dict(response.json())

        return response_200
    if response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR:
        response_500 = response.text
        return response_500
    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: UnusualWhalesClient, response: httpx.Response) -> Response[SectorETFResults | str]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: UnusualWhalesClient,
) -> Response[SectorETFResults | str]:
    """Returns the stats for sector etfs for the most recent trading day

     Returns the current Trading Days statistics for the SPDR sector etfs

    ----
    This can be used to build a market overview such as:

    ![sectors etf](https://i.imgur.com/yQ5o6rR.png)

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[SectorETFResults, str]]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: UnusualWhalesClient,
) -> SectorETFResults | str | None:
    """Returns the stats for sector etfs for the most recent trading day

     Returns the current Trading Days statistics for the SPDR sector etfs

    ----
    This can be used to build a market overview such as:

    ![sectors etf](https://i.imgur.com/yQ5o6rR.png)

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[SectorETFResults, str]
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: UnusualWhalesClient,
) -> Response[SectorETFResults | str]:
    """Returns the stats for sector etfs for the most recent trading day

     Returns the current Trading Days statistics for the SPDR sector etfs

    ----
    This can be used to build a market overview such as:

    ![sectors etf](https://i.imgur.com/yQ5o6rR.png)

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[SectorETFResults, str]]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: UnusualWhalesClient,
) -> SectorETFResults | str | None:
    """Returns the stats for sector etfs for the most recent trading day

     Returns the current Trading Days statistics for the SPDR sector etfs

    ----
    This can be used to build a market overview such as:

    ![sectors etf](https://i.imgur.com/yQ5o6rR.png)

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[SectorETFResults, str]
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
