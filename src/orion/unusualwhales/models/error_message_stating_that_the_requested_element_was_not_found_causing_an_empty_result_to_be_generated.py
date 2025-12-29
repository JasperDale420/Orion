from typing import Any, Dict, List, Type, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated")


@_attrs_define
class ErrorMessageStatingThatTheRequestedElementWasNotFoundCausingAnEmptyResultToBeGenerated:
    """A message containing an empty JSON response, indicating that the requested element was not found causing an empty
    result to be generated.

        Example:
            {}

    """

    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        error_message_stating_that_the_requested_element_was_not_found_causing_an_empty_result_to_be_generated = cls()

        error_message_stating_that_the_requested_element_was_not_found_causing_an_empty_result_to_be_generated.additional_properties = d
        return error_message_stating_that_the_requested_element_was_not_found_causing_an_empty_result_to_be_generated

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
