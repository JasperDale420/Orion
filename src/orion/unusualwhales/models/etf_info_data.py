from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="EtfInfoData")


@_attrs_define
class EtfInfoData:
    """The data of the ETF.

    Attributes:
        aum (Union[Unset, str]): The total assets under management (AUM) of the ETF. Example: 428887833900.
        avg30_volume (Union[Unset, str]): The avg stock volume for the stock last 30 days. Example: 55973002.
        call_vol (Union[Unset, int]): The sum of the size of all the call transactions that executed. Example: 990943.
        description (Union[Unset, str]): Information about the ETF. Example: The Trust seeks to achieve its investment
            objective by holding a portfolio of the common stocks that are included in the index (the “Portfolio”), with the
            weight of each stock in the Portfolio substantially corresponding to the weight of such stock in the index..
        domicile (Union[Unset, str]): The domicile of the ETF. Example: US.
        etf_company (Union[Unset, str]): The company which oversees the ETF. Example: SPDR.
        expense_ratio (Union[Unset, str]): The expense ratio of the ETF. Example: 0.0945.
        has_options (Union[Unset, bool]): Boolean flag whether the company has options. Example: True.
        holdings_count (Union[Unset, int]): The amount of holdings the ETF has. Example: 503.
        inception_date (Union[Unset, str]): The inception date of the ETF as an ISO date. Example: 1993-01-22.
        name (Union[Unset, str]): The full name of the ETF. Example: SPDR S&P 500 ETF Trust.
        opt_vol (Union[Unset, int]): The total options volume traded for the last Trading Day. Example: 533227.
        put_vol (Union[Unset, int]): The sum of the size of all the put transactions that executed. Example: 808326.
        stock_vol (Union[Unset, int]): The volume of the ticker for the Trading Day. Example: 23132119.
    """

    aum: Union[Unset, str] = UNSET
    avg30_volume: Union[Unset, str] = UNSET
    call_vol: Union[Unset, int] = UNSET
    description: Union[Unset, str] = UNSET
    domicile: Union[Unset, str] = UNSET
    etf_company: Union[Unset, str] = UNSET
    expense_ratio: Union[Unset, str] = UNSET
    has_options: Union[Unset, bool] = UNSET
    holdings_count: Union[Unset, int] = UNSET
    inception_date: Union[Unset, str] = UNSET
    name: Union[Unset, str] = UNSET
    opt_vol: Union[Unset, int] = UNSET
    put_vol: Union[Unset, int] = UNSET
    stock_vol: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        aum = self.aum

        avg30_volume = self.avg30_volume

        call_vol = self.call_vol

        description = self.description

        domicile = self.domicile

        etf_company = self.etf_company

        expense_ratio = self.expense_ratio

        has_options = self.has_options

        holdings_count = self.holdings_count

        inception_date = self.inception_date

        name = self.name

        opt_vol = self.opt_vol

        put_vol = self.put_vol

        stock_vol = self.stock_vol

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if aum is not UNSET:
            field_dict["aum"] = aum
        if avg30_volume is not UNSET:
            field_dict["avg30_volume"] = avg30_volume
        if call_vol is not UNSET:
            field_dict["call_vol"] = call_vol
        if description is not UNSET:
            field_dict["description"] = description
        if domicile is not UNSET:
            field_dict["domicile"] = domicile
        if etf_company is not UNSET:
            field_dict["etf_company"] = etf_company
        if expense_ratio is not UNSET:
            field_dict["expense_ratio"] = expense_ratio
        if has_options is not UNSET:
            field_dict["has_options"] = has_options
        if holdings_count is not UNSET:
            field_dict["holdings_count"] = holdings_count
        if inception_date is not UNSET:
            field_dict["inception_date"] = inception_date
        if name is not UNSET:
            field_dict["name"] = name
        if opt_vol is not UNSET:
            field_dict["opt_vol"] = opt_vol
        if put_vol is not UNSET:
            field_dict["put_vol"] = put_vol
        if stock_vol is not UNSET:
            field_dict["stock_vol"] = stock_vol

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        aum = d.pop("aum", UNSET)

        avg30_volume = d.pop("avg30_volume", UNSET)

        call_vol = d.pop("call_vol", UNSET)

        description = d.pop("description", UNSET)

        domicile = d.pop("domicile", UNSET)

        etf_company = d.pop("etf_company", UNSET)

        expense_ratio = d.pop("expense_ratio", UNSET)

        has_options = d.pop("has_options", UNSET)

        holdings_count = d.pop("holdings_count", UNSET)

        inception_date = d.pop("inception_date", UNSET)

        name = d.pop("name", UNSET)

        opt_vol = d.pop("opt_vol", UNSET)

        put_vol = d.pop("put_vol", UNSET)

        stock_vol = d.pop("stock_vol", UNSET)

        etf_info_data = cls(
            aum=aum,
            avg30_volume=avg30_volume,
            call_vol=call_vol,
            description=description,
            domicile=domicile,
            etf_company=etf_company,
            expense_ratio=expense_ratio,
            has_options=has_options,
            holdings_count=holdings_count,
            inception_date=inception_date,
            name=name,
            opt_vol=opt_vol,
            put_vol=put_vol,
            stock_vol=stock_vol,
        )

        etf_info_data.additional_properties = d
        return etf_info_data

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
