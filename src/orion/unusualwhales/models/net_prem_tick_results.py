from typing import TYPE_CHECKING, Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.net_prem_tick import NetPremTick


T = TypeVar("T", bound="NetPremTickResults")


@_attrs_define
class NetPremTickResults:
    """
    Example:
        {'data': [{'date': datetime.date(2023, 9, 7), 'net_call_premium': '-2075581.0000', 'net_call_volume': -2259,
            'net_put_premium': '-15559.0000', 'net_put_volume': 95, 'tape_time': datetime.datetime(2023, 9, 7, 9, 30,
            tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=72000)))}, {'date': datetime.date(2023, 9, 7),
            'net_call_premium': '670064.0000', 'net_call_volume': 754, 'net_put_premium': '-1480020.0000', 'net_put_volume':
            -264, 'tape_time': datetime.datetime(2023, 9, 7, 9, 31, tzinfo=datetime.timezone(datetime.timedelta(days=-1,
            seconds=72000)))}, {'date': datetime.date(2023, 9, 7), 'net_call_premium': '128926.0000', 'net_call_volume':
            1347, 'net_put_premium': '-644069.0000', 'net_put_volume': 2181, 'tape_time': datetime.datetime(2023, 9, 7, 9,
            32, tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=72000)))}, {'date': datetime.date(2023, 9, 7),
            'net_call_premium': '-1095135.0000', 'net_call_volume': 1049, 'net_put_premium': '135732.0000',
            'net_put_volume': 415, 'tape_time': datetime.datetime(2023, 9, 7, 9, 33,
            tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=72000)))}, {'date': datetime.date(2023, 9, 7),
            'net_call_premium': '165842.0000', 'net_call_volume': 429, 'net_put_premium': '-379365.0000', 'net_put_volume':
            224, 'tape_time': datetime.datetime(2023, 9, 7, 9, 34, tzinfo=datetime.timezone(datetime.timedelta(days=-1,
            seconds=72000)))}, {'date': datetime.date(2023, 9, 7), 'net_call_premium': '376569.0000', 'net_call_volume':
            1002, 'net_put_premium': '408447.0000', 'net_put_volume': 1313, 'tape_time': datetime.datetime(2023, 9, 7, 9,
            35, tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=72000)))}, {'date': datetime.date(2023, 9, 7),
            'net_call_premium': '1528190.0000', 'net_call_volume': 4616, 'net_put_premium': '-1385094.0000',
            'net_put_volume': -3197, 'tape_time': datetime.datetime(2023, 9, 7, 9, 36,
            tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=72000)))}]}

    Attributes:
        date (Union[Unset, List['NetPremTick']]):
    """

    date: Union[Unset, List["NetPremTick"]] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        date: Union[Unset, List[Dict[str, Any]]] = UNSET
        if not isinstance(self.date, Unset):
            date = []
            for date_item_data in self.date:
                date_item = date_item_data.to_dict()
                date.append(date_item)

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if date is not UNSET:
            field_dict["date"] = date

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        from ..models.net_prem_tick import NetPremTick

        d = src_dict.copy()
        date = []
        _date = d.pop("date", UNSET)
        for date_item_data in _date or []:
            date_item = NetPremTick.from_dict(date_item_data)

            date.append(date_item)

        net_prem_tick_results = cls(
            date=date,
        )

        net_prem_tick_results.additional_properties = d
        return net_prem_tick_results

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
