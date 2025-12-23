import datetime
from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.market_general_sector import MarketGeneralSector
from ..models.stock_earnings_time import StockEarningsTime
from ..models.stock_issue_type import StockIssueType
from ..types import UNSET, Unset

T = TypeVar("T", bound="TickerInfo")


@_attrs_define
class TickerInfo:
    """General information about the given ticker.

    Example:
        {'data': {'announce_time': 'unkown', 'avg30_volume': '55973002', 'full_name': 'APPLE', 'has_dividend': True,
            'has_earnings_history': True, 'has_investment_arm': False, 'has_options': True, 'issue_type': 'Common Stock',
            'marketcap': '2776014233920', 'marketcap_size': 'big', 'next_earnings_date': datetime.date(2023, 10, 26),
            'sector': 'Technology', 'short_description': 'Apple Inc. is an American multinational technology company
            headquartered in Cupertino, California. Apple is the worlds largest technology company by ...', 'symbol':
            'AAPL', 'uw_tags': []}}

    Attributes:
        announce_time (Union[Unset, StockEarningsTime]): The time when the earnings will be released. Example:
            premarket.
        avg30_volume (Union[Unset, str]): The avg stock volume for the stock last 30 days. Example: 55973002.
        full_name (Union[Unset, str]): Full name of the ticker. Example: APPLE.
        has_dividend (Union[Unset, bool]): Boolean flag whether the company pays dividends. Example: True.
        has_earnings_history (Union[Unset, bool]): Boolean flag whether historic earnings data is present. Example:
            True.
        has_investment_arm (Union[Unset, bool]): Boolean flag whether the given stock is holding stocks of other
            companies. Example: True.
        has_options (Union[Unset, bool]): Boolean flag whether the company has options. Example: True.
        issue_type (Union[Unset, StockIssueType]): The issue type of the ticker. Example: Common Stock.
        marketcap (Union[Unset, str]): The marketcap of the underlying ticker. If the issue type of the ticker is ETF
            then the marketcap represents the AUM. Example: 2965813810400.
        next_earnings_date (Union[Unset, datetime.date]): The next earnings date of the ticker. Null if either unknown
            as of now or if the ticker does not have any earnings such as an ETF Example: 2023-10-26.
        sector (Union[Unset, MarketGeneralSector]): The financial sector of the ticker. Empty if unknown or not
            applicable such as ETF/Index. Example: Technology.
        short_description (Union[Unset, str]): Short description about what the stock does. Example: Apple Inc. is an
            American multinational technology company headquartered in Cupertino, California. Apple is the worlds largest
            technology company by ....
    """

    announce_time: Union[Unset, StockEarningsTime] = UNSET
    avg30_volume: Union[Unset, str] = UNSET
    full_name: Union[Unset, str] = UNSET
    has_dividend: Union[Unset, bool] = UNSET
    has_earnings_history: Union[Unset, bool] = UNSET
    has_investment_arm: Union[Unset, bool] = UNSET
    has_options: Union[Unset, bool] = UNSET
    issue_type: Union[Unset, StockIssueType] = UNSET
    marketcap: Union[Unset, str] = UNSET
    next_earnings_date: Union[Unset, datetime.date] = UNSET
    sector: Union[Unset, MarketGeneralSector] = UNSET
    short_description: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        announce_time: Union[Unset, str] = UNSET
        if not isinstance(self.announce_time, Unset):
            announce_time = self.announce_time.value

        avg30_volume = self.avg30_volume

        full_name = self.full_name

        has_dividend = self.has_dividend

        has_earnings_history = self.has_earnings_history

        has_investment_arm = self.has_investment_arm

        has_options = self.has_options

        issue_type: Union[Unset, str] = UNSET
        if not isinstance(self.issue_type, Unset):
            issue_type = self.issue_type.value

        marketcap = self.marketcap

        next_earnings_date: Union[Unset, str] = UNSET
        if not isinstance(self.next_earnings_date, Unset):
            next_earnings_date = self.next_earnings_date.isoformat()

        sector: Union[Unset, str] = UNSET
        if not isinstance(self.sector, Unset):
            sector = self.sector.value

        short_description = self.short_description

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if announce_time is not UNSET:
            field_dict["announce_time"] = announce_time
        if avg30_volume is not UNSET:
            field_dict["avg30_volume"] = avg30_volume
        if full_name is not UNSET:
            field_dict["full_name"] = full_name
        if has_dividend is not UNSET:
            field_dict["has_dividend"] = has_dividend
        if has_earnings_history is not UNSET:
            field_dict["has_earnings_history"] = has_earnings_history
        if has_investment_arm is not UNSET:
            field_dict["has_investment_arm"] = has_investment_arm
        if has_options is not UNSET:
            field_dict["has_options"] = has_options
        if issue_type is not UNSET:
            field_dict["issue_type"] = issue_type
        if marketcap is not UNSET:
            field_dict["marketcap"] = marketcap
        if next_earnings_date is not UNSET:
            field_dict["next_earnings_date"] = next_earnings_date
        if sector is not UNSET:
            field_dict["sector"] = sector
        if short_description is not UNSET:
            field_dict["short_description"] = short_description

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        _announce_time = d.pop("announce_time", UNSET)
        announce_time: Union[Unset, StockEarningsTime]
        if isinstance(_announce_time, Unset):
            announce_time = UNSET
        else:
            announce_time = StockEarningsTime(_announce_time)

        avg30_volume = d.pop("avg30_volume", UNSET)

        full_name = d.pop("full_name", UNSET)

        has_dividend = d.pop("has_dividend", UNSET)

        has_earnings_history = d.pop("has_earnings_history", UNSET)

        has_investment_arm = d.pop("has_investment_arm", UNSET)

        has_options = d.pop("has_options", UNSET)

        _issue_type = d.pop("issue_type", UNSET)
        issue_type: Union[Unset, StockIssueType]
        if isinstance(_issue_type, Unset):
            issue_type = UNSET
        else:
            issue_type = StockIssueType(_issue_type)

        marketcap = d.pop("marketcap", UNSET)

        _next_earnings_date = d.pop("next_earnings_date", UNSET)
        next_earnings_date: Union[Unset, datetime.date]
        if isinstance(_next_earnings_date, Unset):
            next_earnings_date = UNSET
        else:
            next_earnings_date = isoparse(_next_earnings_date).date()

        _sector = d.pop("sector", UNSET)
        sector: Union[Unset, MarketGeneralSector]
        if isinstance(_sector, Unset):
            sector = UNSET
        else:
            sector = MarketGeneralSector(_sector)

        short_description = d.pop("short_description", UNSET)

        ticker_info = cls(
            announce_time=announce_time,
            avg30_volume=avg30_volume,
            full_name=full_name,
            has_dividend=has_dividend,
            has_earnings_history=has_earnings_history,
            has_investment_arm=has_investment_arm,
            has_options=has_options,
            issue_type=issue_type,
            marketcap=marketcap,
            next_earnings_date=next_earnings_date,
            sector=sector,
            short_description=short_description,
        )

        ticker_info.additional_properties = d
        return ticker_info

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
