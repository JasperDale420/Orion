from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="NetPremTick")


@_attrs_define
class NetPremTick:
    """The net premium for each trading minute.

    Attributes:
        net_call_premium (Union[Unset, str]): Defined as (call premium ask side) - (call premium bid side). Example:
            -29138464.
        net_call_volume (Union[Unset, int]): Defined as (call volume ask side) - (call volume bid side). Example: 1049.
        net_put_premium (Union[Unset, str]): Defined as (put premium ask side) - (put premium bid side). Example:
            23924325.
        net_put_volume (Union[Unset, int]): Defined as (put volume ask side) - (put volume bid side). Example: 1313.
        tape_time (Union[Unset, str]): The start time of the tick as a timestamp with timezone. Example: 2023-09-07
            09:30:00-04:00.
    """

    net_call_premium: Union[Unset, str] = UNSET
    net_call_volume: Union[Unset, int] = UNSET
    net_put_premium: Union[Unset, str] = UNSET
    net_put_volume: Union[Unset, int] = UNSET
    tape_time: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        net_call_premium = self.net_call_premium

        net_call_volume = self.net_call_volume

        net_put_premium = self.net_put_premium

        net_put_volume = self.net_put_volume

        tape_time = self.tape_time

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if net_call_premium is not UNSET:
            field_dict["net_call_premium"] = net_call_premium
        if net_call_volume is not UNSET:
            field_dict["net_call_volume"] = net_call_volume
        if net_put_premium is not UNSET:
            field_dict["net_put_premium"] = net_put_premium
        if net_put_volume is not UNSET:
            field_dict["net_put_volume"] = net_put_volume
        if tape_time is not UNSET:
            field_dict["tape_time"] = tape_time

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        net_call_premium = d.pop("net_call_premium", UNSET)

        net_call_volume = d.pop("net_call_volume", UNSET)

        net_put_premium = d.pop("net_put_premium", UNSET)

        net_put_volume = d.pop("net_put_volume", UNSET)

        tape_time = d.pop("tape_time", UNSET)

        net_prem_tick = cls(
            net_call_premium=net_call_premium,
            net_call_volume=net_call_volume,
            net_put_premium=net_put_premium,
            net_put_volume=net_put_volume,
            tape_time=tape_time,
        )

        net_prem_tick.additional_properties = d
        return net_prem_tick

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
