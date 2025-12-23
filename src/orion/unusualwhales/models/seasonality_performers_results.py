from typing import TYPE_CHECKING, Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.seasonality_performers import SeasonalityPerformers


T = TypeVar("T", bound="SeasonalityPerformersResults")


@_attrs_define
class SeasonalityPerformersResults:
    """Object containing a property named data that holds an array of Seasonality Performers objects.

    Example:
        {'data': [{'avg_change': 0.0592, 'marketcap': 19427901578, 'max_change': 0.1104, 'median_change': 0.0639,
            'min_change': 0.0091, 'month': 5, 'positive_closes': 11, 'positive_months_perc': '1.1', 'sector': 'Healthcare',
            'ticker': 'ICLR', 'years': 10}, {'avg_change': 0.0577, 'marketcap': 1927713687, 'max_change': 0.2301,
            'median_change': 0.0417, 'min_change': -0.2485, 'month': 5, 'positive_closes': 10, 'positive_months_perc': '1',
            'sector': 'Technology', 'ticker': 'AMBA', 'years': 10}]}

    Attributes:
        data (Union[Unset, List['SeasonalityPerformers']]):
    """

    data: Union[Unset, List["SeasonalityPerformers"]] = UNSET
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
        from ..models.seasonality_performers import SeasonalityPerformers

        d = src_dict.copy()
        data = []
        _data = d.pop("data", UNSET)
        for data_item_data in _data or []:
            data_item = SeasonalityPerformers.from_dict(data_item_data)

            data.append(data_item)

        seasonality_performers_results = cls(
            data=data,
        )

        seasonality_performers_results.additional_properties = d
        return seasonality_performers_results

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
