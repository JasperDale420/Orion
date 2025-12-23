from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.error_message_stating_that_the_requested_element_was_not_found_causing_an_empty_result_to_be_generated import (
    ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated,
)
from ...models.ticker_info_results import TickerInfoResults
from ...types import Response


def _get_kwargs(
    ticker: str,
) -> Dict[str, Any]:
    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/stock/{ticker}/info",
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[
    Union[
        ErrorMessage,
        ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated,
        TickerInfoResults,
        str,
    ]
]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = TickerInfoResults.from_dict(response.json())

        return response_200
    if response.status_code == HTTPStatus.NOT_FOUND:
        response_404 = ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated.from_dict(
            response.json()
        )

        return response_404
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
) -> Response[
    Union[
        ErrorMessage,
        ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated,
        TickerInfoResults,
        str,
    ]
]:
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
) -> Response[
    Union[
        ErrorMessage,
        ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated,
        TickerInfoResults,
        str,
    ]
]:
    """Ticker Information

     Returns a information about the given ticker.

    Args:
        ticker (str): A single ticker Example: AAPL.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated, TickerInfoResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ticker: str,
    *,
    client: UnusualWhalesClient,
) -> Optional[
    Union[
        ErrorMessage,
        ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated,
        TickerInfoResults,
        str,
    ]
]:
    """Ticker Information

     Returns a information about the given ticker.

    Args:
        ticker (str): A single ticker Example: AAPL.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated, TickerInfoResults, str]
    """

    return sync_detailed(
        ticker=ticker,
        client=client,
    ).parsed


async def asyncio_detailed(
    ticker: str,
    *,
    client: UnusualWhalesClient,
) -> Response[
    Union[
        ErrorMessage,
        ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated,
        TickerInfoResults,
        str,
    ]
]:
    """Ticker Information

     Returns a information about the given ticker.

    Args:
        ticker (str): A single ticker Example: AAPL.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated, TickerInfoResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ticker: str,
    *,
    client: UnusualWhalesClient,
) -> Optional[
    Union[
        ErrorMessage,
        ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated,
        TickerInfoResults,
        str,
    ]
]:
    """Ticker Information

     Returns a information about the given ticker.

    Args:
        ticker (str): A single ticker Example: AAPL.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated, TickerInfoResults, str]
    """

    return (
        await asyncio_detailed(
            ticker=ticker,
            client=client,
        )
    ).parsed
