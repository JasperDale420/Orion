from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SeasonalityYearMonth")


@_attrs_define
class SeasonalityYearMonth:
    """
    Example:
        {'change': '-0.0469', 'close': 315.75, 'month': 9, 'open': 331.31, 'year': 2023}

    Attributes:
        change (Union[Unset, float]): The relative price change for the month. Example: 0.09494354167379757.
        close (Union[Unset, str]): The closing stock price of the ticker for the month. Example: 182.91.
        month (Union[Unset, int]): The number indicating the month the data applies for. e.g. 1 -> January, 2 ->
            February, ... Example: 5.
        open_ (Union[Unset, str]): The opening stock price of the ticker for the month. Example: 182.91.
        year (Union[Unset, int]): The Year. Example: 5.
    """

    change: Union[Unset, float] = UNSET
    close: Union[Unset, str] = UNSET
    month: Union[Unset, int] = UNSET
    open_: Union[Unset, str] = UNSET
    year: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        change = self.change

        close = self.close

        month = self.month

        open_ = self.open_

        year = self.year

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if change is not UNSET:
            field_dict["change"] = change
        if close is not UNSET:
            field_dict["close"] = close
        if month is not UNSET:
            field_dict["month"] = month
        if open_ is not UNSET:
            field_dict["open"] = open_
        if year is not UNSET:
            field_dict["year"] = year

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        change = d.pop("change", UNSET)

        close = d.pop("close", UNSET)

        month = d.pop("month", UNSET)

        open_ = d.pop("open", UNSET)

        year = d.pop("year", UNSET)

        seasonality_year_month = cls(
            change=change,
            close=close,
            month=month,
            open_=open_,
            year=year,
        )

        seasonality_year_month.additional_properties = d
        return seasonality_year_month

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
