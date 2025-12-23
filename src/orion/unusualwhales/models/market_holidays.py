from typing import Any, Dict, List, Type, TypeVar, Union, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="MarketHolidays")


@_attrs_define
class MarketHolidays:
    """The days at which the market either closes early or is fully closed.

    Example:
        {'data': [{'data': [datetime.date(2025, 1, 1), datetime.date(2024, 1, 1), datetime.date(2023, 1, 2)], 'event':
            'New Years Day'}, {'data': [datetime.date(2025, 1, 20), datetime.date(2024, 1, 15), datetime.date(2023, 1, 16)],
            'event': 'Martin Luther King, Jr. Day'}, {'data': [datetime.date(2025, 2, 17), datetime.date(2024, 2, 19),
            datetime.date(2023, 2, 20)], 'event': "Washington's Birthday"}, {'data': [datetime.date(2025, 4, 18),
            datetime.date(2024, 3, 29), datetime.date(2023, 4, 7)], 'event': 'Good Friday'}, {'data': [datetime.date(2025,
            5, 26), datetime.date(2024, 5, 27), datetime.date(2023, 5, 29)], 'event': 'Memorial Day'}, {'data':
            [datetime.date(2025, 6, 19), datetime.date(2024, 6, 19), datetime.date(2023, 6, 19)], 'event': 'Juneteenth
            National Independence Day'}, {'data': [datetime.date(2025, 7, 3), datetime.date(2024, 7, 3), datetime.date(2023,
            7, 3)], 'early_close': True, 'event': 'Independence Day (early close)'}, {'data': [datetime.date(2025, 7, 4),
            datetime.date(2024, 7, 4), datetime.date(2023, 7, 4)], 'event': 'Independence Day'}, {'data':
            [datetime.date(2025, 9, 1), datetime.date(2024, 9, 2), datetime.date(2023, 9, 4)], 'event': 'Labor Day'},
            {'data': [datetime.date(2025, 11, 27), datetime.date(2024, 11, 28), datetime.date(2023, 11, 23)], 'event':
            'Thanksgiving Day'}, {'data': [datetime.date(2025, 11, 28), datetime.date(2024, 11, 29), datetime.date(2023, 11,
            24)], 'early_close': True, 'event': 'Thanksgiving Day (early close)'}, {'data': [datetime.date(2025, 12, 24),
            datetime.date(2024, 12, 24)], 'early_close': True, 'event': 'Christmas Day (early close)'}, {'data':
            [datetime.date(2025, 12, 25), datetime.date(2024, 12, 25), datetime.date(2023, 12, 25)], 'event': 'Christmas
            Day'}]}

    Attributes:
        data (Union[Unset, List[str]]): An array of dates in ISO format. Example: [datetime.date(2025, 1, 1),
            datetime.date(2025, 1, 2)].
        early_close (Union[Unset, bool]): Boolean flag indicating if the market is open and closes early on the given
            dates. Example: True.
        event (Union[Unset, str]): The event/reason why the market is not open on the given dates. Example: New Years
            Day.
    """

    data: Union[Unset, List[str]] = UNSET
    early_close: Union[Unset, bool] = UNSET
    event: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Union[Unset, List[str]] = UNSET
        if not isinstance(self.data, Unset):
            data = self.data

        early_close = self.early_close

        event = self.event

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if data is not UNSET:
            field_dict["data"] = data
        if early_close is not UNSET:
            field_dict["early_close"] = early_close
        if event is not UNSET:
            field_dict["event"] = event

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        data = cast(List[str], d.pop("data", UNSET))

        early_close = d.pop("early_close", UNSET)

        event = d.pop("event", UNSET)

        market_holidays = cls(
            data=data,
            early_close=early_close,
            event=event,
        )

        market_holidays.additional_properties = d
        return market_holidays

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
