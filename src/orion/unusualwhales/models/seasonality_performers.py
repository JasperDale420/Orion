from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.market_general_sector import MarketGeneralSector
from ..types import UNSET, Unset

T = TypeVar("T", bound="SeasonalityPerformers")


@_attrs_define
class SeasonalityPerformers:
    """The price change for the ticker over the given count of years.

    Example:
        {'data': [{'avg_change': '0.0592', 'marketcap': '19427901578', 'max_change': '0.1104', 'median_change':
            '0.0639', 'min_change': '0.0091', 'month': 5, 'positive_closes': 11, 'positive_months_perc': '1.1', 'sector':
            'Healthcare', 'ticker': 'ICLR', 'years': 10}, {'avg_change': '0.0577', 'marketcap': '1927713687', 'max_change':
            '0.2301', 'median_change': '0.0417', 'min_change': '-0.2485', 'month': 5, 'positive_closes': 10,
            'positive_months_perc': '1', 'sector': 'Technology', 'ticker': 'AMBA', 'years': 10}]}

    Attributes:
        avg_change (Union[Unset, float]): The average relative price change per day for the month. Example:
            0.09494354167379757.
        marketcap (Union[Unset, str]): The marketcap of the underlying ticker. If the issue type of the ticker is ETF
            then the marketcap represents the AUM. Example: 2965813810400.
        max_change (Union[Unset, float]): The maximum relative price change per day for the month. Example:
            0.14970489711277724.
        median_change (Union[Unset, float]): The median relative price change per day for the month. Example:
            0.09494354167379757.
        min_change (Union[Unset, float]): The minimum relative price change per day for the month. Example:
            0.0401821862348179.
        month (Union[Unset, int]): The number indicating the month the data applies for. e.g. 1 -> January, 2 ->
            February, ... Example: 5.
        positive_closes (Union[Unset, int]): The number of years, the month had a positive close. Example: 2.
        positive_months_perc (Union[Unset, float]): The relative amount of times, the month had a positive close.
            Multiply with 100 to get the amount in percent. Example: 0.6667.
        sector (Union[Unset, MarketGeneralSector]): The financial sector of the ticker. Empty if unknown or not
            applicable such as ETF/Index. Example: Technology.
        ticker (Union[Unset, str]): The stock ticker. Example: AAPL.
        years (Union[Unset, float]): The number of years used to calculate the data. Example: 3.
    """

    avg_change: Union[Unset, float] = UNSET
    marketcap: Union[Unset, str] = UNSET
    max_change: Union[Unset, float] = UNSET
    median_change: Union[Unset, float] = UNSET
    min_change: Union[Unset, float] = UNSET
    month: Union[Unset, int] = UNSET
    positive_closes: Union[Unset, int] = UNSET
    positive_months_perc: Union[Unset, float] = UNSET
    sector: Union[Unset, MarketGeneralSector] = UNSET
    ticker: Union[Unset, str] = UNSET
    years: Union[Unset, float] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        avg_change = self.avg_change

        marketcap = self.marketcap

        max_change = self.max_change

        median_change = self.median_change

        min_change = self.min_change

        month = self.month

        positive_closes = self.positive_closes

        positive_months_perc = self.positive_months_perc

        sector: Union[Unset, str] = UNSET
        if not isinstance(self.sector, Unset):
            sector = self.sector.value

        ticker = self.ticker

        years = self.years

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if avg_change is not UNSET:
            field_dict["avg_change"] = avg_change
        if marketcap is not UNSET:
            field_dict["marketcap"] = marketcap
        if max_change is not UNSET:
            field_dict["max_change"] = max_change
        if median_change is not UNSET:
            field_dict["median_change"] = median_change
        if min_change is not UNSET:
            field_dict["min_change"] = min_change
        if month is not UNSET:
            field_dict["month"] = month
        if positive_closes is not UNSET:
            field_dict["positive_closes"] = positive_closes
        if positive_months_perc is not UNSET:
            field_dict["positive_months_perc"] = positive_months_perc
        if sector is not UNSET:
            field_dict["sector"] = sector
        if ticker is not UNSET:
            field_dict["ticker"] = ticker
        if years is not UNSET:
            field_dict["years"] = years

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        avg_change = d.pop("avg_change", UNSET)

        marketcap = d.pop("marketcap", UNSET)

        max_change = d.pop("max_change", UNSET)

        median_change = d.pop("median_change", UNSET)

        min_change = d.pop("min_change", UNSET)

        month = d.pop("month", UNSET)

        positive_closes = d.pop("positive_closes", UNSET)

        positive_months_perc = d.pop("positive_months_perc", UNSET)

        _sector = d.pop("sector", UNSET)
        sector: Union[Unset, MarketGeneralSector]
        if isinstance(_sector, Unset):
            sector = UNSET
        else:
            sector = MarketGeneralSector(_sector)

        ticker = d.pop("ticker", UNSET)

        years = d.pop("years", UNSET)

        seasonality_performers = cls(
            avg_change=avg_change,
            marketcap=marketcap,
            max_change=max_change,
            median_change=median_change,
            min_change=min_change,
            month=month,
            positive_closes=positive_closes,
            positive_months_perc=positive_months_perc,
            sector=sector,
            ticker=ticker,
            years=years,
        )

        seasonality_performers.additional_properties = d
        return seasonality_performers

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
