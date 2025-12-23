from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="Earnings")


@_attrs_define
class Earnings:
    """Earnings information for a ticker and date.

    Example:
        {'data': [{'close': '24.22', 'close_date': datetime.date(2023, 2, 14), 'continent': 'North America',
            'country_code': 'US', 'country_name': 'UNITED STATES', 'ending_fiscal_quarter': datetime.date(2022, 12, 31),
            'eps_mean_est': '0.36', 'full_name': 'INVENTRUST PROPERTIES', 'has_options': True, 'implied_move':
            '0.1003106047550717336683417085', 'is_s_p_500': False, 'marketcap': '1649115201', 'prev': '24.27', 'prev_date':
            datetime.date(2023, 2, 13), 'report_date': datetime.date(2023, 2, 14), 'report_time': 'postmarket', 'sector':
            'Real Estate', 'source': 'company', 'street_mean_est': '0.36', 'symbol': 'IVT'}, {'close': '39.06',
            'close_date': datetime.date(2023, 2, 14), 'continent': 'Europe', 'country_code': 'LU', 'country_name':
            'LUXEMBOURG', 'ending_fiscal_quarter': datetime.date(2022, 12, 31), 'eps_mean_est': '0.18', 'full_name':
            'TERNIUM SA', 'has_options': True, 'implied_move': '0.06470822359148703557312252964', 'is_s_p_500': False,
            'marketcap': '7375279462', 'prev': '37.88', 'prev_date': datetime.date(2023, 2, 13), 'report_date':
            datetime.date(2023, 2, 14), 'report_time': 'postmarket', 'sector': 'Basic Materials', 'source': 'company',
            'street_mean_est': '0.183', 'symbol': 'TX'}]}

    Attributes:
        close (Union[Unset, float]): The price of the tick Example: 19.32.
        close_date (Union[Unset, str]): An ISO date. Example: 2024-01-09.
        continent (Union[Unset, float]): The price of the tick Example: 19.32.
        country_code (Union[Unset, float]): The price of the tick Example: 19.32.
        country_name (Union[Unset, float]): The price of the tick Example: 19.32.
        ending_fiscal_quarter (Union[Unset, str]): An ISO date. Example: 2024-01-09.
        eps_mean_est (Union[Unset, float]): The price of the tick Example: 19.32.
        full_name (Union[Unset, float]): The price of the tick Example: 19.32.
        has_options (Union[Unset, float]): The price of the tick Example: 19.32.
        implied_move (Union[Unset, float]): The price of the tick Example: 19.32.
        is_s_p_500 (Union[Unset, float]): The price of the tick Example: 19.32.
        marketcap (Union[Unset, float]): The price of the tick Example: 19.32.
        prev (Union[Unset, float]): The price of the tick Example: 19.32.
        prev_date (Union[Unset, str]): An ISO date. Example: 2024-01-09.
        report_date (Union[Unset, str]): An ISO date. Example: 2024-01-09.
        sector (Union[Unset, float]): The price of the tick Example: 19.32.
        source (Union[Unset, float]): The price of the tick Example: 19.32.
        street_mean_est (Union[Unset, float]): The price of the tick Example: 19.32.
        symbol (Union[Unset, float]): The price of the tick Example: 19.32.
    """

    close: Union[Unset, float] = UNSET
    close_date: Union[Unset, str] = UNSET
    continent: Union[Unset, float] = UNSET
    country_code: Union[Unset, float] = UNSET
    country_name: Union[Unset, float] = UNSET
    ending_fiscal_quarter: Union[Unset, str] = UNSET
    eps_mean_est: Union[Unset, float] = UNSET
    full_name: Union[Unset, float] = UNSET
    has_options: Union[Unset, float] = UNSET
    implied_move: Union[Unset, float] = UNSET
    is_s_p_500: Union[Unset, float] = UNSET
    marketcap: Union[Unset, float] = UNSET
    prev: Union[Unset, float] = UNSET
    prev_date: Union[Unset, str] = UNSET
    report_date: Union[Unset, str] = UNSET
    sector: Union[Unset, float] = UNSET
    source: Union[Unset, float] = UNSET
    street_mean_est: Union[Unset, float] = UNSET
    symbol: Union[Unset, float] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        close = self.close

        close_date = self.close_date

        continent = self.continent

        country_code = self.country_code

        country_name = self.country_name

        ending_fiscal_quarter = self.ending_fiscal_quarter

        eps_mean_est = self.eps_mean_est

        full_name = self.full_name

        has_options = self.has_options

        implied_move = self.implied_move

        is_s_p_500 = self.is_s_p_500

        marketcap = self.marketcap

        prev = self.prev

        prev_date = self.prev_date

        report_date = self.report_date

        sector = self.sector

        source = self.source

        street_mean_est = self.street_mean_est

        symbol = self.symbol

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if close is not UNSET:
            field_dict["close"] = close
        if close_date is not UNSET:
            field_dict["close_date"] = close_date
        if continent is not UNSET:
            field_dict["continent"] = continent
        if country_code is not UNSET:
            field_dict["country_code"] = country_code
        if country_name is not UNSET:
            field_dict["country_name"] = country_name
        if ending_fiscal_quarter is not UNSET:
            field_dict["ending_fiscal_quarter"] = ending_fiscal_quarter
        if eps_mean_est is not UNSET:
            field_dict["eps_mean_est"] = eps_mean_est
        if full_name is not UNSET:
            field_dict["full_name"] = full_name
        if has_options is not UNSET:
            field_dict["has_options"] = has_options
        if implied_move is not UNSET:
            field_dict["implied_move"] = implied_move
        if is_s_p_500 is not UNSET:
            field_dict["is_s_p_500"] = is_s_p_500
        if marketcap is not UNSET:
            field_dict["marketcap"] = marketcap
        if prev is not UNSET:
            field_dict["prev"] = prev
        if prev_date is not UNSET:
            field_dict["prev_date"] = prev_date
        if report_date is not UNSET:
            field_dict["report_date"] = report_date
        if sector is not UNSET:
            field_dict["sector"] = sector
        if source is not UNSET:
            field_dict["source"] = source
        if street_mean_est is not UNSET:
            field_dict["street_mean_est"] = street_mean_est
        if symbol is not UNSET:
            field_dict["symbol"] = symbol

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        close = d.pop("close", UNSET)

        close_date = d.pop("close_date", UNSET)

        continent = d.pop("continent", UNSET)

        country_code = d.pop("country_code", UNSET)

        country_name = d.pop("country_name", UNSET)

        ending_fiscal_quarter = d.pop("ending_fiscal_quarter", UNSET)

        eps_mean_est = d.pop("eps_mean_est", UNSET)

        full_name = d.pop("full_name", UNSET)

        has_options = d.pop("has_options", UNSET)

        implied_move = d.pop("implied_move", UNSET)

        is_s_p_500 = d.pop("is_s_p_500", UNSET)

        marketcap = d.pop("marketcap", UNSET)

        prev = d.pop("prev", UNSET)

        prev_date = d.pop("prev_date", UNSET)

        report_date = d.pop("report_date", UNSET)

        sector = d.pop("sector", UNSET)

        source = d.pop("source", UNSET)

        street_mean_est = d.pop("street_mean_est", UNSET)

        symbol = d.pop("symbol", UNSET)

        earnings = cls(
            close=close,
            close_date=close_date,
            continent=continent,
            country_code=country_code,
            country_name=country_name,
            ending_fiscal_quarter=ending_fiscal_quarter,
            eps_mean_est=eps_mean_est,
            full_name=full_name,
            has_options=has_options,
            implied_move=implied_move,
            is_s_p_500=is_s_p_500,
            marketcap=marketcap,
            prev=prev,
            prev_date=prev_date,
            report_date=report_date,
            sector=sector,
            source=source,
            street_mean_est=street_mean_est,
            symbol=symbol,
        )

        earnings.additional_properties = d
        return earnings

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
