from http import HTTPStatus
from typing import Any

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
    ticker: Unset | str = UNSET,
    limit: Unset | int = UNSET,
    action: Unset | AnalystAction = UNSET,
    recommendation: Unset | AnalystRecommendation = UNSET,
    sector: Unset | AnalystSector = UNSET,
) -> dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: dict[str, Any] = {}

    params["ticker"] = ticker

    params["limit"] = limit

    json_action: Unset | str = UNSET
    if not isinstance(action, Unset):
        json_action = action.value

    params["action"] = json_action

    json_recommendation: Unset | str = UNSET
    if not isinstance(recommendation, Unset):
        json_recommendation = recommendation.value

    params["recommendation"] = json_recommendation

    json_sector: Unset | str = UNSET
    if not isinstance(sector, Unset):
        json_sector = sector.value

    params["sector"] = json_sector

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/screener/analysts",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> AnalalystRatingResults | ErrorMessage | str | None:
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
) -> Response[AnalalystRatingResults | ErrorMessage | str]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: UnusualWhalesClient,
    ticker: Unset | str = UNSET,
    limit: Unset | int = UNSET,
    action: Unset | AnalystAction = UNSET,
    recommendation: Unset | AnalystRecommendation = UNSET,
    sector: Unset | AnalystSector = UNSET,
) -> Response[AnalalystRatingResults | ErrorMessage | str]:
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
    ticker: Unset | str = UNSET,
    limit: Unset | int = UNSET,
    action: Unset | AnalystAction = UNSET,
    recommendation: Unset | AnalystRecommendation = UNSET,
    sector: Unset | AnalystSector = UNSET,
) -> AnalalystRatingResults | ErrorMessage | str | None:
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
    ticker: Unset | str = UNSET,
    limit: Unset | int = UNSET,
    action: Unset | AnalystAction = UNSET,
    recommendation: Unset | AnalystRecommendation = UNSET,
    sector: Unset | AnalystSector = UNSET,
) -> Response[AnalalystRatingResults | ErrorMessage | str]:
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
    ticker: Unset | str = UNSET,
    limit: Unset | int = UNSET,
    action: Unset | AnalystAction = UNSET,
    recommendation: Unset | AnalystRecommendation = UNSET,
    sector: Unset | AnalystSector = UNSET,
) -> AnalalystRatingResults | ErrorMessage | str | None:
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
