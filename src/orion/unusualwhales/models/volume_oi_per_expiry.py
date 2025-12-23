from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="VolumeOIPerExpiry")


@_attrs_define
class VolumeOIPerExpiry:
    """The volume and open interest per expiry.

    Example:
        {'data': [{'expires': datetime.date(2023, 9, 8), 'oi': 451630, 'volume': 962332}, {'expires':
            datetime.date(2023, 9, 15), 'oi': 1422982, 'volume': 631608}]}

    Attributes:
        expires (Union[Unset, str]): The expiry of an options cycle as an ISO date. Example: 2023-09-08.
        oi (Union[Unset, int]): The sum of open interest for all contracts. Example: 451630.
        volume (Union[Unset, int]): The sum of volume for all contracts. Example: 962332.
    """

    expires: Union[Unset, str] = UNSET
    oi: Union[Unset, int] = UNSET
    volume: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        expires = self.expires

        oi = self.oi

        volume = self.volume

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if expires is not UNSET:
            field_dict["expires"] = expires
        if oi is not UNSET:
            field_dict["oi"] = oi
        if volume is not UNSET:
            field_dict["volume"] = volume

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        expires = d.pop("expires", UNSET)

        oi = d.pop("oi", UNSET)

        volume = d.pop("volume", UNSET)

        volume_oi_per_expiry = cls(
            expires=expires,
            oi=oi,
            volume=volume,
        )

        volume_oi_per_expiry.additional_properties = d
        return volume_oi_per_expiry

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
