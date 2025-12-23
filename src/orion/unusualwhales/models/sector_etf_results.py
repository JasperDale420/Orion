from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SectorETFResults")


@_attrs_define
class SectorETFResults:
    """An array of 1 or more ETFs.

    Example:
        [{'data': [{'avg30_call_volume': '3636459.000000000000', 'avg30_put_volume': '4796289.166666666667',
            'avg30_stock_volume': '74402355', 'avg_30_day_call_volume': '3636459.000000000000', 'avg_30_day_put_volume':
            '4796289.166666666667', 'avg_7_day_call_volume': '3343061.285714285714', 'avg_7_day_put_volume':
            '4521616.428571428571', 'bearish_premium': '258905527', 'bullish_premium': '238729761', 'call_premium':
            '293824502', 'call_volume': 1844830, 'full_name': 'S&P 500 Index', 'high': '447.11', 'last': '446.15', 'low':
            '444.8', 'marketcap': '406517275500', 'open': '444.93', 'prev_close': '444.85', 'prev_date': datetime.date(2023,
            9, 7), 'put_premium': '244159205', 'put_volume': 2009005, 'ticker': 'SPY', 'volume': 23132119, 'week52_high':
            '459.44', 'week52_low': '342.65'}]}]

    Attributes:
        data (Union[Unset, List['SectorETFResults']]):
    """

    data: Union[Unset, List["SectorETFResults"]] = UNSET
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
        d = src_dict.copy()
        data = []
        _data = d.pop("data", UNSET)
        for data_item_data in _data or []:
            data_item = SectorETFResults.from_dict(data_item_data)

            data.append(data_item)

        sector_etf_results = cls(
            data=data,
        )

        sector_etf_results.additional_properties = d
        return sector_etf_results

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
