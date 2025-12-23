from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="Greeks")


@_attrs_define
class Greeks:
    """The greek values for a set of call and put contracts at the same strike and expiry for a ticker.

    Example:
        {'data': [{'call_charm': '9.32', 'call_delta': '0.5', 'call_gamma': '0.0051', 'call_iv': '0.3', 'call_rho':
            '0.0321', 'call_theta': '-0.62', 'call_vanna': '-0.91', 'call_vega': '0.15', 'date': datetime.date(2024, 1, 1),
            'expiry': datetime.date(2024, 1, 5), 'put_charm': '9.32', 'put_delta': '-0.51', 'put_gamma': '0.005', 'put_iv':
            '0.29', 'put_rho': '-0.022', 'put_theta': '-0.62', 'put_vanna': '-0.91', 'put_vega': '0.15', 'strike': '480.0',
            'ticker': 'SPY'}, {'call_charm': '9.32', 'call_delta': '0.45', 'call_gamma': '0.003', 'call_iv': '0.33',
            'call_rho': '0.0321', 'call_theta': '-0.62', 'call_vanna': '-0.91', 'call_vega': '0.15', 'date':
            datetime.date(2024, 1, 1), 'expiry': datetime.date(2024, 1, 5), 'put_charm': '9.32', 'put_delta': '-0.55',
            'put_gamma': '0.007', 'put_iv': '0.32', 'put_rho': '-0.022', 'put_theta': '-0.62', 'put_vanna': '-0.91',
            'put_vega': '0.15', 'strike': '490.0', 'ticker': 'SPY'}]}

    Attributes:
        call_charm (Union[Unset, str]):  Example: 9.2.
        call_delta (Union[Unset, str]):  Example: 0.5.
        call_gamma (Union[Unset, str]):  Example: 0.005.
        call_iv (Union[Unset, str]):  Example: 0.30.
        call_rho (Union[Unset, str]):  Example: -0.002.
        call_theta (Union[Unset, str]): Theta is the change in the option value over time. Example: -0.64.
        call_vanna (Union[Unset, str]): Vanna is the sum of the vanna values of all transactions that executed
            multiplied by the open interest and the number of shares per contract (typically 100 shares per contract).
            Example: -0.9.
        call_vega (Union[Unset, str]): Vega is the sum of the vega values of all transactions that executed multiplied
            by the open interest and the number of shares per contract (typically 100 shares per contract). Example: 0.15.
        date (Union[Unset, str]): An ISO date. Example: 2024-01-09.
        expiry (Union[Unset, str]): An ISO date. Example: 2024-01-09.
        put_charm (Union[Unset, str]):  Example: 9.2.
        put_delta (Union[Unset, str]):  Example: 0.5.
        put_gamma (Union[Unset, str]):  Example: 0.005.
        put_iv (Union[Unset, str]):  Example: 0.30.
        put_rho (Union[Unset, str]):  Example: -0.002.
        put_theta (Union[Unset, str]): Theta is the change in the option value over time. Example: -0.64.
        put_vanna (Union[Unset, str]): Vanna is the sum of the vanna values of all transactions that executed multiplied
            by the open interest and the number of shares per contract (typically 100 shares per contract). Example: -0.9.
        put_vega (Union[Unset, str]): Vega is the sum of the vega values of all transactions that executed multiplied by
            the open interest and the number of shares per contract (typically 100 shares per contract). Example: 0.15.
        strike (Union[Unset, str]): The strike price of an option contract. Example: 150.0.
        ticker (Union[Unset, str]): The stock ticker. Example: AAPL.
    """

    call_charm: Union[Unset, str] = UNSET
    call_delta: Union[Unset, str] = UNSET
    call_gamma: Union[Unset, str] = UNSET
    call_iv: Union[Unset, str] = UNSET
    call_rho: Union[Unset, str] = UNSET
    call_theta: Union[Unset, str] = UNSET
    call_vanna: Union[Unset, str] = UNSET
    call_vega: Union[Unset, str] = UNSET
    date: Union[Unset, str] = UNSET
    expiry: Union[Unset, str] = UNSET
    put_charm: Union[Unset, str] = UNSET
    put_delta: Union[Unset, str] = UNSET
    put_gamma: Union[Unset, str] = UNSET
    put_iv: Union[Unset, str] = UNSET
    put_rho: Union[Unset, str] = UNSET
    put_theta: Union[Unset, str] = UNSET
    put_vanna: Union[Unset, str] = UNSET
    put_vega: Union[Unset, str] = UNSET
    strike: Union[Unset, str] = UNSET
    ticker: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        call_charm = self.call_charm

        call_delta = self.call_delta

        call_gamma = self.call_gamma

        call_iv = self.call_iv

        call_rho = self.call_rho

        call_theta = self.call_theta

        call_vanna = self.call_vanna

        call_vega = self.call_vega

        date = self.date

        expiry = self.expiry

        put_charm = self.put_charm

        put_delta = self.put_delta

        put_gamma = self.put_gamma

        put_iv = self.put_iv

        put_rho = self.put_rho

        put_theta = self.put_theta

        put_vanna = self.put_vanna

        put_vega = self.put_vega

        strike = self.strike

        ticker = self.ticker

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if call_charm is not UNSET:
            field_dict["call_charm"] = call_charm
        if call_delta is not UNSET:
            field_dict["call_delta"] = call_delta
        if call_gamma is not UNSET:
            field_dict["call_gamma"] = call_gamma
        if call_iv is not UNSET:
            field_dict["call_iv"] = call_iv
        if call_rho is not UNSET:
            field_dict["call_rho"] = call_rho
        if call_theta is not UNSET:
            field_dict["call_theta"] = call_theta
        if call_vanna is not UNSET:
            field_dict["call_vanna"] = call_vanna
        if call_vega is not UNSET:
            field_dict["call_vega"] = call_vega
        if date is not UNSET:
            field_dict["date"] = date
        if expiry is not UNSET:
            field_dict["expiry"] = expiry
        if put_charm is not UNSET:
            field_dict["put_charm"] = put_charm
        if put_delta is not UNSET:
            field_dict["put_delta"] = put_delta
        if put_gamma is not UNSET:
            field_dict["put_gamma"] = put_gamma
        if put_iv is not UNSET:
            field_dict["put_iv"] = put_iv
        if put_rho is not UNSET:
            field_dict["put_rho"] = put_rho
        if put_theta is not UNSET:
            field_dict["put_theta"] = put_theta
        if put_vanna is not UNSET:
            field_dict["put_vanna"] = put_vanna
        if put_vega is not UNSET:
            field_dict["put_vega"] = put_vega
        if strike is not UNSET:
            field_dict["strike"] = strike
        if ticker is not UNSET:
            field_dict["ticker"] = ticker

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        call_charm = d.pop("call_charm", UNSET)

        call_delta = d.pop("call_delta", UNSET)

        call_gamma = d.pop("call_gamma", UNSET)

        call_iv = d.pop("call_iv", UNSET)

        call_rho = d.pop("call_rho", UNSET)

        call_theta = d.pop("call_theta", UNSET)

        call_vanna = d.pop("call_vanna", UNSET)

        call_vega = d.pop("call_vega", UNSET)

        date = d.pop("date", UNSET)

        expiry = d.pop("expiry", UNSET)

        put_charm = d.pop("put_charm", UNSET)

        put_delta = d.pop("put_delta", UNSET)

        put_gamma = d.pop("put_gamma", UNSET)

        put_iv = d.pop("put_iv", UNSET)

        put_rho = d.pop("put_rho", UNSET)

        put_theta = d.pop("put_theta", UNSET)

        put_vanna = d.pop("put_vanna", UNSET)

        put_vega = d.pop("put_vega", UNSET)

        strike = d.pop("strike", UNSET)

        ticker = d.pop("ticker", UNSET)

        greeks = cls(
            call_charm=call_charm,
            call_delta=call_delta,
            call_gamma=call_gamma,
            call_iv=call_iv,
            call_rho=call_rho,
            call_theta=call_theta,
            call_vanna=call_vanna,
            call_vega=call_vega,
            date=date,
            expiry=expiry,
            put_charm=put_charm,
            put_delta=put_delta,
            put_gamma=put_gamma,
            put_iv=put_iv,
            put_rho=put_rho,
            put_theta=put_theta,
            put_vanna=put_vanna,
            put_vega=put_vega,
            strike=strike,
            ticker=ticker,
        )

        greeks.additional_properties = d
        return greeks

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
