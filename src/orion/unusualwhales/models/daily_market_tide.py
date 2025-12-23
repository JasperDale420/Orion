from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="DailyMarketTide")


@_attrs_define
class DailyMarketTide:
    """
    Example:
        {'data': [{'date': datetime.date(2023, 9, 8), 'net_call_premium': '660338.0000', 'net_put_premium':
            '-547564.0000', 'net_volume': 23558, 'timestamp': datetime.datetime(2023, 9, 8, 9, 30,
            tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=72000)))}, {'date': datetime.date(2023, 9, 8),
            'net_call_premium': '4907138.0000', 'net_put_premium': '-1709539.0000', 'net_volume': 64312, 'timestamp':
            datetime.datetime(2023, 9, 8, 9, 31, tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=72000)))},
            {'date': datetime.date(2023, 9, 8), 'net_call_premium': '4839265.0000', 'net_put_premium': '-2731793.0000',
            'net_volume': 80029, 'timestamp': datetime.datetime(2023, 9, 8, 9, 32,
            tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=72000)))}]}

    Attributes:
        net_call_premium (Union[Unset, str]): Defined as (call premium ask side) - (call premium bid side). Example:
            -29138464.
        net_put_premium (Union[Unset, str]): Defined as (put premium ask side) - (put premium bid side). Example:
            23924325.
        net_volume (Union[Unset, int]): Defined as (call volume ask side) - (call volume bid side) - ((put volume ask
            side) - (put volume bid side)). Example: 64312.
        timestamp (Union[Unset, str]): The start time of the tick as a timestamp with timezone. Example: 2023-09-07
            09:30:00-04:00.
    """

    net_call_premium: Union[Unset, str] = UNSET
    net_put_premium: Union[Unset, str] = UNSET
    net_volume: Union[Unset, int] = UNSET
    timestamp: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        net_call_premium = self.net_call_premium

        net_put_premium = self.net_put_premium

        net_volume = self.net_volume

        timestamp = self.timestamp

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if net_call_premium is not UNSET:
            field_dict["net_call_premium"] = net_call_premium
        if net_put_premium is not UNSET:
            field_dict["net_put_premium"] = net_put_premium
        if net_volume is not UNSET:
            field_dict["net_volume"] = net_volume
        if timestamp is not UNSET:
            field_dict["timestamp"] = timestamp

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        net_call_premium = d.pop("net_call_premium", UNSET)

        net_put_premium = d.pop("net_put_premium", UNSET)

        net_volume = d.pop("net_volume", UNSET)

        timestamp = d.pop("timestamp", UNSET)

        daily_market_tide = cls(
            net_call_premium=net_call_premium,
            net_put_premium=net_put_premium,
            net_volume=net_volume,
            timestamp=timestamp,
        )

        daily_market_tide.additional_properties = d
        return daily_market_tide

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
