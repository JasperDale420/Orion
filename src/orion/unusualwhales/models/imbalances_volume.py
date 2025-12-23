from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.market_general_imbalance_event import MarketGeneralImbalanceEvent
from ..models.market_general_imbalance_side import MarketGeneralImbalanceSide
from ..models.market_general_imbalance_type import MarketGeneralImbalanceType
from ..types import UNSET, Unset

T = TypeVar("T", bound="ImbalancesVolume")


@_attrs_define
class ImbalancesVolume:
    """
    Example:
        [{'date': datetime.date(2024, 1, 22), 'event': 'moc', 'side': 'sell', 'type': 'first', 'value': 483000000},
            {'date': datetime.date(2024, 1, 22), 'event': 'moc', 'side': 'sell', 'type': 'second', 'value': 483000000},
            {'date': datetime.date(2024, 1, 22), 'event': 'moc', 'side': 'sell', 'type': 'third', 'value': 483000000}]

    Attributes:
        date (Union[Unset, str]): A trading date in ISO format YYYY-MM-DD Example: 2023-09-08.
        event (Union[Unset, MarketGeneralImbalanceEvent]): The event. Example: moc.
        side (Union[Unset, MarketGeneralImbalanceSide]): The side. Example: buy.
        type (Union[Unset, MarketGeneralImbalanceType]): The type of MOC/MOO. Example: third.
        value (Union[Unset, int]): The imbalance value. Example: 483000000.
    """

    date: Union[Unset, str] = UNSET
    event: Union[Unset, MarketGeneralImbalanceEvent] = UNSET
    side: Union[Unset, MarketGeneralImbalanceSide] = UNSET
    type: Union[Unset, MarketGeneralImbalanceType] = UNSET
    value: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        date = self.date

        event: Union[Unset, str] = UNSET
        if not isinstance(self.event, Unset):
            event = self.event.value

        side: Union[Unset, str] = UNSET
        if not isinstance(self.side, Unset):
            side = self.side.value

        type: Union[Unset, str] = UNSET
        if not isinstance(self.type, Unset):
            type = self.type.value

        value = self.value

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if date is not UNSET:
            field_dict["date"] = date
        if event is not UNSET:
            field_dict["event"] = event
        if side is not UNSET:
            field_dict["side"] = side
        if type is not UNSET:
            field_dict["type"] = type
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        date = d.pop("date", UNSET)

        _event = d.pop("event", UNSET)
        event: Union[Unset, MarketGeneralImbalanceEvent]
        if isinstance(_event, Unset):
            event = UNSET
        else:
            event = MarketGeneralImbalanceEvent(_event)

        _side = d.pop("side", UNSET)
        side: Union[Unset, MarketGeneralImbalanceSide]
        if isinstance(_side, Unset):
            side = UNSET
        else:
            side = MarketGeneralImbalanceSide(_side)

        _type = d.pop("type", UNSET)
        type: Union[Unset, MarketGeneralImbalanceType]
        if isinstance(_type, Unset):
            type = UNSET
        else:
            type = MarketGeneralImbalanceType(_type)

        value = d.pop("value", UNSET)

        imbalances_volume = cls(
            date=date,
            event=event,
            side=side,
            type=type,
            value=value,
        )

        imbalances_volume.additional_properties = d
        return imbalances_volume

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
