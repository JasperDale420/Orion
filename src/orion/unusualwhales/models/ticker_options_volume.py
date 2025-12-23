from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="TickerOptionsVolume")


@_attrs_define
class TickerOptionsVolume:
    """The sum of volume for all contracts. With other calculations related to premium and volume

    Example:
        {'data': [{'avg_30_day_call_volume': '658450.333333333333', 'avg_30_day_put_volume': '481505.300000000000',
            'avg_3_day_call_volume': '949763.000000000000', 'avg_3_day_put_volume': '756387.333333333333',
            'avg_7_day_call_volume': '878336.000000000000', 'avg_7_day_put_volume': '580650.857142857143',
            'bearish_premium': '138449839', 'bullish_premium': '152015294', 'call_open_interest': 4358631, 'call_premium':
            '208699280', 'call_volume': 1071546, 'call_volume_ask_side': 486985, 'call_volume_bid_side': 514793, 'date':
            datetime.date(2023, 9, 8), 'net_call_premium': '122015294', 'net_put_premium': '108449839', 'put_open_interest':
            3771656, 'put_premium': '125472872', 'put_volume': 666386, 'put_volume_ask_side': 298282, 'put_volume_bid_side':
            318834}]}

    Attributes:
        avg_30_day_call_volume (Union[Unset, str]): Avg 30 day call volume. Example: 679430.000000000000.
        avg_30_day_put_volume (Union[Unset, str]): Avg 30 day put volume. Example: 401961.285714285714.
        avg_3_day_call_volume (Union[Unset, str]): Avg 3 day call volume. Example: 606258.533333333333.
        avg_3_day_put_volume (Union[Unset, str]): Avg 3 day put volume. Example: 436126.833333333333.
        avg_7_day_call_volume (Union[Unset, str]): Avg 7 day call volume. Example: 679145.333333333333.
        avg_7_day_put_volume (Union[Unset, str]): Avg 7 day put volume. Example: 388676.000000000000.
        bearish_premium (Union[Unset, str]): The bearish premium is defined as (call premium bid side) + (put premium
            ask side). Example: 143198625.
        bullish_premium (Union[Unset, str]): The bullish premium is defined as (call premium ask side) + (put premium
            bid side). Example: 196261414.
        call_open_interest (Union[Unset, int]): The sum of open interest of all the call options. Example: 3975333.
        call_premium (Union[Unset, str]): The sum of the premium of all the call transactions that executed. Example:
            9908777.0.
        call_volume (Union[Unset, int]): The sum of the size of all the call transactions that executed. Example:
            990943.
        call_volume_ask_side (Union[Unset, int]): The sum of the size of all the call transactions that executed on the
            ask side. Example: 417251.
        call_volume_bid_side (Union[Unset, int]): The sum of the size of all the call transactions that executed on the
            bid side. Example: 498271.
        date (Union[Unset, str]): A trading date in ISO format YYYY-MM-DD Example: 2023-09-08.
        net_call_premium (Union[Unset, str]): Defined as (call premium ask side) - (call premium bid side). Example:
            -29138464.
        net_put_premium (Union[Unset, str]): Defined as (put premium ask side) - (put premium bid side). Example:
            23924325.
        put_open_interest (Union[Unset, int]): The sum of the open interest of all the put options. Example: 3564153.
        put_premium (Union[Unset, str]): The sum of the premium of all the put transactions that executed. Example:
            163537151.
        put_volume (Union[Unset, int]): The sum of the size of all the put transactions that executed. Example: 808326.
        put_volume_ask_side (Union[Unset, int]): The sum of the size of all the put transactions that executed on the
            ask side. Example: 431791.
        put_volume_bid_side (Union[Unset, int]): The sum of the size of all the put transactions that executed on the
            bid side. Example: 314160.
    """

    avg_30_day_call_volume: Union[Unset, str] = UNSET
    avg_30_day_put_volume: Union[Unset, str] = UNSET
    avg_3_day_call_volume: Union[Unset, str] = UNSET
    avg_3_day_put_volume: Union[Unset, str] = UNSET
    avg_7_day_call_volume: Union[Unset, str] = UNSET
    avg_7_day_put_volume: Union[Unset, str] = UNSET
    bearish_premium: Union[Unset, str] = UNSET
    bullish_premium: Union[Unset, str] = UNSET
    call_open_interest: Union[Unset, int] = UNSET
    call_premium: Union[Unset, str] = UNSET
    call_volume: Union[Unset, int] = UNSET
    call_volume_ask_side: Union[Unset, int] = UNSET
    call_volume_bid_side: Union[Unset, int] = UNSET
    date: Union[Unset, str] = UNSET
    net_call_premium: Union[Unset, str] = UNSET
    net_put_premium: Union[Unset, str] = UNSET
    put_open_interest: Union[Unset, int] = UNSET
    put_premium: Union[Unset, str] = UNSET
    put_volume: Union[Unset, int] = UNSET
    put_volume_ask_side: Union[Unset, int] = UNSET
    put_volume_bid_side: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        avg_30_day_call_volume = self.avg_30_day_call_volume

        avg_30_day_put_volume = self.avg_30_day_put_volume

        avg_3_day_call_volume = self.avg_3_day_call_volume

        avg_3_day_put_volume = self.avg_3_day_put_volume

        avg_7_day_call_volume = self.avg_7_day_call_volume

        avg_7_day_put_volume = self.avg_7_day_put_volume

        bearish_premium = self.bearish_premium

        bullish_premium = self.bullish_premium

        call_open_interest = self.call_open_interest

        call_premium = self.call_premium

        call_volume = self.call_volume

        call_volume_ask_side = self.call_volume_ask_side

        call_volume_bid_side = self.call_volume_bid_side

        date = self.date

        net_call_premium = self.net_call_premium

        net_put_premium = self.net_put_premium

        put_open_interest = self.put_open_interest

        put_premium = self.put_premium

        put_volume = self.put_volume

        put_volume_ask_side = self.put_volume_ask_side

        put_volume_bid_side = self.put_volume_bid_side

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if avg_30_day_call_volume is not UNSET:
            field_dict["avg_30_day_call_volume"] = avg_30_day_call_volume
        if avg_30_day_put_volume is not UNSET:
            field_dict["avg_30_day_put_volume"] = avg_30_day_put_volume
        if avg_3_day_call_volume is not UNSET:
            field_dict["avg_3_day_call_volume"] = avg_3_day_call_volume
        if avg_3_day_put_volume is not UNSET:
            field_dict["avg_3_day_put_volume"] = avg_3_day_put_volume
        if avg_7_day_call_volume is not UNSET:
            field_dict["avg_7_day_call_volume"] = avg_7_day_call_volume
        if avg_7_day_put_volume is not UNSET:
            field_dict["avg_7_day_put_volume"] = avg_7_day_put_volume
        if bearish_premium is not UNSET:
            field_dict["bearish_premium"] = bearish_premium
        if bullish_premium is not UNSET:
            field_dict["bullish_premium"] = bullish_premium
        if call_open_interest is not UNSET:
            field_dict["call_open_interest"] = call_open_interest
        if call_premium is not UNSET:
            field_dict["call_premium"] = call_premium
        if call_volume is not UNSET:
            field_dict["call_volume"] = call_volume
        if call_volume_ask_side is not UNSET:
            field_dict["call_volume_ask_side"] = call_volume_ask_side
        if call_volume_bid_side is not UNSET:
            field_dict["call_volume_bid_side"] = call_volume_bid_side
        if date is not UNSET:
            field_dict["date"] = date
        if net_call_premium is not UNSET:
            field_dict["net_call_premium"] = net_call_premium
        if net_put_premium is not UNSET:
            field_dict["net_put_premium"] = net_put_premium
        if put_open_interest is not UNSET:
            field_dict["put_open_interest"] = put_open_interest
        if put_premium is not UNSET:
            field_dict["put_premium"] = put_premium
        if put_volume is not UNSET:
            field_dict["put_volume"] = put_volume
        if put_volume_ask_side is not UNSET:
            field_dict["put_volume_ask_side"] = put_volume_ask_side
        if put_volume_bid_side is not UNSET:
            field_dict["put_volume_bid_side"] = put_volume_bid_side

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        avg_30_day_call_volume = d.pop("avg_30_day_call_volume", UNSET)

        avg_30_day_put_volume = d.pop("avg_30_day_put_volume", UNSET)

        avg_3_day_call_volume = d.pop("avg_3_day_call_volume", UNSET)

        avg_3_day_put_volume = d.pop("avg_3_day_put_volume", UNSET)

        avg_7_day_call_volume = d.pop("avg_7_day_call_volume", UNSET)

        avg_7_day_put_volume = d.pop("avg_7_day_put_volume", UNSET)

        bearish_premium = d.pop("bearish_premium", UNSET)

        bullish_premium = d.pop("bullish_premium", UNSET)

        call_open_interest = d.pop("call_open_interest", UNSET)

        call_premium = d.pop("call_premium", UNSET)

        call_volume = d.pop("call_volume", UNSET)

        call_volume_ask_side = d.pop("call_volume_ask_side", UNSET)

        call_volume_bid_side = d.pop("call_volume_bid_side", UNSET)

        date = d.pop("date", UNSET)

        net_call_premium = d.pop("net_call_premium", UNSET)

        net_put_premium = d.pop("net_put_premium", UNSET)

        put_open_interest = d.pop("put_open_interest", UNSET)

        put_premium = d.pop("put_premium", UNSET)

        put_volume = d.pop("put_volume", UNSET)

        put_volume_ask_side = d.pop("put_volume_ask_side", UNSET)

        put_volume_bid_side = d.pop("put_volume_bid_side", UNSET)

        ticker_options_volume = cls(
            avg_30_day_call_volume=avg_30_day_call_volume,
            avg_30_day_put_volume=avg_30_day_put_volume,
            avg_3_day_call_volume=avg_3_day_call_volume,
            avg_3_day_put_volume=avg_3_day_put_volume,
            avg_7_day_call_volume=avg_7_day_call_volume,
            avg_7_day_put_volume=avg_7_day_put_volume,
            bearish_premium=bearish_premium,
            bullish_premium=bullish_premium,
            call_open_interest=call_open_interest,
            call_premium=call_premium,
            call_volume=call_volume,
            call_volume_ask_side=call_volume_ask_side,
            call_volume_bid_side=call_volume_bid_side,
            date=date,
            net_call_premium=net_call_premium,
            net_put_premium=net_put_premium,
            put_open_interest=put_open_interest,
            put_premium=put_premium,
            put_volume=put_volume,
            put_volume_ask_side=put_volume_ask_side,
            put_volume_bid_side=put_volume_bid_side,
        )

        ticker_options_volume.additional_properties = d
        return ticker_options_volume

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
