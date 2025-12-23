from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SpotGreekExposuresByStrike")


@_attrs_define
class SpotGreekExposuresByStrike:
    """
    Example:
        {'data': [{'call_charm_oi': '70827151575067.12', 'call_charm_vol': '70827151575067.12', 'call_gamma_oi':
            '5124108502049.17', 'call_gamma_vol': '5124108502049.17', 'call_vanna_oi': '65476967081.41', 'call_vanna_vol':
            '65476967081.41', 'price': '4650', 'put_charm_oi': '2282895170748.09', 'put_charm_vol': '2282895170748.09',
            'put_gamma_oi': '320909908341.10', 'put_gamma_vol': '320909908341.10', 'put_vanna_oi': '12921519098.30',
            'put_vanna_vol': '12921519098.30', 'time': datetime.datetime(2023, 12, 13, 5, 0, 41, 481000,
            tzinfo=datetime.timezone.utc)}]}

    Attributes:
        call_charm_oi (Union[Unset, str]): The sum of the spot charm exposure values of all call transactions at a given
            strike. Example: 102382359.5786.
        call_charm_vol (Union[Unset, str]): The sum of the spot charm exposure values of all call transactions at a
            given strike. Example: 102382359.5786.
        call_gamma_oi (Union[Unset, str]): The sum of the spot gamma exposure values of all call transactions at a given
            strike. Example: 9356683.4241.
        call_gamma_vol (Union[Unset, str]): The sum of the spot gamma exposure values of all call transactions at a
            given strike. Example: 9356683.4241.
        call_vanna_oi (Union[Unset, str]): The sum of the spot vanna exposure values of all call transactions at a given
            strike. Example: 152099632406.9564.
        call_vanna_vol (Union[Unset, str]): The sum of the spot vanna exposure values of all call transactions at a
            given strike. Example: 152099632406.9564.
        price (Union[Unset, str]): The underlying price used in calculations.
            NOTE: For any index ticker this will be the current ATM strike.
             Example: 4650.
        put_charm_oi (Union[Unset, str]): The sum of the spot charm exposure values of all put transactions at a given
            strike. Example: 102382359.5786.
        put_charm_vol (Union[Unset, str]): The sum of the spot charm exposure values of all put transactions at a given
            strike. Example: 102382359.5786.
        put_gamma_oi (Union[Unset, str]): The sum of the spot gamma exposure values of all put transactions at a given
            strike. Example: 9356683.4241.
        put_gamma_vol (Union[Unset, str]): The sum of the spot gamma exposure values of all put transactions at a given
            strike. Example: 9356683.4241.
        put_vanna_oi (Union[Unset, str]): The sum of the spot vanna exposure values of all put transactions at a given
            strike. Example: 152099632406.9564.
        put_vanna_vol (Union[Unset, str]): The sum of the spot vanna exposure values of all put transactions at a given
            strike. Example: 152099632406.9564.
        strike (Union[Unset, str]): The strike price of an option contract. Example: 150.0.
        time (Union[Unset, str]): The UTC timestamp of the calculation Example: 2023-12-13 05:00:41.481000+00:00.
    """

    call_charm_oi: Union[Unset, str] = UNSET
    call_charm_vol: Union[Unset, str] = UNSET
    call_gamma_oi: Union[Unset, str] = UNSET
    call_gamma_vol: Union[Unset, str] = UNSET
    call_vanna_oi: Union[Unset, str] = UNSET
    call_vanna_vol: Union[Unset, str] = UNSET
    price: Union[Unset, str] = UNSET
    put_charm_oi: Union[Unset, str] = UNSET
    put_charm_vol: Union[Unset, str] = UNSET
    put_gamma_oi: Union[Unset, str] = UNSET
    put_gamma_vol: Union[Unset, str] = UNSET
    put_vanna_oi: Union[Unset, str] = UNSET
    put_vanna_vol: Union[Unset, str] = UNSET
    strike: Union[Unset, str] = UNSET
    time: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        call_charm_oi = self.call_charm_oi

        call_charm_vol = self.call_charm_vol

        call_gamma_oi = self.call_gamma_oi

        call_gamma_vol = self.call_gamma_vol

        call_vanna_oi = self.call_vanna_oi

        call_vanna_vol = self.call_vanna_vol

        price = self.price

        put_charm_oi = self.put_charm_oi

        put_charm_vol = self.put_charm_vol

        put_gamma_oi = self.put_gamma_oi

        put_gamma_vol = self.put_gamma_vol

        put_vanna_oi = self.put_vanna_oi

        put_vanna_vol = self.put_vanna_vol

        strike = self.strike

        time = self.time

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if call_charm_oi is not UNSET:
            field_dict["call_charm_oi"] = call_charm_oi
        if call_charm_vol is not UNSET:
            field_dict["call_charm_vol"] = call_charm_vol
        if call_gamma_oi is not UNSET:
            field_dict["call_gamma_oi"] = call_gamma_oi
        if call_gamma_vol is not UNSET:
            field_dict["call_gamma_vol"] = call_gamma_vol
        if call_vanna_oi is not UNSET:
            field_dict["call_vanna_oi"] = call_vanna_oi
        if call_vanna_vol is not UNSET:
            field_dict["call_vanna_vol"] = call_vanna_vol
        if price is not UNSET:
            field_dict["price"] = price
        if put_charm_oi is not UNSET:
            field_dict["put_charm_oi"] = put_charm_oi
        if put_charm_vol is not UNSET:
            field_dict["put_charm_vol"] = put_charm_vol
        if put_gamma_oi is not UNSET:
            field_dict["put_gamma_oi"] = put_gamma_oi
        if put_gamma_vol is not UNSET:
            field_dict["put_gamma_vol"] = put_gamma_vol
        if put_vanna_oi is not UNSET:
            field_dict["put_vanna_oi"] = put_vanna_oi
        if put_vanna_vol is not UNSET:
            field_dict["put_vanna_vol"] = put_vanna_vol
        if strike is not UNSET:
            field_dict["strike"] = strike
        if time is not UNSET:
            field_dict["time"] = time

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        call_charm_oi = d.pop("call_charm_oi", UNSET)

        call_charm_vol = d.pop("call_charm_vol", UNSET)

        call_gamma_oi = d.pop("call_gamma_oi", UNSET)

        call_gamma_vol = d.pop("call_gamma_vol", UNSET)

        call_vanna_oi = d.pop("call_vanna_oi", UNSET)

        call_vanna_vol = d.pop("call_vanna_vol", UNSET)

        price = d.pop("price", UNSET)

        put_charm_oi = d.pop("put_charm_oi", UNSET)

        put_charm_vol = d.pop("put_charm_vol", UNSET)

        put_gamma_oi = d.pop("put_gamma_oi", UNSET)

        put_gamma_vol = d.pop("put_gamma_vol", UNSET)

        put_vanna_oi = d.pop("put_vanna_oi", UNSET)

        put_vanna_vol = d.pop("put_vanna_vol", UNSET)

        strike = d.pop("strike", UNSET)

        time = d.pop("time", UNSET)

        spot_greek_exposures_by_strike = cls(
            call_charm_oi=call_charm_oi,
            call_charm_vol=call_charm_vol,
            call_gamma_oi=call_gamma_oi,
            call_gamma_vol=call_gamma_vol,
            call_vanna_oi=call_vanna_oi,
            call_vanna_vol=call_vanna_vol,
            price=price,
            put_charm_oi=put_charm_oi,
            put_charm_vol=put_charm_vol,
            put_gamma_oi=put_gamma_oi,
            put_gamma_vol=put_gamma_vol,
            put_vanna_oi=put_vanna_oi,
            put_vanna_vol=put_vanna_vol,
            strike=strike,
            time=time,
        )

        spot_greek_exposures_by_strike.additional_properties = d
        return spot_greek_exposures_by_strike

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
