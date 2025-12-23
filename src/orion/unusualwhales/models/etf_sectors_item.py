from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="EtfSectorsItem")


@_attrs_define
class EtfSectorsItem:
    """A sector and it's corresponding exposure in percentage.

    Example:
        {
              "sector": "Basic Materials",
              "weight": "0.022"
            }

    Attributes:
        sector (Union[Unset, str]): The sector. Example: Healthcare.
        weight (Union[Unset, str]): The sector exposure in percentage. Example: 0.1272.
    """

    sector: Union[Unset, str] = UNSET
    weight: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        sector = self.sector

        weight = self.weight

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if sector is not UNSET:
            field_dict["sector"] = sector
        if weight is not UNSET:
            field_dict["weight"] = weight

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        sector = d.pop("sector", UNSET)

        weight = d.pop("weight", UNSET)

        etf_sectors_item = cls(
            sector=sector,
            weight=weight,
        )

        etf_sectors_item.additional_properties = d
        return etf_sectors_item

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
