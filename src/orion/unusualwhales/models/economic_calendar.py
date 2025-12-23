from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.economic_type import EconomicType
from ..types import UNSET, Unset

T = TypeVar("T", bound="EconomicCalendar")


@_attrs_define
class EconomicCalendar:
    """The economic calendar for the current & next week

    Example:
        {'data': [{'event': 'Consumer sentiment (final)', 'forecast': '69.4', 'prev': '69.4', 'reported_period':
            'December', 'time': datetime.datetime(2023, 12, 22, 15, 0, tzinfo=datetime.timezone.utc), 'type': 'report'},
            {'event': 'PCE index', 'forecast': None, 'prev': '0.0%', 'reported_period': 'November', 'time':
            datetime.datetime(2023, 12, 22, 13, 30, tzinfo=datetime.timezone.utc), 'type': 'report'}]}

    Attributes:
        event (Union[Unset, str]): The event/reason. Can be a fed speaker or an economic report/indicator Example: PCE
            index.
        forecast (Union[Unset, str]): The forecast if the event is an economic report/indicator Example: 69.4.
        prev (Union[Unset, str]): The previous value of the preceding period if the event is an economic
            report/indicator Example: 69.4.
        reported_period (Union[Unset, str]): The period for that the economic report/indicator is being reported.
            Example: December.
        time (Union[Unset, str]): The time at which the event will start as UTC timestamp. Example: 2023-12-22
            13:30:00+00:00.
        type (Union[Unset, EconomicType]): The type of the event Example: fomc.
    """

    event: Union[Unset, str] = UNSET
    forecast: Union[Unset, str] = UNSET
    prev: Union[Unset, str] = UNSET
    reported_period: Union[Unset, str] = UNSET
    time: Union[Unset, str] = UNSET
    type: Union[Unset, EconomicType] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        event = self.event

        forecast = self.forecast

        prev = self.prev

        reported_period = self.reported_period

        time = self.time

        type: Union[Unset, str] = UNSET
        if not isinstance(self.type, Unset):
            type = self.type.value

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if event is not UNSET:
            field_dict["event"] = event
        if forecast is not UNSET:
            field_dict["forecast"] = forecast
        if prev is not UNSET:
            field_dict["prev"] = prev
        if reported_period is not UNSET:
            field_dict["reported_period"] = reported_period
        if time is not UNSET:
            field_dict["time"] = time
        if type is not UNSET:
            field_dict["type"] = type

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        event = d.pop("event", UNSET)

        forecast = d.pop("forecast", UNSET)

        prev = d.pop("prev", UNSET)

        reported_period = d.pop("reported_period", UNSET)

        time = d.pop("time", UNSET)

        _type = d.pop("type", UNSET)
        type: Union[Unset, EconomicType]
        if isinstance(_type, Unset):
            type = UNSET
        else:
            type = EconomicType(_type)

        economic_calendar = cls(
            event=event,
            forecast=forecast,
            prev=prev,
            reported_period=reported_period,
            time=time,
            type=type,
        )

        economic_calendar.additional_properties = d
        return economic_calendar

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
