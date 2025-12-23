from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.option_chain_contract_results import OptionChainContractResults
from ...types import UNSET, Response


def _get_kwargs(
    ticker: str,
    *,
    expirations: str,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["expirations[]"] = expirations

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/stock/{ticker}/atm-chains",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[ErrorMessage, OptionChainContractResults, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = OptionChainContractResults.from_dict(response.json())

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
) -> Response[Union[ErrorMessage, OptionChainContractResults, str]]:
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
    expirations: str,
) -> Response[Union[ErrorMessage, OptionChainContractResults, str]]:
    """ATM option contracts for the given expiries

     Returns the ATM option contracts for the given expirations

    Args:
        ticker (str): A single ticker Example: AAPL.
        expirations (str): A single expiry date in ISO date format. Example: 2024-02-02.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, OptionChainContractResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        expirations=expirations,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    expirations: str,
) -> Optional[Union[ErrorMessage, OptionChainContractResults, str]]:
    """ATM option contracts for the given expiries

     Returns the ATM option contracts for the given expirations

    Args:
        ticker (str): A single ticker Example: AAPL.
        expirations (str): A single expiry date in ISO date format. Example: 2024-02-02.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, OptionChainContractResults, str]
    """

    return sync_detailed(
        ticker=ticker,
        client=client,
        expirations=expirations,
    ).parsed


async def asyncio_detailed(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    expirations: str,
) -> Response[Union[ErrorMessage, OptionChainContractResults, str]]:
    """ATM option contracts for the given expiries

     Returns the ATM option contracts for the given expirations

    Args:
        ticker (str): A single ticker Example: AAPL.
        expirations (str): A single expiry date in ISO date format. Example: 2024-02-02.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, OptionChainContractResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        expirations=expirations,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    expirations: str,
) -> Optional[Union[ErrorMessage, OptionChainContractResults, str]]:
    """ATM option contracts for the given expiries

     Returns the ATM option contracts for the given expirations

    Args:
        ticker (str): A single ticker Example: AAPL.
        expirations (str): A single expiry date in ISO date format. Example: 2024-02-02.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, OptionChainContractResults, str]
    """

    return (
        await asyncio_detailed(
            ticker=ticker,
            client=client,
            expirations=expirations,
        )
    ).parsed
