from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.analalyst_rating_results import AnalalystRatingResults
from ...models.analyst_action import AnalystAction
from ...models.analyst_recommendation import AnalystRecommendation
from ...models.analyst_sector import AnalystSector
from ...models.error_message import ErrorMessage
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    ticker: Union[Unset, str] = UNSET,
    limit: Union[Unset, int] = UNSET,
    action: Union[Unset, AnalystAction] = UNSET,
    recommendation: Union[Unset, AnalystRecommendation] = UNSET,
    sector: Union[Unset, AnalystSector] = UNSET,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["ticker"] = ticker

    params["limit"] = limit

    json_action: Union[Unset, str] = UNSET
    if not isinstance(action, Unset):
        json_action = action.value

    params["action"] = json_action

    json_recommendation: Union[Unset, str] = UNSET
    if not isinstance(recommendation, Unset):
        json_recommendation = recommendation.value

    params["recommendation"] = json_recommendation

    json_sector: Union[Unset, str] = UNSET
    if not isinstance(sector, Unset):
        json_sector = sector.value

    params["sector"] = json_sector

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": "/api/screener/analysts",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[AnalalystRatingResults, ErrorMessage, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = AnalalystRatingResults.from_dict(response.json())

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
) -> Response[Union[AnalalystRatingResults, ErrorMessage, str]]:
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
    limit: Union[Unset, int] = UNSET,
    action: Union[Unset, AnalystAction] = UNSET,
    recommendation: Union[Unset, AnalystRecommendation] = UNSET,
    sector: Union[Unset, AnalystSector] = UNSET,
) -> Response[Union[AnalalystRatingResults, ErrorMessage, str]]:
    """Analyst Ratings for a Ticker

     Returns the latest analyst rating for the given ticker.

    Args:
        ticker (Union[Unset, str]): A single ticker Example: AAPL.
        limit (Union[Unset, int]): How many items to return. Default: 500. Max: 500. Min: 1.
            Example: 10.
        action (Union[Unset, AnalystAction]): The action of the recommendation. Example:
            maintained.
        recommendation (Union[Unset, AnalystRecommendation]): The recommendation the analyst gave
            out. Example: Hold.
        sector (Union[Unset, AnalystSector]): A financial sector Example: Financial.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[AnalalystRatingResults, ErrorMessage, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        limit=limit,
        action=action,
        recommendation=recommendation,
        sector=sector,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: UnusualWhalesClient,
    ticker: Union[Unset, str] = UNSET,
    limit: Union[Unset, int] = UNSET,
    action: Union[Unset, AnalystAction] = UNSET,
    recommendation: Union[Unset, AnalystRecommendation] = UNSET,
    sector: Union[Unset, AnalystSector] = UNSET,
) -> Optional[Union[AnalalystRatingResults, ErrorMessage, str]]:
    """Analyst Ratings for a Ticker

     Returns the latest analyst rating for the given ticker.

    Args:
        ticker (Union[Unset, str]): A single ticker Example: AAPL.
        limit (Union[Unset, int]): How many items to return. Default: 500. Max: 500. Min: 1.
            Example: 10.
        action (Union[Unset, AnalystAction]): The action of the recommendation. Example:
            maintained.
        recommendation (Union[Unset, AnalystRecommendation]): The recommendation the analyst gave
            out. Example: Hold.
        sector (Union[Unset, AnalystSector]): A financial sector Example: Financial.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[AnalalystRatingResults, ErrorMessage, str]
    """

    return sync_detailed(
        client=client,
        ticker=ticker,
        limit=limit,
        action=action,
        recommendation=recommendation,
        sector=sector,
    ).parsed


async def asyncio_detailed(
    *,
    client: UnusualWhalesClient,
    ticker: Union[Unset, str] = UNSET,
    limit: Union[Unset, int] = UNSET,
    action: Union[Unset, AnalystAction] = UNSET,
    recommendation: Union[Unset, AnalystRecommendation] = UNSET,
    sector: Union[Unset, AnalystSector] = UNSET,
) -> Response[Union[AnalalystRatingResults, ErrorMessage, str]]:
    """Analyst Ratings for a Ticker

     Returns the latest analyst rating for the given ticker.

    Args:
        ticker (Union[Unset, str]): A single ticker Example: AAPL.
        limit (Union[Unset, int]): How many items to return. Default: 500. Max: 500. Min: 1.
            Example: 10.
        action (Union[Unset, AnalystAction]): The action of the recommendation. Example:
            maintained.
        recommendation (Union[Unset, AnalystRecommendation]): The recommendation the analyst gave
            out. Example: Hold.
        sector (Union[Unset, AnalystSector]): A financial sector Example: Financial.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[AnalalystRatingResults, ErrorMessage, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        limit=limit,
        action=action,
        recommendation=recommendation,
        sector=sector,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: UnusualWhalesClient,
    ticker: Union[Unset, str] = UNSET,
    limit: Union[Unset, int] = UNSET,
    action: Union[Unset, AnalystAction] = UNSET,
    recommendation: Union[Unset, AnalystRecommendation] = UNSET,
    sector: Union[Unset, AnalystSector] = UNSET,
) -> Optional[Union[AnalalystRatingResults, ErrorMessage, str]]:
    """Analyst Ratings for a Ticker

     Returns the latest analyst rating for the given ticker.

    Args:
        ticker (Union[Unset, str]): A single ticker Example: AAPL.
        limit (Union[Unset, int]): How many items to return. Default: 500. Max: 500. Min: 1.
            Example: 10.
        action (Union[Unset, AnalystAction]): The action of the recommendation. Example:
            maintained.
        recommendation (Union[Unset, AnalystRecommendation]): The recommendation the analyst gave
            out. Example: Hold.
        sector (Union[Unset, AnalystSector]): A financial sector Example: Financial.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[AnalalystRatingResults, ErrorMessage, str]
    """

    return (
        await asyncio_detailed(
            client=client,
            ticker=ticker,
            limit=limit,
            action=action,
            recommendation=recommendation,
            sector=sector,
        )
    ).parsed
