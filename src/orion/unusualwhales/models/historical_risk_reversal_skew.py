from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="HistoricalRiskReversalSkew")


@_attrs_define
class HistoricalRiskReversalSkew:
    """
    Example:
        {'data': [{'date': datetime.date(2024, 1, 1), 'risk_reversal': '0.014', 'ticker': 'SPY'}, {'date':
            datetime.date(2024, 1, 2), 'risk_reversal': '0.009', 'ticker': 'SPY'}]}

    Attributes:
        date (Union[Unset, str]): An ISO date. Example: 2024-01-09.
        risk_reversal (Union[Unset, str]): The difference between the iv of a put and a call with similar absolute
            deltas. Example: -0.021.
        ticker (Union[Unset, str]): The stock ticker. Example: AAPL.
    """

    date: Union[Unset, str] = UNSET
    risk_reversal: Union[Unset, str] = UNSET
    ticker: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        date = self.date

        risk_reversal = self.risk_reversal

        ticker = self.ticker

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if date is not UNSET:
            field_dict["date"] = date
        if risk_reversal is not UNSET:
            field_dict["risk_reversal"] = risk_reversal
        if ticker is not UNSET:
            field_dict["ticker"] = ticker

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        date = d.pop("date", UNSET)

        risk_reversal = d.pop("risk_reversal", UNSET)

        ticker = d.pop("ticker", UNSET)

        historical_risk_reversal_skew = cls(
            date=date,
            risk_reversal=risk_reversal,
            ticker=ticker,
        )

        historical_risk_reversal_skew.additional_properties = d
        return historical_risk_reversal_skew

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
