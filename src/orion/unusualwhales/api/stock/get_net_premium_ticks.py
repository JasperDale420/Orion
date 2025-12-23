from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import UnusualWhalesClient
from ...models.error_message import ErrorMessage
from ...models.net_prem_tick_results import NetPremTickResults
from ...types import UNSET, Response, Unset


def _get_kwargs(
    ticker: str,
    *,
    date: Union[Unset, str] = UNSET,
) -> Dict[str, Any]:
    # Dictionary of query parameters to be sent with the request.
    params: Dict[str, Any] = {}

    params["date"] = date

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: Dict[str, Any] = {
        "method": "get",
        "url": f"/api/stock/{ticker}/net-prem-ticks",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: UnusualWhalesClient, response: httpx.Response
) -> Optional[Union[ErrorMessage, NetPremTickResults, str]]:
    response_json = response.json()
    if response_json.get("data") is not None:
        response_json = response_json["data"]
    if response.status_code == HTTPStatus.OK:
        response_200 = NetPremTickResults.from_dict(response.json())

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
) -> Response[Union[ErrorMessage, NetPremTickResults, str]]:
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
    date: Union[Unset, str] = UNSET,
) -> Response[Union[ErrorMessage, NetPremTickResults, str]]:
    r"""Net Prem Ticks

     Returns the net premium ticks for a given ticker which can be used to build the following chart:
    ![Net Prem chart](https://i.imgur.com/Rom1kcB.png)

    ----
    Each tick is resembling the data for a single minute tick. To build a daily chart
    you would have to add the previous data to the current tick:
    ```javascript
    const url =
      'https://api.unusualwhales.com/api/stock/AAPL/net-prem-ticks';
    const options = {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: 'Bearer YOUR_TOKEN'
      }
    };

    fetch(url, options)
    .then(r => r.json())
    .then(r => {
      const {data} = r.data;
      const fieldsToSum = [
        \"net_call_premium\",
        \"net_call_volume\",
        \"net_put_premium\",
        \"net_put_volume\"
      ];

      let result = [];
      data.forEach((e, idx) => {
        e.net_call_premium = parseFloat(e.net_call_premium);
        e.net_put_premium = parseFloat(e.net_put_premium);
        if (idx !== 0) {
          fieldsToSum.forEach((field) => {
            e[field] = e[field] + result[idx-1][field];
          })
        }
        result.push(e);
      })

      return result;
    });

    ```

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, NetPremTickResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        date=date,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: Union[Unset, str] = UNSET,
) -> Optional[Union[ErrorMessage, NetPremTickResults, str]]:
    r"""Net Prem Ticks

     Returns the net premium ticks for a given ticker which can be used to build the following chart:
    ![Net Prem chart](https://i.imgur.com/Rom1kcB.png)

    ----
    Each tick is resembling the data for a single minute tick. To build a daily chart
    you would have to add the previous data to the current tick:
    ```javascript
    const url =
      'https://api.unusualwhales.com/api/stock/AAPL/net-prem-ticks';
    const options = {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: 'Bearer YOUR_TOKEN'
      }
    };

    fetch(url, options)
    .then(r => r.json())
    .then(r => {
      const {data} = r.data;
      const fieldsToSum = [
        \"net_call_premium\",
        \"net_call_volume\",
        \"net_put_premium\",
        \"net_put_volume\"
      ];

      let result = [];
      data.forEach((e, idx) => {
        e.net_call_premium = parseFloat(e.net_call_premium);
        e.net_put_premium = parseFloat(e.net_put_premium);
        if (idx !== 0) {
          fieldsToSum.forEach((field) => {
            e[field] = e[field] + result[idx-1][field];
          })
        }
        result.push(e);
      })

      return result;
    });

    ```

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, NetPremTickResults, str]
    """

    return sync_detailed(
        ticker=ticker,
        client=client,
        date=date,
    ).parsed


async def asyncio_detailed(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: Union[Unset, str] = UNSET,
) -> Response[Union[ErrorMessage, NetPremTickResults, str]]:
    r"""Net Prem Ticks

     Returns the net premium ticks for a given ticker which can be used to build the following chart:
    ![Net Prem chart](https://i.imgur.com/Rom1kcB.png)

    ----
    Each tick is resembling the data for a single minute tick. To build a daily chart
    you would have to add the previous data to the current tick:
    ```javascript
    const url =
      'https://api.unusualwhales.com/api/stock/AAPL/net-prem-ticks';
    const options = {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: 'Bearer YOUR_TOKEN'
      }
    };

    fetch(url, options)
    .then(r => r.json())
    .then(r => {
      const {data} = r.data;
      const fieldsToSum = [
        \"net_call_premium\",
        \"net_call_volume\",
        \"net_put_premium\",
        \"net_put_volume\"
      ];

      let result = [];
      data.forEach((e, idx) => {
        e.net_call_premium = parseFloat(e.net_call_premium);
        e.net_put_premium = parseFloat(e.net_put_premium);
        if (idx !== 0) {
          fieldsToSum.forEach((field) => {
            e[field] = e[field] + result[idx-1][field];
          })
        }
        result.push(e);
      })

      return result;
    });

    ```

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[ErrorMessage, NetPremTickResults, str]]
    """

    kwargs = _get_kwargs(
        ticker=ticker,
        date=date,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ticker: str,
    *,
    client: UnusualWhalesClient,
    date: Union[Unset, str] = UNSET,
) -> Optional[Union[ErrorMessage, NetPremTickResults, str]]:
    r"""Net Prem Ticks

     Returns the net premium ticks for a given ticker which can be used to build the following chart:
    ![Net Prem chart](https://i.imgur.com/Rom1kcB.png)

    ----
    Each tick is resembling the data for a single minute tick. To build a daily chart
    you would have to add the previous data to the current tick:
    ```javascript
    const url =
      'https://api.unusualwhales.com/api/stock/AAPL/net-prem-ticks';
    const options = {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: 'Bearer YOUR_TOKEN'
      }
    };

    fetch(url, options)
    .then(r => r.json())
    .then(r => {
      const {data} = r.data;
      const fieldsToSum = [
        \"net_call_premium\",
        \"net_call_volume\",
        \"net_put_premium\",
        \"net_put_volume\"
      ];

      let result = [];
      data.forEach((e, idx) => {
        e.net_call_premium = parseFloat(e.net_call_premium);
        e.net_put_premium = parseFloat(e.net_put_premium);
        if (idx !== 0) {
          fieldsToSum.forEach((field) => {
            e[field] = e[field] + result[idx-1][field];
          })
        }
        result.push(e);
      })

      return result;
    });

    ```

    Args:
        ticker (str): A single ticker Example: AAPL.
        date (Union[Unset, str]): A trading date in the format of YYYY-MM-DD.
            This is optional and by default the last trading date.
             Example: 2024-01-18.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[ErrorMessage, NetPremTickResults, str]
    """

    return (
        await asyncio_detailed(
            ticker=ticker,
            client=client,
            date=date,
        )
    ).parsed
