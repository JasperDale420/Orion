# Unusual Whales API — Complete Endpoint List with Descriptions

## GET /api/alerts

**Summary:** Alerts

**Description:**

Returns all the alerts that have been triggered for the user.
Time filtering is available using the `newer_than` and `older_than` parameters:
- The maximum lookback period is 14 days
- If no time range is specified, defaults to the last 14 days
- If only one time parameter is provided, the other is automatically calculated to maintain the 14-day limit
- If both parameters are provided but exceed 14 days, the range is adjusted to 14 days from the `older_than` timestamp
The alerts are the same alerts as the user created on [https://unusualwhales.com/custom-alerts](https://unusualwhales.com/custom-alerts)

---

## GET /api/alerts/configuration

**Summary:** Alert configurations

**Description:**

Returnst all alert configurations of the user.
Users can create alerts for:
- Market tide
- Gamma exposure (GEX), Vanna exposure (VEX), Charm exposure (CEX)
- Interval Contract screeners (replicates and alerts on the Flow Feed)
- Analyst ratings, price targets, and actions
- Politician trades
- Insider trades
- OI changes for contract in premarket
- FDA
- Flow alerts
- Contract screener (replicates and alerts on the Hottest Chains)
- Stock screeners
- News
- Earnings
- Dividends
- Splits
- Trading stats (halts, unhalts)
- Economic release
- SEC filings
The alerts are the same alerts as the user created on [https://unusualwhales.com/custom-alerts](https://unusualwhales.com/custom-alerts)

---

## GET /api/congress/congress-trader

**Summary:** Recent Reports By Trader

**Description:**

Returns the recent reports by the given congress member.

---

## GET /api/congress/late-reports

**Summary:** Recent Late Reports

**Description:**

Returns the recent late reports by congress members.
If a date is given, will only return recent late reports, which's report date is &lt;= the given input date.

---

## GET /api/congress/recent-trades

**Summary:** Recent Congress Trades

**Description:**

Returns the latest transacted trades by congress members.
If a date is given, will only return reports, which's transaction date is &lt;= the given input date.

---

## GET /api/darkpool/recent

**Summary:** Recent Darkpool Trades

**Description:**

Returns the latest darkpool trades.

---

## GET /api/darkpool/{ticker}

**Summary:** Ticker Darkpool Trades

**Description:**

Returns the darkpool trades for the given ticker on a given day.
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/earnings/afterhours

**Summary:** Afterhours

**Description:**

Returns the afterhours earnings for a given date.

---

## GET /api/earnings/premarket

**Summary:** Premarket

**Description:**

Returns the premarket earnings for a given date.

---

## GET /api/earnings/{ticker}

**Summary:** Historical Ticker Earnings

**Description:**

Returns the historical earnings for the given ticker.

---

## GET /api/etfs/{ticker}/exposure

**Summary:** Exposure

**Description:**

Returns all ETFs in which the given ticker is a holding

---

## GET /api/etfs/{ticker}/holdings

**Summary:** Holdings

**Description:**

Returns the holdings of the ETF

---

## GET /api/etfs/{ticker}/in-outflow

**Summary:** Inflow & Outflow

**Description:**

Returns an ETF's inflow and outflow

---

## GET /api/etfs/{ticker}/info

**Summary:** Information

**Description:**

Returns the information about the given ETF ticker.

---

## GET /api/etfs/{ticker}/weights

**Summary:** Sector & Country weights

**Description:**

Returns the sector & country weights for the given ETF ticker.

---

## GET /api/group-flow/{flow_group}/greek-flow

**Summary:** Greek flow

**Description:**

Returns the group flow's greek flow (delta & vega flow) for the given market day broken down per minute.
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/group-flow/{flow_group}/greek-flow/{expiry}

**Summary:** Greek flow by expiry

**Description:**

Returns the group flow's greek flow (delta & vega flow) for the given market day broken down per minute & expiry.
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/insider/transactions

**Summary:** Transactions

**Description:**

Returns the latest insider transactions.
By default all transacations that have been filled by the same person on the same day with the same trade code are aggregated into a single row.
Each of those aggregated rows will a field `trade_ids` which contains the ids of the single transactions that were aggregated as well as the amount
of transactions that were aggregated.
If you want to disable this behaviour you can set the `group` parameter to false to receive the single transacations as they have been filled.

---

## GET /api/insider/{sector}/sector-flow

**Summary:** Sector Flow

**Description:**

Returns an aggregated view of the insider flow for the given sector.
This can be used to quickly examine the buy & sell insider flow for a given trading date

---

## GET /api/insider/{ticker}

**Summary:** Insiders

**Description:**

Returns all insiders for the given ticker

---

## GET /api/insider/{ticker}/ticker-flow

**Summary:** Ticker Flow

**Description:**

Returns an aggregated view of the insider flow for the given ticker.
This can be used to quickly examine the buy & sell insider flow for a given trading date

---

## GET /api/institution/{name}/activity

**Summary:** Institutional Activity

**Description:**

The trading activities for a given institution.

---

## GET /api/institution/{name}/holdings

**Summary:** Institutional Holdings

**Description:**

Returns the holdings for a given institution.

---

## GET /api/institution/{name}/sectors

**Summary:** Sector Exposure

**Description:**

The sector exposure for a given institution.

---

## GET /api/institution/{ticker}/ownership

**Summary:** Institutional Ownership

**Description:**

The institutional ownership of a given ticker.

---

## GET /api/institutions

**Summary:** List of Institutions

**Description:**

Returns a list of institutions.

---

## GET /api/institutions/latest_filings

**Summary:** Latest Filings

**Description:**

The latest institutional filings.

---

## GET /api/market/correlations

**Summary:** Correlations

**Description:**

Returns the correlations between a list of tickers.
Date must be the current or a past date. If no date is given, returns data for the current/last market day.
You can filter the time period either by:
1. Using the `interval` parameter (e.g. "1y", "6m", "3m", "1m")
2. Using `start_date` and optionally `end_date` (if `end_date` is not provided, it defaults to the current date)
If you send `interval` alongside `start_date`, `interval` filter will take priority.

---

## GET /api/market/economic-calendar

**Summary:** Economic calendar

**Description:**

Returns the economic calendar.

---

## GET /api/market/fda-calendar

**Summary:** FDA Calendar

**Description:**

Returns FDA calendar data with filtering options.
The FDA calendar contains information about:
- PDUFA (Prescription Drug User Fee Act) dates
- Advisory Committee Meetings
- FDA Decisions
- Clinical Trial Results
- New Drug Applications
- Biologics License Applications
## Date Format Support
The target_date parameters support various FDA-specific date formats:
- Quarters: YYYY-Q[1-4] (e.g. 2024-Q1)
- Half years: YYYY-H[1-2] (e.g. 2024-H1)
- Mid-year: YYYY-MID (e.g. 2024-MID)
- Late-year: YYYY-LATE (e.g. 2024-LATE)
- Standard dates: YYYY-MM-DD

---

## GET /api/market/insider-buy-sells

**Summary:** Total Insider Buy & Sells

**Description:**

Returns the total amount of purchases & sells as well as notional values for insider transactions
across the market

---

## GET /api/market/market-tide

**Summary:** Market Tide

**Description:**

Market Tide is a proprietary tool that can be viewed from the Market Overview page. The Market Tide chart provides real time data based on a proprietary formula that examines market wide options activity and filters out 'noise'.
Date must be the current or a past date. If no date is given, returns data for the current/last market day.
Per default data are returned in 1 minute intervals. Use `interval_5m=true` to have this return data in 5 minute intervals instead.
For example:
- $15,000 in calls transacted at the ask has the effect of increasing the daily net call premium by $15,000.
- $10,000 in calls transacted at the bid has the effect of decreasing the daily net call premium by $10,000.
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
Data goes back to 2022-09-28

---

## GET /api/market/oi-change

**Summary:** OI Change

**Description:**

Returns the non-Index/non-ETF contracts and OI change data with the highest OI change (default: descending).
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/market/sector-etfs

**Summary:** Sector Etfs

**Description:**

Returns the current trading days statistics for the SPDR sector etfs
----
This can be used to build a market overview such as:
![sectors etf](https://i.imgur.com/yQ5o6rR.png)

---

## GET /api/market/spike

**Summary:** SPIKE

**Description:**

Returns the SPIKE values for the given date.
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/market/top-net-impact

**Summary:** Top Net Impact

**Description:**

Returns the top tickers by net premium (half bullish, half bearish). Defaults to last market day.

---

## GET /api/market/total-options-volume

**Summary:** Total Options Volume

**Description:**

Returns the total options volume and premium for all trade executions
that happened on a given trading date.
----
This can be used to build a market options overview such as:
![Market State](https://i.imgur.com/IioJyq9.png)

---

## GET /api/market/{sector}/sector-tide

**Summary:** Sector Tide

**Description:**

The Sector tide is similar to the Market Tide. While the market tide is based on options activity of the whole market
the sector tide is only based on the options activity of companies which are in that specific sector

---

## GET /api/market/{ticker}/etf-tide

**Summary:** ETF Tide

**Description:**

The ETF tide is similar to the Market Tide. While the market tide is based on options activity of the whole market
the ETF tide is only based on the options activity of the holdings of the specified ETF.

---

## GET /api/net-flow/expiry

**Summary:** Net Flow Expiry

**Description:**

Returns net premium flow by `tide_type` category, `moneyness` category, and `expiration` category, allowing you to create chart variations like [https://unusualwhales.com/zero-dte](https://unusualwhales.com/zero-dte):
![zero dte](https://storage.googleapis.com/uwassets/img/zero-dte-type-charts.png)
About the query parameters:
- **`tide_type`**: For example, setting `tide_type` to "equity_only" will filter out ETFs and indexes, leaving only net premium from single-name equities.
- **`moneyness`**: For example, setting `moneyness` to "otm" will filter out any contract that was not out of the money ("OTM") at the time of the transaction, leaving only net premium from contracts that were OTM at the time of the transaction.
- **`expiration`**: For example, setting `expiration` to "zero_dte" will filter out any contract not expiring this session, leaving only net premium from contracts expiring at 4PM eastern time today.

---

## GET /api/news/headlines

**Summary:** News Headlines

**Description:**

Returns the latest news headlines for financial markets.
This endpoint provides access to news headlines that may impact the markets, including company-specific
news, sector news, and market-wide events. Headlines can be filtered by source, content, and significance.
The data includes the headline text, source, related tickers, sentiment, and whether it's considered a
major news item.

---

## GET /api/option-contract/{id}/flow

**Summary:** Flow Data

**Description:**

Returns the last 50 option trades for the given option chain. Optionally a min premium and a side can be supplied in the query for further filtering.
If no date is specified data for the last trading day is being returned.

---

## GET /api/option-contract/{id}/historic

**Summary:** Historic Data

**Description:**

Returns for every trading day historic data for the given option contract

---

## GET /api/option-contract/{id}/intraday

**Summary:** Intraday Data

**Description:**

Returns 1 minute interval intraday data for the given option contract.
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/option-contract/{id}/volume-profile

**Summary:** Volume Profile

**Description:**

Returns the volume profile (volume - sweep, floor, cross, ask, bid, etc. - per fill price) for an option symbol on a given trading day.
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/option-trades/flow-alerts

**Summary:** Flow Alerts

**Description:**

Returns the latest flow alerts.

---

## GET /api/option-trades/full-tape/{date}

**Summary:** Full Tape

**Description:**

Download all option transactions (the "full tape") for a given trading date.
NOTICE: Access to this endpoint is only included in the Advanced API subscription.
The last 3 trading days are available to download through this endpoint.
You can download the data as a zip file using wget. For example, to download data for Fri Jul 25th, 2025, if your API key is "abc123":
```
wget --header="Authorization: Bearer abc123" https://api.unusualwhales.com/api/option-trades/full-tape/2025-07-25 -O full_tape_20257225.zip
```

---

## GET /api/politician-portfolios/holders/{ticker}

**Summary:** Politician Portfolio Holders by Ticker

**Description:**

Returns all politician portfolio owner names, ID, and holdings for the specified ticker.
This is an enterprise only endpoint. Contact dan@unusualwhales.com for details about accessing this data.

---

## GET /api/politician-portfolios/people

**Summary:** Politicians List

**Description:**

Returns all politician names and IDs.
This is an enterprise only endpoint. Contact dan@unusualwhales.com for details about accessing this data.

---

## GET /api/politician-portfolios/recent_trades

**Summary:** Politician Trades

**Description:**

Returns the latest transacted trades by congress members.
If a date is given, will only return reports, which's transaction date is &lt;= the given input date.
This is an enterprise only endpoint. Contact dan@unusualwhales.com for details about accessing this data.

---

## GET /api/politician-portfolios/{politician_id}

**Summary:** Politician Portfolios

**Description:**

Returns all portfolios and holdings for a politician.
This is an enterprise only endpoint. Contact dan@unusualwhales.com for details about accessing this data.

---

## GET /api/screener/analysts

**Summary:** Analyst Rating

**Description:**

Returns the latest analyst rating for the given ticker.

---

## GET /api/screener/option-contracts

**Summary:** Hottest Chains

**Description:**

A contract screener endpoint to screen the market for contracts by a variety of filter options.
For an example of what can be build with this endpoint check out the [Hottest Contracts](https://unusualwhales.com/hottest-contracts?limit=100&hide_index_etf=true)
on UnusualWhales.
NOTE: Contracts with a volume of less than 200 are not being returned

---

## GET /api/screener/stocks

**Summary:** Stock Screener

**Description:**

A stock screener endpoint to screen the market for stocks by a variety of filter options.
For an example of what can be build with this endpoint check out the [Stock Screener](https://unusualwhales.com/flow/ticker_flows)
on UnusualWhales.

---

## GET /api/seasonality/market

**Summary:** Market Seasonality

**Description:**

Returns the average return by month for the tickers SPY, QQQ, IWM, XLE, XLC, XLK, XLV, XLP, XLY, XLRE, XLF, XLI, XLB .

---

## GET /api/seasonality/{month}/performers

**Summary:** Month Performers

**Description:**

Returns the tickers with the highest performance in terms of price change in the month over the years.
Per default the result is ordered by 'positive_months_perc' descending, then 'median_change' descending, then 'marketcap' descending.

---

## GET /api/seasonality/{ticker}/monthly

**Summary:** Average return per month

**Description:**

Returns the average return by month for the given ticker.

---

## GET /api/seasonality/{ticker}/year-month

**Summary:** Price change per month per year

**Description:**

Returns the relative price change for all past months over multiple years.

---

## GET /api/shorts/{ticker}/data

**Summary:** Short Data

**Description:**

Returns short data including rebate rate and short shares available for a ticker.

---

## GET /api/shorts/{ticker}/ftds

**Summary:** Failures to Deliver

**Description:**

Returns the short failures to deliver per day for the given ticker starting from the given date.
If no date is given, returns the data for the current/last market day.

---

## GET /api/shorts/{ticker}/interest-float

**Summary:** Short Interest and Float

**Description:**

Returns short interest and float data for percentage calculations for a ticker.
This endpoint provides information about the percentage of float that is shorted,
the float size, and the days to cover metric.

---

## GET /api/shorts/{ticker}/volume-and-ratio

**Summary:** Short Volume and Ratio

**Description:**

Returns short volume and short ratio data for a ticker.

---

## GET /api/shorts/{ticker}/volumes-by-exchange

**Summary:** Short Volume By Exchange

**Description:**

Returns short volume data broken down by exchange for a ticker.

---

## GET /api/socket

**Summary:** WebSocket channels

**Description:**

Returns the available WebSocket channels for connections.
## Websocket Guide
#You can find fully-functional examples that stream data from many channels here:
- Python: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output)
- Javascript: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs)
The following channels are available:
| Channel | Description |
|-----------------------------|-----------------------------------------------------------------------------------------------------------------------|
| option_trades | Receive live option trades throughout the trading session. Expect 6-10M records per day. |
| option_trades:TICKER | Similar to `option_trades` but receive all trades only for the specified ticker. |
| flow-alerts | Receive live flow alerts (all of them unfiltered). This data can be used to build views like [https://unusualwhales.com/option-flow-alerts](https://unusualwhales.com/option-flow-alerts). |
| price:TICKER | Receive live price updates for the given ticker. |
| news | Receive live headline news. |
| lit_trades | Receive live lit (exchange-based) trades throughout the trading session. |
| off_lit_trades | Receive live off-lit (dark pool) trades throughout the trading session. |
| gex:TICKER | Receive live gex update for the given ticker. |
| gex_strike:TICKER | Receive live gex strike updates for every strike of the given ticker. |
| gex_strike_expiry:TICKER | Receive live gex strike updates for every strike & expiry of the given ticker. |
The `option_trades` channel will stream all 6,000,000 option trades in real-time, `option_trades:&lt;TICKER&gt;` will stream
all option trades for the given ticker in real-time.
`flow-alerts` will stream from the alerts [page](https://unusualwhales.com/option-flow-alerts?limit=50)
## Connect
For a python example script that streams gex by ticker (gex:TICKER), flow alerts (flow-alerts), and all TSLA option trades (option_trades:TSLA), see our "examples" repo on Github: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output)
We will use [websocat](https://github.com/vi/websocat) to demonstrate how to connect to the WebSocket server.
```bash
websocat "wss://api.unusualwhales.com/socket?token=&lt;YOUR_API_TOKEN&gt;"
{"channel":"option_trades","msg_type":"join"}
```
The server will then reply with
```bash
["option_trades",{"response":{},"status":"ok"}]
```
indicating that the connection was successful.
You will then receive data in the following format:
```bash
[&lt;CHANNEL_NAME&gt;, &lt;PAYLOAD&gt;]
```
during market hours.
To receive the trades only for a specific ticker, use the following command:
```bash
{"channel":"option_trades","msg_type":"join"}
```
You can join multiple channels with the same websocket connection:
```bash
websocat "wss://api.unusualwhales.com/socket?token=&lt;YOUR_API_TOKEN&gt;"
{"channel":"option_trades","msg_type":"join"}
["option_trades",{"response":{},"status":"ok"}]
{"channel":"option_trades:JPM","msg_type":"join"}
["option_trades:JPM",{"response":{},"status":"ok"}]
```
## Using a client
If you are using Python, you can use the [websocket-client](https://github.com/websocket-client/websocket-client) library to connect to the server.
```python
import websocket
import time
import rel
import json
def on_message(ws, msg):
 msg = json.loads(msg)
 channel, payload = msg
 print(f"Got a message on channel {channel}: Payload: {payload}")
def on_error(ws, error):
 print(error)
def on_close(ws, close_status_code, close_msg):
 print("### closed ###")
def on_open(ws):
 print("Opened connection")
 msg = {"channel":"option_trades","msg_type":"join"}
 ws.send(json.dumps(msg))
if __name__ == "__main__":
 websocket.enableTrace(False)
 ws = websocket.WebSocketApp("wss://api.unusualwhales.com/socket?token=&lt;YOUR_TOKEN&gt;",
 on_open=on_open,
 on_message=on_message,
 on_error=on_error,
 on_close=on_close)
 ws.run_forever(dispatcher=rel, reconnect=5) # Set dispatcher to automatic reconnection, 5 second reconnect delay if connection closed unexpectedly
 rel.signal(2, rel.abort) # Keyboard Interrupt
 rel.dispatch()
## Historic data
To download/access historic data, use the endpoint [/api/option-trades/full-tape](https://api.unusualwhales.com/docs#/operations/PublicApi.OptionTradeController.full_tape)

---

## GET /api/socket/flow_alerts

**Summary:** Flow alerts

**Description:**

**NOTE:**
This is the documentation for websocket channel `flow-alerts`.
Websocket access for personal use is only available through the [Advanced plan](https://unusualwhales.com/pricing?product=api).
You can find fully-functional examples that stream data from many channels here:
- Python: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output)
- Javascript: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs)
Connect to the websocket URI:
`wss://api.unusualwhales.com/socket?token=&lt;YOUR_API_TOKEN&gt;`
then `join` the channel you wish to stream: `flow-alerts` for all flow alerts.
Payload format:
```
[
 "flow-alerts",
 {
 "rule_id": "5ce5ec11-087c-4c00-b164-08106b015856",
 "rule_name": "RepeatedHitsDescendingFill",
 "ticker": "DIA",
 "option_chain": "DIA241018C00415000",
 "underlying_price": 415.981,
 "volume": 106,
 "total_size": 50,
 "total_premium": 36466,
 "total_ask_side_prem": 36466,
 "total_bid_side_prem": 0,
 "start_time": 1726670212648,
 "end_time": 1726670212748,
 "url": "",
 "price": 7.3,
 "has_multileg": false,
 "has_sweep": false,
 "has_floor": false,
 "open_interest": 575,
 "all_opening_trades": false,
 "id": "29ed5829-e4ce-4934-876b-51985d2f9b70",
 "has_singleleg": true,
 "volume_oi_ratio": 0,
 "trade_ids": [
 "417f0cd6-09ae-4d43-8542-38557bb713aa",
 "4af4c646-4b21-4a27-8326-db7b0698d3d8",
 "74ddcd55-dcb3-4543-a488-16ee7ca91d45",
 "4ec49859-74a2-4d32-9911-ea329dd77326",
 "e164da3a-a6aa-41d9-a948-c17817453a21",
 "b0d98eeb-1429-4494-9dcc-8d5e7eb46f7d",
 "81b1dcad-f3f6-48a2-bf51-0bfd362ad372"
 ],
 "trade_count": 7,
 "expiry_count": 1,
 "executed_at": 1726670212748,
 "ask_vol": 52,
 "bid_vol": 49,
 "no_side_vol": 0,
 "mid_vol": 5,
 "multi_vol": 0,
 "stock_multi_vol": 0,
 "upstream_condition_details": [
 "auto",
 "slan"
 ],
 "exchanges": [
 "XCBO",
 "MPRL"
 ],
 "bid": "7.15",
 "ask": "7.3"
 }
]
```

---

## GET /api/socket/gex

**Summary:** GEX

**Description:**

**NOTE:**
This is the documentation for websocket channels `gex:&lt;TICKER&gt;`, `gex_strike:&lt;TICKER&gt;`, and `gex_strike_expiry:&lt;TICKER&gt;`.
Websocket access for personal use is only available through the[Advanced plan](https://unusualwhales.com/pricing?product=api).
You can find fully-functional examples that stream data from many channels here:
- Python: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output)
- Javascript: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs)
Connect to the websocket URI:
`wss://api.unusualwhales.com/socket?token=&lt;YOUR_API_TOKEN&gt;`
then `join` the channel you wish to stream, for example `gex:SPY` for live GEX updates for SPY, `gex_strike:SPY` for strike-level GEX data, or `gex_strike_expiry:SPY` for strike and expiry level GEX data.
Payload format:
Format for `gex:&lt;TICKER&gt;`:
```
[
 "gex:SPY",
 {
 "ticker": "SPY",
 "timestamp": 1726670396000,
 "gamma_per_one_percent_move_oi": "-262444980.31",
 "delta_per_one_percent_move_oi": "",
 "charm_per_one_percent_move_oi": "-1677926539943.05",
 "vanna_per_one_percent_move_oi": "2842602508.57",
 "price": "562.86",
 "gamma_per_one_percent_move_vol": "-934307209.58",
 "delta_per_one_percent_move_vol": "",
 "charm_per_one_percent_move_vol": "-556207588704.10",
 "vanna_per_one_percent_move_vol": "128814703.59",
 "gamma_per_one_percent_move_dir": "-9372185.61",
 "charm_per_one_percent_move_dir": "-2055997560.50",
 "vanna_per_one_percent_move_dir": "-6220855.09"
 }
]
```
Format for `gex_strike:&lt;TICKER&gt;`:
```
[
 "gex_strike:SPY",
 {
 "ticker": "SPY",
 "timestamp": 1726670426000,
 "call_gamma_oi": "174792.59",
 "put_gamma_oi": "-1172037.66",
 "call_charm_oi": "85658181.72",
 "put_charm_oi": "-315259003.37",
 "call_vanna_oi": "-6103.51",
 "put_vanna_oi": "1337727.64",
 "call_gamma_vol": "15596.81",
 "put_gamma_vol": "-236.69",
 "call_charm_vol": "-326871.58",
 "put_charm_vol": "-68457.78",
 "call_vanna_vol": "2063.13",
 "put_vanna_vol": "845.06",
 "strike": "290",
 "price": "562.96",
 "call_gamma_ask_vol": "-4064.62",
 "call_gamma_bid_vol": "11532.18",
 "put_gamma_ask_vol": "-140.95",
 "put_gamma_bid_vol": "95.73",
 "call_charm_ask_vol": "85184.72",
 "call_charm_bid_vol": "-241686.87",
 "put_charm_ask_vol": "-59412.37",
 "put_charm_bid_vol": "9045.42",
 "call_vanna_ask_vol": "-537.66",
 "call_vanna_bid_vol": "1525.46",
 "put_vanna_ask_vol": "523.79",
 "put_vanna_bid_vol": "-321.27"
 }
]
```
Format for `gex_strike_expiry:&lt;TICKER&gt;`:
```
[
 "gex_strike_expiry:SPY",
 {
 "ticker": "SPY",
 "expiry": "2025-01-24",
 "timestamp": 1726670426000,
 "call_gamma_oi": "174792.59",
 "put_gamma_oi": "-1172037.66",
 "call_charm_oi": "85658181.72",
 "put_charm_oi": "-315259003.37",
 "call_vanna_oi": "-6103.51",
 "put_vanna_oi": "1337727.64",
 "call_gamma_vol": "15596.81",
 "put_gamma_vol": "-236.69",
 "call_charm_vol": "-326871.58",
 "put_charm_vol": "-68457.78",
 "call_vanna_vol": "2063.13",
 "put_vanna_vol": "845.06",
 "strike": "290",
 "price": "562.96",
 "call_gamma_ask_vol": "-4064.62",
 "call_gamma_bid_vol": "11532.18",
 "put_gamma_ask_vol": "-140.95",
 "put_gamma_bid_vol": "95.73",
 "call_charm_ask_vol": "85184.72",
 "call_charm_bid_vol": "-241686.87",
 "put_charm_ask_vol": "-59412.37",
 "put_charm_bid_vol": "9045.42",
 "call_vanna_ask_vol": "-537.66",
 "call_vanna_bid_vol": "1525.46",
 "put_vanna_ask_vol": "523.79",
 "put_vanna_bid_vol": "-321.27"
 }
]
```

---

## GET /api/socket/news

**Summary:** News

**Description:**

**NOTE:**
This is the documentation for websocket channel `news`.
Websocket access for personal use is only available through the [Advanced plan](https://unusualwhales.com/pricing?product=api).
You can find fully-functional examples that stream data from many channels here:
- Python: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output)
- Javascript: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs)
Connect to the websocket URI:
`wss://api.unusualwhales.com/socket?token=&lt;YOUR_API_TOKEN&gt;`
then `join` the channel you wish to stream: `news` for all live headline news.
Payload format:
```
[
 "news",
 {
 "headline":"US Energy Secretary foresees many more LNG export deals signed",
 "timestamp":"2025-06-11T21:40:56Z",
 "source":"social-media",
 "tickers":[],
 "is_trump_ts":false
 }
]
```

---

## GET /api/socket/option_trades

**Summary:** Option trades

**Description:**

**NOTE:**
This is the documentation for websocket channels `option_trades` and `option_trades:&lt;TICKER&gt;`.
Websocket access for personal use is only available through the [Advanced plan](https://unusualwhales.com/pricing?product=api).
You can find fully-functional examples that stream data from many channels here:
- Python: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output)
- Javascript: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs)
Connect to the websocket URI:
`wss://api.unusualwhales.com/socket?token=&lt;YOUR_API_TOKEN&gt;`
then `join` the channel(s) you wish to stream, for example `option_trades` for all tickers or `option_trades:TSLA` for TSLA transactions only.
Payload format:
```
{
 "id":"a4dc6020-0611-4c23-b0bc-99944c7348ab",
 "underlying_symbol":"UVIX",
 "executed_at":1726670167412,
 "nbbo_bid":"0.01",
 "nbbo_ask":"0.09",
 "size":1,
 "price":"0.01",
 "option_symbol":"UVIX240920C00025000",
 "created_at":1726670167461,
 "report_flags":[
 ],
 "tags":[
 "bid_side",
 "bearish",
 "etf"
 ],
 "expiry":"2024-09-20",
 "option_type":"call",
 "open_interest":410,
 "strike":"25.0000000000",
 "premium":"1.00",
 "volume":105,
 "underlying_price":"4.9261",
 "ewma_nbbo_ask":"0.09",
 "ewma_nbbo_bid":"0.01",
 "implied_volatility":"8.46381958089369",
 "delta":"0.01132315610146539",
 "theta":"-0.02291485773244166",
 "gamma":"0.00962272181839715",
 "vega":"0.0001082948756510385",
 "rho":"0.000002508438316242667",
 "theo":"0.01",
 "trade_code":"slan",
 "exchange":"XCBO",
 "ask_vol":10,
 "bid_vol":95,
 "no_side_vol":0,
 "mid_vol":0,
 "multi_vol":0,
 "stock_multi_vol":0
}
```

---

## GET /api/socket/price

**Summary:** Price

**Description:**

**NOTE:**
This is the documentation for websocket channel `price:&lt;TICKER&gt;`.
Websocket access for personal use is only available through the[Advanced plan](https://unusualwhales.com/pricing?product=api).
You can find fully-functional examples that stream data from many channels here:
- Python: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output)
- Javascript: [https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs](https://github.com/unusual-whales/api-examples/tree/main/examples/ws-multi-channel-multi-output-nodejs)
Connect to the websocket URI:
`wss://api.unusualwhales.com/socket?token=&lt;YOUR_API_TOKEN&gt;`
then `join` the channel you wish to stream, for example `price:SPY` for live price updates for SPY.
Payload format:
```
["price:SPY",{"close":"562.82","time":1726670327692,"vol":6015555}]
```

---

## GET /api/stock/{sector}/tickers

**Summary:** Companies in Sector

**Description:**

Returns a list of tickers which are in the given sector.

---

## GET /api/stock/{ticker}/atm-chains

**Summary:** ATM Chains

**Description:**

Returns the ATM chains for the given expirations

---

## GET /api/stock/{ticker}/expiry-breakdown

**Summary:** Expiry Breakdown

**Description:**

Returns all expirations for the given trading day for a ticker.

---

## GET /api/stock/{ticker}/flow-alerts

**Summary:** Flow Alerts

**Description:**

This endpoint has been deprecated and will be removed.
Please migrate to this Flow Alerts endpoint, which provides a more detailed response: [https://api.unusualwhales.com/docs#/operations/PublicApi.OptionTradeController.flow_alerts](https://api.unusualwhales.com/docs#/operations/PublicApi.OptionTradeController.flow_alerts)

---

## GET /api/stock/{ticker}/flow-per-expiry

**Summary:** Flow per expiry

**Description:**

Returns the option flow per expiry for the last trading day

---

## GET /api/stock/{ticker}/flow-per-strike

**Summary:** Flow per strike

**Description:**

Returns the option flow per strike for a given trading day.

---

## GET /api/stock/{ticker}/flow-per-strike-intraday

**Summary:** Flow per strike intraday

**Description:**

Returns the options flow for a given date in one minute intervals (the one minute intervals are not aggregated with each other).

---

## GET /api/stock/{ticker}/flow-recent

**Summary:** Recent flows

**Description:**

Returns the latest flows for the given ticker. Optionally a min premium and a side can be supplied in the query for further filtering.

---

## GET /api/stock/{ticker}/greek-exposure

**Summary:** Greek Exposure

**Description:**

Greek Exposure is the assumed greek exposure that market makers are exposed to.
The most popular greek exposure is gamma exposure (GEX).
Investors and large funds lower risk and protect their money by selling calls and buying puts. Market makers provide the liquidity to facilitate these trades.
GEX assumes that market makers are part of every transaction and that the bulk of their transactions are buying calls and selling puts to investors hedging their portfolios.
If a market maker has one contract open with a gamma value of 0.05, then that market maker is exposed to 0.05 * [100 shares] of gamma. The total market maker exposure is calculated by summing up the exposure values of all open contracts determined by the daily open interest.
Market makers profit from the bid-ask spreads and as such, they constantly gamma hedge (they buy and sell shares to keep their positions delta neutral).
Long call positions are positive gamma - as the stock price increases and delta rises (approaches 1), market makers hedge by selling shares, and they buy shares if the stock price decreases and delta falls.
Short put positions are negative gamma - as the stock price increases and delta falls (approaches -1), market makers hedge by buying shares, and they sell shares if the stock price decreases and delta rises.
As such, in the event of large positive gamma, volatility is suppressed as market makers will hedge by buying as the stock price decreases and selling as the stock price increases. And in the event of large negative gamma, volatility is amplified as market makers will hedge by buying as the stock price increases and selling as the stock price decreases.

---

## GET /api/stock/{ticker}/greek-exposure/expiry

**Summary:** Greek Exposure By Expiry

**Description:**

The greek exposure of a ticker grouped by expiry dates across all contracts on a given market date.

---

## GET /api/stock/{ticker}/greek-exposure/strike

**Summary:** Greek Exposure By Strike

**Description:**

The greek exposure of a ticker grouped by strike price across all contracts on a given market date.

---

## GET /api/stock/{ticker}/greek-exposure/strike-expiry

**Summary:** Greek Exposure By Strike And Expiry

**Description:**

The greek exposure of a ticker grouped by strike price for a specific expiry date.

---

## GET /api/stock/{ticker}/greek-flow

**Summary:** Greek flow

**Description:**

Returns the tickers greek flow (delta & vega flow) for the given market day broken down per minute.
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/stock/{ticker}/greek-flow/{expiry}

**Summary:** Greek flow by expiry

**Description:**

Returns the tickers greek flow (delta & vega flow) for the given market day broken down per minute & expiry.
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/stock/{ticker}/greeks

**Summary:** Greeks

**Description:**

Returns the greeks for each strike for a single expiry date.

---

## GET /api/stock/{ticker}/historical-risk-reversal-skew

**Summary:** Historical Risk Reversal Skew

**Description:**

Returns the historical risk reversal skew (the difference between put and call volatility) at a delta of 25 or 10 (colloquial for 0.25 or 0.1) for a given expiry date.

---

## GET /api/stock/{ticker}/info

**Summary:** Information

**Description:**

Returns a information about the given ticker.

---

## GET /api/stock/{ticker}/insider-buy-sells

**Summary:** Insider buy & sells

**Description:**

Returns the total amount of purchases & sells as well as notional values for insider transactions
for the given ticker

---

## GET /api/stock/{ticker}/interpolated-iv

**Summary:** Interpolated IV

**Description:**

Returns the Interpolated IV for a given trading day. If there is no expiration then the data is calcualted via linear interpolation
with the next 2 closest expirations
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/stock/{ticker}/iv-rank

**Summary:** IV Rank

**Description:**

Returns the IV rank data for a ticker over a period of time.
IV rank is a measure of where current implied volatility stands relative to its historical range.

---

## GET /api/stock/{ticker}/max-pain

**Summary:** Max Pain

**Description:**

Returns the max pain for all expirations for the given ticker for the last 120 days

---

## GET /api/stock/{ticker}/net-prem-ticks

**Summary:** Call/Put Net/Vol Ticks

**Description:**

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
.then(r =&gt; r.json())
.then(r =&gt; {
 const {data} = r.data;
 const fieldsToSum = [
 "net_call_premium",
 "net_call_volume",
 "net_put_premium",
 "net_put_volume"
 ];
 let result = [];
 data.forEach((e, idx) =&gt; {
 e.net_call_premium = parseFloat(e.net_call_premium);
 e.net_put_premium = parseFloat(e.net_put_premium);
 if (idx !== 0) {
 fieldsToSum.forEach((field) =&gt; {
 e[field] = e[field] + result[idx-1][field];
 })
 }
 result.push(e);
 })
 return result;
});
```

---

## GET /api/stock/{ticker}/nope

**Summary:** Nope

**Description:**

Returns the tickers NOPE for the given market day broken down per minute.
NOPE is the Net Options Pricing Effect, which tracks the intraday net delta of any ticker, but most research has been done on indexes.
It functions under 2 assumptions:
1) MM's take short side of any call or put traded during the day
2) MM's try to minimize risk by dynamically hedging their delta-gamma exposure, and do so by buying/shorting the underlying stock in proportion to the total net delta being tradedBased on these assumptions, options trading in large amounts (re: very liquid tickers) can potentially drive the price of the underlying, to a certain extent. Large movements might exacerbate this real time hedging, and drive price movements further in respective directions.
In short, NOPE represents a best-estimate of expected number of shares to be hedged at any given time, and will show a general expected direction on the underlying
The original NOPE calculation was based on the following formula:
`NOPE = (Call Delta - Put Delta) / Stock Volume`
where call/put delta is obtained by multiplying each chains volume with its latest delta and then summing those values up.
`NOPE fill` on the other hand is based on the delta at the time of the transaction
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/stock/{ticker}/ohlc/{candle_size}

**Summary:** OHLC

**Description:**

Returns the Open High Low Close (OHLC) candle data for a given ticker.
Results are limited to 2,500 elements even if there are more available.
Note: If you select 1d as a candle_size then the candles won't have a start & end time.
Note: Suppose you enter end_date value 2024-11-25 which was a Monday. Your response will include 1-2 hours of data from Tuesday 2024-11-26 due to UTC date rollover.
Rest-assured, the response data covers the full trading day (based on Eastern time) according to your entered end_date.

---

## GET /api/stock/{ticker}/oi-change

**Summary:** OI Change

**Description:**

Returns the tickers contracts' OI change data ordered by absolute OI change (default: descending).
Date must be the current or a past date. If no date is given, returns data for the current/last market day.

---

## GET /api/stock/{ticker}/oi-per-expiry

**Summary:** OI per Expiry

**Description:**

Returns the total open interest for calls and puts for a specific expiry date.

---

## GET /api/stock/{ticker}/oi-per-strike

**Summary:** OI per Strike

**Description:**

Returns the total open interest for calls and puts for a specific strike.

---

## GET /api/stock/{ticker}/option-chains

**Summary:** Option Chains

**Description:**

Returns all option symbols for the given ticker that were present at the given day.
If no date is given, returns data for the current/last market day.
You can use the following regex to extract underlying ticker, option type, expiry & strike:
`^(?&lt;symbol&gt;[\w]*)(?&lt;expiry&gt;(\d{2})(\d{2})(\d{2}))(?&lt;type&gt;[PC])(?&lt;strike&gt;\d{8})$`
Keep in mind that the strike needs to be divided by 1,000.

---

## GET /api/stock/{ticker}/option-contracts

**Summary:** Option contracts

**Description:**

Returns all option contracts for the given ticker

---

## GET /api/stock/{ticker}/option/stock-price-levels

**Summary:** Option Price Levels

**Description:**

Returns the call and put volume per price level for the given ticker.
----
Can be used to build a chart such as following:
![Option Price Level chart](https://i.imgur.com/y6BZ4sG.png)

---

## GET /api/stock/{ticker}/option/volume-oi-expiry

**Summary:** Volume & OI per Expiry

**Description:**

Returns the total volume and open interest per expiry for the given ticker.

---

## GET /api/stock/{ticker}/options-volume

**Summary:** Options Volume

**Description:**

Returns the options volume & premium for all trade executions
that happened on a given trading date for the given ticker.
----
This can be used to build a ticker options overview such as:
![Table](https://i.imgur.com/7FHyuqc.png)
----
![Line](https://i.imgur.com/UnVryDK.png)

---

## GET /api/stock/{ticker}/spot-exposures

**Summary:** Spot GEX exposures per 1min

**Description:**

Returns the spot GEX exposures for the given ticker per minute.
Spot GEX is the assumed $ value of the given greek (ie. gamma) exposure that market makers need to hedge per 1% change of the underlying stock's price movement. A positive value is long and a negative value is short.
Investors and large funds lower risk and protect their money by selling calls and buying puts. Market makers provide the liquidity to facilitate these trades.
GEX assumes that market makers are part of every transaction and that the bulk of their transactions are buying calls and selling puts to investors hedging their portfolios.
If a market maker has one contract open with a gamma value of 0.05, then if the underlying stock price moves by 1%, that market maker is exposed to $[0.05 * 100 shares * 0.01 * stock price * underlying parameter of the greek variable (for gamma this variable is the stock price)]. The total market maker spot exposure is calculated by summing up the spot exposure of all open contracts determined by the daily open interest or by volume.
Market makers profit from the bid-ask spreads and as such, they constantly gamma hedge (they buy and sell shares to keep their positions delta neutral).
Long call positions are positive gamma - as the stock price increases and delta rises (approaches 1), market makers hedge by selling shares, and they buy shares if the stock price decreases and delta falls.
Short put positions are negative gamma - as the stock price increases and delta falls (approaches -1), market makers hedge by buying shares, and they sell shares if the stock price decreases and delta rises.
As such, in the event of large positive gamma, volatility is suppressed as market makers will hedge by buying as the stock price decreases and selling as the stock price increases. And in the event of large negative gamma, volatility is amplified as market makers will hedge by buying as the stock price increases and selling as the stock price decreases.

---

## GET /api/stock/{ticker}/spot-exposures/expiry-strike

**Summary:** Spot GEX exposures by strike & expiry

**Description:**

Returns the most recent spot GEX exposures across all strikes for the given ticker & expiration on a given date. Calculated either with open interest or with volume.
Data is available since 2025-01-16.
[Click here for the spot docs](https://api.unusualwhales.com/docs#/operations/PublicApi.TickerController.spot_exposures_by_strike)

---

## GET /api/stock/{ticker}/spot-exposures/strike

**Summary:** Spot GEX exposures by strike

**Description:**

Returns the most recent spot GEX exposures across all strikes for the given ticker on a given date. Calculated either with open interest or with volume.
Spot GEX is the assumed $ value of the given greek (ie. gamma) exposure that market makers need to hedge per 1% change of the underlying stock's price movement. A positive value is long and a negative value is short.
Investors and large funds lower risk and protect their money by selling calls and buying puts. Market makers provide the liquidity to facilitate these trades.
GEX assumes that market makers are part of every transaction and that the bulk of their transactions are buying calls and selling puts to investors hedging their portfolios.
If a market maker has one contract open with a gamma value of 0.05, then if the underlying stock price moves by 1%, that market maker is exposed to $[0.05 * 100 shares * 0.01 * stock price * underlying parameter of the greek variable (for gamma this variable is the stock price)]. The total market maker spot exposure is calculated by summing up the spot exposure of all open contracts determined by the daily open interest or by volume.
Market makers profit from the bid-ask spreads and as such, they constantly gamma hedge (they buy and sell shares to keep their positions delta neutral).
Long call positions are positive gamma - as the stock price increases and delta rises (approaches 1), market makers hedge by selling shares, and they buy shares if the stock price decreases and delta falls.
Short put positions are negative gamma - as the stock price increases and delta falls (approaches -1), market makers hedge by buying shares, and they sell shares if the stock price decreases and delta rises.
As such, in the event of large positive gamma, volatility is suppressed as market makers will hedge by buying as the stock price decreases and selling as the stock price increases. And in the event of large negative gamma, volatility is amplified as market makers will hedge by buying as the stock price increases and selling as the stock price decreases.
In the case of directionalized volume, the bid/ask spread is used when calculating the exposures. When a trade is made closer to the ask, the Market Maker would be selling the contract and when a trade is closer to the bid then the Market Maker would be buying the contract.
For example, the gamma exposure for directional volume is call_gamma_ask, and the value will be negative since a trade made at the ask means the market makers are selling/short the call.
To get the full directionalized exposure, just sum up the call ask, call bid, put ask and put bid of a greek and strike.

---

## GET /api/stock/{ticker}/spot-exposures/{expiry}/strike

**Summary:** Spot GEX exposures by strike & expiry (Deprecated)

**Description:**

This endpoint has been deprecated and will be removed, please migrate to the new [endpoint](https://api.unusualwhales.com/docs#/operations/PublicApi.TickerController.spot_exposures_by_strike_expiry_v2)

---

## GET /api/stock/{ticker}/stock-state

**Summary:** Stock State

**Description:**

Returns the last stock state for the given ticker.
This is the easiest way to retreive the open, close, high, low and volume of the last trading day.

---

## GET /api/stock/{ticker}/stock-volume-price-levels

**Summary:** Off/Lit Price Levels

**Description:**

Returns the lit & off lit stock volume per price level for the given ticker.
----
Important: The volume does **NOT** represent the full market dialy volume. It
only represents the volume of executed trades on exchanges operated by Nasdaq
and FINRA off lit exchanges.

---

## GET /api/stock/{ticker}/volatility/realized

**Summary:** Realized Volatility

**Description:**

The implied and realized volatility of a given ticker. The implied volatility is the expected 30 day forward looking volatility.
The realized/historical volatility is the volatility of the stock price in the last 30 days.
Since IV is forward looking, the realized volatility is shifted 30 days backwards to see if the past IV pricings were frequently underpricing or overpricing the realized volatility risk.

---

## GET /api/stock/{ticker}/volatility/stats

**Summary:** Volatility Statistics

**Description:**

Returns comprehensive volatility statistics for a ticker on a specific date, including
implied volatility data, realized volatility data, and their respective high/low values
for the past year.

---

## GET /api/stock/{ticker}/volatility/term-structure

**Summary:** Implied Volatility Term Structure

**Description:**

The average of the latest volatilities for the at the money call and put contracts for every expiry date.
