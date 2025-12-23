from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SPIKEValue")


@_attrs_define
class SPIKEValue:
    """The SPIKE value at a time.

    Example:
        {'data': [{'time': datetime.datetime(2023, 2, 14, 14, 31, tzinfo=datetime.timezone.utc), 'value': '20.42'},
            {'time': datetime.datetime(2023, 2, 14, 14, 32, tzinfo=datetime.timezone.utc), 'value': '20.55'}, {'time':
            datetime.datetime(2023, 2, 14, 14, 33, tzinfo=datetime.timezone.utc), 'value': '20.52'}]}

    Attributes:
        publish_date (Union[Unset, str]): The timestamp for the SPIKE value. Example: 2023-02-14 14:31:00+00:00.
        title (Union[Unset, str]): The SPIKE value. Example: 24.53.
    """

    publish_date: Union[Unset, str] = UNSET
    title: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        publish_date = self.publish_date

        title = self.title

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if publish_date is not UNSET:
            field_dict["publish_date"] = publish_date
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        publish_date = d.pop("publish_date", UNSET)

        title = d.pop("title", UNSET)

        spike_value = cls(
            publish_date=publish_date,
            title=title,
        )

        spike_value.additional_properties = d
        return spike_value

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
