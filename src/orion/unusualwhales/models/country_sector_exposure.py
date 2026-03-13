from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.etf_countries_item import EtfCountriesItem
    from ..models.etf_sectors_item import EtfSectorsItem


T = TypeVar("T", bound="CountrySectorExposure")


@_attrs_define
class CountrySectorExposure:
    """
    Example:
        {'country': [{'country': 'Ireland', 'weight': '0.0164'}, {'country': 'Other', 'weight': '0.0059'}, {'country':
            'Switzerland', 'weight': '0.0043'}, {'country': 'Netherlands', 'weight': '0.0015'}, {'country': 'Canada',
            'weight': '0.0014'}, {'country': 'Bermuda', 'weight': '0.0011'}, {'country': 'Israel', 'weight': '0.0001'},
            {'country': 'United States', 'weight': '0.9693'}], 'sector': [{'sector': 'Basic Materials', 'weight': '0.022'},
            {'sector': 'Communication Services', 'weight': '0.0861'}, {'sector': 'Consumer Cyclical', 'weight': '0.1091'},
            {'sector': 'Consumer Defensive', 'weight': '0.0626'}, {'sector': 'Energy', 'weight': '0.041'}, {'sector':
            'Financial Services', 'weight': '0.125'}, {'sector': 'Healthcare', 'weight': '0.1272'}, {'sector':
            'Industrials', 'weight': '0.0816'}, {'sector': 'Other', 'weight': '0.00009999999999990905'}, {'sector': 'Real
            Estate', 'weight': '0.0243'}, {'sector': 'Technology', 'weight': '0.2971'}, {'sector': 'Utilities', 'weight':
            '0.0239'}]}

    Attributes:
        country (Union[Unset, List['EtfCountriesItem']]): A list of countries with their exposure by percentage.
            Example: [
              {
                "country": "Netherlands",
                "weight": "0.0015"
              },
              {
                "country": "Canada",
                "weight": "0.0014"
              }
            ]
            .
        sector (Union[Unset, List['EtfSectorsItem']]): A list of sectors with their exposure by percentage. Example: [
              {
                "sector": "Basic Materials",
                "weight": "0.022"
              },
              {
                "sector": "Communication Services",
                "weight": "0.0861"
              }
            ]
                title: Etf Sectors
            .
    """

    country: Unset | list["EtfCountriesItem"] = UNSET
    sector: Unset | list["EtfSectorsItem"] = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        country: Unset | list[dict[str, Any]] = UNSET
        if not isinstance(self.country, Unset):
            country = []
            for componentsschemas_etf_countries_item_data in self.country:
                componentsschemas_etf_countries_item = componentsschemas_etf_countries_item_data.to_dict()
                country.append(componentsschemas_etf_countries_item)

        sector: Unset | list[dict[str, Any]] = UNSET
        if not isinstance(self.sector, Unset):
            sector = []
            for componentsschemas_etf_sectors_item_data in self.sector:
                componentsschemas_etf_sectors_item = componentsschemas_etf_sectors_item_data.to_dict()
                sector.append(componentsschemas_etf_sectors_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if country is not UNSET:
            field_dict["country"] = country
        if sector is not UNSET:
            field_dict["sector"] = sector

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: dict[str, Any]) -> T:
        from ..models.etf_countries_item import EtfCountriesItem
        from ..models.etf_sectors_item import EtfSectorsItem

        d = src_dict.copy()
        country = []
        _country = d.pop("country", UNSET)
        for componentsschemas_etf_countries_item_data in _country or []:
            componentsschemas_etf_countries_item = EtfCountriesItem.from_dict(componentsschemas_etf_countries_item_data)

            country.append(componentsschemas_etf_countries_item)

        sector = []
        _sector = d.pop("sector", UNSET)
        for componentsschemas_etf_sectors_item_data in _sector or []:
            componentsschemas_etf_sectors_item = EtfSectorsItem.from_dict(componentsschemas_etf_sectors_item_data)

            sector.append(componentsschemas_etf_sectors_item)

        country_sector_exposure = cls(
            country=country,
            sector=sector,
        )

        country_sector_exposure.additional_properties = d
        return country_sector_exposure

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
