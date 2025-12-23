from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SpotGEXExposuresPer1Min")


@_attrs_define
class SpotGEXExposuresPer1Min:
    """
    Example:
        {'data': [{'charm_per_one_percent_move_oi': '5124108502049.17', 'charm_per_one_percent_move_vol':
            '320909908341.10', 'delta_per_one_percent_move_oi': '70827151575067.12', 'delta_per_one_percent_move_vol':
            '2282895170748.09', 'gamma_per_one_percent_move_oi': '65476967081.41', 'gamma_per_one_percent_move_vol':
            '12921519098.30', 'price': '4650', 'time': datetime.datetime(2023, 12, 13, 5, 0, 41, 481000,
            tzinfo=datetime.timezone.utc), 'vanna_per_one_percent_move_oi': '-54622844772.90',
            'vanna_per_one_percent_move_vol': '-5559678859.12'}]}

    Attributes:
        charm_per_one_percent_move_oi (Union[Unset, str]): The charm 1% move based on OI:
            This is calculated as charm * open interest * 365
             Example: 5124108502049.17.
        charm_per_one_percent_move_vol (Union[Unset, str]): The charm 1% move based on volume:
            This is calculated as charm * volume * 365
             Example: 320909908341.10.
        delta_per_one_percent_move_oi (Union[Unset, str]): The delta 1% move based on OI:
            This is calculated as delta * open interest * price * price
             Example: 70827151575067.12.
        delta_per_one_percent_move_vol (Union[Unset, str]): The delta 1% move based on volume:
            This is calculated as delta * volume * price * price
             Example: 2282895170748.09.
        gamma_per_one_percent_move_oi (Union[Unset, str]): The gamma 1% move based on OI:
            This is calculated as gamma * open interest * price * price
             Example: 65476967081.41.
        gamma_per_one_percent_move_vol (Union[Unset, str]): The gamma 1% move based on volume:
            This is calculated as gamma * volume * price * price
             Example: 12921519098.30.
        price (Union[Unset, str]): The underlying price used in calculations.
            NOTE: For any index ticker this will be the current ATM strike.
             Example: 4650.
        time (Union[Unset, str]): The UTC timestamp of the calculation Example: 2023-12-13 05:00:41.481000+00:00.
        vanna_per_one_percent_move_oi (Union[Unset, str]): The vanna 1% move based on OI:
            This is calculated as vanna * open interest
             Example: -54622844772.90.
        vanna_per_one_percent_move_vol (Union[Unset, str]): The vanna 1% move based on volume:
            This is calculated as vanna * volume
             Example: -5559678859.12.
    """

    charm_per_one_percent_move_oi: Union[Unset, str] = UNSET
    charm_per_one_percent_move_vol: Union[Unset, str] = UNSET
    delta_per_one_percent_move_oi: Union[Unset, str] = UNSET
    delta_per_one_percent_move_vol: Union[Unset, str] = UNSET
    gamma_per_one_percent_move_oi: Union[Unset, str] = UNSET
    gamma_per_one_percent_move_vol: Union[Unset, str] = UNSET
    price: Union[Unset, str] = UNSET
    time: Union[Unset, str] = UNSET
    vanna_per_one_percent_move_oi: Union[Unset, str] = UNSET
    vanna_per_one_percent_move_vol: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        charm_per_one_percent_move_oi = self.charm_per_one_percent_move_oi

        charm_per_one_percent_move_vol = self.charm_per_one_percent_move_vol

        delta_per_one_percent_move_oi = self.delta_per_one_percent_move_oi

        delta_per_one_percent_move_vol = self.delta_per_one_percent_move_vol

        gamma_per_one_percent_move_oi = self.gamma_per_one_percent_move_oi

        gamma_per_one_percent_move_vol = self.gamma_per_one_percent_move_vol

        price = self.price

        time = self.time

        vanna_per_one_percent_move_oi = self.vanna_per_one_percent_move_oi

        vanna_per_one_percent_move_vol = self.vanna_per_one_percent_move_vol

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if charm_per_one_percent_move_oi is not UNSET:
            field_dict["charm_per_one_percent_move_oi"] = charm_per_one_percent_move_oi
        if charm_per_one_percent_move_vol is not UNSET:
            field_dict["charm_per_one_percent_move_vol"] = charm_per_one_percent_move_vol
        if delta_per_one_percent_move_oi is not UNSET:
            field_dict["delta_per_one_percent_move_oi"] = delta_per_one_percent_move_oi
        if delta_per_one_percent_move_vol is not UNSET:
            field_dict["delta_per_one_percent_move_vol"] = delta_per_one_percent_move_vol
        if gamma_per_one_percent_move_oi is not UNSET:
            field_dict["gamma_per_one_percent_move_oi"] = gamma_per_one_percent_move_oi
        if gamma_per_one_percent_move_vol is not UNSET:
            field_dict["gamma_per_one_percent_move_vol"] = gamma_per_one_percent_move_vol
        if price is not UNSET:
            field_dict["price"] = price
        if time is not UNSET:
            field_dict["time"] = time
        if vanna_per_one_percent_move_oi is not UNSET:
            field_dict["vanna_per_one_percent_move_oi"] = vanna_per_one_percent_move_oi
        if vanna_per_one_percent_move_vol is not UNSET:
            field_dict["vanna_per_one_percent_move_vol"] = vanna_per_one_percent_move_vol

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        charm_per_one_percent_move_oi = d.pop("charm_per_one_percent_move_oi", UNSET)

        charm_per_one_percent_move_vol = d.pop("charm_per_one_percent_move_vol", UNSET)

        delta_per_one_percent_move_oi = d.pop("delta_per_one_percent_move_oi", UNSET)

        delta_per_one_percent_move_vol = d.pop("delta_per_one_percent_move_vol", UNSET)

        gamma_per_one_percent_move_oi = d.pop("gamma_per_one_percent_move_oi", UNSET)

        gamma_per_one_percent_move_vol = d.pop("gamma_per_one_percent_move_vol", UNSET)

        price = d.pop("price", UNSET)

        time = d.pop("time", UNSET)

        vanna_per_one_percent_move_oi = d.pop("vanna_per_one_percent_move_oi", UNSET)

        vanna_per_one_percent_move_vol = d.pop("vanna_per_one_percent_move_vol", UNSET)

        spot_gex_exposures_per_1_min = cls(
            charm_per_one_percent_move_oi=charm_per_one_percent_move_oi,
            charm_per_one_percent_move_vol=charm_per_one_percent_move_vol,
            delta_per_one_percent_move_oi=delta_per_one_percent_move_oi,
            delta_per_one_percent_move_vol=delta_per_one_percent_move_vol,
            gamma_per_one_percent_move_oi=gamma_per_one_percent_move_oi,
            gamma_per_one_percent_move_vol=gamma_per_one_percent_move_vol,
            price=price,
            time=time,
            vanna_per_one_percent_move_oi=vanna_per_one_percent_move_oi,
            vanna_per_one_percent_move_vol=vanna_per_one_percent_move_vol,
        )

        spot_gex_exposures_per_1_min.additional_properties = d
        return spot_gex_exposures_per_1_min

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
