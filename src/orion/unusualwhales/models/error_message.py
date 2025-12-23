from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ErrorMessage")


@_attrs_define
class ErrorMessage:
    """A json object containing information on the error cause.

    Example:
        {'msg': 'Invalid path input: MSFT12 (valid example: AAPL) - Invalid query input(s): date=2023-02-140 (valid
            example: date=2024-01-18)', 'path': '/api/darkpool/MSFT12', 'query': 'date=2023-02-140', 'url':
            'localhost:4000/api/darkpool/MSFT12?date=2023-02-140'}

    Attributes:
        msg (Union[Unset, str]): An error message containing information about the faulty input.
        path (Union[Unset, str]): The URL path segment.
        query (Union[Unset, str]): The URL query segment.
        url (Union[Unset, str]): The full URL causing the error.
    """

    msg: Union[Unset, str] = UNSET
    path: Union[Unset, str] = UNSET
    query: Union[Unset, str] = UNSET
    url: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        msg = self.msg

        path = self.path

        query = self.query

        url = self.url

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if msg is not UNSET:
            field_dict["msg"] = msg
        if path is not UNSET:
            field_dict["path"] = path
        if query is not UNSET:
            field_dict["query"] = query
        if url is not UNSET:
            field_dict["url"] = url

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        msg = d.pop("msg", UNSET)

        path = d.pop("path", UNSET)

        query = d.pop("query", UNSET)

        url = d.pop("url", UNSET)

        error_message = cls(
            msg=msg,
            path=path,
            query=query,
            url=url,
        )

        error_message.additional_properties = d
        return error_message

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
