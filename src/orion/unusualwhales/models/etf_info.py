from typing import TYPE_CHECKING, Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.etf_info_data import EtfInfoData


T = TypeVar("T", bound="EtfInfo")


@_attrs_define
class EtfInfo:
    """Returns information about the ETF.

    Example:
        {'data': {'aum': '428887833900', 'avg30_volume': '73784934', 'call_vol': 284364, 'description': 'The Trust seeks
            to achieve its investment objective by holding a portfolio of the common stocks that are included in the index
            (the “Portfolio”), with the weight of each stock in the Portfolio substantially corresponding to the weight of
            such stock in the index.', 'domicile': 'US', 'etf_company': 'SPDR', 'expense_ratio': '0.0945', 'has_options':
            True, 'holdings_count': 503, 'inception_date': datetime.date(1993, 1, 22), 'name': 'SPDR S&P 500 ETF Trust',
            'opt_vol': 533227, 'put_vol': 248863, 'stock_vol': 4348819, 'website':
            'https://www.ssga.com/us/en/institutional/etfs/funds/spdr-sp-500-etf-trust-spy'}}

    Attributes:
        data (Union[Unset, EtfInfoData]): The data of the ETF.
        website (Union[Unset, str]): A link to the website of the ETF. Example:
            https://www.ssga.com/us/en/institutional/etfs/funds/spdr-sp-500-etf-trust-spy.
    """

    data: Union[Unset, "EtfInfoData"] = UNSET
    website: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Union[Unset, Dict[str, Any]] = UNSET
        if not isinstance(self.data, Unset):
            data = self.data.to_dict()

        website = self.website

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if data is not UNSET:
            field_dict["data"] = data
        if website is not UNSET:
            field_dict["website"] = website

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        from ..models.etf_info_data import EtfInfoData

        d = src_dict.copy()
        _data = d.pop("data", UNSET)
        data: Union[Unset, EtfInfoData]
        if isinstance(_data, Unset):
            data = UNSET
        else:
            data = EtfInfoData.from_dict(_data)

        website = d.pop("website", UNSET)

        etf_info = cls(
            data=data,
            website=website,
        )

        etf_info.additional_properties = d
        return etf_info

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
