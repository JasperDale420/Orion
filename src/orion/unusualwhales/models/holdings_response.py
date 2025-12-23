from typing import TYPE_CHECKING, Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.holdings import Holdings


T = TypeVar("T", bound="HoldingsResponse")


@_attrs_define
class HoldingsResponse:
    """An object with a property named data containing an array of Holdings objects

    Attributes:
        data (Union[Unset, Holdings]): An object containing information about the holdings of an ETF Example:
            {'avg30_volume': '52433648', 'bearish_premium': '32565174', 'bullish_premium': '22987045', 'call_premium':
            '45254976', 'call_volume': 197685, 'close': '194.84', 'has_options': True, 'high': '196.579', 'low': '194.41',
            'name': 'APPLE INC', 'prev_price': '197.14', 'put_premium': '16338631', 'put_volume': 106773, 'sector':
            'Technology', 'shares': 169938760, 'short_name': 'APPLE', 'ticker': 'AAPL', 'type': 'stock', 'volume': 12314310,
            'week52_high': '199.62', 'week52_low': '123.15', 'weight': '7.335'}.
    """

    data: Union[Unset, "Holdings"] = UNSET
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
        from ..models.holdings import Holdings

        d = src_dict.copy()
        _data = d.pop("data", UNSET)
        data: Union[Unset, Holdings]
        if isinstance(_data, Unset):
            data = UNSET
        else:
            data = Holdings.from_dict(_data)

        holdings_response = cls(
            data=data,
        )

        holdings_response.additional_properties = d
        return holdings_response

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
