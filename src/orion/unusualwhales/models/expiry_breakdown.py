from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ExpiryBreakdown")


@_attrs_define
class ExpiryBreakdown:
    """All expirations for a ticker

    Example:
        {'data': [{'chains': 5000, 'expiry': datetime.date(2023, 9, 7), 'open_interest': 554, 'volume': 1566232},
            {'chains': 50, 'expiry': datetime.date(2023, 10, 20), 'open_interest': 0, 'volume': 1532}, {'chains': 20,
            'expiry': datetime.date(2023, 11, 30), 'open_interest': 33112, 'volume': 931}]}

    Attributes:
        chains (Union[Unset, int]): The total amount of chains for that expiry Example: 12223.
        expiry (Union[Unset, str]): The contract expiry date in ISO format. Example: 2023-12-22.
        open_interest (Union[Unset, int]): The total open interest for that expiry Example: 12223.
        volume (Union[Unset, int]): The total volume for that expiry Example: 12223.
    """

    chains: Union[Unset, int] = UNSET
    expiry: Union[Unset, str] = UNSET
    open_interest: Union[Unset, int] = UNSET
    volume: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        chains = self.chains

        expiry = self.expiry

        open_interest = self.open_interest

        volume = self.volume

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if chains is not UNSET:
            field_dict["chains"] = chains
        if expiry is not UNSET:
            field_dict["expiry"] = expiry
        if open_interest is not UNSET:
            field_dict["open_interest"] = open_interest
        if volume is not UNSET:
            field_dict["volume"] = volume

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        chains = d.pop("chains", UNSET)

        expiry = d.pop("expiry", UNSET)

        open_interest = d.pop("open_interest", UNSET)

        volume = d.pop("volume", UNSET)

        expiry_breakdown = cls(
            chains=chains,
            expiry=expiry,
            open_interest=open_interest,
            volume=volume,
        )

        expiry_breakdown.additional_properties = d
        return expiry_breakdown

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
