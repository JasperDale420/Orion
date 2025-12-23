from typing import TYPE_CHECKING, Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.insider_statistic import InsiderStatistic


T = TypeVar("T", bound="InsiderStatistics")


@_attrs_define
class InsiderStatistics:
    """An objected containing a property named data that returns an array of InsiderStatistic objects

    Example:
        {'data': [{'filing_date': datetime.date(2023, 12, 13), 'purchases': 12, 'purchases_notional': '14317122.490',
            'sells': 10, 'sells_notional': '-1291692.4942'}, {'filing_date': datetime.date(2023, 12, 12), 'purchases': 78,
            'purchases_notional': '46598915.1911', 'sells': 211, 'sells_notional': '-182466466.7165'}, {'filing_date':
            datetime.date(2023, 12, 11), 'purchases': 96, 'purchases_notional': '431722108.8184', 'sells': 210,
            'sells_notional': '-1058043617.3548'}]}

    Attributes:
        data (Union[Unset, List['InsiderStatistic']]):
    """

    data: Union[Unset, List["InsiderStatistic"]] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Union[Unset, List[Dict[str, Any]]] = UNSET
        if not isinstance(self.data, Unset):
            data = []
            for data_item_data in self.data:
                data_item = data_item_data.to_dict()
                data.append(data_item)

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if data is not UNSET:
            field_dict["data"] = data

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        from ..models.insider_statistic import InsiderStatistic

        d = src_dict.copy()
        data = []
        _data = d.pop("data", UNSET)
        for data_item_data in _data or []:
            data_item = InsiderStatistic.from_dict(data_item_data)

            data.append(data_item)

        insider_statistics = cls(
            data=data,
        )

        insider_statistics.additional_properties = d
        return insider_statistics

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
