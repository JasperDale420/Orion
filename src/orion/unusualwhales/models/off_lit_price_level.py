from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OffLitPriceLevel")


@_attrs_define
class OffLitPriceLevel:
    """
    Example:
        {'data': [{'lit_vol': 19941285, 'off_vol': 22074116, 'price': '120.12'}, {'lit_vol': 199415, 'off_vol': 220741,
            'price': '123.12'}]}

    Attributes:
        lit_vol (Union[Unset, int]): The lit volume (this only represents stock trades executed on exchanges operated by
            Nasdaq). Example: 19941285.
        off_vol (Union[Unset, int]): The off lit stock volume (this only represents the FINRA operated exchanges).
            Example: 22074116.
        price (Union[Unset, str]): The price level. Example: 120.12.
    """

    lit_vol: Union[Unset, int] = UNSET
    off_vol: Union[Unset, int] = UNSET
    price: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        lit_vol = self.lit_vol

        off_vol = self.off_vol

        price = self.price

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if lit_vol is not UNSET:
            field_dict["lit_vol"] = lit_vol
        if off_vol is not UNSET:
            field_dict["off_vol"] = off_vol
        if price is not UNSET:
            field_dict["price"] = price

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        lit_vol = d.pop("lit_vol", UNSET)

        off_vol = d.pop("off_vol", UNSET)

        price = d.pop("price", UNSET)

        off_lit_price_level = cls(
            lit_vol=lit_vol,
            off_vol=off_vol,
            price=price,
        )

        off_lit_price_level.additional_properties = d
        return off_lit_price_level

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
