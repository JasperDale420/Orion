from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OIChange")


@_attrs_define
class OIChange:
    r"""A list of option contracts with their OI change relative to the previous day.

    Example:
        {'data': [{'avg_price': '30.0543738131838322', 'curr_date': datetime.date(2024, 1, 9), 'curr_oi': 35207,
            'last_ask': '34.00', 'last_bid': '32.90', 'last_date': datetime.date(2024, 1, 8), 'last_fill': '33.50',
            'last_oi': 2119, 'oi_change': '15.6149126946672959', 'oi_diff_plain': 33088, 'option_symbol':
            'MSFT240315C00350000', 'percentage_of_total': '0.08879378869021333312', 'prev_ask_volume': 32861,
            'prev_bid_volume': 235, 'prev_mid_volume': 81, 'prev_multi_leg_volume': 32762, 'prev_neutral_volume': 81,
            'prev_stock_multi_leg_volume': 0, 'prev_total_premium': '99711396.00', 'rnk': 74, 'trades': 187,
            'underlying_symbol': 'MSFT', 'volume': 33177}, {'avg_price': '0.16762696308382622884', 'curr_date':
            datetime.date(2024, 1, 9), 'curr_oi': 33253, 'last_ask': '0.23', 'last_bid': '0.20', 'last_date':
            datetime.date(2024, 1, 8), 'last_fill': '0.22', 'last_oi': 27361, 'oi_change': '0.21534300646906180330',
            'oi_diff_plain': 5892, 'option_symbol': 'MSFT240119C00400000', 'percentage_of_total': '0.02624444319547373013',
            'prev_ask_volume': 8915, 'prev_bid_volume': 860, 'prev_mid_volume': 31, 'prev_multi_leg_volume': 214,
            'prev_neutral_volume': 31, 'prev_stock_multi_leg_volume': 0, 'prev_total_premium': '164375.00', 'rnk': 1638,
            'trades': 602, 'underlying_symbol': 'MSFT', 'volume': 9806}]}

    Attributes:
        avg_price (Union[Unset, float]): The price of the tick Example: 19.32.
        curr_date (Union[Unset, str]): An ISO date. Example: 2024-01-09.
        curr_oi (Union[Unset, float]): The price of the tick Example: 19.32.
        last_ask (Union[Unset, float]): The price of the tick Example: 19.32.
        last_bid (Union[Unset, float]): The price of the tick Example: 19.32.
        last_date (Union[Unset, str]): An ISO date. Example: 2024-01-09.
        last_fill (Union[Unset, float]): The price of the tick Example: 19.32.
        last_oi (Union[Unset, float]): The price of the tick Example: 19.32.
        oi_change (Union[Unset, float]): The price of the tick Example: 19.32.
        oi_diff_plain (Union[Unset, float]): The price of the tick Example: 19.32.
        option_symbol (Union[Unset, str]): The option symbol of the contract.

            You can use the following regex to extract underlying ticker, option type, expiry & strike:
            `^(?<symbol>[\w]*)(?<expiry>(\d{2})(\d{2})(\d{2}))(?<type>[PC])(?<strike>\d{8})$`

            Keep in mind that the strike needs to be multiplied by 1,000.
        percentage_of_total (Union[Unset, float]): The price of the tick Example: 19.32.
        prev_ask_volume (Union[Unset, float]): The price of the tick Example: 19.32.
        prev_bid_volume (Union[Unset, float]): The price of the tick Example: 19.32.
        prev_mid_volume (Union[Unset, float]): The price of the tick Example: 19.32.
        prev_multi_leg_volume (Union[Unset, float]): The price of the tick Example: 19.32.
        prev_neutral_volume (Union[Unset, float]): The price of the tick Example: 19.32.
        prev_stock_multi_leg_volume (Union[Unset, float]): The price of the tick Example: 19.32.
        prev_total_premium (Union[Unset, float]): The price of the tick Example: 19.32.
        rnk (Union[Unset, float]): The price of the tick Example: 19.32.
        trades (Union[Unset, float]): The price of the tick Example: 19.32.
        underlying_symbol (Union[Unset, float]): The price of the tick Example: 19.32.
        volume (Union[Unset, float]): The price of the tick Example: 19.32.
    """

    avg_price: Union[Unset, float] = UNSET
    curr_date: Union[Unset, str] = UNSET
    curr_oi: Union[Unset, float] = UNSET
    last_ask: Union[Unset, float] = UNSET
    last_bid: Union[Unset, float] = UNSET
    last_date: Union[Unset, str] = UNSET
    last_fill: Union[Unset, float] = UNSET
    last_oi: Union[Unset, float] = UNSET
    oi_change: Union[Unset, float] = UNSET
    oi_diff_plain: Union[Unset, float] = UNSET
    option_symbol: Union[Unset, str] = UNSET
    percentage_of_total: Union[Unset, float] = UNSET
    prev_ask_volume: Union[Unset, float] = UNSET
    prev_bid_volume: Union[Unset, float] = UNSET
    prev_mid_volume: Union[Unset, float] = UNSET
    prev_multi_leg_volume: Union[Unset, float] = UNSET
    prev_neutral_volume: Union[Unset, float] = UNSET
    prev_stock_multi_leg_volume: Union[Unset, float] = UNSET
    prev_total_premium: Union[Unset, float] = UNSET
    rnk: Union[Unset, float] = UNSET
    trades: Union[Unset, float] = UNSET
    underlying_symbol: Union[Unset, float] = UNSET
    volume: Union[Unset, float] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        avg_price = self.avg_price

        curr_date = self.curr_date

        curr_oi = self.curr_oi

        last_ask = self.last_ask

        last_bid = self.last_bid

        last_date = self.last_date

        last_fill = self.last_fill

        last_oi = self.last_oi

        oi_change = self.oi_change

        oi_diff_plain = self.oi_diff_plain

        option_symbol = self.option_symbol

        percentage_of_total = self.percentage_of_total

        prev_ask_volume = self.prev_ask_volume

        prev_bid_volume = self.prev_bid_volume

        prev_mid_volume = self.prev_mid_volume

        prev_multi_leg_volume = self.prev_multi_leg_volume

        prev_neutral_volume = self.prev_neutral_volume

        prev_stock_multi_leg_volume = self.prev_stock_multi_leg_volume

        prev_total_premium = self.prev_total_premium

        rnk = self.rnk

        trades = self.trades

        underlying_symbol = self.underlying_symbol

        volume = self.volume

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if avg_price is not UNSET:
            field_dict["avg_price"] = avg_price
        if curr_date is not UNSET:
            field_dict["curr_date"] = curr_date
        if curr_oi is not UNSET:
            field_dict["curr_oi"] = curr_oi
        if last_ask is not UNSET:
            field_dict["last_ask"] = last_ask
        if last_bid is not UNSET:
            field_dict["last_bid"] = last_bid
        if last_date is not UNSET:
            field_dict["last_date"] = last_date
        if last_fill is not UNSET:
            field_dict["last_fill"] = last_fill
        if last_oi is not UNSET:
            field_dict["last_oi"] = last_oi
        if oi_change is not UNSET:
            field_dict["oi_change"] = oi_change
        if oi_diff_plain is not UNSET:
            field_dict["oi_diff_plain"] = oi_diff_plain
        if option_symbol is not UNSET:
            field_dict["option_symbol"] = option_symbol
        if percentage_of_total is not UNSET:
            field_dict["percentage_of_total"] = percentage_of_total
        if prev_ask_volume is not UNSET:
            field_dict["prev_ask_volume"] = prev_ask_volume
        if prev_bid_volume is not UNSET:
            field_dict["prev_bid_volume"] = prev_bid_volume
        if prev_mid_volume is not UNSET:
            field_dict["prev_mid_volume"] = prev_mid_volume
        if prev_multi_leg_volume is not UNSET:
            field_dict["prev_multi_leg_volume"] = prev_multi_leg_volume
        if prev_neutral_volume is not UNSET:
            field_dict["prev_neutral_volume"] = prev_neutral_volume
        if prev_stock_multi_leg_volume is not UNSET:
            field_dict["prev_stock_multi_leg_volume"] = prev_stock_multi_leg_volume
        if prev_total_premium is not UNSET:
            field_dict["prev_total_premium"] = prev_total_premium
        if rnk is not UNSET:
            field_dict["rnk"] = rnk
        if trades is not UNSET:
            field_dict["trades"] = trades
        if underlying_symbol is not UNSET:
            field_dict["underlying_symbol"] = underlying_symbol
        if volume is not UNSET:
            field_dict["volume"] = volume

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        avg_price = d.pop("avg_price", UNSET)

        curr_date = d.pop("curr_date", UNSET)

        curr_oi = d.pop("curr_oi", UNSET)

        last_ask = d.pop("last_ask", UNSET)

        last_bid = d.pop("last_bid", UNSET)

        last_date = d.pop("last_date", UNSET)

        last_fill = d.pop("last_fill", UNSET)

        last_oi = d.pop("last_oi", UNSET)

        oi_change = d.pop("oi_change", UNSET)

        oi_diff_plain = d.pop("oi_diff_plain", UNSET)

        option_symbol = d.pop("option_symbol", UNSET)

        percentage_of_total = d.pop("percentage_of_total", UNSET)

        prev_ask_volume = d.pop("prev_ask_volume", UNSET)

        prev_bid_volume = d.pop("prev_bid_volume", UNSET)

        prev_mid_volume = d.pop("prev_mid_volume", UNSET)

        prev_multi_leg_volume = d.pop("prev_multi_leg_volume", UNSET)

        prev_neutral_volume = d.pop("prev_neutral_volume", UNSET)

        prev_stock_multi_leg_volume = d.pop("prev_stock_multi_leg_volume", UNSET)

        prev_total_premium = d.pop("prev_total_premium", UNSET)

        rnk = d.pop("rnk", UNSET)

        trades = d.pop("trades", UNSET)

        underlying_symbol = d.pop("underlying_symbol", UNSET)

        volume = d.pop("volume", UNSET)

        oi_change = cls(
            avg_price=avg_price,
            curr_date=curr_date,
            curr_oi=curr_oi,
            last_ask=last_ask,
            last_bid=last_bid,
            last_date=last_date,
            last_fill=last_fill,
            last_oi=last_oi,
            oi_change=oi_change,
            oi_diff_plain=oi_diff_plain,
            option_symbol=option_symbol,
            percentage_of_total=percentage_of_total,
            prev_ask_volume=prev_ask_volume,
            prev_bid_volume=prev_bid_volume,
            prev_mid_volume=prev_mid_volume,
            prev_multi_leg_volume=prev_multi_leg_volume,
            prev_neutral_volume=prev_neutral_volume,
            prev_stock_multi_leg_volume=prev_stock_multi_leg_volume,
            prev_total_premium=prev_total_premium,
            rnk=rnk,
            trades=trades,
            underlying_symbol=underlying_symbol,
            volume=volume,
        )

        oi_change.additional_properties = d
        return oi_change

    @property
    def additional_keys(self) -> List[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
