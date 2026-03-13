from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.single_trade_external_hour_sold_code import SingleTradeExternalHourSoldCode
from ..models.single_trade_sale_cond_code import SingleTradeSaleCondCode
from ..models.single_trade_settlement import SingleTradeSettlement
from ..models.single_trade_trade_code import SingleTradeTradeCode
from ..types import UNSET, Unset

T = TypeVar("T", bound="DarkpoolTrade")


@_attrs_define
class DarkpoolTrade:
    """A darkpool trade.

    Example:
        {'executed_at': datetime.datetime(2023, 2, 16, 0, 59, 44, tzinfo=datetime.timezone.utc), 'ext_hour_sold_codes':
            'extended_hours_trade', 'market_center': 'L', 'nbbo_ask': '19', 'nbbo_ask_quantity': 6600, 'nbbo_bid': '18.99',
            'nbbo_bid_quantity': 29100, 'premium': '121538.56', 'price': '18.9904', 'sale_cond_codes': None, 'size': 6400,
            'ticker': 'QID', 'tracking_id': 71984388012245, 'trade_code': None, 'trade_settlement': 'regular_settlement',
            'volume': 9946819}

    Attributes:
        canceled (Union[Unset, bool]): Whether the trade has been cancelled. Example: True.
        executed_at (Union[Unset, str]): The time with timezone when a trade was executed. Example: 2023-02-16
            00:59:44+00:00.
        ext_hour_sold_codes (Union[Unset, SingleTradeExternalHourSoldCode]): The code describing why the trade happened
            outside of regular market hours. Null if none applies. Example: sold_out_of_sequence.
        market_center (Union[Unset, str]): The market center code. Example: L.
        nbbo_ask (Union[Unset, float]): The price of the tick Example: 19.32.
        nbbo_ask_quantity (Union[Unset, float]): The price of the tick Example: 19.32.
        nbbo_bid (Union[Unset, float]): The price of the tick Example: 19.32.
        nbbo_bid_quantity (Union[Unset, float]): The price of the tick Example: 19.32.
        premium (Union[Unset, str]): The total option premium. Example: 27723806.00.
        price (Union[Unset, str]): The price of the trade. Example: 18.9904.
        sale_cond_codes (Union[Unset, SingleTradeSaleCondCode]): The sale condition code. Null if none applies. Example:
            contingent_trade.
        size (Union[Unset, int]): The size of the transaction. Example: 6400.
        ticker (Union[Unset, str]): The stock ticker. Example: AAPL.
        tracking_id (Union[Unset, int]): The tracking ID of the trade. Example: 71984388012245.
        trade_code (Union[Unset, SingleTradeTradeCode]): The trade code. Null if none applies. Example:
            derivative_priced.
        trade_settlement (Union[Unset, SingleTradeSettlement]): The kind of trade settlement. Example: cash_settlement.
        volume (Union[Unset, int]): The volume of the ticker for the Trading Day. Example: 23132119.
    """

    canceled: Unset | bool = UNSET
    executed_at: Unset | str = UNSET
    ext_hour_sold_codes: Unset | None | SingleTradeExternalHourSoldCode = UNSET
    market_center: Unset | str = UNSET
    nbbo_ask: Unset | float = UNSET
    nbbo_ask_quantity: Unset | float = UNSET
    nbbo_bid: Unset | float = UNSET
    nbbo_bid_quantity: Unset | float = UNSET
    premium: Unset | str = UNSET
    price: Unset | str = UNSET
    sale_cond_codes: Unset | None | SingleTradeSaleCondCode = UNSET
    size: Unset | int = UNSET
    ticker: Unset | str = UNSET
    tracking_id: Unset | int = UNSET
    trade_code: Unset | None | SingleTradeTradeCode = UNSET
    trade_settlement: Unset | None | SingleTradeSettlement = UNSET
    volume: Unset | int = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        canceled = self.canceled

        executed_at = self.executed_at

        ext_hour_sold_codes: Unset | None | str = UNSET
        if not isinstance(self.ext_hour_sold_codes, Unset):
            ext_hour_sold_codes = self.ext_hour_sold_codes.value if self.ext_hour_sold_codes else None

        market_center = self.market_center

        nbbo_ask = self.nbbo_ask

        nbbo_ask_quantity = self.nbbo_ask_quantity

        nbbo_bid = self.nbbo_bid

        nbbo_bid_quantity = self.nbbo_bid_quantity

        premium = self.premium

        price = self.price

        sale_cond_codes: Unset | None | str = UNSET
        if not isinstance(self.sale_cond_codes, Unset):
            sale_cond_codes = self.sale_cond_codes.value if self.sale_cond_codes else None

        size = self.size

        ticker = self.ticker

        tracking_id = self.tracking_id

        trade_code: Unset | None | str = UNSET
        if not isinstance(self.trade_code, Unset):
            trade_code = self.trade_code.value if self.trade_code else None

        trade_settlement: Unset | None | str = UNSET
        if not isinstance(self.trade_settlement, Unset):
            trade_settlement = self.trade_settlement.value if self.trade_settlement else None

        volume = self.volume

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if canceled is not UNSET:
            field_dict["canceled"] = canceled
        if executed_at is not UNSET:
            field_dict["executed_at"] = executed_at
        if ext_hour_sold_codes is not UNSET:
            field_dict["ext_hour_sold_codes"] = ext_hour_sold_codes
        if market_center is not UNSET:
            field_dict["market_center"] = market_center
        if nbbo_ask is not UNSET:
            field_dict["nbbo_ask"] = nbbo_ask
        if nbbo_ask_quantity is not UNSET:
            field_dict["nbbo_ask_quantity"] = nbbo_ask_quantity
        if nbbo_bid is not UNSET:
            field_dict["nbbo_bid"] = nbbo_bid
        if nbbo_bid_quantity is not UNSET:
            field_dict["nbbo_bid_quantity"] = nbbo_bid_quantity
        if premium is not UNSET:
            field_dict["premium"] = premium
        if price is not UNSET:
            field_dict["price"] = price
        if sale_cond_codes is not UNSET:
            field_dict["sale_cond_codes"] = sale_cond_codes
        if size is not UNSET:
            field_dict["size"] = size
        if ticker is not UNSET:
            field_dict["ticker"] = ticker
        if tracking_id is not UNSET:
            field_dict["tracking_id"] = tracking_id
        if trade_code is not UNSET:
            field_dict["trade_code"] = trade_code
        if trade_settlement is not UNSET:
            field_dict["trade_settlement"] = trade_settlement
        if volume is not UNSET:
            field_dict["volume"] = volume

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: dict[str, Any]) -> T:
        d = src_dict.copy()
        canceled = d.pop("canceled", UNSET)

        executed_at = d.pop("executed_at", UNSET)

        _ext_hour_sold_codes = d.pop("ext_hour_sold_codes", UNSET)
        ext_hour_sold_codes: Unset | None | SingleTradeExternalHourSoldCode
        if isinstance(_ext_hour_sold_codes, Unset):
            ext_hour_sold_codes = UNSET
        elif _ext_hour_sold_codes is None:
            ext_hour_sold_codes = None
        else:
            ext_hour_sold_codes = SingleTradeExternalHourSoldCode(_ext_hour_sold_codes)

        market_center = d.pop("market_center", UNSET)

        nbbo_ask = d.pop("nbbo_ask", UNSET)

        nbbo_ask_quantity = d.pop("nbbo_ask_quantity", UNSET)

        nbbo_bid = d.pop("nbbo_bid", UNSET)

        nbbo_bid_quantity = d.pop("nbbo_bid_quantity", UNSET)

        premium = d.pop("premium", UNSET)

        price = d.pop("price", UNSET)

        _sale_cond_codes = d.pop("sale_cond_codes", UNSET)
        sale_cond_codes: Unset | None | SingleTradeSaleCondCode
        if isinstance(_sale_cond_codes, Unset):
            sale_cond_codes = UNSET
        elif _sale_cond_codes is None:
            sale_cond_codes = None
        else:
            sale_cond_codes = SingleTradeSaleCondCode(_sale_cond_codes)

        size = d.pop("size", UNSET)

        ticker = d.pop("ticker", UNSET)

        tracking_id = d.pop("tracking_id", UNSET)

        _trade_code = d.pop("trade_code", UNSET)
        trade_code: Unset | None | SingleTradeTradeCode
        if isinstance(_trade_code, Unset):
            trade_code = UNSET
        elif _trade_code is None:
            trade_code = None
        else:
            trade_code = SingleTradeTradeCode(_trade_code)

        _trade_settlement = d.pop("trade_settlement", UNSET)
        trade_settlement: Unset | None | SingleTradeSettlement
        if isinstance(_trade_settlement, Unset):
            trade_settlement = UNSET
        elif _trade_settlement is None:
            trade_settlement = None
        else:
            trade_settlement = SingleTradeSettlement(_trade_settlement)

        volume = d.pop("volume", UNSET)

        darkpool_trade = cls(
            canceled=canceled,
            executed_at=executed_at,
            ext_hour_sold_codes=ext_hour_sold_codes,
            market_center=market_center,
            nbbo_ask=nbbo_ask,
            nbbo_ask_quantity=nbbo_ask_quantity,
            nbbo_bid=nbbo_bid,
            nbbo_bid_quantity=nbbo_bid_quantity,
            premium=premium,
            price=price,
            sale_cond_codes=sale_cond_codes,
            size=size,
            ticker=ticker,
            tracking_id=tracking_id,
            trade_code=trade_code,
            trade_settlement=trade_settlement,
            volume=volume,
        )

        darkpool_trade.additional_properties = d
        return darkpool_trade

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
