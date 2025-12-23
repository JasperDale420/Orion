from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ImpliedVolatilityTermStructure")


@_attrs_define
class ImpliedVolatilityTermStructure:
    """
    Example:
        {'data': {'date': datetime.date(2023, 9, 8), 'dte': 7, 'expiry': datetime.date(2023, 9, 15), 'implied_move':
            '4.923', 'implied_move_perc': '0.02747', 'volatility': '0.2352'}}

    Attributes:
        date (Union[Unset, str]): A trading date in ISO format YYYY-MM-DD Example: 2023-09-08.
        dte (Union[Unset, int]): The number of days until the option expires. Example: 5.
        expiry (Union[Unset, str]): The expiry of an options cycle as an ISO date. Example: 2023-09-08.
        implied_move (Union[Unset, str]): The implied move of the underlying stock by a given date based on the money
            option contracts. It is calculated by multiplying the sum of the call and put price by 0.85. If no expiry date
            is included, then the implied move is for the nearest end of the week expiration (the nearest monthly expiration
            if there are no weekly contracts). Example: 2.2398043036460877.
        implied_move_perc (Union[Unset, str]): The implied move as a percentage of the underlying stock price. Example:
            0.012247398860706955.
        volatility (Union[Unset, str]): The implied volatility average of the at the money put and call option
            contracts. If no expiry date is included, then the volatility is of the nearest end of the week expiration (the
            nearest monthly expiration if there are no weekly contracts). Example: 0.18338055163621902.
    """

    date: Union[Unset, str] = UNSET
    dte: Union[Unset, int] = UNSET
    expiry: Union[Unset, str] = UNSET
    implied_move: Union[Unset, str] = UNSET
    implied_move_perc: Union[Unset, str] = UNSET
    volatility: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        date = self.date

        dte = self.dte

        expiry = self.expiry

        implied_move = self.implied_move

        implied_move_perc = self.implied_move_perc

        volatility = self.volatility

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if date is not UNSET:
            field_dict["date"] = date
        if dte is not UNSET:
            field_dict["dte"] = dte
        if expiry is not UNSET:
            field_dict["expiry"] = expiry
        if implied_move is not UNSET:
            field_dict["implied_move"] = implied_move
        if implied_move_perc is not UNSET:
            field_dict["implied_move_perc"] = implied_move_perc
        if volatility is not UNSET:
            field_dict["volatility"] = volatility

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        date = d.pop("date", UNSET)

        dte = d.pop("dte", UNSET)

        expiry = d.pop("expiry", UNSET)

        implied_move = d.pop("implied_move", UNSET)

        implied_move_perc = d.pop("implied_move_perc", UNSET)

        volatility = d.pop("volatility", UNSET)

        implied_volatility_term_structure = cls(
            date=date,
            dte=dte,
            expiry=expiry,
            implied_move=implied_move,
            implied_move_perc=implied_move_perc,
            volatility=volatility,
        )

        implied_volatility_term_structure.additional_properties = d
        return implied_volatility_term_structure

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
