from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="MaxPain")


@_attrs_define
class MaxPain:
    """the strike with the highest premiums at stake for puts and calls

    Example:
        {'date': datetime.date(2024, 3, 4), 'values': [[datetime.date(2024, 3, 4), '473'], [datetime.date(2024, 3, 5),
            '472']]}

    Attributes:
        date (Union[Unset, str]): A trading date in ISO format YYYY-MM-DD Example: 2023-09-08.
        values (Union[Unset, List[List[str]]]): Max pain strike and expiry date Example: [[datetime.date(2024, 3, 4),
            '473']].
    """

    date: Unset | str = UNSET
    values: Unset | list[list[str]] = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        date = self.date

        values: Unset | list[list[str]] = UNSET
        if not isinstance(self.values, Unset):
            values = []
            for values_item_data in self.values:
                values_item = values_item_data

                values.append(values_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if date is not UNSET:
            field_dict["date"] = date
        if values is not UNSET:
            field_dict["values"] = values

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: dict[str, Any]) -> T:
        d = src_dict.copy()
        date = d.pop("date", UNSET)

        values = []
        _values = d.pop("values", UNSET)
        for values_item_data in _values or []:
            values_item = cast(list[str], values_item_data)

            values.append(values_item)

        max_pain = cls(
            date=date,
            values=values,
        )

        max_pain.additional_properties = d
        return max_pain

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
