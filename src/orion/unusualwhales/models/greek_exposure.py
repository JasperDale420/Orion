from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="GreekExposure")


@_attrs_define
class GreekExposure:
    """
    Example:
        {'data': [{'call_charm': '102382359.5786', 'call_delta': '227549667.4651', 'call_gamma': '9356683.4241',
            'call_vanna': '152099632406.9564', 'date': datetime.date(2023, 9, 8), 'put_charm': '-943028472.4815',
            'put_delta': '-191893077.7193', 'put_gamma': '-12337386.0524', 'put_vanna': '488921784213.1121'}, {'call_charm':
            '81465130.0002', 'call_delta': '210202465.3421', 'call_gamma': '8456599.8505', 'call_vanna':
            '161231587973.6811', 'date': datetime.date(2023, 9, 7), 'put_charm': '-1054548432.6111', 'put_delta':
            '-210881557.3003', 'put_gamma': '-12703877.0243', 'put_vanna': '488921784213.1121'}]}

    Attributes:
        call_charm (Union[Unset, str]): The sum of the charm values of all call transactions that executed multiplied by
            the open interest and the number of shares per contract (typically 100 shares per contract). Example:
            102382359.5786.
        call_delta (Union[Unset, str]): The sum of the delta values of all call transactions that executed multiplied by
            the open interest and the number of shares per contract (typically 100 shares per contract). Example:
            227549667.4651.
        call_gamma (Union[Unset, str]): The sum of the gamma values of all call transactions that executed multiplied by
            the open interest and the number of shares per contract (typically 100 shares per contract). Example:
            9356683.4241.
        call_vanna (Union[Unset, str]): The sum of the vanna values of all call transactions that executed multiplied by
            the open interest and the number of shares per contract (typically 100 shares per contract). Example:
            152099632406.9564.
        date (Union[Unset, str]): A trading date in ISO format YYYY-MM-DD Example: 2023-09-08.
        put_charm (Union[Unset, str]): The sum of the charm values of all put transactions that executed multiplied by
            the open interest and the number of shares per contract (typically 100 shares per contract). Example:
            -943028472.4815.
        put_delta (Union[Unset, str]): The sum of the delta values of all put transactions that executed multiplied by
            the open interest and the number of shares per contract (typically 100 shares per contract). Example:
            -191893077.7193.
        put_gamma (Union[Unset, str]): The sum of the gamma values of all put transactions that executed multiplied by
            the open interest and the number of shares per contract (typically 100 shares per contract). Example:
            -12337386.0524.
        put_vanna (Union[Unset, str]): The sum of the vanna values of all put transactions that executed multiplied by
            the open interest and the number of shares per contract (typically 100 shares per contract). Example:
            488921784213.1121.
    """

    call_charm: Union[Unset, str] = UNSET
    call_delta: Union[Unset, str] = UNSET
    call_gamma: Union[Unset, str] = UNSET
    call_vanna: Union[Unset, str] = UNSET
    date: Union[Unset, str] = UNSET
    put_charm: Union[Unset, str] = UNSET
    put_delta: Union[Unset, str] = UNSET
    put_gamma: Union[Unset, str] = UNSET
    put_vanna: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        call_charm = self.call_charm

        call_delta = self.call_delta

        call_gamma = self.call_gamma

        call_vanna = self.call_vanna

        date = self.date

        put_charm = self.put_charm

        put_delta = self.put_delta

        put_gamma = self.put_gamma

        put_vanna = self.put_vanna

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if call_charm is not UNSET:
            field_dict["call_charm"] = call_charm
        if call_delta is not UNSET:
            field_dict["call_delta"] = call_delta
        if call_gamma is not UNSET:
            field_dict["call_gamma"] = call_gamma
        if call_vanna is not UNSET:
            field_dict["call_vanna"] = call_vanna
        if date is not UNSET:
            field_dict["date"] = date
        if put_charm is not UNSET:
            field_dict["put_charm"] = put_charm
        if put_delta is not UNSET:
            field_dict["put_delta"] = put_delta
        if put_gamma is not UNSET:
            field_dict["put_gamma"] = put_gamma
        if put_vanna is not UNSET:
            field_dict["put_vanna"] = put_vanna

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        call_charm = d.pop("call_charm", UNSET)

        call_delta = d.pop("call_delta", UNSET)

        call_gamma = d.pop("call_gamma", UNSET)

        call_vanna = d.pop("call_vanna", UNSET)

        date = d.pop("date", UNSET)

        put_charm = d.pop("put_charm", UNSET)

        put_delta = d.pop("put_delta", UNSET)

        put_gamma = d.pop("put_gamma", UNSET)

        put_vanna = d.pop("put_vanna", UNSET)

        greek_exposure = cls(
            call_charm=call_charm,
            call_delta=call_delta,
            call_gamma=call_gamma,
            call_vanna=call_vanna,
            date=date,
            put_charm=put_charm,
            put_delta=put_delta,
            put_gamma=put_gamma,
            put_vanna=put_vanna,
        )

        greek_exposure.additional_properties = d
        return greek_exposure

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
