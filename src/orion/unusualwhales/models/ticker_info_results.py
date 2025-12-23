from typing import TYPE_CHECKING, Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ticker_info import TickerInfo


T = TypeVar("T", bound="TickerInfoResults")


@_attrs_define
class TickerInfoResults:
    """
    Attributes:
        data (Union[Unset, TickerInfo]): General information about the given ticker. Example: {'data': {'announce_time':
            'unkown', 'avg30_volume': '55973002', 'full_name': 'APPLE', 'has_dividend': True, 'has_earnings_history': True,
            'has_investment_arm': False, 'has_options': True, 'issue_type': 'Common Stock', 'marketcap': '2776014233920',
            'marketcap_size': 'big', 'next_earnings_date': datetime.date(2023, 10, 26), 'sector': 'Technology',
            'short_description': 'Apple Inc. is an American multinational technology company headquartered in Cupertino,
            California. Apple is the worlds largest technology company by ...', 'symbol': 'AAPL', 'uw_tags': []}}.
    """

    data: Union[Unset, "TickerInfo"] = UNSET
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
        from ..models.ticker_info import TickerInfo

        d = src_dict.copy()
        _data = d.pop("data", UNSET)
        data: Union[Unset, TickerInfo]
        if isinstance(_data, Unset):
            data = UNSET
        else:
            data = TickerInfo.from_dict(_data)

        ticker_info_results = cls(
            data=data,
        )

        ticker_info_results.additional_properties = d
        return ticker_info_results

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
