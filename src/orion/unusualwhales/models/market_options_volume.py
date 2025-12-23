from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="MarketOptionsVolume")


@_attrs_define
class MarketOptionsVolume:
    """
    Example:
        {'data': [{'call_premium': '7306144788.68', 'call_volume': 22074116, 'date': datetime.date(2023, 9, 8),
            'put_premium': '8413594929.65', 'put_volume': 19941285}]}

    Attributes:
        call_premium (Union[Unset, str]): The sum of the premium of all the call transactions that executed. Example:
            9908777.0.
        call_volume (Union[Unset, int]): The sum of the size of all the call transactions that executed. Example:
            990943.
        date (Union[Unset, str]): A trading date in ISO format YYYY-MM-DD Example: 2023-09-08.
        put_premium (Union[Unset, str]): The sum of the premium of all the put transactions that executed. Example:
            163537151.
        put_volume (Union[Unset, int]): The sum of the size of all the put transactions that executed. Example: 808326.
    """

    call_premium: Union[Unset, str] = UNSET
    call_volume: Union[Unset, int] = UNSET
    date: Union[Unset, str] = UNSET
    put_premium: Union[Unset, str] = UNSET
    put_volume: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        call_premium = self.call_premium

        call_volume = self.call_volume

        date = self.date

        put_premium = self.put_premium

        put_volume = self.put_volume

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if call_premium is not UNSET:
            field_dict["call_premium"] = call_premium
        if call_volume is not UNSET:
            field_dict["call_volume"] = call_volume
        if date is not UNSET:
            field_dict["date"] = date
        if put_premium is not UNSET:
            field_dict["put_premium"] = put_premium
        if put_volume is not UNSET:
            field_dict["put_volume"] = put_volume

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        call_premium = d.pop("call_premium", UNSET)

        call_volume = d.pop("call_volume", UNSET)

        date = d.pop("date", UNSET)

        put_premium = d.pop("put_premium", UNSET)

        put_volume = d.pop("put_volume", UNSET)

        market_options_volume = cls(
            call_premium=call_premium,
            call_volume=call_volume,
            date=date,
            put_premium=put_premium,
            put_volume=put_volume,
        )

        market_options_volume.additional_properties = d
        return market_options_volume

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
