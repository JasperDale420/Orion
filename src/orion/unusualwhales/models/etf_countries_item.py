from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="EtfCountriesItem")


@_attrs_define
class EtfCountriesItem:
    """A country and it's corresponding exposure in percentage.

    Example:
        {
              "country": "Switzerland",
              "weight": "0.0043"
            }

    Attributes:
        country (Union[Unset, str]): The country. Example: Ireland.
        weight (Union[Unset, str]): The country exposure in percentage. Example: 0.0164.
    """

    country: Unset | str = UNSET
    weight: Unset | str = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        country = self.country

        weight = self.weight

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if country is not UNSET:
            field_dict["country"] = country
        if weight is not UNSET:
            field_dict["weight"] = weight

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: dict[str, Any]) -> T:
        d = src_dict.copy()
        country = d.pop("country", UNSET)

        weight = d.pop("weight", UNSET)

        etf_countries_item = cls(
            country=country,
            weight=weight,
        )

        etf_countries_item.additional_properties = d
        return etf_countries_item

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
