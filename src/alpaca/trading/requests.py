from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from pydantic import model_validator

from alpaca.common.enums import Sort
from alpaca.common.models import ModelWithID
from alpaca.common.requests import NonEmptyRequest
from alpaca.trading.enums import (
    AssetClass,
    AssetExchange,
    AssetStatus,
    ContractType,
    CorporateActionDateType,
    CorporateActionType,
    ExerciseStyle,
    OrderClass,
    OrderSide,
    OrderType,
    PositionIntent,
    QueryOrderStatus,
    TimeInForce,
)


class ClosePositionRequest(NonEmptyRequest):
    """
    Attributes:
        qty (str): The number of shares to liquidate.
        percentage (str): The percentage of shares to liquidate.
    """

    qty: str | None = None
    percentage: str | None = None

    @model_validator(mode="before")

    @classmethod
    def root_validator(cls, values: dict) -> dict:
        qty = values.get("qty")
        percentage = values.get("percentage")
        if qty is None and percentage is None:
            raise ValueError(
                "qty or percentage must be given to the ClosePositionRequest, got None for both."
            )

        if qty is not None and percentage is not None:
            raise ValueError(
                "Only one of qty or percentage must be given to the ClosePositionRequest, got both."
            )

        return values


class GetPortfolioHistoryRequest(NonEmptyRequest):
    """
    Attributes:
        period (Optional[str]): The duration of the data in number + unit, such as 1D. unit can be D for day, W for
          week, M for month and A for year. Defaults to 1M.
        timeframe (Optional[str]): The resolution of time window. 1Min, 5Min, 15Min, 1H, or 1D. If omitted, 1Min for
          less than 7 days period, 15Min for less than 30 days, or otherwise 1D.
        date_end (Optional[date]): The date the data is returned up to. Defaults to the current market date (rolls over
          at the market open if extended_hours is false, otherwise at 7am ET).
        extended_hours (Optional[bool]): If true, include extended hours in the result. This is effective only for
          timeframe less than 1D.
    """

    period: str | None = None
    timeframe: str | None = None
    date_end: date | None = None
    extended_hours: bool | None = None


class GetCalendarRequest(NonEmptyRequest):
    """
    Represents the optional filtering you can do when requesting a Calendar object
    """

    start: date | None = None
    end: date | None = None


class CreateWatchlistRequest(NonEmptyRequest):
    """
    Represents the fields you can specify when creating a Watchlist

    Attributes:
        name(str): Name of the Watchlist
        symbols(List[str]): Symbols of Assets to watch
    """

    name: str
    symbols: list[str]


class UpdateWatchlistRequest(NonEmptyRequest):
    """
    Represents the fields you can specify when updating a Watchlist

    Attributes:
        name(Optional[str]): Name of the Watchlist
        symbols(Optional[List[str]]): Symbols of Assets to watch
    """

    name: str | None = None
    symbols: list[str] | None = None

    @model_validator(mode="before")

    @classmethod
    def root_validator(cls, values: dict) -> dict:
        if ("name" not in values or values["name"] is None) and (
            "symbols" not in values or values["symbols"] is None
        ):
            raise ValueError("One of 'name' or 'symbols' must be defined")

        return values


class GetAssetsRequest(NonEmptyRequest):
    """
    When querying for available assets, this model provides the parameters that can be filtered by.

    Attributes:
        status (Optional[AssetStatus]): The active status of the asset.
        asset_class (Optional[AssetClass]): The type of asset (i.e. us_equity, crypto).
        exchange (Optional[AssetExchange]): The exchange the asset trades on.
        attributes (Optional[str]): Comma separated values to query for more than one attribute.
    """

    status: AssetStatus | None = None
    asset_class: AssetClass | None = None
    exchange: AssetExchange | None = None
    attributes: str | None = None


class TakeProfitRequest(NonEmptyRequest):
    """
    Used for providing take profit details for a bracket order.

    Attributes:
        limit_price (float): The execution price for exiting a profitable trade.
    """

    limit_price: float


class StopLossRequest(NonEmptyRequest):
    """
    Used for providing stop loss details for a bracket order.

    Attributes:
        stop_price (float): The price at which the stop loss is triggered.
        limit_price (Optional[float]): The execution price for exiting a losing trade. If not provided, the
            stop loss will execute as a market order.
    """

    stop_price: float
    limit_price: float | None = None


class GetOrdersRequest(NonEmptyRequest):
    """Contains data for submitting a request to retrieve orders.

    Attributes:
        status (Optional[QueryOrderStatus]): Order status to be queried. open, closed or all. Defaults to open. Not same as OrderStatus property of Order.
        limit (Optional[int]): The maximum number of orders in response. Defaults to 50 and max is 500.
        after (Optional[datetime]): The response will include only ones submitted after this timestamp.
        until (Optional[datetime]): The response will include only ones submitted until this timestamp.
        direction (Optional[Sort]): The chronological order of response based on the submission time. asc or desc. Defaults to desc.
        nested (Optional[bool]): If true, the result will roll up multi-leg orders under the legs field of primary order.
        side (Optional[OrderSide]): Filters down to orders that have a matching side field set.
        symbols (Optional[List[str]]): List of symbols to filter by.
    """

    status: QueryOrderStatus | None = None
    limit: int | None = None  # not pagination = None
    after: datetime | None = None
    until: datetime | None = None
    direction: Sort | None = None
    nested: bool | None = None
    side: OrderSide | None = None
    symbols: list[str] | None = None


class GetOrderByIdRequest(NonEmptyRequest):
    """Contains data for submitting a request to retrieve a single order by its order id.

    Attributes:
        nested (bool): If true, the result will roll up multi-leg orders under the legs field of primary order.
    """

    nested: bool


class ReplaceOrderRequest(NonEmptyRequest):
    """Contains data for submitting a request to replace an order.

    Attributes:
        qty (Optional[int]): Number of shares to trade
        time_in_force (Optional[TimeInForce]): The new expiration logic of the order.
        limit_price (Optional[float]): Required if type of order being replaced is limit or stop_limit
        stop_price (Optional[float]): Required if type of order being replaced is stop or stop_limit
        trail (Optional[float]): The new value of the trail_price or trail_percent value (works only for type=“trailing_stop”)
        client_order_id (Optional[str]): A unique identifier for the order.
    """

    qty: int | None = None
    time_in_force: TimeInForce | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    trail: float | None = None
    client_order_id: str | None = None

    @model_validator(mode="before")

    @classmethod
    def root_validator(cls, values: dict) -> dict:
        qty = values.get("qty")
        limit_price = values.get("limit_price")
        stop_price = values.get("stop_price")
        trail = values.get("trail")

        if (qty is not None) and (qty <= 0):
            raise ValueError("qty must be greater than 0")
        if (limit_price is not None) and (limit_price <= 0):
            raise ValueError("limit_price must be greater than 0")
        if (stop_price is not None) and (stop_price <= 0):
            raise ValueError("stop_price must be greater than 0")
        if (trail is not None) and (trail <= 0):
            raise ValueError("trail must be greater than 0")

        return values


class CancelOrderResponse(ModelWithID):
    """
    Data returned after requesting to cancel an order. It contains the cancel status of an order.

    Attributes:
        id (UUID): The order id
        status (int): The HTTP status returned after attempting to cancel the order.
        body (Dict[str, Any]): an error description
    """

    status: int
    body: dict[str, Any] | None = None


class OrderRequest(NonEmptyRequest):
    """A base class for requests for creating an order. You probably shouldn't directly use
    this class when submitting an order. Instead, use one of the order type specific classes.

    Attributes:
        symbol (str): The symbol identifier for the asset being traded
        qty (Optional[float]): The number of shares to trade. Fractional qty for stocks only with market orders.
        notional (Optional[float]): The base currency value of the shares to trade. For stocks, only works with MarketOrders.
            **Does not work with qty**.
        side (OrderSide): Whether the order will buy or sell the asset.
        type (OrderType): The execution logic type of the order (market, limit, etc).
        time_in_force (TimeInForce): The expiration logic of the order.
        extended_hours (Optional[float]): Whether the order can be executed during regular market hours.
        client_order_id (Optional[str]): A string to identify which client submitted the order.
        order_class (Optional[OrderClass]): The class of the order. Simple orders have no other legs.
        take_profit (Optional[TakeProfitRequest]): For orders with multiple legs, an order to exit a profitable trade.
        stop_loss (Optional[StopLossRequest]): For orders with multiple legs, an order to exit a losing trade.
        position_intent (Optional[PositionIntent]): An enum to indicate the desired position strategy: BTO, BTC, STO, STC.
    """

    symbol: str
    qty: float | None = None
    notional: float | None = None
    side: OrderSide
    type: OrderType
    time_in_force: TimeInForce
    order_class: OrderClass | None = None
    extended_hours: bool | None = None
    client_order_id: str | None = None
    take_profit: TakeProfitRequest | None = None
    stop_loss: StopLossRequest | None = None
    position_intent: PositionIntent | None = None

    @model_validator(mode="before")

    @classmethod
    def root_validator(cls, values: dict) -> dict:
        qty_set = "qty" in values and values["qty"] is not None
        notional_set = "notional" in values and values["notional"] is not None

        if not qty_set and not notional_set:
            raise ValueError("At least one of qty or notional must be provided")
        elif qty_set and notional_set:
            raise ValueError("Both qty and notional can not be set.")

        return values


class MarketOrderRequest(OrderRequest):
    """
    Used to submit a market order.

    Attributes:
        symbol (str): The symbol identifier for the asset being traded
        qty (Optional[float]): The number of shares to trade. Fractional qty for stocks only with market orders.
        notional (Optional[float]): The base currency value of the shares to trade. For stocks, only works with MarketOrders.
            **Does not work with qty**.
        side (OrderSide): Whether the order will buy or sell the asset.
        type (OrderType): The execution logic type of the order (market, limit, etc).
        time_in_force (TimeInForce): The expiration logic of the order.
        extended_hours (Optional[float]): Whether the order can be executed during regular market hours.
        client_order_id (Optional[str]): A string to identify which client submitted the order.
        order_class (Optional[OrderClass]): The class of the order. Simple orders have no other legs.
        take_profit (Optional[TakeProfitRequest]): For orders with multiple legs, an order to exit a profitable trade.
        stop_loss (Optional[StopLossRequest]): For orders with multiple legs, an order to exit a losing trade.
        position_intent (Optional[PositionIntent]): An enum to indicate the desired position strategy: BTO, BTC, STO, STC.
    """

    def __init__(self, **data: Any) -> None:
        data["type"] = OrderType.MARKET

        super().__init__(**data)


class StopOrderRequest(OrderRequest):
    """
    Used to submit a stop order.

    Attributes:
        symbol (str): The symbol identifier for the asset being traded
        qty (Optional[float]): The number of shares to trade. Fractional qty for stocks only with market orders.
        notional (Optional[float]): The base currency value of the shares to trade. For stocks, only works with MarketOrders.
            **Does not work with qty**.
        side (OrderSide): Whether the order will buy or sell the asset.
        type (OrderType): The execution logic type of the order (market, limit, etc).
        time_in_force (TimeInForce): The expiration logic of the order.
        extended_hours (Optional[float]): Whether the order can be executed during regular market hours.
        client_order_id (Optional[str]): A string to identify which client submitted the order.
        order_class (Optional[OrderClass]): The class of the order. Simple orders have no other legs.
        take_profit (Optional[TakeProfitRequest]): For orders with multiple legs, an order to exit a profitable trade.
        stop_loss (Optional[StopLossRequest]): For orders with multiple legs, an order to exit a losing trade.
        stop_price (float): The price at which the stop order is converted to a market order or a stop limit
            order is converted to a limit order.
        position_intent (Optional[PositionIntent]): An enum to indicate the desired position strategy: BTO, BTC, STO, STC.
    """

    stop_price: float

    def __init__(self, **data: Any) -> None:
        data["type"] = OrderType.STOP

        super().__init__(**data)


class LimitOrderRequest(OrderRequest):
    """
    Used to submit a limit order.

    Attributes:
        symbol (str): The symbol identifier for the asset being traded
        qty (Optional[float]): The number of shares to trade. Fractional qty for stocks only with market orders.
        notional (Optional[float]): The base currency value of the shares to trade. For stocks, only works with MarketOrders.
            **Does not work with qty**.
        side (OrderSide): Whether the order will buy or sell the asset.
        type (OrderType): The execution logic type of the order (market, limit, etc).
        time_in_force (TimeInForce): The expiration logic of the order.
        extended_hours (Optional[float]): Whether the order can be executed during regular market hours.
        client_order_id (Optional[str]): A string to identify which client submitted the order.
        order_class (Optional[OrderClass]): The class of the order. Simple orders have no other legs.
        take_profit (Optional[TakeProfitRequest]): For orders with multiple legs, an order to exit a profitable trade.
        stop_loss (Optional[StopLossRequest]): For orders with multiple legs, an order to exit a losing trade.
        limit_price (Optional[float]): The worst fill price for a limit or stop limit order.
        position_intent (Optional[PositionIntent]): An enum to indicate the desired position strategy: BTO, BTC, STO, STC.
    """

    limit_price: float | None = None

    def __init__(self, **data: Any) -> None:
        data["type"] = OrderType.LIMIT

        super().__init__(**data)

    @model_validator(mode="before")

    @classmethod
    def root_validator(cls, values: dict) -> dict:
        if values.get("order_class", "") != OrderClass.OCO:
            limit_price = values.get("limit_price")
            if limit_price is None:
                raise ValueError("limit_price is required")
        return values


class StopLimitOrderRequest(OrderRequest):
    """
    Used to submit a stop limit order.

    Attributes:
        symbol (str): The symbol identifier for the asset being traded
        qty (Optional[float]): The number of shares to trade. Fractional qty for stocks only with market orders.
        notional (Optional[float]): The base currency value of the shares to trade. For stocks, only works with MarketOrders.
            **Does not work with qty**.
        side (OrderSide): Whether the order will buy or sell the asset.
        type (OrderType): The execution logic type of the order (market, limit, etc).
        time_in_force (TimeInForce): The expiration logic of the order.
        extended_hours (Optional[float]): Whether the order can be executed during regular market hours.
        client_order_id (Optional[str]): A string to identify which client submitted the order.
        order_class (Optional[OrderClass]): The class of the order. Simple orders have no other legs.
        take_profit (Optional[TakeProfitRequest]): For orders with multiple legs, an order to exit a profitable trade.
        stop_loss (Optional[StopLossRequest]): For orders with multiple legs, an order to exit a losing trade.
        stop_price (float): The price at which the stop order is converted to a market order or a stop limit
            order is converted to a limit order.
        limit_price (float): The worst fill price for a limit or stop limit order.
        position_intent (Optional[PositionIntent]): An enum to indicate the desired position strategy: BTO, BTC, STO, STC.
    """

    stop_price: float
    limit_price: float

    def __init__(self, **data: Any) -> None:
        data["type"] = OrderType.STOP_LIMIT

        super().__init__(**data)


class TrailingStopOrderRequest(OrderRequest):
    """
    Used to submit a trailing stop order.

    Attributes:
        symbol (str): The symbol identifier for the asset being traded
        qty (Optional[float]): The number of shares to trade. Fractional qty for stocks only with market orders.
        notional (Optional[float]): The base currency value of the shares to trade. For stocks, only works with MarketOrders.
            **Does not work with qty**.
        side (OrderSide): Whether the order will buy or sell the asset.
        type (OrderType): The execution logic type of the order (market, limit, etc).
        time_in_force (TimeInForce): The expiration logic of the order.
        extended_hours (Optional[float]): Whether the order can be executed during regular market hours.
        client_order_id (Optional[str]): A string to identify which client submitted the order.
        order_class (Optional[OrderClass]): The class of the order. Simple orders have no other legs.
        take_profit (Optional[TakeProfitRequest]): For orders with multiple legs, an order to exit a profitable trade.
        stop_loss (Optional[StopLossRequest]): For orders with multiple legs, an order to exit a losing trade.
        trail_price (Optional[float]): The absolute price difference by which the trailing stop will trail.
        trail_percent (Optional[float]): The percent price difference by which the trailing stop will trail.
        position_intent (Optional[PositionIntent]): An enum to indicate the desired position strategy: BTO, BTC, STO, STC.
    """

    trail_price: float | None = None
    trail_percent: float | None = None

    def __init__(self, **data: Any) -> None:
        data["type"] = OrderType.TRAILING_STOP

        super().__init__(**data)

    @model_validator(mode="before")

    @classmethod
    def root_validator(cls, values: dict) -> dict:
        trail_percent_set = (
            "trail_percent" in values and values["trail_percent"] is not None
        )
        trail_price_set = "trail_price" in values and values["trail_price"] is not None

        if not trail_percent_set and not trail_price_set:
            raise ValueError(
                "Either trail_percent or trail_price must be set for a trailing stop order."
            )
        elif trail_percent_set and trail_price_set:
            raise ValueError("Both trail_percent and trail_price cannot be set.")

        return values


class GetCorporateAnnouncementsRequest(NonEmptyRequest):
    """
    Contains parameters for querying corporate action data.
    Attributes:
        ca_types (List[CorporateActionType]): A list of corporate action types.
        since (date): The start (inclusive) of the date range when searching corporate action announcements.
            The date range is limited to 90 days.
        until (date): The end (inclusive) of the date range when searching corporate action announcements.
            The date range is limited to 90 days.
        symbol (Optional[str]): The symbol of the company initiating the announcement.
        cusip (Optional[str]): The CUSIP of the company initiating the announcement.
        date_type (Optional[CorporateActionDateType]): The date type for the announcement.
    """

    ca_types: list[CorporateActionType]
    since: date
    until: date
    symbol: str | None = None
    cusip: str | None = None
    date_type: CorporateActionDateType | None = None

    @model_validator(mode="before")

    @classmethod
    def root_validator(cls, values: dict) -> dict:
        since = pd.Timestamp(values.get("since")).date()
        until = pd.Timestamp(values.get("until")).date()

        if (
            since is not None
            and until is not None
            and (until - since) > timedelta(days=90)
        ):
            raise ValueError("The date range is limited to 90 days.")

        return values


class GetOptionContractsRequest(NonEmptyRequest):
    """
    Used to fetch option contracts for a given underlying symbol.

    Attributes:
        underlying_symbols (Optional[List[str]]): The underlying symbols for the option contracts to be returned. (e.g. ["AAPL", "SPY"])
        status (Optional[AssetStatus]): The status of the asset.
        expiration_date (Optional[Union[date, str]]): The expiration date of the option contract. (YYYY-MM-DD)
        expiration_date_gte (Optional[Union[date, str]]): The expiration date of the option contract greater than or equal to. (YYYY-MM-DD)
        expiration_date_lte (Optional[Union[date, str]]): The expiration date of the option contract less than or equal to. (YYYY-MM-DD)
        root_symbol (Optional[str]): The option root symbol.
        type (Optional[ContractType]): The option contract type.
        style (Optional[ExerciseStyle]): The option contract style.
        strike_price_gte (Optional[str]): The option contract strike price greater than or equal to.
        strike_price_lte (Optional[str]): The option contract strike price less than or equal to.
        limit (Optional[int]): The number of contracts to limit per page (default=100, max=10000).
        page_token (Optional[str]): Pagination token to continue from. The value to pass here is returned in specific requests when more data is available than the request limit allows.
    """

    underlying_symbols: list[str] | None = None
    status: AssetStatus | None = AssetStatus.ACTIVE
    expiration_date: date | str | None = None
    expiration_date_gte: date | str | None = None
    expiration_date_lte: date | str | None = None
    root_symbol: str | None = None
    type: ContractType | None = None
    style: ExerciseStyle | None = None
    strike_price_gte: str | None = None
    strike_price_lte: str | None = None

    limit: int | None = None
    page_token: str | None = None
