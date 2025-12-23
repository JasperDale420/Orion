from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OptionPriceLevel")


@_attrs_define
class OptionPriceLevel:
    """
    Example:
        {'data': [{'call_volume': 22074116, 'price': '120.12', 'put_volume': 19941285}, {'call_volume': 220741, 'price':
            '123.12', 'put_volume': 199415}]}

    Attributes:
        call_volume (Union[Unset, int]): The sum of the size of all the call transactions that executed. Example:
            990943.
        price (Union[Unset, str]): The price level. Example: 120.12.
        put_volume (Union[Unset, int]): The sum of the size of all the put transactions that executed. Example: 808326.
    """

    call_volume: Union[Unset, int] = UNSET
    price: Union[Unset, str] = UNSET
    put_volume: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        call_volume = self.call_volume

        price = self.price

        put_volume = self.put_volume

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if call_volume is not UNSET:
            field_dict["call_volume"] = call_volume
        if price is not UNSET:
            field_dict["price"] = price
        if put_volume is not UNSET:
            field_dict["put_volume"] = put_volume

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        call_volume = d.pop("call_volume", UNSET)

        price = d.pop("price", UNSET)

        put_volume = d.pop("put_volume", UNSET)

        option_price_level = cls(
            call_volume=call_volume,
            price=price,
            put_volume=put_volume,
        )

        option_price_level.additional_properties = d
        return option_price_level

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
