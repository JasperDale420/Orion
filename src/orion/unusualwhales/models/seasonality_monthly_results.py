from typing import TYPE_CHECKING, Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.seasonality_monthly import SeasonalityMonthly


T = TypeVar("T", bound="SeasonalityMonthlyResults")


@_attrs_define
class SeasonalityMonthlyResults:
    """Object containing a property named data that holds an array of Seasonality Monthly objects.

    Example:
        {'data': [{'avg_change': 0.0034, 'max_change': 0.0635, 'median_change': 0.0195, 'min_change': -0.0727, 'month':
            1, 'positive_closes': 2, 'positive_months_perc': 0.6667, 'years': 3}, {'avg_change': -0.0153, 'max_change':
            0.0724, 'median_change': -0.0153, 'min_change': -0.1029, 'month': 4, 'positive_closes': 1,
            'positive_months_perc': 0.5, 'years': 2}]}

    Attributes:
        data (Union[Unset, SeasonalityMonthly]): The price change for the ticker in the month over the given count of
            years.
             Example: {'avg_change': 0.0034, 'max_change': 0.0635, 'median_change': 0.0195, 'min_change': -0.0727, 'month':
            1, 'positive_closes': 2, 'positive_months_perc': 0.6667, 'years': 3}.
    """

    data: Union[Unset, "SeasonalityMonthly"] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Union[Unset, Dict[str, Any]] = UNSET
        if not isinstance(self.data, Unset):
            data = self.data.to_dict()

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if data is not UNSET:
            field_dict["data"] = data

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        from ..models.seasonality_monthly import SeasonalityMonthly

        d = src_dict.copy()
        _data = d.pop("data", UNSET)
        data: Union[Unset, SeasonalityMonthly]
        if isinstance(_data, Unset):
            data = UNSET
        else:
            data = SeasonalityMonthly.from_dict(_data)

        seasonality_monthly_results = cls(
            data=data,
        )

        seasonality_monthly_results.additional_properties = d
        return seasonality_monthly_results

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
