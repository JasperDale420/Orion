from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="InsiderStatistic")


@_attrs_define
class InsiderStatistic:
    """An object containing data around an insider trade

    Attributes:
        filing_date (Union[Unset, str]): The filing date as ISO date. Example: 2023-12-13.
        purchases (Union[Unset, int]): The amount of purchase transactions. Example: 12.
        purchases_notional (Union[Unset, str]): The total notional value of purchase transactions. Example:
            14317122.490.
        sells (Union[Unset, int]): The amount of sell transactions. Example: 10.
        sells_notional (Union[Unset, str]): The total notional value of sell transactions. Example: -1291692.4942.
    """

    filing_date: Union[Unset, str] = UNSET
    purchases: Union[Unset, int] = UNSET
    purchases_notional: Union[Unset, str] = UNSET
    sells: Union[Unset, int] = UNSET
    sells_notional: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        filing_date = self.filing_date

        purchases = self.purchases

        purchases_notional = self.purchases_notional

        sells = self.sells

        sells_notional = self.sells_notional

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if filing_date is not UNSET:
            field_dict["filing_date"] = filing_date
        if purchases is not UNSET:
            field_dict["purchases"] = purchases
        if purchases_notional is not UNSET:
            field_dict["purchases_notional"] = purchases_notional
        if sells is not UNSET:
            field_dict["sells"] = sells
        if sells_notional is not UNSET:
            field_dict["sells_notional"] = sells_notional

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        filing_date = d.pop("filing_date", UNSET)

        purchases = d.pop("purchases", UNSET)

        purchases_notional = d.pop("purchases_notional", UNSET)

        sells = d.pop("sells", UNSET)

        sells_notional = d.pop("sells_notional", UNSET)

        insider_statistic = cls(
            filing_date=filing_date,
            purchases=purchases,
            purchases_notional=purchases_notional,
            sells=sells,
            sells_notional=sells_notional,
        )

        insider_statistic.additional_properties = d
        return insider_statistic

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
