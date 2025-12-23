from typing import TYPE_CHECKING, Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.darkpool_trade import DarkpoolTrade


T = TypeVar("T", bound="DarkpoolTradeResponse")


@_attrs_define
class DarkpoolTradeResponse:
    """The response of the darkpool trades endpoint. Contains a property named data that contains an array of DarkpoolTrade
    objects

        Example:
            {'data': [{'executed_at': datetime.datetime(2023, 2, 16, 0, 59, 44, tzinfo=datetime.timezone.utc),
                'ext_hour_sold_codes': 'extended_hours_trade', 'market_center': 'L', 'nbbo_ask': '19', 'nbbo_ask_quantity':
                6600, 'nbbo_bid': '18.99', 'nbbo_bid_quantity': 29100, 'premium': '121538.56', 'price': '18.9904',
                'sale_cond_codes': None, 'size': 6400, 'ticker': 'QID', 'tracking_id': 71984388012245, 'trade_code': None,
                'trade_settlement': 'regular_settlement', 'volume': 9946819}, {'executed_at': datetime.datetime(2023, 2, 16, 0,
                59, 44, tzinfo=datetime.timezone.utc), 'ext_hour_sold_codes': 'extended_hours_trade', 'market_center': 'L',
                'nbbo_ask': '19', 'nbbo_ask_quantity': 6600, 'nbbo_bid': '18.99', 'nbbo_bid_quantity': 29100, 'premium':
                '121538.56', 'price': '18.9904', 'sale_cond_codes': None, 'size': 6400, 'ticker': 'QID', 'tracking_id':
                71984388012245, 'trade_code': None, 'trade_settlement': 'regular_settlement', 'volume': 9946819}]}

        Attributes:
            data (Union[Unset, List['DarkpoolTrade']]):
    """

    data: Union[Unset, List["DarkpoolTrade"]] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Union[Unset, List[Dict[str, Any]]] = UNSET
        if not isinstance(self.data, Unset):
            data = []
            for data_item_data in self.data:
                data_item = data_item_data.to_dict()
                data.append(data_item)

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if data is not UNSET:
            field_dict["data"] = data

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        from ..models.darkpool_trade import DarkpoolTrade

        d = src_dict.copy()
        data = []
        _data = d.pop("data", UNSET)
        for data_item_data in _data or []:
            data_item = DarkpoolTrade.from_dict(data_item_data)

            data.append(data_item)

        darkpool_trade_response = cls(
            data=data,
        )

        darkpool_trade_response.additional_properties = d
        return darkpool_trade_response

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
